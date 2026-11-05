from fastapi import APIRouter, Depends, HTTPException, Query
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
from app.models import (
    Users, GtfsAgencies
)
from app.core.auth import get_current_user, require_admin, get_password_hash

router = APIRouter()


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
