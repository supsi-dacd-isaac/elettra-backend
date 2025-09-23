from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List, Optional
from uuid import UUID, uuid4

from app.database import get_async_session
from app.schemas.database import (
    BusesModelsCreate, BusesModelsRead, BusesModelsUpdate,
    BusesCreate, BusesRead, BusesUpdate,
)
from app.schemas.responses import (
    DepotCreateRequest, DepotUpdateRequest, DepotReadWithLocation,
    ShiftReadWithStructure, ShiftStructureItem,
)
from app.schemas.requests import ShiftCreateRequest, ShiftUpdateRequest
from app.models import (
    Users, BusesModels, Buses, Depots, GtfsStops,
    Shifts, ShiftsStructures, GtfsTrips
)
from app.core.auth import get_current_user

router = APIRouter()


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


@router.post("/bus-models/", response_model=BusesModelsRead)
async def create_bus_model(bus_model: BusesModelsCreate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
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
async def update_bus_model(model_id: UUID, bus_model_update: BusesModelsUpdate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
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


@router.get("/shifts/", response_model=List[ShiftReadWithStructure])
async def list_shifts(skip: int = 0, limit: int = 100, bus_id: Optional[UUID] = None, user_id: Optional[UUID] = None, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    q = select(Shifts)
    if bus_id is not None:
        q = q.where(Shifts.bus_id == bus_id)
    if user_id is not None:
        # Filter shifts by user via their bus user_id
        q = q.join(Buses, isouter=True).where((Buses.user_id == user_id))
    q = q.offset(skip).limit(limit)
    shifts = (await db.execute(q)).scalars().all()

    # Batch fetch structures
    shift_ids = [s.id for s in shifts]
    structures_by_shift: dict[UUID, list[ShiftsStructures]] = {sid: [] for sid in shift_ids}
    if shift_ids:
        rows = (await db.execute(select(ShiftsStructures).where(ShiftsStructures.shift_id.in_(shift_ids)).order_by(ShiftsStructures.shift_id, ShiftsStructures.sequence_number))).scalars().all()
        for r in rows:
            structures_by_shift[r.shift_id].append(r)

    results: List[ShiftReadWithStructure] = []
    for s in shifts:
        struct_items = [ShiftStructureItem(id=r.id, trip_id=r.trip_id, shift_id=r.shift_id, sequence_number=r.sequence_number) for r in structures_by_shift.get(s.id, [])]
        results.append(ShiftReadWithStructure(id=s.id, name=s.name, bus_id=s.bus_id, structure=struct_items))
    return results


@router.get("/shifts/{shift_id}", response_model=ShiftReadWithStructure)
async def read_shift(shift_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    shift = await db.get(Shifts, shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    rows = (await db.execute(select(ShiftsStructures).where(ShiftsStructures.shift_id == shift.id).order_by(ShiftsStructures.sequence_number))).scalars().all()
    structure = [ShiftStructureItem(id=r.id, trip_id=r.trip_id, shift_id=r.shift_id, sequence_number=r.sequence_number) for r in rows]
    return ShiftReadWithStructure(id=shift.id, name=shift.name, bus_id=shift.bus_id, structure=structure)


@router.put("/shifts/{shift_id}", response_model=ShiftReadWithStructure)
async def update_shift(shift_id: UUID, payload: ShiftUpdateRequest, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    shift = await db.get(Shifts, shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")

    update_data = payload.model_dump(exclude_unset=True)

    if 'bus_id' in update_data and update_data['bus_id'] is not None:
        bus = await db.get(Buses, update_data['bus_id'])
        if bus is None:
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
    shift = await db.get(Shifts, shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    await db.execute(delete(ShiftsStructures).where(ShiftsStructures.shift_id == shift.id))
    await db.delete(shift)
    await db.commit()
    return {"message": "Shift deleted successfully"}


