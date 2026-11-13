from collections import OrderedDict
from threading import Lock
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, or_
from typing import List
from uuid import UUID

from app.database import get_async_session
from app.schemas.database import (
    UsersCreate, UsersRead, UsersUpdate,
    GtfsAgenciesCreate, GtfsAgenciesRead,
)
from app.schemas.auth import validate_password_breach
from app.schemas.responses import (
    AgencyNetworkPreviewLine,
    AgencyNetworkPreviewPoint,
    AgencyNetworkPreviewRead,
)
from app.models import (
    Users, GtfsAgencies, GtfsRoutes, GtfsTrips, GtfsStops, GtfsStopsTimes
)
from app.core.auth import get_current_user, require_admin, get_password_hash
from app.core.rate_limit import SlidingWindowRateLimiter

router = APIRouter()

NETWORK_PREVIEW_POINT_LIMIT = 32
NETWORK_PREVIEW_COORDINATE_PRECISION = 3
NETWORK_PREVIEW_CACHE_SECONDS = 3600
NETWORK_PREVIEW_CACHE_LIMIT = 256

_network_preview_limiter = SlidingWindowRateLimiter(
    max_requests=30,
    window_seconds=60,
)
_network_preview_cache: OrderedDict[
    UUID, tuple[float, AgencyNetworkPreviewRead]
] = OrderedDict()
_network_preview_cache_lock = Lock()


def _sample_network_points(
    points: list[tuple[float, float]],
    limit: int = NETWORK_PREVIEW_POINT_LIMIT,
) -> list[tuple[float, float]]:
    """Keep route endpoints and evenly sample the middle for a compact preview."""
    if len(points) <= limit:
        return points
    indexes = {
        round(position * (len(points) - 1) / (limit - 1))
        for position in range(limit)
    }
    return [points[index] for index in sorted(indexes)]


def _coarsen_network_point(latitude: float, longitude: float) -> tuple[float, float]:
    """Reduce public coordinates to roughly street-level, not stop-level, precision."""
    return (
        round(float(latitude), NETWORK_PREVIEW_COORDINATE_PRECISION),
        round(float(longitude), NETWORK_PREVIEW_COORDINATE_PRECISION),
    )


def _get_cached_network_preview(
    agency_id: UUID,
    *,
    now: float | None = None,
) -> AgencyNetworkPreviewRead | None:
    current = time.monotonic() if now is None else now
    with _network_preview_cache_lock:
        cached = _network_preview_cache.get(agency_id)
        if cached is None:
            return None
        expires_at, preview = cached
        if expires_at <= current:
            del _network_preview_cache[agency_id]
            return None
        _network_preview_cache.move_to_end(agency_id)
        return preview


def _cache_network_preview(
    preview: AgencyNetworkPreviewRead,
    *,
    now: float | None = None,
) -> AgencyNetworkPreviewRead:
    current = time.monotonic() if now is None else now
    with _network_preview_cache_lock:
        _network_preview_cache[preview.agency_id] = (
            current + NETWORK_PREVIEW_CACHE_SECONDS,
            preview,
        )
        _network_preview_cache.move_to_end(preview.agency_id)
        while len(_network_preview_cache) > NETWORK_PREVIEW_CACHE_LIMIT:
            _network_preview_cache.popitem(last=False)
    return preview


def _normalized_email(value: str) -> str:
    return value.strip().casefold()


async def _email_exists(db: AsyncSession, email: str, *, exclude_user_id=None) -> bool:
    query = select(Users.id).where(func.lower(Users.email) == _normalized_email(email))
    if exclude_user_id is not None:
        query = query.where(Users.id != exclude_user_id)
    return await db.scalar(query.limit(1)) is not None

# Users endpoints (admin only)
@router.get("/users/", response_model=List[UsersRead])
async def read_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(require_admin),
):
    result = await db.execute(
        select(Users)
        .where(Users.company_id == current_user.company_id)
        .order_by(Users.email.asc(), Users.id.asc())
        .offset(skip)
        .limit(limit)
    )
    users = result.scalars().all()
    return users

@router.get("/users/{user_id}", response_model=UsersRead)
async def read_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(require_admin),
):
    result = await db.execute(
        select(Users).where(
            Users.id == user_id,
            Users.company_id == current_user.company_id,
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.post("/users/", response_model=UsersRead)
async def create_user(
    user: UsersCreate,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(require_admin),
):
    if user.company_id != current_user.company_id:
        raise HTTPException(
            status_code=403,
            detail="Administrators can only create users in their own agency",
        )
    # Include schema defaults (in particular the analyst role) while accepting
    # only the explicit public contract declared by ``UsersCreate``.
    user_data = user.model_dump()
    normalized_email = _normalized_email(str(user_data["email"]))
    if await _email_exists(db, normalized_email):
        raise HTTPException(status_code=409, detail="User account could not be created")
    try:
        await validate_password_breach(user_data["password"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user_data["email"] = normalized_email
    user_data["role"] = user_data["role"].value
    user_data['password_hash'] = get_password_hash(user_data.pop('password'))

    db_user = Users(**user_data)
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

@router.put("/users/{user_id}", response_model=UsersRead)
async def update_user(
    user_id: UUID,
    user_update: UsersUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(require_admin),
):
    result = await db.execute(
        select(Users).where(
            Users.id == user_id,
            Users.company_id == current_user.company_id,
        )
    )
    db_user = result.scalar_one_or_none()
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = user_update.model_dump(exclude_unset=True)
    if db_user.id == current_user.id and "role" in update_data:
        raise HTTPException(
            status_code=400,
            detail="Administrators cannot change their own role",
        )
    if "email" in update_data:
        update_data["email"] = _normalized_email(str(update_data["email"]))
        if await _email_exists(db, update_data["email"], exclude_user_id=db_user.id):
            raise HTTPException(status_code=409, detail="User account could not be updated")
    if "password" in update_data:
        try:
            await validate_password_breach(update_data["password"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        update_data["password_hash"] = get_password_hash(update_data.pop("password"))
    if "role" in update_data:
        update_data["role"] = update_data["role"].value
    for field, value in update_data.items():
        setattr(db_user, field, value)

    await db.commit()
    await db.refresh(db_user)
    return db_user

# GTFS Agencies endpoints (authenticated users only)
@router.get("/agencies/", response_model=List[GtfsAgenciesRead])
async def read_agencies(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    search: str = Query("", max_length=100),
    db: AsyncSession = Depends(get_async_session),
):
    """List all agencies - public endpoint for registration"""
    query = select(GtfsAgencies).order_by(GtfsAgencies.agency_name)
    
    # Add search filter if provided
    if search:
        search_term = f"%{search.lower()}%"
        query = query.where(
            or_(
                GtfsAgencies.agency_name.ilike(search_term),
                GtfsAgencies.gtfs_agency_id.ilike(search_term)
            )
        )
    
    query = query.order_by(GtfsAgencies.id.asc()).offset(skip).limit(limit)
    result = await db.execute(query)
    agencies = result.scalars().all()
    return agencies


@router.get(
    "/agencies/{agency_id}/network-preview",
    response_model=AgencyNetworkPreviewRead,
)
async def read_agency_network_preview(
    agency_id: UUID,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_session),
):
    """Return an anonymous-safe sketch of every drawable route in the latest network."""
    _network_preview_limiter.enforce(request)
    response.headers["Cache-Control"] = "public, max-age=3600"

    cached = _get_cached_network_preview(agency_id)
    if cached is not None:
        return cached

    agency = await db.get(GtfsAgencies, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")

    latest_year = await db.scalar(
        select(func.max(GtfsRoutes.gtfs_year)).where(
            GtfsRoutes.agency_id == agency_id
        )
    )
    if latest_year is None:
        return _cache_network_preview(AgencyNetworkPreviewRead(
            agency_id=agency.id,
            agency_name=agency.agency_name,
            route_count=0,
            lines=[],
        ))

    latest_file_date = await db.scalar(
        select(func.max(GtfsRoutes.gtfs_file_date)).where(
            GtfsRoutes.agency_id == agency_id,
            GtfsRoutes.gtfs_year == latest_year,
        )
    )
    if latest_file_date is None:
        return _cache_network_preview(AgencyNetworkPreviewRead(
            agency_id=agency.id,
            agency_name=agency.agency_name,
            route_count=0,
            lines=[],
        ))

    dataset_filters = (
        GtfsRoutes.agency_id == agency_id,
        GtfsRoutes.gtfs_year == latest_year,
        GtfsRoutes.gtfs_file_date == latest_file_date,
    )
    route_count = int(
        await db.scalar(
            select(func.count()).select_from(GtfsRoutes).where(*dataset_filters)
        )
        or 0
    )
    route_result = await db.execute(
        select(GtfsRoutes)
        .where(*dataset_filters)
        .order_by(
            GtfsRoutes.route_sort_order.asc().nullslast(),
            GtfsRoutes.route_short_name.asc().nullslast(),
            GtfsRoutes.id.asc(),
        )
    )
    routes = list(route_result.scalars().all())
    if not routes:
        return _cache_network_preview(AgencyNetworkPreviewRead(
            agency_id=agency.id,
            agency_name=agency.agency_name,
            route_count=route_count,
            lines=[],
        ))

    route_ids = [route.id for route in routes]
    ranked_trips = (
        select(
            GtfsTrips.route_id.label("route_id"),
            GtfsTrips.id.label("trip_id"),
            func.row_number().over(
                partition_by=GtfsTrips.route_id,
                order_by=(
                    GtfsTrips.trip_id.asc(),
                    GtfsTrips.id.asc(),
                ),
            ).label("route_rank"),
        )
        .where(
            GtfsTrips.route_id.in_(route_ids),
            GtfsTrips.status == "gtfs",
        )
        .subquery()
    )
    representative_trips = await db.execute(
        select(ranked_trips.c.route_id, ranked_trips.c.trip_id).where(
            ranked_trips.c.route_rank == 1
        )
    )
    representative_trip_by_route = dict(representative_trips.all())

    trip_ids = list(representative_trip_by_route.values())
    points_by_trip: dict[UUID, list[tuple[float, float]]] = {
        trip_id: [] for trip_id in trip_ids
    }
    if trip_ids:
        stop_rows = await db.execute(
            select(
                GtfsStopsTimes.trip_id,
                GtfsStops.stop_lat,
                GtfsStops.stop_lon,
            )
            .join(GtfsStops, GtfsStops.id == GtfsStopsTimes.stop_id)
            .where(
                GtfsStopsTimes.trip_id.in_(trip_ids),
                GtfsStops.stop_lat.is_not(None),
                GtfsStops.stop_lon.is_not(None),
            )
            .order_by(
                GtfsStopsTimes.trip_id.asc(),
                GtfsStopsTimes.stop_sequence.asc().nullslast(),
                GtfsStopsTimes.id.asc(),
            )
        )
        for trip_id, latitude, longitude in stop_rows.all():
            point = _coarsen_network_point(latitude, longitude)
            if not points_by_trip[trip_id] or points_by_trip[trip_id][-1] != point:
                points_by_trip[trip_id].append(point)

    lines = []
    for route in routes:
        trip_id = representative_trip_by_route.get(route.id)
        sampled_points = _sample_network_points(points_by_trip.get(trip_id, []))
        if len(sampled_points) < 2:
            continue
        lines.append(
            AgencyNetworkPreviewLine(
                label=(route.route_short_name or route.route_long_name or "").strip(),
                color=(route.route_color or "").strip() or None,
                points=[
                    AgencyNetworkPreviewPoint(lat=latitude, lon=longitude)
                    for latitude, longitude in sampled_points
                ],
            )
        )

    return _cache_network_preview(AgencyNetworkPreviewRead(
        agency_id=agency.id,
        agency_name=agency.agency_name,
        route_count=route_count,
        lines=lines,
    ))

@router.get("/agencies/{agency_id}", response_model=GtfsAgenciesRead)
async def read_agency(agency_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    if agency_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Agency not found")
    agency = await db.get(GtfsAgencies, current_user.company_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    return agency

@router.post("/agencies/", response_model=GtfsAgenciesRead)
async def create_agency(agency: GtfsAgenciesCreate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(require_admin)):
    db_agency = GtfsAgencies(**agency.model_dump(exclude_unset=True))
    db.add(db_agency)
    await db.commit()
    await db.refresh(db_agency)
    return db_agency
