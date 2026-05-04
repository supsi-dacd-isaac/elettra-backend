from typing import List, Union
from uuid import UUID

import numpy as np
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_async_session
from app.schemas.database import PredictionRunsRead, TripPredictionsRead, OptimizationRunsRead
from app.schemas.responses import (
    PredictionSubmitResponse,
    OptimizationSubmitResponse,
    CombinedTripStatisticsResponse,
    OptimizationRunListItemRead,
)
from app.schemas.pagination import (
    PaginatedResponse, PaginationParams, build_paginated_response,
)
from app.schemas.requests import TripStatisticsRequest, PredictionRequest, OptimizationRequest, _OPTIMIZATION_EXAMPLES
from app.schemas.external_apis import (
    PvgisTmyResponse,
    PvgisTmyMetadataResponse,
    WeatherClusteringRequest,
    WeatherClusteringResponse,
    ClusterItem,
)
from app.models import (
    Users, WeatherMeasurements,
    GtfsTrips, GtfsStops, GtfsStopsTimes,
    PredictionRuns, TripPredictions, Shifts, BusesModels,
    OptimizationRuns, YearlyAnalysis,
)
from app.core.auth import get_current_user
from app.services.weather import (
    count_weather_records,
    fetch_weather_records,
    compute_daily_avg_temps,
    run_kmeans_clustering,
    save_clustering,
    get_saved_clustering,
    sanitize_weather_values,
)
from app.utils.trip_statistics import (
    compute_global_trip_statistics_combined,
    extract_stop_to_stop_statistics_for_schedule,
    extract_route_difficulty_metrics_from_elevation
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_optimization_run_name(run: OptimizationRuns) -> str | None:
    """Return the display name for an optimization run.

    Resolution order (read-time, with backward-compat fallback for legacy rows):
    1. `optimization_runs.name` (DB column) if set and non-empty after trim.
    2. `input_params["name"]` if present, a string, and non-empty after trim.
    3. None.

    Old rows can have a NULL DB column; some transitional rows may carry a
    `name` inside `input_params`. New rows store the name only in the
    dedicated DB column.
    """
    db_name = getattr(run, "name", None)
    if isinstance(db_name, str):
        trimmed = db_name.strip()
        if trimmed:
            return trimmed

    input_params = getattr(run, "input_params", None)
    if isinstance(input_params, dict):
        legacy_name = input_params.get("name")
        if isinstance(legacy_name, str):
            trimmed = legacy_name.strip()
            if trimmed:
                return trimmed

    return None


def _serialize_optimization_run(run: OptimizationRuns) -> OptimizationRunsRead:
    """Build an OptimizationRunsRead applying the read-time name fallback."""
    return OptimizationRunsRead.model_validate(run).model_copy(
        update={"name": _resolve_optimization_run_name(run)}
    )


def _serialize_optimization_run_list_item(
    run: OptimizationRuns,
) -> OptimizationRunListItemRead:
    """Build a lightweight list item, extracting summary fields from results.

    The full ``input_params`` and ``results`` blobs are intentionally not
    returned in the list payload — fetch the detail endpoint to read them.
    """
    results = run.results if isinstance(run.results, dict) else {}
    objective_value = results.get("objective_value")
    return OptimizationRunListItemRead(
        id=run.id,
        user_id=run.user_id,
        bus_model_id=run.bus_model_id,
        name=_resolve_optimization_run_name(run),
        mode=run.mode,
        status=run.status,
        created_at=run.created_at,
        completed_at=run.completed_at,
        electrification_feasible=results.get("electrification_feasible"),
        solver_status=results.get("solver_status"),
        objective_value=(
            float(objective_value) if isinstance(objective_value, (int, float)) else None
        ),
    )


# ---------------------------------------------------------------------------
# Prediction Runs endpoints
# ---------------------------------------------------------------------------

@router.post("/prediction-runs/", response_model=PredictionSubmitResponse)
async def create_prediction_runs(
    request: PredictionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    bus_model = await db.get(BusesModels, request.bus_model_id)
    if bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")

    if request.yearly_analysis_id is not None:
        ya = await db.get(YearlyAnalysis, request.yearly_analysis_id)
        if ya is None:
            raise HTTPException(status_code=404, detail="Yearly analysis not found")

    run_ids: list[UUID] = []
    for shift_id in request.shift_ids:
        shift = await db.get(Shifts, shift_id)
        if shift is None:
            raise HTTPException(status_code=404, detail=f"Shift {shift_id} not found")

        run = PredictionRuns(
            user_id=current_user.id,
            shift_id=shift_id,
            bus_model_id=request.bus_model_id,
            yearly_analysis_id=request.yearly_analysis_id,
            model_name=request.model_name,
            external_temp_celsius=request.external_temp_celsius,
            auxiliary_heating_type=request.auxiliary_heating_type,
            occupancy_percent=request.occupancy_percent,
            status="pending",
        )
        db.add(run)
        await db.flush()
        run_ids.append(run.id)

    await db.commit()

    from app.services.prediction import run_prediction_background
    for run_id in run_ids:
        background_tasks.add_task(
            run_prediction_background,
            prediction_run_id=run_id,
            quantiles=request.quantiles,
            num_battery_packs=request.num_battery_packs,
        )

    return PredictionSubmitResponse(prediction_run_ids=run_ids)


@router.get("/prediction-runs/", response_model=List[PredictionRunsRead])
async def list_prediction_runs(
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    result = await db.execute(
        select(PredictionRuns)
        .where(PredictionRuns.user_id == current_user.id)
        .order_by(PredictionRuns.created_at.desc())
    )
    return result.scalars().all()


@router.get("/prediction-runs/{run_id}", response_model=PredictionRunsRead)
async def get_prediction_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    run = await db.get(PredictionRuns, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Prediction run not found")
    return run


@router.get("/prediction-runs/{run_id}/predictions", response_model=List[TripPredictionsRead])
async def get_prediction_run_predictions(
    run_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    run = await db.get(PredictionRuns, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Prediction run not found")

    result = await db.execute(
        select(TripPredictions)
        .where(TripPredictions.prediction_run_id == run_id)
        .order_by(TripPredictions.sequence_number)
    )
    return result.scalars().all()


# ---------------------------------------------------------------------------
# Optimization Runs endpoints
# ---------------------------------------------------------------------------

@router.post("/optimization-runs/", response_model=OptimizationSubmitResponse)
async def create_optimization_run(
    background_tasks: BackgroundTasks,
    request: OptimizationRequest = Body(openapi_examples=_OPTIMIZATION_EXAMPLES),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    """Create and start an optimization run (background task)."""
    if request.bus_model_id is not None:
        bus_model = await db.get(BusesModels, request.bus_model_id)
        if bus_model is None:
            raise HTTPException(status_code=404, detail="Bus model not found")

    for shift_id in request.shift_ids:
        shift = await db.get(Shifts, shift_id)
        if shift is None:
            raise HTTPException(status_code=404, detail=f"Shift {shift_id} not found")

    if request.prediction_run_ids:
        for pred_id in request.prediction_run_ids:
            pred = await db.get(PredictionRuns, pred_id)
            if pred is None:
                raise HTTPException(status_code=404, detail=f"Prediction run {pred_id} not found")
            if pred.status != "completed":
                raise HTTPException(
                    status_code=400,
                    detail=f"Prediction run {pred_id} is not completed (status={pred.status})",
                )

    # `name` is a first-class column on optimization_runs; keep it out of
    # input_params (which is reserved for solver/technical inputs).
    input_params = request.model_dump(mode="json", exclude={"name"})

    run = OptimizationRuns(
        user_id=current_user.id,
        bus_model_id=request.bus_model_id,
        mode=request.mode,
        name=request.name,
        input_params=input_params,
        prediction_run_ids=[str(pid) for pid in request.prediction_run_ids] if request.prediction_run_ids else None,
        status="pending",
    )
    db.add(run)
    await db.flush()
    run_id = run.id
    await db.commit()

    from app.services.optimization import run_optimization_background
    background_tasks.add_task(run_optimization_background, run_id)

    return OptimizationSubmitResponse(optimization_run_id=run_id)


@router.get(
    "/optimization-runs/",
    response_model=PaginatedResponse[OptimizationRunListItemRead],
    summary="List optimization runs (paginated)",
    description=(
        "Returns a paginated list of optimization runs owned by the current "
        "user, ordered by ``created_at DESC, id DESC``. The heavy "
        "``input_params`` and ``results`` blobs are omitted; a few summary "
        "fields (``electrification_feasible``, ``solver_status``, "
        "``objective_value``) are extracted from ``results`` for "
        "convenience. Use the detail endpoint to load the full payload."
    ),
)
async def list_optimization_runs(
    pagination: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    base_query = select(OptimizationRuns).where(
        OptimizationRuns.user_id == current_user.id
    )

    total = await db.scalar(
        select(func.count()).select_from(base_query.subquery())
    )

    items_result = await db.execute(
        base_query
        .order_by(
            OptimizationRuns.created_at.desc(),
            OptimizationRuns.id.desc(),
        )
        .offset(pagination.skip)
        .limit(pagination.limit)
    )
    items = [
        _serialize_optimization_run_list_item(r)
        for r in items_result.scalars().all()
    ]
    return build_paginated_response(
        items=items,
        total=int(total or 0),
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/optimization-runs/{run_id}", response_model=OptimizationRunsRead)
async def get_optimization_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    """Get optimization run status and results."""
    run = await db.get(OptimizationRuns, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Optimization run not found")
    return _serialize_optimization_run(run)


# ---------------------------------------------------------------------------
# PVGIS TMY endpoint (authenticated users only)
# ---------------------------------------------------------------------------
@router.get(
    "/pvgis-tmy/",
    response_model=Union[PvgisTmyResponse, PvgisTmyMetadataResponse],
    summary="PVGIS TMY data — download or check availability",
    description=(
        "Ensures TMY data for the requested lat/lon is available in the "
        "database. If it is missing, the data is always fetched from PVGIS "
        "and stored — regardless of the `download` flag.\n\n"
        "The **download** flag controls only what is returned to the client:\n\n"
        "- **download=false** (default): return lightweight metadata "
        "(availability, record count, source).\n"
        "- **download=true**: return the full TMY time-series payload."
    ),
)
async def generate_pvgis_tmy(
    latitude: float,
    longitude: float,
    download: bool = Query(
        False,
        description="If true, return full TMY data to the client. "
                    "If false, only return availability metadata. "
                    "In both cases, missing data is fetched from PVGIS and stored.",
    ),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    import datetime as dt_mod
    from decimal import Decimal
    from app.core.config import get_cached_settings

    try:
        settings = get_cached_settings()
        coerce_year = settings.pvgis_coerce_year

        lat_rounded = round(float(latitude), 2)
        lon_rounded = round(float(longitude), 2)
        lat_dec = Decimal(str(lat_rounded))
        lon_dec = Decimal(str(lon_rounded))

        existing_count = await count_weather_records(db, lat_dec, lon_dec)

        # ---- Always ensure data is in the DB ----
        source = "db"
        if existing_count < 8760:
            import pvlib

            data, metadata = pvlib.iotools.get_pvgis_tmy(
                latitude=lat_rounded,
                longitude=lon_rounded,
                coerce_year=coerce_year,
            )

            base_year = coerce_year
            weather_measurements = []
            for i, (_timestamp, row) in enumerate(data.iterrows()):
                hour_of_year = i
                day_of_year = (hour_of_year // 24) + 1
                hour_of_day = hour_of_year % 24

                dt_val = dt_mod.datetime(base_year, 1, 1) + dt_mod.timedelta(days=day_of_year - 1, hours=hour_of_day)
                dt_utc = dt_val.replace(tzinfo=dt_mod.timezone.utc)

                raw_rh = float(row['relative_humidity']) if pd.notna(row['relative_humidity']) else None
                raw_ws = float(row['wind_speed']) if pd.notna(row['wind_speed']) else None
                raw_wd = float(row['wind_direction']) if pd.notna(row['wind_direction']) else None
                raw_pr = int(row['pressure']) if pd.notna(row['pressure']) else None

                # Sanitize values that are subject to DB CHECK constraints
                clean = sanitize_weather_values(
                    pressure=raw_pr,
                    relative_humidity=raw_rh,
                    wind_direction=raw_wd,
                    wind_speed=raw_ws,
                )

                weather_measurements.append(
                    WeatherMeasurements(
                        time_utc=dt_utc,
                        latitude=lat_dec,
                        longitude=lon_dec,
                        temp_air=float(row['temp_air']) if pd.notna(row['temp_air']) else None,
                        relative_humidity=clean["relative_humidity"],
                        ghi=float(row['ghi']) if pd.notna(row['ghi']) else None,
                        dni=float(row['dni']) if pd.notna(row['dni']) else None,
                        dhi=float(row['dhi']) if pd.notna(row['dhi']) else None,
                        ir_h=float(row['IR(h)']) if pd.notna(row['IR(h)']) else None,
                        wind_speed=clean["wind_speed"],
                        wind_direction=clean["wind_direction"],
                        pressure=clean["pressure"],
                    )
                )

            db.add_all(weather_measurements)
            await db.commit()
            source = "pvgis"
            existing_count = len(weather_measurements)

        # ---- download=false: return metadata only ----
        if not download:
            return PvgisTmyMetadataResponse(
                latitude=lat_rounded,
                longitude=lon_rounded,
                available_in_db=True,
                records_count=existing_count,
                source=source,
            )

        # ---- download=true: return full TMY payload ----
        weather_records = await fetch_weather_records(db, lat_dec, lon_dec)
        data_records = [
            {
                'time_utc': r.time_utc.isoformat() if r.time_utc is not None else None,
                'temp_air': float(r.temp_air) if r.temp_air is not None else None,
                'relative_humidity': float(r.relative_humidity) if r.relative_humidity is not None else None,
                'ghi': float(r.ghi) if r.ghi is not None else None,
                'dni': float(r.dni) if r.dni is not None else None,
                'dhi': float(r.dhi) if r.dhi is not None else None,
                'IR(h)': float(r.ir_h) if r.ir_h is not None else None,
                'wind_speed': float(r.wind_speed) if r.wind_speed is not None else None,
                'wind_direction': float(r.wind_direction) if r.wind_direction is not None else None,
                'pressure': int(r.pressure) if r.pressure is not None else None,
            }
            for r in weather_records
        ]

        metadata_dict = {
            'inputs': {
                'latitude': lat_rounded,
                'longitude': lon_rounded,
                'radiation_database': 'PVGIS-SARAH2',
                'meteo_database': 'ERA5',
                'year_min': coerce_year,
                'year_max': coerce_year,
            },
            'outputs': {
                'tmy_hourly': {
                    'variables': {
                        'time_utc': 'Timestamp in UTC (ISO 8601)',
                        'temp_air': 'Air temperature (°C)',
                        'relative_humidity': 'Relative humidity (%)',
                        'ghi': 'Global horizontal irradiance (W/m²)',
                        'dni': 'Direct normal irradiance (W/m²)',
                        'dhi': 'Diffuse horizontal irradiance (W/m²)',
                        'IR(h)': 'Infrared radiation from sky (W/m²)',
                        'wind_speed': 'Wind speed (m/s)',
                        'wind_direction': 'Wind direction (°)',
                        'pressure': 'Air pressure (Pa)',
                    }
                }
            },
            'months_selected': list(range(1, 13)),
        }

        return PvgisTmyResponse(
            data={"records": data_records},
            metadata=metadata_dict,
            latitude=lat_rounded,
            longitude=lon_rounded,
            coerce_year=coerce_year,
            generated_at=dt_mod.datetime.now(),
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating PVGIS TMY data: {str(e)}")


# ---------------------------------------------------------------------------
# Weather temperature clustering endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/weather-temperature-clusters/",
    response_model=WeatherClusteringResponse,
    summary="Compute K-means clustering on daily average temperature",
    description=(
        "Compute K-means clustering on **daily average** `temp_air` from "
        "`weather_measurements` for the given lat/lon.\n\n"
        "Only measurements whose time-of-day falls within "
        "`[start_time, end_time)` are used. Defaults: k=8, start_time=05:00, "
        "end_time=24:00 (end-of-day inclusive). '24:00' is accepted.\n\n"
        "The result is persisted in `weather_temperature_clusters` (replacing "
        "any previous clustering for the same configuration)."
    ),
)
async def create_weather_temperature_clusters(
    request: WeatherClusteringRequest,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    from decimal import Decimal
    from app.services.weather import _parse_time_str

    try:
        _parse_time_str(request.start_time)
        _parse_time_str(request.end_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    lat_rounded = round(float(request.latitude), 2)
    lon_rounded = round(float(request.longitude), 2)
    lat_dec = Decimal(str(lat_rounded))
    lon_dec = Decimal(str(lon_rounded))

    records = await fetch_weather_records(db, lat_dec, lon_dec)
    if not records:
        raise HTTPException(
            status_code=400,
            detail=f"No weather measurements found for lat={lat_rounded}, lon={lon_rounded}. "
                   "Download TMY data first using GET /pvgis-tmy/?download=true.",
        )

    daily_avgs = compute_daily_avg_temps(records, request.start_time, request.end_time)
    n_days = len(daily_avgs)
    if n_days < request.k:
        raise HTTPException(
            status_code=400,
            detail=f"Not enough daily data points ({n_days}) for k={request.k} clusters. "
                   f"Need at least k={request.k} days with valid temp_air in the time window.",
        )

    clusters = run_kmeans_clustering(daily_avgs, request.k)

    await save_clustering(db, lat_dec, lon_dec, request.k, request.start_time, request.end_time, clusters)

    return WeatherClusteringResponse(
        latitude=lat_rounded,
        longitude=lon_rounded,
        k=request.k,
        start_time=request.start_time,
        end_time=request.end_time,
        n_days_used=n_days,
        clusters=[ClusterItem(**c) for c in clusters],
    )


@router.get(
    "/weather-temperature-clusters/",
    response_model=WeatherClusteringResponse,
    summary="Retrieve saved temperature clustering",
    description=(
        "Return previously computed K-means clustering from "
        "`weather_temperature_clusters` for the exact configuration "
        "(lat, lon, k, start_time, end_time). Returns 404 if no saved "
        "clustering matches."
    ),
)
async def get_weather_temperature_clusters(
    latitude: float,
    longitude: float,
    k: int = Query(8, ge=1, description="Number of clusters"),
    start_time: str = Query("05:00", description="Daily window start (HH:MM)"),
    end_time: str = Query("24:00", description="Daily window end (HH:MM). '24:00' accepted."),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    from decimal import Decimal

    lat_rounded = round(float(latitude), 2)
    lon_rounded = round(float(longitude), 2)
    lat_dec = Decimal(str(lat_rounded))
    lon_dec = Decimal(str(lon_rounded))

    clusters = await get_saved_clustering(db, lat_dec, lon_dec, k, start_time, end_time)
    if clusters is None:
        raise HTTPException(
            status_code=404,
            detail=f"No saved clustering found for lat={lat_rounded}, lon={lon_rounded}, "
                   f"k={k}, start_time={start_time}, end_time={end_time}. "
                   "Run POST /weather-temperature-clusters/ first.",
        )

    return WeatherClusteringResponse(
        latitude=lat_rounded,
        longitude=lon_rounded,
        k=k,
        start_time=start_time,
        end_time=end_time,
        clusters=[ClusterItem(**c) for c in clusters],
    )


# ---------------------------------------------------------------------------
# Trip statistics endpoint
# ---------------------------------------------------------------------------

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

    # Compute combined stats with tolerant error handling
    try:
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
    except Exception as e:
        # Return 200 with error message and empty statistics as per tests expectations
        return CombinedTripStatisticsResponse(
            trip_ids=request.trip_ids,
            statistics={},
            error=str(e)
        )