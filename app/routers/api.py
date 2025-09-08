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
    VariantsCreate, VariantsRead, VariantsUpdate, VariantsReadWithRoute,
    GtfsStopsTimesCreate, GtfsStopsTimesRead, GtfsStopsTimesUpdate,
    GtfsRoutesCreate, GtfsRoutesRead, GtfsRoutesUpdate, GtfsRoutesReadWithVariant,
    PvgisTmyResponse
)
from app.models import (
    Users, GtfsAgencies, SimulationRuns, GtfsCalendar,
    GtfsStops, BusModels, GtfsTrips, Variants,
    GtfsStopsTimes, GtfsRoutes, WeatherMeasurements
)
from app.core.auth import get_current_user, require_admin, require_analyst, get_password_hash

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

# GTFS Routes endpoints (authenticated users only)
@router.post("/gtfs-routes/", response_model=GtfsRoutesRead)
async def create_route(route: GtfsRoutesCreate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_route = GtfsRoutes(**route.model_dump(exclude_unset=True))
    db.add(db_route)
    await db.commit()
    await db.refresh(db_route)
    return db_route

@router.get("/gtfs-routes/", response_model=List[GtfsRoutesRead])
async def read_routes(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(select(GtfsRoutes).offset(skip).limit(limit))
    routes = result.scalars().all()
    return routes

@router.get("/gtfs-routes/{route_id}", response_model=GtfsRoutesRead)
async def read_route(route_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    route = await db.get(GtfsRoutes, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="Route not found")
    return route

@router.get("/gtfs-routes/by-agency/{agency_id}", response_model=List[GtfsRoutesRead])
async def read_routes_by_agency(agency_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(
        select(GtfsRoutes).filter(GtfsRoutes.agency_id == agency_id)
    )
    routes = result.scalars().all()
    return routes

@router.get("/gtfs-routes/by-agency/{agency_id}/with-variant-1", response_model=List[GtfsRoutesReadWithVariant])
async def read_routes_by_agency_with_variant_1(agency_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    """Get all routes for an agency with variant number 1 data included"""
    # Import settings here to avoid circular imports
    from app.core.config import get_cached_settings
    import os
    import csv

    # Get settings for elevation profiles path
    settings = get_cached_settings()

    # Query to get all routes for the agency with their variant 1 data
    result = await db.execute(
        select(
            GtfsRoutes,
            Variants,
            GtfsAgencies.gtfs_agency_id.label('agency_gtfs_id')
        )
        .join(Variants, GtfsRoutes.id == Variants.route_id)
        .join(GtfsAgencies, GtfsRoutes.agency_id == GtfsAgencies.id)
        .filter(
            GtfsRoutes.agency_id == agency_id,
            Variants.variant_num == 1
        )
        .order_by(GtfsRoutes.route_short_name)
    )
    rows = result.all()

    if not rows:
        raise HTTPException(status_code=404, detail="No routes with variant 1 found for this agency")

    routes_with_variant = []

    for route, variant, agency_gtfs_id in rows:
        # Construct elevation file path for variant 1
        elevation_file_path = os.path.join(
            settings.elevation_profiles_path,
            agency_gtfs_id,
            "routes_variants",
            f"route_{route.route_id}_variant_1",
            "elevation_data.csv"
        )

        # Read elevation data from CSV file
        elevation_data = []
        elevation_data_fields = ["segment_id", "point_number", "latitude", "longitude", "altitude_m"]

        try:
            if os.path.exists(elevation_file_path):
                with open(elevation_file_path, 'r', encoding='utf-8') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        # Extract and convert the required columns with proper data types
                        elevation_row = [
                            row.get('segment_id', ''),  # Keep as string
                            int(row.get('point_number', 0)) if row.get('point_number', '').strip() else 0,  # Convert to int
                            float(row.get('latitude', 0.0)) if row.get('latitude', '').strip() else 0.0,  # Convert to float
                            float(row.get('longitude', 0.0)) if row.get('longitude', '').strip() else 0.0,  # Convert to float
                            float(row.get('altitude_m', 0.0)) if row.get('altitude_m', '').strip() else 0.0  # Convert to float
                        ]
                        elevation_data.append(elevation_row)
            else:
                # If file doesn't exist, log a warning but don't fail the request
                print(f"Warning: Elevation file not found at {elevation_file_path}")
        except Exception as e:
            # If there's an error reading the file, log it but don't fail the request
            print(f"Error reading elevation file {elevation_file_path}: {str(e)}")

        # Create the response object with route data and variant 1 elevation data
        route_with_variant = GtfsRoutesReadWithVariant(
            id=route.id,
            route_id=route.route_id,
            agency_id=route.agency_id,
            route_short_name=route.route_short_name,
            route_long_name=route.route_long_name,
            route_desc=route.route_desc,
            route_type=route.route_type,
            route_url=route.route_url,
            route_color=route.route_color,
            route_text_color=route.route_text_color,
            route_sort_order=route.route_sort_order,
            continuous_pickup=route.continuous_pickup,
            continuous_drop_off=route.continuous_drop_off,
            variant_elevation_file_path=elevation_file_path,
            variant_elevation_data_fields=elevation_data_fields,
            variant_elevation_data=elevation_data
        )
        routes_with_variant.append(route_with_variant)

    return routes_with_variant

@router.get("/gtfs-routes/by-agency/{agency_id}/with-largest-variant", response_model=List[GtfsRoutesReadWithVariant])
async def read_routes_by_agency_with_largest_variant(agency_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    """Get all routes for an agency with the largest variant data included (based on elevation_data.csv file size)"""
    # Import settings here to avoid circular imports
    from app.core.config import get_cached_settings
    import os
    import csv
    import subprocess

    # Get settings for elevation profiles path
    settings = get_cached_settings()

    # First, get all routes for the agency and their variants
    result = await db.execute(
        select(
            GtfsRoutes,
            Variants,
            GtfsAgencies.gtfs_agency_id.label('agency_gtfs_id')
        )
        .join(Variants, GtfsRoutes.id == Variants.route_id)
        .join(GtfsAgencies, GtfsRoutes.agency_id == GtfsAgencies.id)
        .filter(GtfsRoutes.agency_id == agency_id)
        .order_by(GtfsRoutes.route_short_name, Variants.variant_num)
    )
    all_rows = result.all()

    if not all_rows:
        raise HTTPException(status_code=404, detail="No routes found for this agency")

    # Group by route to find the largest variant for each route
    routes_data = {}
    for route, variant, agency_gtfs_id in all_rows:
        route_id = route.route_id
        if route_id not in routes_data:
            routes_data[route_id] = {
                'route': route,
                'agency_gtfs_id': agency_gtfs_id,
                'variants': []
            }
        routes_data[route_id]['variants'].append(variant)

    routes_with_variant = []

    for route_id, route_data in routes_data.items():
        route = route_data['route']
        agency_gtfs_id = route_data['agency_gtfs_id']
        variants = route_data['variants']

        # Find the variant with the largest elevation_data.csv file
        largest_variant = None
        largest_file_size = -1

        for variant in variants:
            elevation_file_path = os.path.join(
                settings.elevation_profiles_path,
                agency_gtfs_id,
                "routes_variants",
                f"route_{route.route_id}_variant_{variant.variant_num}",
                "elevation_data.csv"
            )

            # Check file size using ls -l command
            try:
                if os.path.exists(elevation_file_path):
                    # Use ls -l to get file size
                    result = subprocess.run(['ls', '-l', elevation_file_path],
                                          capture_output=True, text=True, check=True)
                    # Parse the output to get file size (5th column in ls -l output)
                    file_size = int(result.stdout.split()[4])

                    if file_size > largest_file_size:
                        largest_file_size = file_size
                        largest_variant = variant
            except (subprocess.CalledProcessError, IndexError, ValueError) as e:
                # If there's an error getting file size, skip this variant
                print(f"Error getting file size for {elevation_file_path}: {str(e)}")
                continue

        # If no variant was found with elevation data, use the first available variant
        if largest_variant is None and variants:
            largest_variant = variants[0]

        # If we still don't have a variant, skip this route
        if largest_variant is None:
            continue

        # Construct elevation file path for the largest variant
        elevation_file_path = os.path.join(
            settings.elevation_profiles_path,
            agency_gtfs_id,
            "routes_variants",
            f"route_{route.route_id}_variant_{largest_variant.variant_num}",
            "elevation_data.csv"
        )

        # Read elevation data from CSV file
        elevation_data = []
        elevation_data_fields = ["segment_id", "point_number", "latitude", "longitude", "altitude_m"]

        try:
            if os.path.exists(elevation_file_path):
                with open(elevation_file_path, 'r', encoding='utf-8') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        # Extract and convert the required columns with proper data types
                        elevation_row = [
                            row.get('segment_id', ''),  # Keep as string
                            int(row.get('point_number', 0)) if row.get('point_number', '').strip() else 0,  # Convert to int
                            float(row.get('latitude', 0.0)) if row.get('latitude', '').strip() else 0.0,  # Convert to float
                            float(row.get('longitude', 0.0)) if row.get('longitude', '').strip() else 0.0,  # Convert to float
                            float(row.get('altitude_m', 0.0)) if row.get('altitude_m', '').strip() else 0.0  # Convert to float
                        ]
                        elevation_data.append(elevation_row)
            else:
                # If file doesn't exist, log a warning but don't fail the request
                print(f"Warning: Elevation file not found at {elevation_file_path}")
        except Exception as e:
            # If there's an error reading the file, log it but don't fail the request
            print(f"Error reading elevation file {elevation_file_path}: {str(e)}")

        # Create the response object with route data and largest variant elevation data
        route_with_variant = GtfsRoutesReadWithVariant(
            id=route.id,
            route_id=route.route_id,
            agency_id=route.agency_id,
            route_short_name=route.route_short_name,
            route_long_name=route.route_long_name,
            route_desc=route.route_desc,
            route_type=route.route_type,
            route_url=route.route_url,
            route_color=route.route_color,
            route_text_color=route.route_text_color,
            route_sort_order=route.route_sort_order,
            continuous_pickup=route.continuous_pickup,
            continuous_drop_off=route.continuous_drop_off,
            variant_elevation_file_path=elevation_file_path,
            variant_elevation_data_fields=elevation_data_fields,
            variant_elevation_data=elevation_data
        )
        routes_with_variant.append(route_with_variant)

    if not routes_with_variant:
        raise HTTPException(status_code=404, detail="No routes with elevation data found for this agency")

    return routes_with_variant

# GTFS Trips endpoints (authenticated users only)
@router.get("/gtfs-trips/by-route/{route_id}", response_model=List[GtfsTripsRead])
async def read_trips_by_route(route_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(
        select(GtfsTrips).filter(GtfsTrips.route_id == route_id)
    )
    trips = result.scalars().all()
    return trips

# Variants endpoints (authenticated users only)
@router.get("/variants/by-route/{route_id}", response_model=List[VariantsReadWithRoute])
async def read_variants_by_route(route_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    """Get all variants for a given route ID with elevation data included"""
    # Import settings here to avoid circular imports
    from app.core.config import get_cached_settings
    import os
    import csv

    # Get settings for elevation profiles path
    settings = get_cached_settings()

    # Query to get all variants for the route, route info, and user's company info
    result = await db.execute(
        select(
            Variants,
            GtfsRoutes.route_id.label('gtfs_route_id'),
            GtfsAgencies.gtfs_agency_id.label('agency_id')
        )
        .join(GtfsRoutes, Variants.route_id == GtfsRoutes.id)
        .join(GtfsAgencies, GtfsAgencies.id == current_user.company_id)
        .filter(Variants.route_id == route_id)
        .order_by(Variants.variant_num)
    )
    rows = result.all()

    if not rows:
        raise HTTPException(status_code=404, detail="No variants found for this route")

    variants_with_elevation = []

    for variant, gtfs_route_id, agency_id in rows:
        # Construct elevation file path for each variant
        elevation_file_path = os.path.join(
            settings.elevation_profiles_path,
            agency_id,
            "routes_variants",
            f"route_{gtfs_route_id}_variant_{variant.variant_num}",
            "elevation_data.csv"
        )

        # Read elevation data from CSV file for each variant
        elevation_data = []
        elevation_data_fields = ["segment_id", "point_number", "latitude", "longitude", "altitude_m"]

        try:
            if os.path.exists(elevation_file_path):
                with open(elevation_file_path, 'r', encoding='utf-8') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        # Extract and convert the required columns with proper data types
                        elevation_row = [
                            row.get('segment_id', ''),  # Keep as string
                            int(row.get('point_number', 0)) if row.get('point_number', '').strip() else 0,  # Convert to int
                            float(row.get('latitude', 0.0)) if row.get('latitude', '').strip() else 0.0,  # Convert to float
                            float(row.get('longitude', 0.0)) if row.get('longitude', '').strip() else 0.0,  # Convert to float
                            float(row.get('altitude_m', 0.0)) if row.get('altitude_m', '').strip() else 0.0  # Convert to float
                        ]
                        elevation_data.append(elevation_row)
            else:
                # If file doesn't exist, log a warning but don't fail the request
                print(f"Warning: Elevation file not found at {elevation_file_path}")
        except Exception as e:
            # If there's an error reading the file, log it but don't fail the request
            print(f"Error reading elevation file {elevation_file_path}: {str(e)}")

        # Create the response object for this variant
        variant_with_elevation = VariantsReadWithRoute(
            id=variant.id,
            route_id=variant.route_id,
            variant_num=variant.variant_num,
            created_at=variant.created_at,
            gtfs_route_id=gtfs_route_id,
            elevation_file_path=elevation_file_path,
            elevation_data_fields=elevation_data_fields,
            elevation_data=elevation_data
        )
        variants_with_elevation.append(variant_with_elevation)

    return variants_with_elevation

@router.get("/variants/{route_id}/{variant_num}", response_model=VariantsReadWithRoute)
async def read_variant_by_route_and_number(route_id: UUID, variant_num: int, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    """Get a specific variant by route ID and variant number, including the GTFS route ID and elevation file path"""
    # Import settings here to avoid circular imports
    from app.core.config import get_cached_settings
    import os
    import csv

    # Get settings for elevation profiles path
    settings = get_cached_settings()

    # Query to get variant, route info, and user's company info
    result = await db.execute(
        select(
            Variants,
            GtfsRoutes.route_id.label('gtfs_route_id'),
            GtfsAgencies.gtfs_agency_id.label('agency_id')
        )
        .join(GtfsRoutes, Variants.route_id == GtfsRoutes.id)
        .join(GtfsAgencies, GtfsAgencies.id == current_user.company_id)
        .filter(
            Variants.route_id == route_id,
            Variants.variant_num == variant_num
        )
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Variant not found")

    variant, gtfs_route_id, agency_id = row

    # Construct elevation file path
    # Pattern: {base_path}/{agency_id}/routes_variants/route_{route_id}_variant_{variant_num}/elevation_data.csv
    elevation_file_path = os.path.join(
        settings.elevation_profiles_path,
        agency_id,
        "routes_variants",
        f"route_{gtfs_route_id}_variant_{variant_num}",
        "elevation_data.csv"
    )

    # Read elevation data from CSV file
    elevation_data = []
    elevation_data_fields = ["segment_id", "point_number", "latitude", "longitude", "altitude_m"]

    try:
        if os.path.exists(elevation_file_path):
            with open(elevation_file_path, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                for row in reader:
                    # Extract and convert the required columns with proper data types
                    elevation_row = [
                        row.get('segment_id', ''),  # Keep as string
                        int(row.get('point_number', 0)) if row.get('point_number', '').strip() else 0,  # Convert to int
                        float(row.get('latitude', 0.0)) if row.get('latitude', '').strip() else 0.0,  # Convert to float
                        float(row.get('longitude', 0.0)) if row.get('longitude', '').strip() else 0.0,  # Convert to float
                        float(row.get('altitude_m', 0.0)) if row.get('altitude_m', '').strip() else 0.0  # Convert to float
                    ]
                    elevation_data.append(elevation_row)
        else:
            # If file doesn't exist, log a warning but don't fail the request
            print(f"Warning: Elevation file not found at {elevation_file_path}")
    except Exception as e:
        # If there's an error reading the file, log it but don't fail the request
        print(f"Error reading elevation file {elevation_file_path}: {str(e)}")

    # Create the response object manually since we need to add the computed fields
    return VariantsReadWithRoute(
        id=variant.id,
        route_id=variant.route_id,
        variant_num=variant.variant_num,
        created_at=variant.created_at,
        gtfs_route_id=gtfs_route_id,
        elevation_file_path=elevation_file_path,
        elevation_data_fields=elevation_data_fields,
        elevation_data=elevation_data
    )

# GTFS Calendar endpoints (authenticated users only)
@router.get("/gtfs-calendar/by-trip/{trip_id}", response_model=List[GtfsCalendarRead])
async def read_calendar_by_trip(trip_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(
        select(GtfsCalendar).filter(GtfsCalendar.trip_id == trip_id)
    )
    calendar_entries = result.scalars().all()
    return calendar_entries

# GTFS Stops endpoints (authenticated users only)
@router.get("/gtfs-stops/by-trip/{trip_id}", response_model=List[GtfsStopsRead])
async def read_stops_by_trip(trip_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
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
async def read_trips_by_stop(stop_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    """Get all GTFS trips for a given stop ID"""
    result = await db.execute(
        select(GtfsTrips)
        .join(GtfsStopsTimes, GtfsTrips.id == GtfsStopsTimes.trip_id)
        .filter(GtfsStopsTimes.stop_id == stop_id)
    )
    trips = result.scalars().all()
    return trips

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

# Simulation Runs endpoints (authenticated users only)
@router.post("/simulation-runs/", response_model=SimulationRunsRead)
async def create_simulation_run(sim_run: SimulationRunsCreate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_sim_run = SimulationRuns(**sim_run.model_dump(exclude_unset=True))
    db.add(db_sim_run)
    await db.commit()
    await db.refresh(db_sim_run)
    return db_sim_run

@router.get("/simulation-runs/", response_model=List[SimulationRunsRead])
async def read_simulation_runs(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    result = await db.execute(select(SimulationRuns).offset(skip).limit(limit))
    sim_runs = result.scalars().all()
    return sim_runs

@router.get("/simulation-runs/{run_id}", response_model=SimulationRunsRead)
async def read_simulation_run(run_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    sim_run = await db.get(SimulationRuns, run_id)
    if sim_run is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")
    return sim_run

@router.put("/simulation-runs/{run_id}", response_model=SimulationRunsRead)
async def update_simulation_run(run_id: UUID, sim_run_update: SimulationRunsUpdate, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    db_sim_run = await db.get(SimulationRuns, run_id)
    if db_sim_run is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")

    update_data = sim_run_update.model_dump(exclude_unset=True, exclude={'id'})
    for field, value in update_data.items():
        setattr(db_sim_run, field, value)

    await db.commit()
    await db.refresh(db_sim_run)
    return db_sim_run

# PVGIS TMY endpoint (authenticated users only)
@router.get("/pvgis-tmy/", response_model=PvgisTmyResponse)
async def generate_pvgis_tmy(latitude: float, longitude: float, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    """Generate TMY (Typical Meteorological Year) dataset from PVGIS using latitude and longitude.
    First checks if data exists in database, otherwise downloads from PVGIS and stores it.
    The coerce_year is configured via config files (pvgis_coerce_year setting)."""
    import datetime
    import pvlib
    import pandas as pd
    from sqlalchemy import func, and_
    from decimal import Decimal
    from app.core.config import get_cached_settings

    try:
        # Get configuration settings
        settings = get_cached_settings()
        coerce_year = settings.pvgis_coerce_year

        # Round latitude and longitude to 3 decimal places to minimize PVGIS requests
        # This groups nearby locations together (~111m precision at equator)
        lat_rounded = round(float(latitude), 3)
        lon_rounded = round(float(longitude), 3)

        # Check if we already have a complete dataset (8760 entries) for this location
        count_result = await db.execute(
            select(func.count(WeatherMeasurements.id))
            .filter(
                and_(
                    WeatherMeasurements.latitude == Decimal(str(lat_rounded)),
                    WeatherMeasurements.longitude == Decimal(str(lon_rounded))
                )
            )
        )
        existing_count = count_result.scalar()

        if existing_count >= 8760:
            # We have a complete dataset, retrieve it from database
            result = await db.execute(
                select(WeatherMeasurements)
                .filter(
                    and_(
                        WeatherMeasurements.latitude == Decimal(str(lat_rounded)),
                        WeatherMeasurements.longitude == Decimal(str(lon_rounded))
                    )
                )
                .order_by(WeatherMeasurements.time_utc)
            )
            weather_records = result.scalars().all()

            # Convert database records to pandas DataFrame format similar to PVGIS
            data_records = []
            for record in weather_records:
                data_records.append({
                    'temp_air': float(record.temp_air) if record.temp_air is not None else None,
                    'relative_humidity': float(record.relative_humidity) if record.relative_humidity is not None else None,
                    'ghi': float(record.ghi) if record.ghi is not None else None,
                    'dni': float(record.dni) if record.dni is not None else None,
                    'dhi': float(record.dhi) if record.dhi is not None else None,
                    'IR(h)': float(record.ir_h) if record.ir_h is not None else None,
                    'wind_speed': float(record.wind_speed) if record.wind_speed is not None else None,
                    'wind_direction': float(record.wind_direction) if record.wind_direction is not None else None,
                    'pressure': int(record.pressure) if record.pressure is not None else None
                })

            # Create basic metadata similar to PVGIS format
            metadata_dict = {
                'inputs': {
                    'latitude': lat_rounded,
                    'longitude': lon_rounded,
                    'radiation_database': 'PVGIS-SARAH2',
                    'meteo_database': 'ERA5',
                    'year_min': coerce_year,
                    'year_max': coerce_year
                },
                'outputs': {
                    'tmy_hourly': {
                        'variables': {
                            'temp_air': 'Air temperature (°C)',
                            'relative_humidity': 'Relative humidity (%)',
                            'ghi': 'Global horizontal irradiance (W/m²)',
                            'dni': 'Direct normal irradiance (W/m²)',
                            'dhi': 'Diffuse horizontal irradiance (W/m²)',
                            'IR(h)': 'Infrared radiation from sky (W/m²)',
                            'wind_speed': 'Wind speed (m/s)',
                            'wind_direction': 'Wind direction (°)',
                            'pressure': 'Air pressure (Pa)'
                        }
                    }
                },
                'months_selected': list(range(1, 13))
            }

        else:
            # No complete dataset exists, download from PVGIS
            data, metadata = pvlib.iotools.get_pvgis_tmy(
                latitude=lat_rounded,
                longitude=lon_rounded,
                coerce_year=coerce_year
            )

            # Store the downloaded data in the database
            weather_measurements = []

            # Create base datetime for the year - TMY data represents a typical year
            base_year = coerce_year

            for i, (timestamp, row) in enumerate(data.iterrows()):
                # Create a datetime for each hour of the year
                hour_of_year = i  # 0-8759
                day_of_year = (hour_of_year // 24) + 1
                hour_of_day = hour_of_year % 24

                # Create datetime using the specified year
                dt = datetime.datetime(base_year, 1, 1) + datetime.timedelta(days=day_of_year-1, hours=hour_of_day)
                dt_utc = dt.replace(tzinfo=datetime.timezone.utc)

                weather_measurement = WeatherMeasurements(
                    time_utc=dt_utc,
                    latitude=Decimal(str(lat_rounded)),
                    longitude=Decimal(str(lon_rounded)),
                    temp_air=float(row['temp_air']) if pd.notna(row['temp_air']) else None,
                    relative_humidity=float(row['relative_humidity']) if pd.notna(row['relative_humidity']) else None,
                    ghi=float(row['ghi']) if pd.notna(row['ghi']) else None,
                    dni=float(row['dni']) if pd.notna(row['dni']) else None,
                    dhi=float(row['dhi']) if pd.notna(row['dhi']) else None,
                    ir_h=float(row['IR(h)']) if pd.notna(row['IR(h)']) else None,
                    wind_speed=float(row['wind_speed']) if pd.notna(row['wind_speed']) else None,
                    wind_direction=float(row['wind_direction']) % 360.0 if pd.notna(row['wind_direction']) else None,  # Normalize 360 to 0
                    pressure=int(row['pressure']) if pd.notna(row['pressure']) else None
                )
                weather_measurements.append(weather_measurement)

            # Bulk insert the weather measurements
            db.add_all(weather_measurements)
            await db.commit()

            # Convert pandas DataFrame to dict for JSON serialization
            data_records = data.to_dict(orient='records')

            # Convert metadata to dict if it's not already
            metadata_dict = dict(metadata) if metadata else {}

        # Create response
        response = PvgisTmyResponse(
            data={"records": data_records},
            metadata=metadata_dict,
            latitude=lat_rounded,
            longitude=lon_rounded,
            coerce_year=coerce_year,
            generated_at=datetime.datetime.now()
        )

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating PVGIS TMY data: {str(e)}")
