from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from uuid import UUID, uuid4

from app.database import get_async_session
from app.schemas.database import (
    UsersCreate, UsersRead, UsersUpdate,
    GtfsAgenciesCreate, GtfsAgenciesRead,
    BusesModelsCreate, BusesModelsRead, BusesModelsUpdate,
    BusesCreate, BusesRead, BusesUpdate,
)
from app.schemas.responses import (
    DepotCreateRequest, DepotUpdateRequest, DepotReadWithLocation,
)
from app.models import (
    Users, GtfsAgencies, BusesModels, Buses, Depots, GtfsStops
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
@router.post("/bus-models/", response_model=BusesModelsRead)
async def create_bus_model(bus_model: BusesModelsCreate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    # Validate agency exists
    agency = await db.get(GtfsAgencies, bus_model.agency_id)
    if agency is None:
        raise HTTPException(status_code=400, detail="Agency not found")
    db_bus_model = BusesModels(**bus_model.model_dump(exclude_unset=True))
    db.add(db_bus_model)
    await db.commit()
    await db.refresh(db_bus_model)
    return db_bus_model

@router.get("/bus-models/", response_model=List[BusesModelsRead])
async def read_bus_models(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(select(BusesModels).offset(skip).limit(limit))
    bus_models = result.scalars().all()
    return bus_models

@router.get("/bus-models/{model_id}", response_model=BusesModelsRead)
async def read_bus_model(model_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    bus_model = await db.get(BusesModels, model_id)
    if bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")
    return bus_model

@router.put("/bus-models/{model_id}", response_model=BusesModelsRead)
async def update_bus_model(model_id: UUID, bus_model_update: BusesModelsUpdate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_bus_model = await db.get(BusesModels, model_id)
    if db_bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")

    update_data = bus_model_update.model_dump(exclude_unset=True, exclude={'id'})
    # Validate agency if being changed
    if 'agency_id' in update_data:
        agency = await db.get(GtfsAgencies, update_data['agency_id'])
        if agency is None:
            raise HTTPException(status_code=400, detail="Agency not found")
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
@router.post("/buses/", response_model=BusesRead)
async def create_bus(bus: BusesCreate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    # Validate agency
    agency = await db.get(GtfsAgencies, bus.agency_id)
    if agency is None:
        raise HTTPException(status_code=400, detail="Agency not found")
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

@router.put("/buses/{bus_id}", response_model=BusesRead)
async def update_bus(bus_id: UUID, bus_update: BusesUpdate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_bus = await db.get(Buses, bus_id)
    if db_bus is None:
        raise HTTPException(status_code=404, detail="Bus not found")
    update_data = bus_update.model_dump(exclude_unset=True, exclude={'id'})
    # Validate foreign keys if changing
    if 'agency_id' in update_data:
        agency = await db.get(GtfsAgencies, update_data['agency_id'])
        if agency is None:
            raise HTTPException(status_code=400, detail="Agency not found")
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
    # Validate coords and agency
    _validate_coords(depot.latitude, depot.longitude)
    agency = await db.get(GtfsAgencies, depot.agency_id)
    if agency is None:
        raise HTTPException(status_code=400, detail="Agency not found")

    # 1) Create GTFS stop for this depot
    generated_stop_id = f"depot_{uuid4()}"
    stop = GtfsStops(
        id=uuid4(),
        stop_id=generated_stop_id,
        stop_name=depot.name,
        stop_lat=float(depot.latitude) if depot.latitude is not None else None,
        stop_lon=float(depot.longitude) if depot.longitude is not None else None,
    )
    db.add(stop)
    
    # 2) Create depot referencing the stop
    db_depot = Depots(
        agency_id=depot.agency_id,
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
        agency_id=db_depot.agency_id,
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
            agency_id=dep.agency_id,
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
        agency_id=dep.agency_id,
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
                id=uuid4(),
                stop_id=f"depot_{uuid4()}",
            )
            db.add(stop)
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
        agency_id=db_depot.agency_id,
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
