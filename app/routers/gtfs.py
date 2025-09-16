from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from uuid import UUID

from app.database import get_async_session
from app.schemas import (
    GtfsCalendarRead, GtfsStopsReadWithTimes, GtfsTripsRead, VariantsReadWithRoute,
    GtfsRoutesCreate, GtfsRoutesRead, GtfsRoutesReadWithVariant, ElevationProfileResponse,
)
from app.models import (
    Users, GtfsAgencies, GtfsCalendar,
    GtfsStops, GtfsTrips, Variants,
    GtfsStopsTimes, GtfsRoutes
)
from app.core.auth import get_current_user
from minio import Minio
import pandas as pd
import io
import os
import httpx

try:
    # pyproj is a dependency of geopandas, but import may fail if extras not installed
    from pyproj import Transformer
except Exception:  # pragma: no cover
    Transformer = None  # type: ignore

try:
    # For decoding OSRM polyline geometry
    import polyline
except ImportError:
    polyline = None  # type: ignore

router = APIRouter()



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

@router.get("/gtfs-routes/by-stop/{stop_id}", response_model=List[GtfsRoutesRead])
async def read_routes_by_stop(
    stop_id: UUID, 
    agency_id: Optional[UUID] = None,
    db: AsyncSession = Depends(get_async_session), 
    current_user: Users = Depends(get_current_user)
):
    """Get all unique GTFS routes for a given stop ID, optionally filtered by agency"""
    query = (
        select(GtfsRoutes)
        .join(GtfsTrips, GtfsRoutes.id == GtfsTrips.route_id)
        .join(GtfsStopsTimes, GtfsTrips.id == GtfsStopsTimes.trip_id)
        .filter(GtfsStopsTimes.stop_id == stop_id)
        .distinct()
    )
    
    if agency_id is not None:
        query = query.filter(GtfsRoutes.agency_id == agency_id)
    
    result = await db.execute(query)
    routes = result.scalars().all()
    return routes

# GTFS Trips endpoints (authenticated users only)
@router.get("/gtfs-trips/by-route/{route_id}", response_model=List[GtfsTripsRead])
async def read_trips_by_route(
    route_id: UUID,
    day_of_week: Optional[str] = None,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    query = select(GtfsTrips).filter(GtfsTrips.route_id == route_id)

    if day_of_week is not None:
        day = day_of_week.strip().lower()
        valid_days = {
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        }
        if day not in valid_days:
            raise HTTPException(status_code=400, detail="Invalid day_of_week. Use monday..sunday")

        query = (
            query.join(GtfsCalendar, GtfsTrips.service_id == GtfsCalendar.id)
            .filter(getattr(GtfsCalendar, day) == 1)
        )

    result = await db.execute(query)
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
@router.get("/gtfs-stops/by-trip/{trip_id}", response_model=List[GtfsStopsReadWithTimes])
async def read_stops_by_trip(trip_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    """Get all GTFS stops for a given trip ID"""
    result = await db.execute(
        select(
            GtfsStops,
            GtfsStopsTimes.arrival_time,
            GtfsStopsTimes.departure_time
        )
        .join(GtfsStopsTimes, GtfsStops.id == GtfsStopsTimes.stop_id)
        .filter(GtfsStopsTimes.trip_id == trip_id)
        .order_by(GtfsStopsTimes.stop_sequence)
    )
    rows = result.all()
    return [
        GtfsStopsReadWithTimes(
            id=stop.id,
            stop_id=stop.stop_id,
            stop_code=stop.stop_code,
            stop_name=stop.stop_name,
            stop_desc=stop.stop_desc,
            stop_lat=stop.stop_lat,
            stop_lon=stop.stop_lon,
            zone_id=stop.zone_id,
            stop_url=stop.stop_url,
            location_type=stop.location_type,
            parent_station=stop.parent_station,
            stop_timezone=stop.stop_timezone,
            wheelchair_boarding=stop.wheelchair_boarding,
            platform_code=stop.platform_code,
            level_id=stop.level_id,
            arrival_time=arrival_time,
            departure_time=departure_time,
        )
        for (stop, arrival_time, departure_time) in rows
    ]

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


# OSRM distance endpoint
@router.get("/osrm/driving-distance")
async def get_driving_distance_to_stop(
    stop_uuid: UUID,
    lat: float,
    lon: float,
    direction: str = "to_stop",  # "to_stop" or "from_stop"
    coord_sys: str = "wgs84",  # "wgs84" (EPSG:4326), "lv95" (EPSG:2056), "lv03" (EPSG:21781)
    include_geometry: str = "false",  # Include full route geometry as lat/lon points ("true" or "false")
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    """Return OSRM driving distance and duration between a coordinate and a GTFS stop.

    - stop_uuid: UUID primary key of `gtfs_stops.id`
    - lat, lon: input coordinates in the selected `coord_sys`
    - direction: "to_stop" (lat,lon -> stop) or "from_stop" (stop -> lat,lon)
    - coord_sys: one of {"wgs84", "lv95", "lv03"}
    - include_geometry: if True, returns full route geometry as lat/lon coordinates
    """
    # Fetch stop
    stop = await db.get(GtfsStops, stop_uuid)
    if stop is None:
        raise HTTPException(status_code=404, detail="Stop not found")
    if stop.stop_lat is None or stop.stop_lon is None:
        raise HTTPException(status_code=400, detail="Stop has no coordinates")

    # Convert input to WGS84 (lat, lon)
    def to_wgs84(input_lat: float, input_lon: float, system: str) -> tuple[float, float]:
        if system.lower() in ("wgs84", "epsg:4326"):
            return float(input_lat), float(input_lon)
        if Transformer is None:
            raise HTTPException(status_code=500, detail="Coordinate transform not available: pyproj missing")

        if system.lower() in ("lv95", "epsg:2056"):
            # LV95 (EPSG:2056) input order: E, N (x, y)
            transformer = Transformer.from_crs(2056, 4326, always_xy=True)
            wgs_lon, wgs_lat = transformer.transform(float(input_lon), float(input_lat))
            return wgs_lat, wgs_lon
        if system.lower() in ("lv03", "epsg:21781"):
            # LV03 (EPSG:21781) input order: E, N (x, y)
            transformer = Transformer.from_crs(21781, 4326, always_xy=True)
            wgs_lon, wgs_lat = transformer.transform(float(input_lon), float(input_lat))
            return wgs_lat, wgs_lon

        raise HTTPException(status_code=400, detail="Unsupported coord_sys. Use wgs84, lv95, or lv03")

    src_lat, src_lon = to_wgs84(lat, lon, coord_sys)
    stop_lat = float(stop.stop_lat)
    stop_lon = float(stop.stop_lon)

    # OSRM expects lon,lat order
    if direction == "to_stop":
        start_lon, start_lat = src_lon, src_lat
        end_lon, end_lat = stop_lon, stop_lat
    elif direction == "from_stop":
        start_lon, start_lat = stop_lon, stop_lat
        end_lon, end_lat = src_lon, src_lat
    else:
        raise HTTPException(status_code=400, detail="Invalid direction. Use to_stop or from_stop")

    # Convert string parameter to boolean
    include_geom = include_geometry.lower() in ("true", "1", "yes", "on")
    
    osrm_base = os.getenv("OSRM_BASE_URL", "http://osrm:5000")
    overview_param = "full" if include_geom else "false"
    url = f"{osrm_base}/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview={overview_param}"
    

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"OSRM request failed: {exc}")

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=f"OSRM error: {resp.text}")

    data = resp.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise HTTPException(status_code=502, detail=f"OSRM routing failed: {data}")

    route = data["routes"][0]
    response = {
        "direction": direction,
        "coord_sys": coord_sys,
        "distance_meters": route.get("distance"),
        "duration_seconds": route.get("duration"),
        "waypoints": data.get("waypoints", []),
    }
    
    # Add geometry if requested
    if include_geom and "geometry" in route:
        geometry = route.get("geometry")
        if polyline and isinstance(geometry, str):
            # Decode polyline to get actual lat/lon coordinates
            try:
                decoded_coords = polyline.decode(geometry)
                # Convert to list of [lat, lon] pairs
                response["geometry"] = [[lat, lon] for lat, lon in decoded_coords]
                response["geometry_type"] = "coordinates"
                response["geometry_count"] = len(decoded_coords)
            except Exception as e:
                # Fallback to raw geometry if decoding fails
                response["geometry"] = geometry
                response["geometry_type"] = "polyline"
                response["geometry_error"] = str(e)
        else:
            # Return as-is if polyline library not available or geometry not a string
            response["geometry"] = geometry
            response["geometry_type"] = "raw"
    
    return response

# Elevation profile by trip
@router.get("/elevation-profile/by-trip/{trip_id}", response_model=ElevationProfileResponse)
async def get_elevation_profile_by_trip(trip_id: UUID, db: AsyncSession = Depends(get_async_session), current_user: Users = Depends(get_current_user)):
    """Fetch elevation profile parquet by trip's shape_id from MinIO and return as JSON records"""
    # 1) Find the trip to get shape_id
    trip = await db.get(GtfsTrips, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    if not trip.shape_id:
        raise HTTPException(status_code=404, detail="Trip has no shape_id")

    shape_id = trip.shape_id

    # 2) Connect to MinIO within the docker network
    # Using docker-compose defaults: endpoint http://minio:9000 and env AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
    endpoint = os.getenv("MINIO_ENDPOINT", "minio:9000")
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "minio_user")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "minio_password")
    secure = os.getenv("MINIO_SECURE", "false").lower() in ("1", "true", "yes", "on")

    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    bucket_name = "elevation-profiles"
    object_name = f"{shape_id}.parquet"

    # 3) Fetch object and load parquet into pandas
    try:
        response = client.get_object(bucket_name, object_name)
        try:
            data = response.read()
        finally:
            response.close()
            response.release_conn()
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Elevation profile not found for shape_id {shape_id}: {str(e)}")

    try:
        df = pd.read_parquet(io.BytesIO(data))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse parquet for shape_id {shape_id}: {str(e)}")

    # 4) Return as list of dict records
    records = df.to_dict(orient="records")
    return ElevationProfileResponse(shape_id=shape_id, records=records)
