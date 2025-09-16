from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID

from app.database import get_async_session
from app.schemas.database import (
    UsersCreate, UsersRead, UsersUpdate,
    GtfsAgenciesCreate, GtfsAgenciesRead,
    BusModelsCreate, BusModelsRead, BusModelsUpdate,
    DepotsCreate, DepotsRead, DepotsUpdate,
)
from app.models import (
    Users, GtfsAgencies, BusModels, Depots
)
from app.core.auth import get_current_user, require_admin, get_password_hash

router = APIRouter()

# Users endpoints (admin only)
@router.post("/users/", response_model=UsersRead, dependencies=[Depends(require_admin)])
async def create_user(user: UsersCreate, db: AsyncSession = Depends(get_async_session)):
    # Hash the password if provided
    user_data = user.model_dump(exclude_unset=True)
    if 'password' in user_data:
        user_data['password_hash'] = get_password_hash(user_data.pop('password'))

    db_user = Users(**user_data)
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

@router.get("/users/", response_model=List[UsersRead], dependencies=[Depends(require_admin)])
async def read_users(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(select(Users).offset(skip).limit(limit))
    users = result.scalars().all()
    return users

@router.get("/users/{user_id}", response_model=UsersRead, dependencies=[Depends(require_admin)])
async def read_user(user_id: UUID, db: AsyncSession = Depends(get_async_session)):
    user = await db.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.put("/users/{user_id}", response_model=UsersRead, dependencies=[Depends(require_admin)])
async def update_user(user_id: UUID, user_update: UsersUpdate, db: AsyncSession = Depends(get_async_session)):
    db_user = await db.get(Users, user_id)
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found")

    update_data = user_update.model_dump(exclude_unset=True, exclude={'id'})
    for field, value in update_data.items():
        setattr(db_user, field, value)

    await db.commit()
    await db.refresh(db_user)
    return db_user

# GTFS Agencies endpoints (authenticated users only)
@router.post("/agencies/", response_model=GtfsAgenciesRead)
async def create_agency(agency: GtfsAgenciesCreate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_agency = GtfsAgencies(**agency.model_dump(exclude_unset=True))
    db.add(db_agency)
    await db.commit()
    await db.refresh(db_agency)
    return db_agency

@router.get("/agencies/", response_model=List[GtfsAgenciesRead])
async def read_agencies(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(select(GtfsAgencies).offset(skip).limit(limit))
    agencies = result.scalars().all()
    return agencies

@router.get("/agencies/{agency_id}", response_model=GtfsAgenciesRead)
async def read_agency(agency_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    agency = await db.get(GtfsAgencies, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    return agency

# Bus Models endpoints (authenticated users only)
@router.post("/bus-models/", response_model=BusModelsRead)
async def create_bus_model(bus_model: BusModelsCreate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_bus_model = BusModels(**bus_model.model_dump(exclude_unset=True))
    db.add(db_bus_model)
    await db.commit()
    await db.refresh(db_bus_model)
    return db_bus_model

@router.get("/bus-models/", response_model=List[BusModelsRead])
async def read_bus_models(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(select(BusModels).offset(skip).limit(limit))
    bus_models = result.scalars().all()
    return bus_models

@router.get("/bus-models/{model_id}", response_model=BusModelsRead)
async def read_bus_model(model_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    bus_model = await db.get(BusModels, model_id)
    if bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")
    return bus_model

@router.put("/bus-models/{model_id}", response_model=BusModelsRead)
async def update_bus_model(model_id: UUID, bus_model_update: BusModelsUpdate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_bus_model = await db.get(BusModels, model_id)
    if db_bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")

    update_data = bus_model_update.model_dump(exclude_unset=True, exclude={'id'})
    for field, value in update_data.items():
        setattr(db_bus_model, field, value)

    await db.commit()
    await db.refresh(db_bus_model)
    return db_bus_model

# Depot endpoints (authenticated users only)
@router.post("/depots/", response_model=DepotsRead)
async def create_depot(depot: DepotsCreate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    # Validate coordinates
    if depot.latitude is not None and (depot.latitude < -90 or depot.latitude > 90):
        raise HTTPException(status_code=400, detail="Latitude must be between -90 and 90")
    if depot.longitude is not None and (depot.longitude < -180 or depot.longitude > 180):
        raise HTTPException(status_code=400, detail="Longitude must be between -180 and 180")
    
    # Validate agency exists
    from app.models import GtfsAgencies
    agency = await db.get(GtfsAgencies, depot.agency_id)
    if agency is None:
        raise HTTPException(status_code=400, detail="Agency not found")
    
    db_depot = Depots(**depot.model_dump(exclude_unset=True))
    db.add(db_depot)
    await db.commit()
    await db.refresh(db_depot)
    return db_depot

@router.get("/depots/", response_model=List[DepotsRead])
async def read_depots(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(select(Depots).offset(skip).limit(limit))
    depots = result.scalars().all()
    return depots

@router.get("/depots/{depot_id}", response_model=DepotsRead)
async def read_depot(depot_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    depot = await db.get(Depots, depot_id)
    if depot is None:
        raise HTTPException(status_code=404, detail="Depot not found")
    return depot

@router.put("/depots/{depot_id}", response_model=DepotsRead)
async def update_depot(depot_id: UUID, depot_update: DepotsUpdate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_depot = await db.get(Depots, depot_id)
    if db_depot is None:
        raise HTTPException(status_code=404, detail="Depot not found")

    update_data = depot_update.model_dump(exclude_unset=True, exclude={'id'})
    
    # Validate coordinates if provided
    if 'latitude' in update_data and update_data['latitude'] is not None:
        if update_data['latitude'] < -90 or update_data['latitude'] > 90:
            raise HTTPException(status_code=400, detail="Latitude must be between -90 and 90")
    if 'longitude' in update_data and update_data['longitude'] is not None:
        if update_data['longitude'] < -180 or update_data['longitude'] > 180:
            raise HTTPException(status_code=400, detail="Longitude must be between -180 and 180")
    
    # Validate agency exists if provided
    if 'agency_id' in update_data:
        from app.models import GtfsAgencies
        agency = await db.get(GtfsAgencies, update_data['agency_id'])
        if agency is None:
            raise HTTPException(status_code=400, detail="Agency not found")
    
    for field, value in update_data.items():
        setattr(db_depot, field, value)

    await db.commit()
    await db.refresh(db_depot)
    return db_depot

@router.delete("/depots/{depot_id}")
async def delete_depot(depot_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_depot = await db.get(Depots, depot_id)
    if db_depot is None:
        raise HTTPException(status_code=404, detail="Depot not found")
    
    await db.delete(db_depot)
    await db.commit()
    return {"message": "Depot deleted successfully"}
