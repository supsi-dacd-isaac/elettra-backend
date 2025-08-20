from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID

from app.database import get_async_session
from app.schemas import (
    UsersCreate, UsersRead, UsersUpdate,
    GtfsAgenciesCreate, GtfsAgenciesRead, GtfsAgenciesUpdate,
    SimulationRunsCreate, SimulationRunsRead, SimulationRunsUpdate,
    GtfsCalendarCreate, GtfsCalendarRead, GtfsCalendarUpdate,
    GtfsStopsCreate, GtfsStopsRead, GtfsStopsUpdate,
    BusModelsCreate, BusModelsRead, BusModelsUpdate,
    GtfsTripsCreate, GtfsTripsRead, GtfsTripsUpdate,
    VariantsCreate, VariantsRead, VariantsUpdate,
    GtfsStopsTimesCreate, GtfsStopsTimesRead, GtfsStopsTimesUpdate,
    GtfsRoutesCreate, GtfsRoutesRead, GtfsRoutesUpdate
)
from app.models import (
    Users, GtfsAgencies, SimulationRuns, GtfsCalendar,
    GtfsStops, BusModels, GtfsTrips, Variants,
    GtfsStopsTimes, GtfsRoutes
)

router = APIRouter()

# Users endpoints
@router.post("/users/", response_model=UsersRead)
async def create_user(user: UsersCreate, db: AsyncSession = Depends(get_async_session)):
    db_user = Users(**user.model_dump(exclude_unset=True))
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

@router.get("/users/", response_model=List[UsersRead])
async def read_users(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(select(Users).offset(skip).limit(limit))
    users = result.scalars().all()
    return users

@router.get("/users/{user_id}", response_model=UsersRead)
async def read_user(user_id: UUID, db: AsyncSession = Depends(get_async_session)):
    user = await db.get(Users, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.put("/users/{user_id}", response_model=UsersRead)
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

# GTFS Agencies endpoints
@router.post("/agencies/", response_model=GtfsAgenciesRead)
async def create_agency(agency: GtfsAgenciesCreate, db: AsyncSession = Depends(get_async_session)):
    db_agency = GtfsAgencies(**agency.model_dump(exclude_unset=True))
    db.add(db_agency)
    await db.commit()
    await db.refresh(db_agency)
    return db_agency

@router.get("/agencies/", response_model=List[GtfsAgenciesRead])
async def read_agencies(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(select(GtfsAgencies).offset(skip).limit(limit))
    agencies = result.scalars().all()
    return agencies

@router.get("/agencies/{agency_id}", response_model=GtfsAgenciesRead)
async def read_agency(agency_id: UUID, db: AsyncSession = Depends(get_async_session)):
    agency = await db.get(GtfsAgencies, agency_id)
    if agency is None:
        raise HTTPException(status_code=404, detail="Agency not found")
    return agency

# GTFS Routes endpoints
@router.post("/gtfs-routes/", response_model=GtfsRoutesRead)
async def create_route(route: GtfsRoutesCreate, db: AsyncSession = Depends(get_async_session)):
    db_route = GtfsRoutes(**route.model_dump(exclude_unset=True))
    db.add(db_route)
    await db.commit()
    await db.refresh(db_route)
    return db_route

@router.get("/gtfs-routes/", response_model=List[GtfsRoutesRead])
async def read_routes(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(select(GtfsRoutes).offset(skip).limit(limit))
    routes = result.scalars().all()
    return routes

@router.get("/gtfs-routes/{route_id}", response_model=GtfsRoutesRead)
async def read_route(route_id: UUID, db: AsyncSession = Depends(get_async_session)):
    route = await db.get(GtfsRoutes, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="Route not found")
    return route

@router.get("/gtfs-routes/by-agency/{agency_id}", response_model=List[GtfsRoutesRead])
async def read_routes_by_agency(agency_id: UUID, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(
        select(GtfsRoutes).filter(GtfsRoutes.agency_id == agency_id)
    )
    routes = result.scalars().all()
    return routes

# GTFS Trips endpoints
@router.get("/gtfs-trips/by-route/{route_id}", response_model=List[GtfsTripsRead])
async def read_trips_by_route(route_id: UUID, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(
        select(GtfsTrips).filter(GtfsTrips.route_id == route_id)
    )
    trips = result.scalars().all()
    return trips

# Variants endpoints
@router.get("/variants/by-route/{route_id}", response_model=List[VariantsRead])
async def read_variants_by_route(route_id: UUID, db: AsyncSession = Depends(get_async_session)):
    """Get all variants for a given route ID"""
    result = await db.execute(
        select(Variants).filter(Variants.route_id == route_id)
    )
    variants = result.scalars().all()
    return variants

# GTFS Calendar endpoints
@router.get("/gtfs-calendar/by-trip/{trip_id}", response_model=List[GtfsCalendarRead])
async def read_calendar_by_trip(trip_id: UUID, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(
        select(GtfsCalendar).filter(GtfsCalendar.trip_id == trip_id)
    )
    calendar_entries = result.scalars().all()
    return calendar_entries

# GTFS Stops endpoints
@router.get("/gtfs-stops/by-trip/{trip_id}", response_model=List[GtfsStopsRead])
async def read_stops_by_trip(trip_id: UUID, db: AsyncSession = Depends(get_async_session)):
    """Get all GTFS stops for a given trip ID"""
    result = await db.execute(
        select(GtfsStops)
        .join(GtfsStopsTimes, GtfsStops.id == GtfsStopsTimes.stop_id)
        .filter(GtfsStopsTimes.trip_id == trip_id)
        .order_by(GtfsStopsTimes.stop_sequence)
    )
    stops = result.scalars().all()
    return stops

@router.get("/gtfs-trips/by-stop/{stop_id}", response_model=List[GtfsTripsRead])
async def read_trips_by_stop(stop_id: UUID, db: AsyncSession = Depends(get_async_session)):
    """Get all GTFS trips for a given stop ID"""
    result = await db.execute(
        select(GtfsTrips)
        .join(GtfsStopsTimes, GtfsTrips.id == GtfsStopsTimes.trip_id)
        .filter(GtfsStopsTimes.stop_id == stop_id)
    )
    trips = result.scalars().all()
    return trips

# Bus Models endpoints
@router.post("/bus-models/", response_model=BusModelsRead)
async def create_bus_model(bus_model: BusModelsCreate, db: AsyncSession = Depends(get_async_session)):
    db_bus_model = BusModels(**bus_model.model_dump(exclude_unset=True))
    db.add(db_bus_model)
    await db.commit()
    await db.refresh(db_bus_model)
    return db_bus_model

@router.get("/bus-models/", response_model=List[BusModelsRead])
async def read_bus_models(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(select(BusModels).offset(skip).limit(limit))
    bus_models = result.scalars().all()
    return bus_models

@router.get("/bus-models/{model_id}", response_model=BusModelsRead)
async def read_bus_model(model_id: UUID, db: AsyncSession = Depends(get_async_session)):
    bus_model = await db.get(BusModels, model_id)
    if bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")
    return bus_model

@router.put("/bus-models/{model_id}", response_model=BusModelsRead)
async def update_bus_model(model_id: UUID, bus_model_update: BusModelsUpdate, db: AsyncSession = Depends(get_async_session)):
    db_bus_model = await db.get(BusModels, model_id)
    if db_bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")

    update_data = bus_model_update.model_dump(exclude_unset=True, exclude={'id'})
    for field, value in update_data.items():
        setattr(db_bus_model, field, value)

    await db.commit()
    await db.refresh(db_bus_model)
    return db_bus_model

# Simulation Runs endpoints
@router.post("/simulation-runs/", response_model=SimulationRunsRead)
async def create_simulation_run(sim_run: SimulationRunsCreate, db: AsyncSession = Depends(get_async_session)):
    db_sim_run = SimulationRuns(**sim_run.model_dump(exclude_unset=True))
    db.add(db_sim_run)
    await db.commit()
    await db.refresh(db_sim_run)
    return db_sim_run

@router.get("/simulation-runs/", response_model=List[SimulationRunsRead])
async def read_simulation_runs(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session)):
    result = await db.execute(select(SimulationRuns).offset(skip).limit(limit))
    sim_runs = result.scalars().all()
    return sim_runs

@router.get("/simulation-runs/{run_id}", response_model=SimulationRunsRead)
async def read_simulation_run(run_id: UUID, db: AsyncSession = Depends(get_async_session)):
    sim_run = await db.get(SimulationRuns, run_id)
    if sim_run is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")
    return sim_run

@router.put("/simulation-runs/{run_id}", response_model=SimulationRunsRead)
async def update_simulation_run(run_id: UUID, sim_run_update: SimulationRunsUpdate, db: AsyncSession = Depends(get_async_session)):
    db_sim_run = await db.get(SimulationRuns, run_id)
    if db_sim_run is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")

    update_data = sim_run_update.model_dump(exclude_unset=True, exclude={'id'})
    for field, value in update_data.items():
        setattr(db_sim_run, field, value)

    await db.commit()
    await db.refresh(db_sim_run)
    return db_sim_run
