from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from typing import List, Optional
from uuid import UUID, uuid4

from app.database import get_async_session
from app.schemas.database import (
    BusesModelsRead,
    BusesCreate, BusesRead, BusesUpdate,
    BusesManufacturersRead, BusesModelsRefsRead,
)
from app.schemas.responses import (
    DepotCreateRequest, DepotUpdateRequest, DepotReadWithLocation,
    ShiftReadWithStructure, ShiftStructureItem,
    ShiftInfoResponse, RouteInfoBrief, TripInfoBrief,
    BusesModelsListItemRead, ShiftListItemRead,
)
from app.schemas.pagination import (
    PaginatedResponse, PaginationParams, build_paginated_response,
)
from app.schemas.requests import (
    BusModelCreateRequest,
    BusModelUpdateRequest,
    ShiftCreateRequest,
    ShiftUpdateRequest,
)
from app.models import (
    Users, BusesModels, Buses, Depots, GtfsStops,
    Shifts, ShiftsStructures, GtfsTrips, GtfsRoutes, GtfsCalendar,
    BusesManufacturers, BusesModelsRefs,
)
from app.core.auth import get_current_user
from app.core.shift_distance import (
    RecurrenceType,
    compute_shift_yearly_distance,
)
from app.schemas.lca import (
    ShiftTripDistance,
    ShiftYearlyDistanceResponse,
)

router = APIRouter()


@router.get("/bus-manufacturers/", response_model=List[BusesManufacturersRead])
async def read_bus_manufacturers(
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    result = await db.execute(select(BusesManufacturers).order_by(BusesManufacturers.name))
    return result.scalars().all()


@router.get("/bus-manufacturers/{manufacturer_id}/models", response_model=List[BusesModelsRefsRead])
async def read_bus_models_refs_by_manufacturer(
    manufacturer_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    manufacturer = await db.get(BusesManufacturers, manufacturer_id)
    if manufacturer is None:
        raise HTTPException(status_code=404, detail="Manufacturer not found")
    result = await db.execute(
        select(BusesModelsRefs)
        .where(BusesModelsRefs.buses_manufacturer_id == manufacturer_id)
        .order_by(BusesModelsRefs.name)
    )
    return result.scalars().all()


@router.get(
    "/bus-models/",
    response_model=PaginatedResponse[BusesModelsListItemRead],
    summary="List bus models (paginated)",
    description=(
        "Returns a paginated, deterministically ordered list of bus models "
        "(name ASC, id ASC). Use the detail endpoint to fetch the full "
        "specs payload."
    ),
)
async def read_bus_models(
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    base_query = select(BusesModels)

    total = await db.scalar(
        select(func.count()).select_from(base_query.subquery())
    )

    items_result = await db.execute(
        base_query
        .order_by(BusesModels.name.asc(), BusesModels.id.asc())
        .offset(pagination.skip)
        .limit(pagination.limit)
    )
    items = [
        BusesModelsListItemRead.model_validate(m)
        for m in items_result.scalars().all()
    ]
    return build_paginated_response(
        items=items,
        total=int(total or 0),
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/bus-models/{model_id}", response_model=BusesModelsRead)
async def read_bus_model(model_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    bus_model = await db.get(BusesModels, model_id)
    if bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")
    return bus_model


@router.post("/bus-models/", response_model=BusesModelsRead)
async def create_bus_model(bus_model: BusModelCreateRequest, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    # Validate user exists
    user = await db.get(Users, bus_model.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="User not found")
    db_bus_model = BusesModels(**bus_model.model_dump(exclude_unset=True))
    db.add(db_bus_model)
    await db.commit()
    await db.refresh(db_bus_model)
    return db_bus_model

@router.put("/bus-models/{model_id}", response_model=BusesModelsRead)
async def update_bus_model(model_id: UUID, bus_model_update: BusModelUpdateRequest, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_bus_model = await db.get(BusesModels, model_id)
    if db_bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")

    update_data = bus_model_update.model_dump(exclude_unset=True, exclude={'id'})
    # Validate user if being changed
    if 'user_id' in update_data:
        user = await db.get(Users, update_data['user_id'])
        if user is None:
            raise HTTPException(status_code=400, detail="User not found")
    for field, value in update_data.items():
        setattr(db_bus_model, field, value)

    await db.commit()
    await db.refresh(db_bus_model)
    return db_bus_model


@router.delete("/bus-models/{model_id}")
async def delete_bus_model(model_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_bus_model = await db.get(BusesModels, model_id)
    if db_bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")
    await db.delete(db_bus_model)
    await db.commit()
    return {"message": "Bus model deleted successfully"}


# Buses endpoints (authenticated users only)


@router.get("/buses/", response_model=List[BusesRead])
async def read_buses(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(select(Buses).offset(skip).limit(limit))
    buses = result.scalars().all()
    return buses


@router.get("/buses/{bus_id}", response_model=BusesRead)
async def read_bus(bus_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    bus = await db.get(Buses, bus_id)
    if bus is None:
        raise HTTPException(status_code=404, detail="Bus not found")
    return bus


@router.post("/buses/", response_model=BusesRead)
async def create_bus(bus: BusesCreate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    # Validate user
    user = await db.get(Users, bus.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="User not found")
    # Validate bus model if provided
    if bus.bus_model_id is not None:
        bm = await db.get(BusesModels, bus.bus_model_id)
        if bm is None:
            raise HTTPException(status_code=400, detail="Bus model not found")
    db_bus = Buses(**bus.model_dump(exclude_unset=True))
    db.add(db_bus)
    await db.commit()
    await db.refresh(db_bus)
    return db_bus

@router.put("/buses/{bus_id}", response_model=BusesRead)
async def update_bus(bus_id: UUID, bus_update: BusesUpdate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_bus = await db.get(Buses, bus_id)
    if db_bus is None:
        raise HTTPException(status_code=404, detail="Bus not found")
    update_data = bus_update.model_dump(exclude_unset=True, exclude={'id'})
    # Validate foreign keys if changing
    if 'user_id' in update_data:
        user = await db.get(Users, update_data['user_id'])
        if user is None:
            raise HTTPException(status_code=400, detail="User not found")
    if 'bus_model_id' in update_data and update_data['bus_model_id'] is not None:
        bm = await db.get(BusesModels, update_data['bus_model_id'])
        if bm is None:
            raise HTTPException(status_code=400, detail="Bus model not found")
    for field, value in update_data.items():
        setattr(db_bus, field, value)
    await db.commit()
    await db.refresh(db_bus)
    return db_bus


@router.delete("/buses/{bus_id}")
async def delete_bus(bus_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_bus = await db.get(Buses, bus_id)
    if db_bus is None:
        raise HTTPException(status_code=404, detail="Bus not found")
    await db.delete(db_bus)
    await db.commit()
    return {"message": "Bus deleted successfully"}


def _validate_coords(lat: Optional[float], lon: Optional[float]):
    if lat is not None and (lat < -90 or lat > 90):
        raise HTTPException(status_code=400, detail="Latitude must be between -90 and 90")
    if lon is not None and (lon < -180 or lon > 180):
        raise HTTPException(status_code=400, detail="Longitude must be between -180 and 180")


# Depot endpoints (authenticated users only)
@router.post("/depots/", response_model=DepotReadWithLocation)
async def create_depot(depot: DepotCreateRequest, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    # Validate coords and user
    _validate_coords(depot.latitude, depot.longitude)
    user = await db.get(Users, depot.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="User not found")

    # 1) Create GTFS stop for this depot
    generated_stop_id = f"depot_{uuid4()}"
    stop = GtfsStops(
        stop_id=generated_stop_id,
        stop_name=depot.name,
        stop_lat=float(depot.latitude) if depot.latitude is not None else None,
        stop_lon=float(depot.longitude) if depot.longitude is not None else None,
    )
    db.add(stop)
    # Ensure stop gets a database-generated primary key before referencing it
    await db.flush()

    # 2) Create depot referencing the stop
    db_depot = Depots(
        user_id=depot.user_id,
        name=depot.name,
        address=depot.address,
        features=depot.features,
        stop_id=stop.id,
    )
    db.add(db_depot)

    await db.commit()
    await db.refresh(db_depot)
    await db.refresh(stop)

    return DepotReadWithLocation(
        id=db_depot.id,
        user_id=db_depot.user_id,
        name=db_depot.name,
        address=db_depot.address,
        features=db_depot.features,
        stop_id=db_depot.stop_id,
        latitude=stop.stop_lat,
        longitude=stop.stop_lon,
    )


@router.get("/depots/", response_model=List[DepotReadWithLocation])
async def read_depots(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(
        select(Depots, GtfsStops.stop_lat, GtfsStops.stop_lon)
        .join(GtfsStops, Depots.stop_id == GtfsStops.id, isouter=True)
        .offset(skip).limit(limit)
    )
    rows = result.all()
    return [
        DepotReadWithLocation(
            id=dep.id,
            user_id=dep.user_id,
            name=dep.name,
            address=dep.address,
            features=dep.features,
            stop_id=dep.stop_id,
            latitude=lat,
            longitude=lon,
        )
        for (dep, lat, lon) in rows
    ]


@router.get("/depots/{depot_id}", response_model=DepotReadWithLocation)
async def read_depot(depot_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(
        select(Depots, GtfsStops.stop_lat, GtfsStops.stop_lon)
        .join(GtfsStops, Depots.stop_id == GtfsStops.id, isouter=True)
        .filter(Depots.id == depot_id)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Depot not found")
    dep, lat, lon = row
    return DepotReadWithLocation(
        id=dep.id,
        user_id=dep.user_id,
        name=dep.name,
        address=dep.address,
        features=dep.features,
        stop_id=dep.stop_id,
        latitude=lat,
        longitude=lon,
    )


@router.put("/depots/{depot_id}", response_model=DepotReadWithLocation)
async def update_depot(depot_id: UUID, depot_update: DepotUpdateRequest, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_depot = await db.get(Depots, depot_id)
    if db_depot is None:
        raise HTTPException(status_code=404, detail="Depot not found")

    update_data = depot_update.model_dump(exclude_unset=True)

    # Validate coordinates if provided
    _validate_coords(update_data.get("latitude"), update_data.get("longitude"))

    # Update depot primitive fields
    for field in ("name", "address", "features"):
        if field in update_data:
            setattr(db_depot, field, update_data[field])

    # Update or create linked stop if needed
    should_update_stop = any(k in update_data for k in ("name", "latitude", "longitude"))
    if should_update_stop:
        stop: Optional[GtfsStops] = None
        if db_depot.stop_id:
            stop = await db.get(GtfsStops, db_depot.stop_id)
        if stop is None:
            # Create a stop if missing
            stop = GtfsStops(
                stop_id=f"depot_{uuid4()}",
            )
            db.add(stop)
            # Flush to get the generated stop.id before assigning FK
            await db.flush()
            db_depot.stop_id = stop.id

        if "name" in update_data:
            stop.stop_name = update_data["name"]
        if "latitude" in update_data:
            stop.stop_lat = float(update_data["latitude"]) if update_data["latitude"] is not None else None
        if "longitude" in update_data:
            stop.stop_lon = float(update_data["longitude"]) if update_data["longitude"] is not None else None

    await db.commit()
    await db.refresh(db_depot)

    # Load stop coords for response
    lat = lon = None
    if db_depot.stop_id:
        stop = await db.get(GtfsStops, db_depot.stop_id)
        if stop is not None:
            lat = stop.stop_lat
            lon = stop.stop_lon

    return DepotReadWithLocation(
        id=db_depot.id,
        user_id=db_depot.user_id,
        name=db_depot.name,
        address=db_depot.address,
        features=db_depot.features,
        stop_id=db_depot.stop_id,
        latitude=lat,
        longitude=lon,
    )


@router.delete("/depots/{depot_id}")
async def delete_depot(depot_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_depot = await db.get(Depots, depot_id)
    if db_depot is None:
        raise HTTPException(status_code=404, detail="Depot not found")
    
    await db.delete(db_depot)
    await db.commit()
    return {"message": "Depot deleted successfully"}


# Shifts endpoints (authenticated users only)
@router.post("/shifts/", response_model=ShiftReadWithStructure)
async def create_shift(payload: ShiftCreateRequest, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    # Validate bus if provided
    bus_id = payload.bus_id
    if bus_id is not None:
        bus = await db.get(Buses, bus_id)
        if bus is None:
            raise HTTPException(status_code=400, detail="Bus not found")

    trip_ids = payload.trip_ids or []
    if not trip_ids:
        raise HTTPException(status_code=400, detail="trip_ids must be a non-empty list")
    trips = (await db.execute(select(GtfsTrips.id).where(GtfsTrips.id.in_(trip_ids)))).scalars().all()
    missing = set(trip_ids) - set(trips)
    if missing:
        raise HTTPException(status_code=400, detail=f"Trips not found: {', '.join(str(x) for x in missing)}")

    db_shift = Shifts(name=payload.name, bus_id=bus_id)
    db.add(db_shift)
    await db.flush()

    for idx, trip_id in enumerate(trip_ids, start=1):
        ss = ShiftsStructures(trip_id=trip_id, shift_id=db_shift.id, sequence_number=idx)
        db.add(ss)

    await db.commit()
    await db.refresh(db_shift)

    rows = (await db.execute(select(ShiftsStructures).where(ShiftsStructures.shift_id == db_shift.id).order_by(ShiftsStructures.sequence_number))).scalars().all()
    structure = [ShiftStructureItem(id=r.id, trip_id=r.trip_id, shift_id=r.shift_id, sequence_number=r.sequence_number) for r in rows]
    return ShiftReadWithStructure(id=db_shift.id, name=db_shift.name, bus_id=db_shift.bus_id, structure=structure)


@router.get(
    "/shifts/",
    response_model=PaginatedResponse[ShiftListItemRead],
    summary="List shifts (paginated)",
    description=(
        "Returns a paginated, deterministically ordered list of shifts "
        "(name ASC, id ASC) owned by the current user. The full structure "
        "payload is intentionally omitted; use ``GET /shifts/{id}`` to load "
        "it. Each row carries a ``trip_count`` summary computed at the "
        "database level."
    ),
)
async def list_shifts(
    pagination: PaginationParams = Depends(),
    bus_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,  # noqa: ARG001 — kept for backwards compatibility, ignored by design
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    # Always scope to the current authenticated user's buses. The user_id
    # query parameter is intentionally ignored for security reasons.
    base_query = (
        select(Shifts)
        .join(Buses)
        .where(Buses.user_id == current_user.id)
    )
    if bus_id is not None:
        base_query = base_query.where(Shifts.bus_id == bus_id)

    total = await db.scalar(
        select(func.count()).select_from(base_query.subquery())
    )

    rows = (
        await db.execute(
            base_query
            .order_by(Shifts.name.asc(), Shifts.id.asc())
            .offset(pagination.skip)
            .limit(pagination.limit)
        )
    ).scalars().all()

    shift_ids = [s.id for s in rows]
    trip_counts: dict[UUID, int] = {sid: 0 for sid in shift_ids}
    if shift_ids:
        count_rows = await db.execute(
            select(
                ShiftsStructures.shift_id,
                func.count(ShiftsStructures.id),
            )
            .where(ShiftsStructures.shift_id.in_(shift_ids))
            .group_by(ShiftsStructures.shift_id)
        )
        for sid, c in count_rows.all():
            trip_counts[sid] = int(c)

    items = [
        ShiftListItemRead(
            id=s.id,
            name=s.name,
            bus_id=s.bus_id,
            trip_count=trip_counts.get(s.id, 0),
        )
        for s in rows
    ]
    return build_paginated_response(
        items=items,
        total=int(total or 0),
        skip=pagination.skip,
        limit=pagination.limit,
    )


async def _get_shift_if_owned(shift_id: UUID, user_id: UUID, db: AsyncSession) -> Shifts:
    """Fetch a shift and verify the current user owns it (via bus). Raises 404 if not found or not owned."""
    result = await db.execute(
        select(Shifts)
        .join(Buses)
        .where(Shifts.id == shift_id, Buses.user_id == user_id)
    )
    shift = result.scalar_one_or_none()
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    return shift


@router.get("/shifts/{shift_id}", response_model=ShiftReadWithStructure)
async def read_shift(shift_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    shift = await _get_shift_if_owned(shift_id, current_user.id, db)
    rows = (await db.execute(select(ShiftsStructures).where(ShiftsStructures.shift_id == shift.id).order_by(ShiftsStructures.sequence_number))).scalars().all()
    structure = [ShiftStructureItem(id=r.id, trip_id=r.trip_id, shift_id=r.shift_id, sequence_number=r.sequence_number) for r in rows]
    return ShiftReadWithStructure(id=shift.id, name=shift.name, bus_id=shift.bus_id, structure=structure)


@router.get("/shifts/{shift_id}/info", response_model=ShiftInfoResponse)
async def shift_info(shift_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    """
    Get detailed information about a shift including:
    - Route info (id, name)
    - Days of the week (from trip service/calendar)
    - All trips with their details
    """
    shift = await _get_shift_if_owned(shift_id, current_user.id, db)

    # Get all trips with their routes and calendars via shift_structures
    result = await db.execute(
        select(
            ShiftsStructures.sequence_number,
            GtfsTrips,
            GtfsRoutes,
            GtfsCalendar
        )
        .join(GtfsTrips, ShiftsStructures.trip_id == GtfsTrips.id)
        .join(GtfsRoutes, GtfsTrips.route_id == GtfsRoutes.id)
        .join(GtfsCalendar, GtfsTrips.service_id == GtfsCalendar.id)
        .where(ShiftsStructures.shift_id == shift.id)
        .order_by(ShiftsStructures.sequence_number)
    )
    rows = result.all()

    # Build trips list
    trips: list[TripInfoBrief] = []
    route_info: Optional[RouteInfoBrief] = None
    days_set: set[str] = set()

    for seq_num, trip, route, calendar in rows:
        # Build trip info
        trips.append(TripInfoBrief(
            id=trip.id,
            trip_id=trip.trip_id,
            trip_headsign=trip.trip_headsign,
            departure_time=trip.departure_time,
            arrival_time=trip.arrival_time,
            start_stop_name=trip.start_stop_name,
            end_stop_name=trip.end_stop_name,
            sequence_number=seq_num,
        ))

        # Get route info from first trip (all trips in a shift typically share the same route)
        if route_info is None:
            route_name = route.route_short_name or route.route_long_name or route.route_id
            route_info = RouteInfoBrief(id=route.id, name=route_name)

        # Collect days of week from calendar
        if calendar.monday:
            days_set.add("monday")
        if calendar.tuesday:
            days_set.add("tuesday")
        if calendar.wednesday:
            days_set.add("wednesday")
        if calendar.thursday:
            days_set.add("thursday")
        if calendar.friday:
            days_set.add("friday")
        if calendar.saturday:
            days_set.add("saturday")
        if calendar.sunday:
            days_set.add("sunday")

    # Sort days in order
    day_order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    days_of_week = [d for d in day_order if d in days_set]

    return ShiftInfoResponse(
        id=shift.id,
        name=shift.name,
        bus_id=shift.bus_id,
        route=route_info,
        days_of_week=days_of_week,
        trips=trips,
    )


@router.put("/shifts/{shift_id}", response_model=ShiftReadWithStructure)
async def update_shift(shift_id: UUID, payload: ShiftUpdateRequest, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    shift = await _get_shift_if_owned(shift_id, current_user.id, db)

    update_data = payload.model_dump(exclude_unset=True)

    if 'bus_id' in update_data and update_data['bus_id'] is not None:
        # Ensure the new bus also belongs to the current user
        bus = await db.execute(select(Buses).where(Buses.id == update_data['bus_id'], Buses.user_id == current_user.id))
        if bus.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="Bus not found")

    if 'name' in update_data:
        shift.name = update_data['name']
    if 'bus_id' in update_data:
        shift.bus_id = update_data['bus_id']

    if 'trip_ids' in update_data:
        trip_ids = update_data['trip_ids'] or []
        trips = (await db.execute(select(GtfsTrips.id).where(GtfsTrips.id.in_(trip_ids)))).scalars().all() if trip_ids else []
        missing = set(trip_ids) - set(trips)
        if missing:
            raise HTTPException(status_code=400, detail=f"Trips not found: {', '.join(str(x) for x in missing)}")

        await db.execute(delete(ShiftsStructures).where(ShiftsStructures.shift_id == shift.id))

        for idx, trip_id in enumerate(trip_ids, start=1):
            ss = ShiftsStructures(trip_id=trip_id, shift_id=shift.id, sequence_number=idx)
            db.add(ss)

    await db.commit()
    await db.refresh(shift)

    rows = (await db.execute(select(ShiftsStructures).where(ShiftsStructures.shift_id == shift.id).order_by(ShiftsStructures.sequence_number))).scalars().all()
    structure = [ShiftStructureItem(id=r.id, trip_id=r.trip_id, shift_id=r.shift_id, sequence_number=r.sequence_number) for r in rows]
    return ShiftReadWithStructure(id=shift.id, name=shift.name, bus_id=shift.bus_id, structure=structure)


@router.delete("/shifts/{shift_id}")
async def delete_shift(shift_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    shift = await _get_shift_if_owned(shift_id, current_user.id, db)
    await db.execute(delete(ShiftsStructures).where(ShiftsStructures.shift_id == shift.id))
    await db.delete(shift)
    await db.commit()
    return {"message": "Shift deleted successfully"}


# ========================================================================== #
# Shift yearly distance
# ========================================================================== #

@router.get(
    "/shifts/{shift_id}/yearly-distance",
    response_model=ShiftYearlyDistanceResponse,
    summary="Calculate shift yearly distance",
    description=(
        "Computes the total daily distance of a shift (sum of its trip "
        "distances derived from GTFS ``shape_dist_traveled``) and projects "
        "it to a yearly figure based on the chosen recurrence pattern.\n\n"
        "**Recurrence options:**\n"
        "- ``weekly_once`` – shift runs 1 day/week → 52 days/year\n"
        "- ``weekdays`` – shift runs Mon–Fri → 260 days/year\n"
        "- ``daily`` – shift runs every day → 364 days/year\n"
        "- ``custom`` – provide ``custom_days`` (number of operating days "
        "per year)\n\n"
        "Trips without shape data (e.g. depot trips) are included in the "
        "breakdown with ``distance_m = null`` but do not count towards the "
        "total."
    ),
)
async def get_shift_yearly_distance(
    shift_id: UUID,
    recurrence: RecurrenceType = Query(
        ...,
        description="How often the shift repeats.",
    ),
    custom_days: Optional[int] = Query(
        None,
        ge=1,
        le=366,
        description="Number of operating days/year (required when recurrence=custom).",
    ),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    info = await compute_shift_yearly_distance(shift_id, recurrence, custom_days, db)

    trips = [
        ShiftTripDistance(
            trip_id=t.trip_id,
            gtfs_trip_id=t.gtfs_trip_id,
            sequence_number=t.sequence_number,
            distance_m=t.distance_m,
        )
        for t in info.trips
    ]

    return ShiftYearlyDistanceResponse(
        shift_id=info.shift_id,
        shift_name=info.shift_name,
        daily_distance_m=info.daily_distance_m,
        daily_distance_km=info.daily_distance_km,
        recurrence=recurrence,
        recurrence_days=info.recurrence_days,
        yearly_distance_m=info.yearly_distance_m,
        yearly_distance_km=info.yearly_distance_km,
        trips=trips,
    )

