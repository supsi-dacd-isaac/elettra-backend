from typing import List, Union
from uuid import UUID

import numpy as np
import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_async_session
from app.schemas.database import PredictionRunsRead, TripPredictionsRead, OptimizationRunsRead
from app.schemas.responses import (
    PredictionSubmitResponse,
    OptimizationSubmitResponse,
    OptimizationDeleteResponse,
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
    Users,
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
    get_active_temperature_series,
    apply_hybrid_temperature_series,
)
from app.services.hybrid_temperature import (
    HYBRID_PROVIDER,
    OPENMETEO_MODEL,
    PROCESSING_VERSION,
    HybridTemperatureError,
    canonical_coordinates,
    coordinate_decimals,
    fetch_hybrid_temperature_series,
)
from app.services.elevation_profiles import (
    ElevationProfileFormatError,
    ElevationProfileNotFoundError,
    ElevationProfileNotReadyError,
    ElevationProfileStorageError,
    ensure_shift_profiles_ready,
    load_trip_elevation_dataframe,
)
from app.services.runtime_release import (
    LEGACY_AUXILIARY_ESTIMATOR,
    PredictionStackRelease,
    RuntimeReleaseConfigurationError,
    resolve_prediction_selection,
)
from app.utils.trip_statistics import (
    combine_elevation_profiles,
    combine_trip_schedules,
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


def _assert_yearly_prediction_stack_compatible(
    existing: list,
    selected: PredictionStackRelease,
    *,
    auxiliary_heating_type: str,
) -> None:
    expected = (
        selected.stack.value,
        selected.model_release,
        selected.auxiliary_estimator,
        auxiliary_heating_type,
    )
    for row in existing:
        stack = str(row[0] or "legacy")
        auxiliary = row[2]
        if auxiliary is None and stack == "legacy":
            auxiliary = LEGACY_AUXILIARY_ESTIMATOR
        actual = (stack, row[1], auxiliary, str(row[3] or "default"))
        if actual != expected:
            raise ValueError(
                "a yearly analysis cannot mix prediction stack, model release "
                "or auxiliary estimator/heating type"
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
    try:
        selected_stack = resolve_prediction_selection(
            prediction_stack=request.prediction_stack,
            model_name=request.model_name,
        )
    except RuntimeReleaseConfigurationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    bus_model = await db.get(BusesModels, request.bus_model_id)
    if bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")

    if request.yearly_analysis_id is not None:
        result = await db.execute(
            select(YearlyAnalysis)
            .where(YearlyAnalysis.id == request.yearly_analysis_id)
            .with_for_update()
        )
        ya = result.scalar_one_or_none()
        if ya is None:
            raise HTTPException(status_code=404, detail="Yearly analysis not found")
        result = await db.execute(
            select(
                PredictionRuns.prediction_stack,
                PredictionRuns.model_name,
                PredictionRuns.auxiliary_estimator_release,
                PredictionRuns.auxiliary_heating_type,
            )
            .where(PredictionRuns.yearly_analysis_id == request.yearly_analysis_id)
            .distinct()
        )
        try:
            _assert_yearly_prediction_stack_compatible(
                list(result.all()),
                selected_stack,
                auxiliary_heating_type=request.auxiliary_heating_type,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    for shift_id in request.shift_ids:
        shift = await db.get(Shifts, shift_id)
        if shift is None:
            raise HTTPException(status_code=404, detail=f"Shift {shift_id} not found")

    try:
        await ensure_shift_profiles_ready(db, request.shift_ids)
    except ElevationProfileNotReadyError as exc:
        raise HTTPException(status_code=409, detail=exc.as_detail()) from exc

    run_ids: list[UUID] = []
    for shift_id in request.shift_ids:
        run = PredictionRuns(
            user_id=current_user.id,
            shift_id=shift_id,
            bus_model_id=request.bus_model_id,
            yearly_analysis_id=request.yearly_analysis_id,
            model_name=selected_stack.model_release,
            prediction_stack=selected_stack.stack.value,
            auxiliary_estimator_release=selected_stack.auxiliary_estimator,
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

    try:
        await ensure_shift_profiles_ready(db, request.shift_ids)
    except ElevationProfileNotReadyError as exc:
        raise HTTPException(status_code=409, detail=exc.as_detail()) from exc

    prediction_bus_model_ids: set[UUID] = set()
    prediction_stack_pairs: set[tuple[str, str, str]] = set()
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
            prediction_bus_model_ids.add(pred.bus_model_id)
            try:
                selected = resolve_prediction_selection(
                    prediction_stack=getattr(pred, "prediction_stack", None) or None,
                    model_name=pred.model_name,
                )
            except RuntimeReleaseConfigurationError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            stored_estimator = getattr(pred, "auxiliary_estimator_release", None)
            legacy_unversioned = (
                stored_estimator is None and selected.stack.value == "legacy"
            )
            if (
                not legacy_unversioned
                and stored_estimator != selected.auxiliary_estimator
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Prediction run {pred_id} auxiliary estimator does not "
                        "match its registered prediction stack"
                    ),
                )
            prediction_stack_pairs.add(
                (
                    selected.stack.value,
                    selected.model_release,
                    selected.auxiliary_estimator,
                )
            )

        if request.bus_model_id is not None:
            mismatched_model_ids = prediction_bus_model_ids - {request.bus_model_id}
            if mismatched_model_ids:
                found = ", ".join(str(model_id) for model_id in sorted(mismatched_model_ids, key=str))
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "prediction_run_ids must use the same bus_model_id as the optimization "
                        f"request bus_model_id ({request.bus_model_id}); found {found}"
                    ),
                )

        if len(prediction_stack_pairs) > 1:
            raise HTTPException(
                status_code=400,
                detail=(
                    "prediction_run_ids must all use the same prediction stack, "
                    "model release and auxiliary estimator"
                ),
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


@router.delete("/optimization-runs/{run_id}", response_model=OptimizationDeleteResponse)
async def delete_optimization_run(
    run_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    """Delete an optimization run owned by the current user."""
    result = await db.execute(
        select(OptimizationRuns).where(
            OptimizationRuns.id == run_id,
            OptimizationRuns.user_id == current_user.id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Optimization run not found")

    try:
        await db.execute(
            update(YearlyAnalysis)
            .where(YearlyAnalysis.optimization_run_id == run_id)
            .values(optimization_run_id=None)
        )
        await db.delete(run)
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Failed to delete optimization run",
        ) from exc

    return OptimizationDeleteResponse(deleted=True, id=run_id)


# ---------------------------------------------------------------------------
# PVGIS/Open-Meteo hybrid TMY endpoint (authenticated users only)
# ---------------------------------------------------------------------------
@router.get(
    "/pvgis-tmy/",
    response_model=Union[PvgisTmyResponse, PvgisTmyMetadataResponse],
    summary="PVGIS-selected, Open-Meteo-corrected TMY temperature",
    description=(
        "Ensures a hybrid TMY for the requested lat/lon is available in the "
        "database. PVGIS selects the typical months; Open-Meteo supplies "
        "terrain-corrected temperature. Missing or legacy data is upgraded "
        "regardless of the `download` flag.\n\n"
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
                    "In both cases, missing or legacy data is generated and stored.",
    ),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    import datetime as dt_mod
    from app.core.config import get_cached_settings

    try:
        settings = get_cached_settings()
        coerce_year = settings.pvgis_coerce_year

        canonical_lat, canonical_lon = canonical_coordinates(latitude, longitude)
        lat_dec, lon_dec = coordinate_decimals(canonical_lat, canonical_lon)

        existing_count = await count_weather_records(db, lat_dec, lon_dec)
        temperature_series = await get_active_temperature_series(db, lat_dec, lon_dec)

        source = "db"
        needs_generation = (
            existing_count != 8760
            or temperature_series is None
            or temperature_series.provider != HYBRID_PROVIDER
            or temperature_series.openmeteo_model != OPENMETEO_MODEL
            or temperature_series.processing_version != PROCESSING_VERSION
        )
        if needs_generation:
            hybrid = await fetch_hybrid_temperature_series(
                latitude=canonical_lat,
                longitude=canonical_lon,
                coerce_year=coerce_year,
            )
            temperature_series, applied = await apply_hybrid_temperature_series(
                db, hybrid, resume=True
            )
            source = HYBRID_PROVIDER if applied else "db"
            existing_count = 8760

        openmeteo_elevation = None
        for month_metadata in temperature_series.openmeteo_metadata or []:
            if month_metadata.get("returned_elevation_m") is not None:
                openmeteo_elevation = float(month_metadata["returned_elevation_m"])
                break

        # ---- download=false: return metadata only ----
        if not download:
            return PvgisTmyMetadataResponse(
                latitude=canonical_lat,
                longitude=canonical_lon,
                available_in_db=True,
                records_count=existing_count,
                source=source,
                temperature_provider=temperature_series.provider,
                temperature_model=temperature_series.openmeteo_model,
                temperature_series_id=temperature_series.id,
                processing_version=temperature_series.processing_version,
                requested_latitude=float(temperature_series.requested_latitude),
                requested_longitude=float(temperature_series.requested_longitude),
                openmeteo_elevation_m=openmeteo_elevation,
                pvgis_months_selected=temperature_series.pvgis_months_selected,
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
                'latitude': canonical_lat,
                'longitude': canonical_lon,
                'temperature_provider': temperature_series.provider,
                'temperature_model': temperature_series.openmeteo_model,
                'openmeteo_elevation_m': openmeteo_elevation,
                'processing_version': temperature_series.processing_version,
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
            'months_selected': temperature_series.pvgis_months_selected,
            'field_provenance': {
                'temp_air': 'Open-Meteo Archive temperature_2m at PVGIS-selected source timestamps',
                'relative_humidity': 'PVGIS',
                'ghi': 'PVGIS',
                'dni': 'PVGIS',
                'dhi': 'PVGIS',
                'IR(h)': 'PVGIS',
                'wind_speed': 'PVGIS',
                'wind_direction': 'PVGIS',
                'pressure': 'PVGIS',
            },
        }

        return PvgisTmyResponse(
            data={"records": data_records},
            metadata=metadata_dict,
            latitude=canonical_lat,
            longitude=canonical_lon,
            coerce_year=coerce_year,
            generated_at=dt_mod.datetime.now(dt_mod.timezone.utc),
            source=source,
            temperature_provider=temperature_series.provider,
            temperature_model=temperature_series.openmeteo_model,
            temperature_series_id=temperature_series.id,
            processing_version=temperature_series.processing_version,
        )

    except HybridTemperatureError as exc:
        raise HTTPException(status_code=502, detail=f"Hybrid TMY upstream error: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error generating hybrid TMY data: {exc}") from exc


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
    from app.services.weather import _parse_time_str

    try:
        _parse_time_str(request.start_time)
        _parse_time_str(request.end_time)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    canonical_lat, canonical_lon = canonical_coordinates(
        request.latitude, request.longitude
    )
    lat_dec, lon_dec = coordinate_decimals(canonical_lat, canonical_lon)

    records = await fetch_weather_records(db, lat_dec, lon_dec)
    if not records:
        raise HTTPException(
            status_code=400,
            detail=f"No weather measurements found for lat={canonical_lat}, lon={canonical_lon}. "
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
    temperature_series = await get_active_temperature_series(db, lat_dec, lon_dec)

    await save_clustering(
        db,
        lat_dec,
        lon_dec,
        request.k,
        request.start_time,
        request.end_time,
        clusters,
        temperature_series_id=(temperature_series.id if temperature_series else None),
    )

    return WeatherClusteringResponse(
        latitude=canonical_lat,
        longitude=canonical_lon,
        k=request.k,
        start_time=request.start_time,
        end_time=request.end_time,
        n_days_used=n_days,
        temperature_series_id=(temperature_series.id if temperature_series else None),
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
    canonical_lat, canonical_lon = canonical_coordinates(latitude, longitude)
    lat_dec, lon_dec = coordinate_decimals(canonical_lat, canonical_lon)

    clusters = await get_saved_clustering(db, lat_dec, lon_dec, k, start_time, end_time)
    if clusters is None:
        raise HTTPException(
            status_code=404,
            detail=f"No saved clustering found for lat={canonical_lat}, lon={canonical_lon}, "
                   f"k={k}, start_time={start_time}, end_time={end_time}. "
                   "Run POST /weather-temperature-clusters/ first.",
        )

    temperature_series = await get_active_temperature_series(db, lat_dec, lon_dec)
    return WeatherClusteringResponse(
        latitude=canonical_lat,
        longitude=canonical_lon,
        k=k,
        start_time=start_time,
        end_time=end_time,
        temperature_series_id=(temperature_series.id if temperature_series else None),
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
    if not request.trip_ids:
        return CombinedTripStatisticsResponse(trip_ids=[], statistics={}, error=None)

    # Keep each schedule/profile pair atomic. Independent lists can silently
    # shift when one trip is incomplete and associate a profile with the wrong
    # schedule.
    trip_inputs: list[tuple[pd.DataFrame, pd.DataFrame]] = []

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
        if not rows:
            raise HTTPException(
                status_code=422,
                detail=f"Trip {trip_id} has no stop_times; sequence is incomplete",
            )
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
        schedule_df = pd.DataFrame(trip_schedule_data)

        # 2) Elevation (release-aware for GTFS, job-aware for auxiliary trips)
        trip = await db.get(GtfsTrips, trip_id)
        if trip is None:
            raise HTTPException(status_code=404, detail=f"Trip {trip_id} not found")
        if not trip.shape_id:
            raise HTTPException(status_code=404, detail=f"Trip {trip_id} has no shape_id")
        try:
            elevation_df = await load_trip_elevation_dataframe(db, trip)
        except ElevationProfileNotReadyError as exc:
            raise HTTPException(status_code=409, detail=exc.as_detail()) from exc
        except ElevationProfileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ElevationProfileStorageError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ElevationProfileFormatError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        if elevation_df is None or elevation_df.empty:
            raise HTTPException(
                status_code=404,
                detail=f"Trip {trip_id} has an empty elevation profile",
            )
        trip_inputs.append((schedule_df, elevation_df))

    if len(trip_inputs) != len(request.trip_ids):
        raise HTTPException(
            status_code=500,
            detail="Trip input cardinality changed while preparing statistics",
        )
    schedules = [schedule for schedule, _profile in trip_inputs]
    elevation_dfs = [profile for _schedule, profile in trip_inputs]
    concat_schedule = combine_trip_schedules(schedules)
    combined_elev = combine_elevation_profiles(elevation_dfs)

    # Core errors invalidate the whole sequence; never return an apparently
    # successful 200 response containing partial or empty statistics.
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
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to compute complete trip statistics: {exc}",
        ) from exc
