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
from app.utils.trip_statistics import (
    compute_global_trip_statistics_combined,
    extract_stop_to_stop_statistics_for_schedule,
    extract_route_difficulty_metrics_from_elevation
)

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