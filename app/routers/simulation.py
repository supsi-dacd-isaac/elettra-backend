from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID
import numpy as np
import pandas as pd

from app.database import get_async_session
from app.schemas.database import (
    SimulationRunsCreate, SimulationRunsRead, SimulationRunsUpdate,
)
from app.schemas.responses import SimulationRunResults, TripStatisticsResponse, CombinedTripStatisticsResponse
from app.schemas.requests import TripStatisticsRequest
from app.schemas.external_apis import PvgisTmyResponse
from app.models import (
    Users, SimulationRuns, WeatherMeasurements,
    GtfsTrips, GtfsStops, GtfsStopsTimes
)
from app.core.auth import get_current_user

router = APIRouter()

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

@router.get("/simulation-runs/{run_id}/results", response_model=SimulationRunResults)
async def get_simulation_run_results(
    run_id: UUID,
    keys: str = None,  # Optional comma-separated list of keys to filter
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user)
):
    """
    Get simulation run output results, either complete or filtered by specific keys.

    Args:
        run_id: UUID of the simulation run
        keys: Optional comma-separated list of JSON keys to extract from output_results
              Example: ?keys=energy_consumption,battery_usage,costs
              If not provided, returns all output_results

    Returns:
        SimulationRunResults with either complete or filtered output_results
    """
    # Get the simulation run
    sim_run = await db.get(SimulationRuns, run_id)
    if sim_run is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")

    requested_keys = None

    # Check if output_results exists
    if sim_run.output_results is None:
        output_results = None
    elif keys:
        # Filter output_results by requested keys
        requested_keys = [key.strip() for key in keys.split(',')]

        # Handle both dict and list cases for output_results
        if isinstance(sim_run.output_results, dict):
            # Filter dictionary keys
            filtered_results = {}
            for key in requested_keys:
                if key in sim_run.output_results:
                    filtered_results[key] = sim_run.output_results[key]
            output_results = filtered_results if filtered_results else None
        elif isinstance(sim_run.output_results, list):
            # For list results, return the original list but note the requested keys
            output_results = sim_run.output_results
            # Note: For list results, we can't filter by keys, so we return all data
        else:
            output_results = sim_run.output_results
    else:
        # Return all output_results
        output_results = sim_run.output_results

    # Create response
    return SimulationRunResults(
        run_id=sim_run.id,
        status=sim_run.status,
        output_results=output_results,
        completed_at=sim_run.completed_at,
        requested_keys=requested_keys
    )

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


@router.post("/trip-statistics/", response_model=CombinedTripStatisticsResponse)
async def compute_trip_statistics(
    request: TripStatisticsRequest,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user)
):
    """
    Compute combined trip statistics for one or multiple trips as a single sequence.
    Concatenates GTFS schedules and elevation profiles (offsetting cumulative distance)
    and returns a single statistics object.
    """
    import os
    import io
    from minio import Minio

    if not request.trip_ids:
        return CombinedTripStatisticsResponse(trip_ids=[], statistics={}, error=None)

    # Collect schedules and elevation dfs
    schedules: list[pd.DataFrame] = []
    elevation_dfs: list[pd.DataFrame] = []

    for idx, trip_id in enumerate(request.trip_ids):
        # 1) Schedule
        result = await db.execute(
            select(
                GtfsStops,
                GtfsStopsTimes.arrival_time,
                GtfsStopsTimes.departure_time,
                GtfsStopsTimes.stop_sequence
            )
            .join(GtfsStopsTimes, GtfsStops.id == GtfsStopsTimes.stop_id)
            .filter(GtfsStopsTimes.trip_id == trip_id)
            .order_by(GtfsStopsTimes.stop_sequence)
        )
        rows = result.all()
        if rows:
            trip_schedule_data = [{
                'stop_id': stop.stop_id,
                'stop_name': stop.stop_name,
                'stop_lat': stop.stop_lat,
                'stop_lon': stop.stop_lon,
                'arrival_time': arrival_time,
                'departure_time': departure_time,
                'stop_sequence': stop_sequence,
                'trip_index': idx
            } for (stop, arrival_time, departure_time, stop_sequence) in rows]
            schedules.append(pd.DataFrame(trip_schedule_data))

        # 2) Elevation (MinIO)
        try:
            trip = await db.get(GtfsTrips, trip_id)
            if trip and trip.shape_id:
                endpoint = os.getenv("MINIO_ENDPOINT", "minio:9000")
                access_key = os.getenv("AWS_ACCESS_KEY_ID", "minio_user")
                secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "minio_password")
                secure = os.getenv("MINIO_SECURE", "false").lower() in ("1", "true", "yes", "on")
                client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
                bucket_name = "elevation-profiles"
                object_name = f"{trip.shape_id}.parquet"
                response = client.get_object(bucket_name, object_name)
                try:
                    data = response.read()
                finally:
                    response.close()
                    response.release_conn()
                df = pd.read_parquet(io.BytesIO(data))
                if 'cumulative_distance_m' not in df.columns:
                    if len(df) > 1:
                        from math import radians, cos, sin, asin, sqrt
                        distances = [0.0]
                        for i in range(1, len(df)):
                            lat1 = radians(df.iloc[i-1]['latitude'])
                            lon1 = radians(df.iloc[i-1]['longitude'])
                            lat2 = radians(df.iloc[i]['latitude'])
                            lon2 = radians(df.iloc[i]['longitude'])
                            dlat = lat2 - lat1
                            dlon = lon2 - lon1
                            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
                            c = 2 * asin(sqrt(a))
                            r = 6371000
                            distances.append(distances[-1] + c * r)
                        df['cumulative_distance_m'] = distances
                    else:
                        df['cumulative_distance_m'] = [0.0]
                elevation_dfs.append(df)
        except Exception:
            # tolerate missing elevation
            pass

    # Concatenate schedules
    if schedules:
        concat_schedule = pd.concat(schedules, ignore_index=True)
    else:
        concat_schedule = pd.DataFrame()

    # Concatenate elevation with offset
    if elevation_dfs:
        combined_elev_parts = []
        offset = 0.0
        for edf in elevation_dfs:
            edfc = edf.copy()
            if 'cumulative_distance_m' in edfc.columns:
                edfc['cumulative_distance_m'] = edfc['cumulative_distance_m'] + offset
                offset = float(edfc['cumulative_distance_m'].max())
            combined_elev_parts.append(edfc)
        combined_elev = pd.concat(combined_elev_parts, ignore_index=True)
    else:
        combined_elev = pd.DataFrame()

    # Compute combined stats
    global_stats = compute_global_trip_statistics_combined(concat_schedule, combined_elev)
    segment_stats = extract_stop_to_stop_statistics_for_schedule(concat_schedule, combined_elev)
    difficulty_stats = extract_route_difficulty_metrics_from_elevation(combined_elev)

    stats = {}
    stats.update(global_stats)
    stats.update(segment_stats)
    stats.update(difficulty_stats)

    return CombinedTripStatisticsResponse(
        trip_ids=request.trip_ids,
        statistics=stats,
        error=None
    )


def parse_gtfs_hms_to_seconds(t: str) -> int:
    """Parse GTFS time string (HH:MM:SS) to seconds from midnight."""
    h, m, s = map(int, t.split(':'))
    return h*3600 + m*60 + s


def dur_sec(dep_hms: str, arr_hms: str) -> int:
    """Calculate duration in seconds between departure and arrival times."""
    dep = parse_gtfs_hms_to_seconds(dep_hms)
    arr = parse_gtfs_hms_to_seconds(arr_hms)
    if arr < dep:  # crossed midnight
        arr += 24*3600
    return max(arr - dep, 0)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance between two points on Earth."""
    from math import radians, cos, sin, asin, sqrt
    
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    r = 6371  # Radius of earth in kilometers
    return c * r * 1000  # Convert to meters


def compute_global_trip_statistics_combined(trip_schedule: pd.DataFrame, elevation_df: pd.DataFrame) -> dict:
    """Compute global stats for a trip using schedule for timing and elevation for distance/elevation metrics."""
    stats = {}
    try:
        if trip_schedule is None or len(trip_schedule) == 0:
            return stats
        
        # Extract start and end times (minutes from midnight)
        first_stop_overall = trip_schedule.iloc[0]
        last_stop_overall = trip_schedule.iloc[-1]
        
        # Start time: arrival at first stop
        start_seconds = parse_gtfs_hms_to_seconds(first_stop_overall['arrival_time'])
        stats['start_time_minutes'] = start_seconds / 60
        
        # End time: departure from last stop
        end_seconds = parse_gtfs_hms_to_seconds(last_stop_overall['departure_time'])
        stats['end_time_minutes'] = end_seconds / 60
            
        # Combined duration = sum(per-trip durations) + sum(inter-trip gaps)
        if 'trip_index' in trip_schedule.columns:
            grouped = [g for _, g in trip_schedule.groupby('trip_index', sort=True)]
            # Sum per-trip durations
            total_trip_seconds = 0
            for g in grouped:
                first_stop_trip = g.iloc[0]
                last_stop_trip = g.iloc[-1]
                total_trip_seconds += dur_sec(first_stop_trip['departure_time'], last_stop_trip['arrival_time'])
            # Sum inter-trip gaps (arrival of t -> departure of t+1)
            inter_trip_gap_seconds_for_duration = 0
            for i in range(len(grouped) - 1):
                last_arrival = grouped[i].iloc[-1]['arrival_time']
                next_departure = grouped[i + 1].iloc[0]['departure_time']
                inter_trip_gap_seconds_for_duration += dur_sec(last_arrival, next_departure)
            total_seconds = total_trip_seconds + inter_trip_gap_seconds_for_duration
        else:
            total_seconds = dur_sec(first_stop_overall['departure_time'], last_stop_overall['arrival_time'])
        stats['total_duration_minutes'] = total_seconds / 60
        stats['total_number_of_stops'] = len(trip_schedule)

        dwell_times = []
        per_stop_dwell_seconds = 0
        for _, stop in trip_schedule.iterrows():
            per_stop_dwell_seconds += dur_sec(stop['arrival_time'], stop['departure_time'])

        # Include inter-trip gaps as dwell (arrival of trip t -> departure of trip t+1)
        inter_trip_gap_seconds = 0
        if 'trip_index' in trip_schedule.columns:
            grouped = [g for _, g in trip_schedule.groupby('trip_index', sort=True)]
            for i in range(len(grouped) - 1):
                last_arrival = grouped[i].iloc[-1]['arrival_time']
                next_departure = grouped[i + 1].iloc[0]['departure_time']
                inter_trip_gap_seconds += dur_sec(last_arrival, next_departure)

        total_dwell_seconds = per_stop_dwell_seconds + inter_trip_gap_seconds
        stats['total_dwell_time_minutes'] = total_dwell_seconds / 60
        stats['driving_time_minutes'] = max(stats['total_duration_minutes'] - stats['total_dwell_time_minutes'], 0)

        # Use elevation_df for distance if available; fallback to haversine along schedule
        if elevation_df is not None and len(elevation_df) > 0 and 'cumulative_distance_m' in elevation_df.columns:
            stats['total_distance_m'] = float(elevation_df['cumulative_distance_m'].max())
        else:
            total_distance = 0.0
            for i in range(len(trip_schedule) - 1):
                lat1, lon1 = trip_schedule.iloc[i]['stop_lat'], trip_schedule.iloc[i]['stop_lon']
                lat2, lon2 = trip_schedule.iloc[i + 1]['stop_lat'], trip_schedule.iloc[i + 1]['stop_lon']
                total_distance += haversine_distance(lat1, lon1, lat2, lon2)
            stats['total_distance_m'] = total_distance

        dur_h = max(stats['total_duration_minutes'], 0.0) / 60.0
        stats['average_speed_kmh'] = (stats['total_distance_m'] / 1000.0) / dur_h if dur_h > 0 else 0.0
        drive_h = max(stats.get('driving_time_minutes', 0.0), 0.0) / 60.0
        stats['driving_average_speed_kmh'] = (stats['total_distance_m'] / 1000.0) / drive_h if drive_h > 0 else 0.0

        # Elevation metrics from elevation_df
        if elevation_df is not None and len(elevation_df) > 0 and 'altitude_m' in elevation_df.columns and 'cumulative_distance_m' in elevation_df.columns:
            altitudes = elevation_df['altitude_m'].values
            stats['elevation_range_m'] = float(np.max(altitudes) - np.min(altitudes))
            stats['mean_elevation_m'] = float(np.mean(altitudes))
            stats['min_elevation_m'] = float(np.min(altitudes))
            stats['max_elevation_m'] = float(np.max(altitudes))

            elevation_changes = np.diff(altitudes)
            distances = elevation_df['cumulative_distance_m'].values
            distance_changes = np.diff(distances)
            # Suppress divide by zero warnings (handled by np.where)
            with np.errstate(divide='ignore', invalid='ignore'):
                gradients = np.where(distance_changes != 0, elevation_changes / distance_changes, 0)

            stats['total_ascent_m'] = float(np.sum(elevation_changes[elevation_changes > 0]))
            stats['total_descent_m'] = float(np.abs(np.sum(elevation_changes[elevation_changes < 0])))
            stats['mean_gradient'] = float(np.mean(gradients))
            stats['net_elevation_change_m'] = float(altitudes[-1] - altitudes[0])

            # Profile type classification
            min_elevation_threshold = 1.0
            if stats['total_descent_m'] < min_elevation_threshold:
                if stats['total_ascent_m'] < min_elevation_threshold:
                    stats['ascent_descent_ratio'] = None
                    stats['elevation_profile_type'] = 'flat'
                else:
                    stats['ascent_descent_ratio'] = None
                    stats['elevation_profile_type'] = 'ascent_only'
            else:
                if stats['total_ascent_m'] < min_elevation_threshold:
                    stats['ascent_descent_ratio'] = 0.0
                    stats['elevation_profile_type'] = 'descent_only'
                else:
                    stats['ascent_descent_ratio'] = stats['total_ascent_m'] / stats['total_descent_m']
                    stats['elevation_profile_type'] = 'mixed'
        else:
            stats['elevation_range_m'] = 0.0
            stats['mean_elevation_m'] = 0.0
            stats['min_elevation_m'] = 0.0
            stats['max_elevation_m'] = 0.0
            stats['total_ascent_m'] = 0.0
            stats['total_descent_m'] = 0.0
            stats['mean_gradient'] = 0.0
            stats['net_elevation_change_m'] = 0.0
            stats['ascent_descent_ratio'] = 0.0

        return stats
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error computing combined global trip stats: {e}")
        return {}


def _calculate_stop_distance(stop1, stop2):
    """Calculate distance between two stops using haversine formula."""
    from math import radians, cos, sin, asin, sqrt
    
    lat1, lon1 = radians(stop1['stop_lat']), radians(stop1['stop_lon'])
    lat2, lon2 = radians(stop2['stop_lat']), radians(stop2['stop_lon'])
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    
    # Earth's radius in meters
    r = 6371000
    return c * r


def _calculate_segment_duration(stop1, stop2):
    """Calculate duration between two stops in seconds."""
    return dur_sec(stop1['departure_time'], stop2['arrival_time'])


def _calculate_dwell_time(stop):
    """Calculate dwell time at a stop in minutes."""
    dwell_seconds = dur_sec(stop['arrival_time'], stop['departure_time'])
    return dwell_seconds / 60  # Convert to minutes


def _calculate_segment_elevation_stats(stop1, stop2, elevation_df):
    """Calculate elevation statistics for a segment between two stops using actual elevation data."""
    if elevation_df is None or len(elevation_df) == 0:
        return {}
    
    # Get stop IDs and coordinates from GTFS data
    stop1_id = stop1.get('stop_id', '')
    stop2_id = stop2.get('stop_id', '')

    # Skip identical stops
    if stop1_id == stop2_id:
        return {}

    # Check if elevation data has pre-segmented data with start_stop_id and end_stop_id
    if 'start_stop_id' in elevation_df.columns and 'end_stop_id' in elevation_df.columns:
        segment_mask = (
            (elevation_df['start_stop_id'] == stop1_id) &
            (elevation_df['end_stop_id'] == stop2_id)
        )
        
        if segment_mask.any():
            segment_elevation = elevation_df[segment_mask]
        else:
            return {}
    else:
        # Work with raw elevation profile - find closest points to stops
        # Need latitude, longitude, and altitude_m columns
        if 'latitude' not in elevation_df.columns or 'longitude' not in elevation_df.columns or 'altitude_m' not in elevation_df.columns:
            return {}
        
        # Get stop coordinates
        stop1_lat = stop1.get('stop_lat')
        stop1_lon = stop1.get('stop_lon')
        stop2_lat = stop2.get('stop_lat')
        stop2_lon = stop2.get('stop_lon')
        
        if stop1_lat is None or stop1_lon is None or stop2_lat is None or stop2_lon is None:
            return {}
        
        # Find closest elevation points to each stop
        def find_closest_index(df, target_lat, target_lon):
            """Find index of closest point in elevation profile to target coordinates."""
            distances = np.sqrt(
                (df['latitude'] - target_lat)**2 + 
                (df['longitude'] - target_lon)**2
            )
            return distances.argmin()
        
        idx1 = find_closest_index(elevation_df, stop1_lat, stop1_lon)
        idx2 = find_closest_index(elevation_df, stop2_lat, stop2_lon)
        
        # Ensure idx1 < idx2 (forward direction)
        if idx1 >= idx2:
            return {}
        
        # Extract segment from elevation profile
        segment_elevation = elevation_df.iloc[idx1:idx2+1].copy()
    
    if len(segment_elevation) == 0:
        return {}
    
    # Calculate actual route distance from cumulative distance
    if 'cumulative_distance_m' in segment_elevation.columns:
        start_distance = segment_elevation['cumulative_distance_m'].iloc[0]
        end_distance = segment_elevation['cumulative_distance_m'].iloc[-1]
        segment_distance = end_distance - start_distance
    else:
        # Calculate distance from coordinates if cumulative_distance not available
        segment_distance = 0
        if 'latitude' in segment_elevation.columns and 'longitude' in segment_elevation.columns:
            for i in range(len(segment_elevation) - 1):
                lat1 = segment_elevation.iloc[i]['latitude']
                lon1 = segment_elevation.iloc[i]['longitude']
                lat2 = segment_elevation.iloc[i+1]['latitude']
                lon2 = segment_elevation.iloc[i+1]['longitude']
                segment_distance += haversine_distance(lat1, lon1, lat2, lon2)
    
    # Calculate elevation statistics
    start_elevation = segment_elevation['altitude_m'].iloc[0]
    end_elevation = segment_elevation['altitude_m'].iloc[-1]
    elevation_diff = end_elevation - start_elevation
    
    # Calculate cumulative ascent/descent
    if len(segment_elevation) > 1:
        diffs = segment_elevation['altitude_m'].diff().dropna()
        ascent = float(diffs.clip(lower=0).sum())
        descent = float((-diffs).clip(lower=0).sum())
    else:
        ascent = max(elevation_diff, 0)
        descent = max(-elevation_diff, 0)
    
    # Calculate gradients
    mean_gradient = elevation_diff / segment_distance if segment_distance > 0 else 0
    
    # Calculate max gradient
    if len(segment_elevation) > 1:
        elevation_diffs = segment_elevation['altitude_m'].diff().dropna()
        if 'cumulative_distance_m' in segment_elevation.columns:
            distance_diffs = segment_elevation['cumulative_distance_m'].diff().dropna()
        else:
            # Calculate point-to-point distances
            distance_diffs = []
            for i in range(len(segment_elevation) - 1):
                lat1 = segment_elevation.iloc[i]['latitude']
                lon1 = segment_elevation.iloc[i]['longitude']
                lat2 = segment_elevation.iloc[i+1]['latitude']
                lon2 = segment_elevation.iloc[i+1]['longitude']
                distance_diffs.append(haversine_distance(lat1, lon1, lat2, lon2))
            distance_diffs = pd.Series(distance_diffs)
        
        with np.errstate(divide='ignore', invalid='ignore'):
            gradients = np.where(distance_diffs != 0, elevation_diffs / distance_diffs, 0)
        max_gradient = np.abs(gradients).max() if len(gradients) > 0 else 0
    else:
        max_gradient = abs(mean_gradient)
    
    return {
        'start_elevation_m': float(start_elevation),
        'end_elevation_m': float(end_elevation),
        'segment_distance_m': float(segment_distance),
        'ascent_m': float(ascent),
        'descent_m': float(descent),
        'mean_gradient': float(mean_gradient),
        'max_gradient': float(max_gradient)
    }


def extract_stop_to_stop_statistics_for_schedule(trip_schedule: pd.DataFrame, elevation_df: pd.DataFrame) -> dict:
    """Compute segment statistics across a GTFS schedule, using elevation data when available."""
    stats = {}
    try:
        if trip_schedule is None or len(trip_schedule) < 2:
            return stats
        segment_stats = []
        for i in range(len(trip_schedule) - 1):
            current_stop = trip_schedule.iloc[i]
            next_stop = trip_schedule.iloc[i + 1]
            # Skip cross-boundary segments between different trips when concatenated
            try:
                if 'trip_index' in trip_schedule.columns and current_stop['trip_index'] != next_stop['trip_index']:
                    continue
            except Exception:
                pass
            # Skip stitched boundaries: identical stop repeated at trip junctions,
            # or virtually the same location within a small threshold
            try:
                if current_stop['stop_id'] == next_stop['stop_id']:
                    continue
                approx_gap_m = haversine_distance(
                    float(current_stop['stop_lat']), float(current_stop['stop_lon']),
                    float(next_stop['stop_lat']), float(next_stop['stop_lon'])
                )
                if approx_gap_m < 200.0:
                    continue
            except Exception:
                pass
            segment_duration = _calculate_segment_duration(current_stop, next_stop)
            
            # Get elevation data and actual route distance for this segment
            segment_elevation_stats = _calculate_segment_elevation_stats(
                current_stop, next_stop, elevation_df
            )
            
            # Use actual route distance from elevation data, fallback to haversine
            segment_distance = segment_elevation_stats.get('segment_distance_m', 0)
            if segment_distance == 0:
                segment_distance = _calculate_stop_distance(current_stop, next_stop)
            
            km = segment_distance / 1000.0
            h = segment_duration / 3600.0
            segment_speed_kmh = km / h if h > 0 else 0.0
            
            segment_data = {
                'segment_distance_m': segment_distance,
                'segment_duration_minutes': segment_duration / 60,
                'segment_speed_kmh': segment_speed_kmh,
                'start_stop_id': current_stop['stop_id'],
                'end_stop_id': next_stop['stop_id'],
                'start_elevation_m': segment_elevation_stats.get('start_elevation_m', 0),
                'end_elevation_m': segment_elevation_stats.get('end_elevation_m', 0),
                'segment_ascent_m': segment_elevation_stats.get('ascent_m', 0),
                'segment_descent_m': segment_elevation_stats.get('descent_m', 0),
                'segment_mean_gradient': segment_elevation_stats.get('mean_gradient', 0),
                'segment_max_gradient': segment_elevation_stats.get('max_gradient', 0),
                'dwell_time_at_end_minutes': _calculate_dwell_time(next_stop)
            }
            
            segment_stats.append(segment_data)
        
        if segment_stats:
            distances = [s['segment_distance_m'] for s in segment_stats]
            durations = [s['segment_duration_minutes'] for s in segment_stats]
            speeds = [s['segment_speed_kmh'] for s in segment_stats]
            ascents = [s['segment_ascent_m'] for s in segment_stats]
            descents = [s['segment_descent_m'] for s in segment_stats]
            gradients = [s['segment_mean_gradient'] for s in segment_stats]
            max_gradients = [s['segment_max_gradient'] for s in segment_stats]
            dwell_times = [s['dwell_time_at_end_minutes'] for s in segment_stats]
            
            # Statistical aggregations
            stats.update({
                'num_segments': len(segment_stats),
                'mean_segment_distance_m': float(np.mean(distances)),
                'median_segment_distance_m': float(np.median(distances)),
                'min_segment_distance_m': float(np.min(distances)),
                'max_segment_distance_m': float(np.max(distances)),
                'std_segment_distance_m': float(np.std(distances)),
                'mean_segment_duration_minutes': float(np.mean(durations)),
                'median_segment_duration_minutes': float(np.median(durations)),
                'min_segment_duration_minutes': float(np.min(durations)),
                'max_segment_duration_minutes': float(np.max(durations)),
                'mean_segment_speed_kmh': float(np.mean(speeds)),
                'median_segment_speed_kmh': float(np.median(speeds)),
                'min_segment_speed_kmh': float(np.min(speeds)),
                'max_segment_speed_kmh': float(np.max(speeds)),
                'mean_segment_ascent_m': float(np.mean(ascents)),
                'median_segment_ascent_m': float(np.median(ascents)),
                'max_segment_ascent_m': float(np.max(ascents)),
                'mean_segment_descent_m': float(np.mean(descents)),
                'median_segment_descent_m': float(np.median(descents)),
                'max_segment_descent_m': float(np.max(descents)),
                'mean_segment_gradient': float(np.mean(gradients)),
                'median_segment_gradient': float(np.median(gradients)),
                'std_segment_gradient': float(np.std(gradients)),
                'max_segment_gradient': float(np.max(max_gradients)),
                'mean_dwell_time_minutes': float(np.mean(dwell_times)),
                'median_dwell_time_minutes': float(np.median(dwell_times)),
                'num_steep_segments_5pct_threshold': len([g for g in max_gradients if abs(g) > 0.05]),
                'num_steep_segments_10pct_threshold': len([g for g in max_gradients if abs(g) > 0.10]),
                'variance_segment_gradients': float(np.var(gradients))
            })
        return stats
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error extracting segment statistics: {e}")
        return {}


def extract_route_difficulty_metrics_from_elevation(elevation_df: pd.DataFrame) -> dict:
    """Compute route difficulty metrics from elevation profile."""
    stats = {}
    try:
        if elevation_df is None or len(elevation_df) == 0:
            return stats
        total_distance = elevation_df['cumulative_distance_m'].max() if 'cumulative_distance_m' in elevation_df.columns else 0.0
        if total_distance > 0:
            elevation_variance = elevation_df['altitude_m'].var()
            roughness_index = elevation_variance / total_distance
        else:
            roughness_index = 0

        if len(elevation_df) > 1 and 'altitude_m' in elevation_df.columns and 'cumulative_distance_m' in elevation_df.columns:
            elevation_diffs = elevation_df['altitude_m'].diff().dropna()
            distance_diffs = elevation_df['cumulative_distance_m'].diff().dropna()
            with np.errstate(divide='ignore', invalid='ignore'):
                gradients = np.where(distance_diffs != 0, elevation_diffs / distance_diffs, 0)
            uphill_segments = (gradients > 0.01).sum()
            downhill_segments = (gradients < -0.01).sum()
            flat_segments = ((gradients >= -0.01) & (gradients <= 0.01)).sum()
            total_segments = len(gradients)
            if total_segments > 0:
                pct_uphill = uphill_segments / total_segments * 100
                pct_downhill = downhill_segments / total_segments * 100
                pct_flat = flat_segments / total_segments * 100
            else:
                pct_uphill = pct_downhill = pct_flat = 0
        else:
            pct_uphill = pct_downhill = pct_flat = 0

        if len(elevation_df) > 1 and 'altitude_m' in elevation_df.columns and 'cumulative_distance_m' in elevation_df.columns:
            elevation_diffs = elevation_df['altitude_m'].diff().dropna()
            distance_diffs = elevation_df['cumulative_distance_m'].diff().dropna()
            with np.errstate(divide='ignore', invalid='ignore'):
                gradients = np.where(distance_diffs != 0, elevation_diffs / distance_diffs, 0)
            ratio_gradient_negative = (gradients < 0).sum() / len(gradients) if len(gradients) > 0 else 0
            ratio_gradient_0_3 = ((gradients >= 0) & (gradients < 0.03)).sum() / len(gradients) if len(gradients) > 0 else 0
            ratio_gradient_3_6 = ((gradients >= 0.03) & (gradients < 0.06)).sum() / len(gradients) if len(gradients) > 0 else 0
            ratio_gradient_6_plus = (gradients >= 0.06).sum() / len(gradients) if len(gradients) > 0 else 0
        else:
            ratio_gradient_negative = ratio_gradient_0_3 = ratio_gradient_3_6 = ratio_gradient_6_plus = 0

        total_ascent = elevation_df['altitude_m'].diff().clip(lower=0).sum() if 'altitude_m' in elevation_df.columns else 0
        total_distance_km = (total_distance / 1000) if total_distance else 0
        if len(elevation_df) > 1 and total_distance_km > 0:
            elevation_changes = elevation_df['altitude_m'].diff().abs()
            significant_changes = (elevation_changes > 1.0).sum()
            change_frequency_per_km = significant_changes / total_distance_km
        else:
            significant_changes = 0
            change_frequency_per_km = 0

        ratio_uphill = (pct_uphill / 100.0) if isinstance(pct_uphill, (int, float)) else 0
        normalized_roughness = min((roughness_index * 1000), 1.0) if isinstance(roughness_index, (int, float)) else 0
        normalized_frequency = min((change_frequency_per_km / 10.0), 1.0) if isinstance(change_frequency_per_km, (int, float)) else 0
        complexity_score = (
            normalized_roughness * 0.3 +
            ratio_uphill * 0.3 +
            ratio_gradient_6_plus * 0.3 +
            normalized_frequency * 0.1
        )
        stats.update({
            'roughness_index': float(roughness_index),
            'pct_uphill_segments': float(pct_uphill),
            'pct_downhill_segments': float(pct_downhill),
            'pct_flat_segments': float(pct_flat),
            'ratio_gradient_negative': float(ratio_gradient_negative),
            'ratio_gradient_0_3': float(ratio_gradient_0_3),
            'ratio_gradient_3_6': float(ratio_gradient_3_6),
            'ratio_gradient_6_plus': float(ratio_gradient_6_plus),
            'significant_elevation_changes': int(significant_changes),
            'elevation_change_frequency_per_km': float(change_frequency_per_km),
            'route_complexity_score': float(complexity_score)
        })
        return stats
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error extracting route difficulty metrics: {e}")
        return {}