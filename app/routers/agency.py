from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import List
from uuid import UUID

from app.database import get_async_session
from app.schemas.database import (
    UsersCreate, UsersRead, UsersUpdate,
    GtfsAgenciesCreate, GtfsAgenciesRead,
)
from app.models import (
    Users, GtfsAgencies
)
from app.core.auth import get_current_user, require_admin, get_password_hash

router = APIRouter()

# Users endpoints (admin only)
@router.get("/users/", response_model=List[UsersRead])
async def read_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(require_admin),
):
    result = await db.execute(
        select(Users)
        .where(Users.company_id == current_user.company_id)
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
    # Hash the password if provided
    user_data = user.model_dump(exclude_unset=True)
    if 'password' in user_data:
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

    update_data = user_update.model_dump(exclude_unset=True, exclude={'id'})
    requested_company_id = update_data.get("company_id")
    if (
        requested_company_id is not None
        and requested_company_id != current_user.company_id
    ):
        raise HTTPException(
            status_code=403,
            detail="Administrators cannot move users to another agency",
        )
    for field, value in update_data.items():
        setattr(db_user, field, value)

    await db.commit()
    await db.refresh(db_user)
    return db_user

# GTFS Agencies endpoints (authenticated users only)
@router.get("/agencies/", response_model=List[GtfsAgenciesRead])
async def read_agencies(skip: int = 0, limit: int = 100, search: str = "", db: AsyncSession = Depends(get_async_session)):
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
    
    query = query.offset(skip).limit(limit)
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
async def create_agency(agency: GtfsAgenciesCreate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_agency = GtfsAgencies(**agency.model_dump(exclude_unset=True))
    db.add(db_agency)
    await db.commit()
    await db.refresh(db_agency)
    return db_agency
