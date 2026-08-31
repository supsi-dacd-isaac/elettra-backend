from __future__ import annotations

import json
import logging
import math
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Literal, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_async_session
from app.schemas.database import YearlyAnalysisCreate, YearlyAnalysisUpdate, YearlyAnalysisRead
from app.schemas.pagination import (
    PaginatedResponse, PaginationParams, build_paginated_response,
)
from app.schemas.economic import (
    CapexLineItem,
    CostSummary,
    OpexLineItem,
    YearlyCostAssumptions,
    YearlyCostResponse,
    YearlyCostScenario,
)
from app.schemas.lca import (
    YearlyEmissionsAssumptions,
    YearlyEmissionsComparatorIndicator,
    YearlyEmissionsIndicator,
    YearlyEmissionsResponse,
    YearlyEmissionsScenario,
    IndicatorSummary,
    MixedCaseDecomposition,
    MixedCaseIndicator,
    LifecycleBreakdown,
    LifecycleEbus,
    LifecycleDieselComparator,
    LifecyclePhases,
    PrimaryEnergyBreakdown,
    PrimaryEnergySide,
    SavingsBlock,
    SavingsItem,
)
from app.schemas.responses import (
    YearlyEnergySummaryResponse,
    YearlyAnalysisListItemRead,
)
from app.models import (
    BusesLcaData, BusesModels,
    YearlyAnalysis, OptimizationRuns, PredictionRuns, Users,
)
from app.core.auth import get_current_user
from app.core.config import get_cached_settings
from app.services.yearly_weather_recalculation import (
    AnalysisWeatherResolutionError,
    DEFAULT_CLUSTER_END,
    DEFAULT_CLUSTER_START,
    binding_for_series_id,
    resolve_analysis_weather_binding,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Economic-defaults loader (same config file used by the economic router)
# ---------------------------------------------------------------------------

_ECON_CONFIG = Path(__file__).resolve().parents[2] / "config" / "economic_defaults.json"
_EMISSION_CONFIG = Path(__file__).resolve().parents[2] / "config" / "emission_defaults.json"


@lru_cache(maxsize=1)
def _load_econ_defaults() -> dict:
    with open(_ECON_CONFIG) as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def _load_emission_defaults() -> dict:
    with open(_EMISSION_CONFIG) as fh:
        return json.load(fh)


def _econ(key: str):
    return _load_econ_defaults()[key]


def _econ_or(value, key: str):
    return value if value is not None else _econ(key)


def _econ_or_int(value, key: str) -> int:
    return value if value is not None else int(_econ(key))


# ---------------------------------------------------------------------------
# Pure economic calculation helpers (mirror economic.py, kept local to avoid
# cross-router imports)
# ---------------------------------------------------------------------------

def _maint_per_km(length_m: float, a: float, b: float) -> float:
    return a * length_m + b


def _consumption_l_per_km(length_m: float, a: float, b: float) -> float:
    return a * length_m + b


def _capital_recovery_factor(interest_rate: float, lifetime: int) -> float:
    if interest_rate == 0.0:
        return 1.0 / lifetime
    q = 1.0 + interest_rate
    try:
        q_t = q ** lifetime
    except OverflowError:
        return interest_rate
    if math.isinf(q_t):
        return interest_rate
    return (q_t * interest_rate) / (q_t - 1.0)


def _annualize(investment: float, lifetime: int, interest_rate: float) -> float:
    return investment * _capital_recovery_factor(interest_rate, lifetime)


def _require_number(value, field_name: str, *, positive: bool = False) -> float:
    """Validate numeric optimization-result fields from free-form JSONB."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(
            status_code=422,
            detail=f"Linked optimization run results missing numeric field: {field_name}.",
        )
    number = float(value)
    if positive and number <= 0:
        raise HTTPException(
            status_code=422,
            detail=f"Linked optimization run results field {field_name} must be > 0.",
        )
    if not positive and number < 0:
        raise HTTPException(
            status_code=422,
            detail=f"Linked optimization run results field {field_name} must be >= 0.",
        )
    return number


def _extract_optimization_capex_inputs(results: dict) -> dict[str, float]:
    """Extract full battery capacity and charger investment from optimizer results.

    The optimizer stores results in versionless JSONB, so this helper is strict:
    it prefers per-bus rows and fails rather than undercounting collapsed
    shift-level ``battery_results``.
    """
    if not isinstance(results, dict):
        raise HTTPException(
            status_code=409,
            detail="Linked optimization run has no usable results.",
        )

    per_bus_summary = results.get("per_bus_summary")
    battery_results = results.get("battery_results")
    if not isinstance(per_bus_summary, list) or not per_bus_summary:
        raise HTTPException(
            status_code=422,
            detail=(
                "Linked optimization run results do not include per-bus summary; "
                "cannot safely derive fleet battery CAPEX."
            ),
        )
    if not isinstance(battery_results, dict) or not battery_results:
        raise HTTPException(
            status_code=422,
            detail="Linked optimization run results do not include battery_results.",
        )

    optimized_battery_capacity_kwh = 0.0
    for idx, bus_summary in enumerate(per_bus_summary):
        if not isinstance(bus_summary, dict):
            raise HTTPException(
                status_code=422,
                detail=f"Linked optimization run per_bus_summary[{idx}] is invalid.",
            )
        shift_id = bus_summary.get("shift_id")
        if not shift_id:
            raise HTTPException(
                status_code=422,
                detail=f"Linked optimization run per_bus_summary[{idx}] missing shift_id.",
            )
        battery_row = battery_results.get(str(shift_id))
        if not isinstance(battery_row, dict):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Linked optimization run battery_results missing entry for "
                    f"per-bus shift_id {shift_id}."
                ),
            )
        optimized_battery_capacity_kwh += _require_number(
            battery_row.get("optimized_kwh"),
            f"battery_results[{shift_id}].optimized_kwh",
            positive=True,
        )

    installed_chargers = results.get("installed_chargers")
    total_installation_raw = results.get("total_installation_cost_chf")
    has_total_installation = total_installation_raw is not None
    if has_total_installation:
        installation_cost_chf = _require_number(
            total_installation_raw,
            "total_installation_cost_chf",
        )
    else:
        installation_cost_chf = 0.0

    if installed_chargers is not None:
        if not isinstance(installed_chargers, dict):
            raise HTTPException(
                status_code=422,
                detail="Linked optimization run installed_chargers field is invalid.",
            )
        summed_installation_cost = 0.0
        for stop_id, charger_row in installed_chargers.items():
            if not isinstance(charger_row, dict):
                raise HTTPException(
                    status_code=422,
                    detail=f"Linked optimization run installed_chargers[{stop_id}] is invalid.",
                )
            summed_installation_cost += _require_number(
                charger_row.get("cost_chf"),
                f"installed_chargers[{stop_id}].cost_chf",
            )
        if has_total_installation:
            if abs(summed_installation_cost - installation_cost_chf) > 0.01:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "Linked optimization run charger CAPEX fields are inconsistent: "
                        "total_installation_cost_chf does not match installed_chargers cost sum."
                    ),
                )
        else:
            installation_cost_chf = summed_installation_cost
    elif not has_total_installation:
        raise HTTPException(
            status_code=422,
            detail=(
                "Linked optimization run results missing total_installation_cost_chf "
                "and installed_chargers."
            ),
        )

    return {
        "optimized_battery_capacity_kwh": optimized_battery_capacity_kwh,
        "installation_cost_chf": installation_cost_chf,
    }


async def _load_optimization_run_for_capex(
    db: AsyncSession,
    yearly_analysis_id: UUID,
    current_user: Users,
) -> OptimizationRuns:
    ya = await db.get(YearlyAnalysis, yearly_analysis_id)
    if ya is None:
        raise HTTPException(status_code=404, detail="Yearly analysis not found")
    if ya.optimization_run_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "capex_source=optimization requires yearly_analysis.optimization_run_id "
                "to be set."
            ),
        )

    opt_run = await db.get(OptimizationRuns, ya.optimization_run_id)
    if opt_run is None:
        raise HTTPException(
            status_code=409,
            detail="Linked optimization run not found.",
        )
    if str(opt_run.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=403,
            detail="Linked optimization run is not accessible to the current user.",
        )
    if opt_run.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Linked optimization run must be completed (status={opt_run.status}).",
        )
    if not isinstance(opt_run.results, dict):
        raise HTTPException(
            status_code=409,
            detail="Linked optimization run has no results.",
        )

    solver_status = opt_run.results.get("solver_status")
    if solver_status != "optimal":
        raise HTTPException(
            status_code=409,
            detail=(
                "Linked optimization run solver_status must be optimal "
                f"(solver_status={solver_status})."
            ),
        )

    electrification_feasible = opt_run.results.get("electrification_feasible")
    if electrification_feasible is not None and electrification_feasible is not True:
        raise HTTPException(
            status_code=409,
            detail="Linked optimization run is not electrification-feasible.",
        )

    return opt_run


# ---------------------------------------------------------------------------
# LCA API helpers (shared approach with environmental.py)
# ---------------------------------------------------------------------------

_LCA_TIMEOUT = 30.0
_LCA_PHASES = (
    "direct", "directNonExhaust", "energyChain",
    "maintenance", "vehicle", "endOfLife", "infrastructure",
)

_SIZE_PREFIXES = ("9m", "13m", "18m")


def _lca_base_url() -> str:
    return get_cached_settings().lca_api_base_url.rstrip("/")


async def _lca_get_impact(vehicle_id: str) -> Optional[dict]:
    """Call the external LCA API for per-pkm impact data.

    Returns the JSON response dict on success, or None on failure
    (so the endpoint can degrade gracefully).
    """
    url = f"{_lca_base_url()}/vehicle/{vehicle_id}/impact"
    try:
        async with httpx.AsyncClient(timeout=_LCA_TIMEOUT) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("LCA API call failed for vehicle %s: %s", vehicle_id, exc)
        return None


def _extract_size_prefix(model_size: str | None) -> str | None:
    if not model_size or model_size in ("None", "N/A"):
        return None
    for prefix in _SIZE_PREFIXES:
        if model_size.startswith(prefix):
            return prefix
    return None


def _bus_length_to_size_prefix(bus_length_m: float | int | None) -> str | None:
    """Map a numeric bus length to the closest standard LCA size prefix."""
    if bus_length_m is None:
        return None
    length = float(bus_length_m)
    if length <= 10:
        return "9m"
    elif length <= 14:
        return "13m"
    else:
        return "18m"


def _resolve_bus_size_class(bus_length_m: float | int | None) -> str | None:
    """Map a numeric bus length to the ELETTRA size class (9m, 12m, 18m)."""
    if bus_length_m is None:
        return None
    length = float(bus_length_m)
    if length <= 10:
        return "9m"
    elif length <= 14:
        return "12m"
    else:
        return "18m"


def _get_configured_diesel_lca_vehicle(size_class: str) -> Optional[dict]:
    """Get the canonical diesel comparator LCA vehicle config for a size class."""
    defaults = _load_emission_defaults()
    mapping = defaults.get("diesel_comparator_lca_vehicles", {})
    return mapping.get(size_class)


async def _resolve_lca_vehicle(
    db: AsyncSession, yearly_analysis_id: UUID, powertrain_filter: Optional[str] = None,
) -> Optional[BusesLcaData]:
    """Resolve the LCA vehicle for a yearly analysis.

    Chain: yearly_analysis → prediction_runs → bus_model → specs.size (or
    specs.bus_length_m as fallback) → BusesLcaData
    """
    pr_stmt = (
        select(PredictionRuns.bus_model_id)
        .where(PredictionRuns.yearly_analysis_id == yearly_analysis_id)
        .limit(1)
    )
    pr_result = await db.execute(pr_stmt)
    bus_model_id = pr_result.scalar_one_or_none()
    if bus_model_id is None:
        logger.debug("LCA resolve: no prediction_runs for yearly_analysis %s", yearly_analysis_id)
        return None

    bus_model = await db.get(BusesModels, bus_model_id)
    if bus_model is None:
        logger.debug("LCA resolve: bus_model %s not found", bus_model_id)
        return None

    specs = bus_model.specs or {}
    model_size = specs.get("size")
    size_prefix = _extract_size_prefix(str(model_size) if model_size is not None else None)

    if size_prefix is None:
        size_prefix = _bus_length_to_size_prefix(specs.get("bus_length_m"))
        if size_prefix is not None:
            logger.debug(
                "LCA resolve: specs.size not set for bus_model '%s', "
                "falling back to specs.bus_length_m=%s → prefix '%s'",
                bus_model.name, specs.get("bus_length_m"), size_prefix,
            )

    if size_prefix is None:
        logger.warning(
            "LCA resolve: cannot determine size prefix for bus_model '%s' "
            "(specs.size=%r, specs.bus_length_m=%r)",
            bus_model.name, model_size, specs.get("bus_length_m"),
        )
        return None

    lca_stmt = select(BusesLcaData).where(
        BusesLcaData.active.is_(True),
        BusesLcaData.size.startswith(size_prefix),
    )
    if powertrain_filter is not None:
        lca_stmt = lca_stmt.where(BusesLcaData.powertrain == powertrain_filter)

    result = await db.execute(lca_stmt)
    lca_vehicle = result.scalars().first()

    if lca_vehicle is None:
        logger.info(
            "LCA resolve: no active BusesLcaData for size_prefix='%s', powertrain=%r",
            size_prefix, powertrain_filter,
        )
    else:
        logger.debug(
            "LCA resolve: matched LCA vehicle '%s' (id=%s, passengers=%s)",
            lca_vehicle.name, lca_vehicle.id, lca_vehicle.passenger_capacity,
        )

    return lca_vehicle


def _scale_indicator_phases(
    breakdown: dict, factor: float,
) -> Dict[str, Optional[float]]:
    """Scale LCA phase values by a multiplication factor (absolute pkm scaling).

    Used by the environmental/shift endpoint.  NOT used by yearly-analysis
    emissions (which uses _allocate_phases_by_share instead).
    """
    scaled: Dict[str, Optional[float]] = {}
    total = 0.0
    for phase in _LCA_PHASES:
        raw = breakdown.get(phase)
        if raw is not None:
            val = float(raw) * factor
            scaled[phase] = round(val, 4)
            total += val
        else:
            scaled[phase] = None
    scaled["total"] = round(total, 4)
    return scaled


def _allocate_phases_by_share(
    breakdown: dict, operational_total: float,
) -> Dict[str, Optional[float]]:
    """Allocate an operational total across LCA phases using Mobitool shares.

    Instead of absolute pkm scaling, this uses the external LCA API phase
    values only as relative proportions, then distributes the given
    operational_total (e.g. electric-side CO₂) across those phases.

    Returns a dict with phase keys (float or None) and "total" (always equal
    to operational_total when phases are available).
    """
    raw_values: Dict[str, Optional[float]] = {}
    raw_sum = 0.0
    has_any_value = False

    for phase in _LCA_PHASES:
        raw = breakdown.get(phase)
        if raw is not None:
            raw_values[phase] = float(raw)
            raw_sum += float(raw)
            has_any_value = True
        else:
            raw_values[phase] = None

    if not has_any_value or raw_sum == 0.0:
        result: Dict[str, Optional[float]] = {p: None for p in _LCA_PHASES}
        result["total"] = round(operational_total, 4)
        return result

    allocated: Dict[str, Optional[float]] = {}
    allocated_sum = 0.0
    for phase in _LCA_PHASES:
        raw = raw_values[phase]
        if raw is not None:
            share = raw / raw_sum
            val = share * operational_total
            allocated[phase] = round(val, 4)
            allocated_sum += val
        else:
            allocated[phase] = None

    allocated["total"] = round(allocated_sum, 4)
    return allocated

router = APIRouter()


@router.post("/", response_model=YearlyAnalysisRead)
async def create_yearly_analysis(
    payload: YearlyAnalysisCreate,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    """Create a new yearly analysis, optionally linked to an optimization run."""
    if payload.optimization_run_id is not None:
        opt_run = await db.get(OptimizationRuns, payload.optimization_run_id)
        if opt_run is None:
            raise HTTPException(status_code=404, detail="Optimization run not found")

    obj = YearlyAnalysis(**payload.model_dump(exclude_unset=True))
    db.add(obj)
    try:
        await db.flush()
        scenarios = (obj.features or {}).get("scenarios") or []
        if scenarios:
            k = obj.weather_cluster_k or len(scenarios)
            start_time = obj.weather_cluster_start_time or DEFAULT_CLUSTER_START
            end_time = obj.weather_cluster_end_time or DEFAULT_CLUSTER_END
            if obj.weather_temperature_series_id is not None:
                binding = await binding_for_series_id(
                    db,
                    obj.weather_temperature_series_id,
                    k=k,
                    start_time=start_time,
                    end_time=end_time,
                )
            else:
                binding = await resolve_analysis_weather_binding(
                    db,
                    obj,
                    owner_id=current_user.id,
                    cluster_k=k,
                    cluster_start_time=start_time,
                    cluster_end_time=end_time,
                )
            obj.weather_temperature_series_id = binding.series.id
            obj.weather_cluster_k = binding.k
            obj.weather_cluster_start_time = binding.start_time
            obj.weather_cluster_end_time = binding.end_time
        await db.commit()
        await db.refresh(obj)
        return obj
    except AnalysisWeatherResolutionError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/",
    response_model=PaginatedResponse[YearlyAnalysisListItemRead],
    summary="List yearly analyses (paginated)",
    description=(
        "Returns a paginated list of yearly analyses owned by the current "
        "user, ordered by ``created_at DESC, id DESC``. The heavy "
        "``features`` blob is omitted; use the detail endpoint to load it."
    ),
)
async def list_yearly_analyses(
    pagination: PaginationParams = Depends(),
    optimization_run_id: Optional[UUID] = Query(None, description="Filter by optimization run"),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    base_query = (
        select(YearlyAnalysis)
        .join(OptimizationRuns, YearlyAnalysis.optimization_run_id == OptimizationRuns.id)
        .where(OptimizationRuns.user_id == current_user.id)
    )
    if optimization_run_id is not None:
        base_query = base_query.where(YearlyAnalysis.optimization_run_id == optimization_run_id)

    total = await db.scalar(
        select(func.count()).select_from(base_query.subquery())
    )

    items_result = await db.execute(
        base_query
        .order_by(
            YearlyAnalysis.created_at.desc(),
            YearlyAnalysis.id.desc(),
        )
        .offset(pagination.skip)
        .limit(pagination.limit)
    )
    items = [
        YearlyAnalysisListItemRead.model_validate(ya)
        for ya in items_result.scalars().all()
    ]
    return build_paginated_response(
        items=items,
        total=int(total or 0),
        skip=pagination.skip,
        limit=pagination.limit,
    )


@router.get("/{yearly_analysis_id}", response_model=YearlyAnalysisRead)
async def get_yearly_analysis(
    yearly_analysis_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    """Get a yearly analysis by ID."""
    obj = await db.get(YearlyAnalysis, yearly_analysis_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Yearly analysis not found")
    return obj


@router.patch("/{yearly_analysis_id}", response_model=YearlyAnalysisRead)
async def update_yearly_analysis(
    yearly_analysis_id: UUID,
    payload: YearlyAnalysisUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    """Partially update a yearly analysis."""
    obj = await db.get(YearlyAnalysis, yearly_analysis_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Yearly analysis not found")

    update_data = payload.model_dump(exclude_unset=True)

    if "optimization_run_id" in update_data and update_data["optimization_run_id"] is not None:
        opt_run = await db.get(OptimizationRuns, update_data["optimization_run_id"])
        if opt_run is None:
            raise HTTPException(status_code=404, detail="Optimization run not found")

    for field, value in update_data.items():
        setattr(obj, field, value)

    try:
        scenarios = (obj.features or {}).get("scenarios") or []
        weather_fields = {
            "weather_temperature_series_id",
            "weather_cluster_k",
            "weather_cluster_start_time",
            "weather_cluster_end_time",
            "features",
        }
        if scenarios and (
            obj.weather_temperature_series_id is None
            or bool(weather_fields.intersection(update_data))
        ):
            k = obj.weather_cluster_k or len(scenarios)
            start_time = obj.weather_cluster_start_time or DEFAULT_CLUSTER_START
            end_time = obj.weather_cluster_end_time or DEFAULT_CLUSTER_END
            if obj.weather_temperature_series_id is not None:
                binding = await binding_for_series_id(
                    db,
                    obj.weather_temperature_series_id,
                    k=k,
                    start_time=start_time,
                    end_time=end_time,
                )
            else:
                binding = await resolve_analysis_weather_binding(
                    db,
                    obj,
                    owner_id=current_user.id,
                    cluster_k=k,
                    cluster_start_time=start_time,
                    cluster_end_time=end_time,
                )
            obj.weather_temperature_series_id = binding.series.id
            obj.weather_cluster_k = binding.k
            obj.weather_cluster_start_time = binding.start_time
            obj.weather_cluster_end_time = binding.end_time
        await db.commit()
        await db.refresh(obj)
        return obj
    except AnalysisWeatherResolutionError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{yearly_analysis_id}")
async def delete_yearly_analysis(
    yearly_analysis_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    """Delete a yearly analysis."""
    obj = await db.get(YearlyAnalysis, yearly_analysis_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Yearly analysis not found")
    await db.delete(obj)
    await db.commit()
    return {"message": "Yearly analysis deleted successfully"}


# ---------------------------------------------------------------------------
# Yearly energy summary (aggregates prediction-run data)
# ---------------------------------------------------------------------------

async def _build_energy_summary(
    ya: YearlyAnalysis,
    pred_runs: list,
) -> dict:
    """Pure aggregation logic shared by GET and POST endpoints.

    Returns a dict that is directly serialisable to
    ``YearlyEnergySummaryResponse``.
    """
    features = ya.features or {}
    scenarios_raw = features.get("scenarios", [])
    config = features.get("config", {})
    global_aux_type = config.get("auxiliary_heating_type", "default")

    occ_by_temp: dict[float, int] = {}
    for sc in scenarios_raw:
        t = sc.get("temperature")
        o = sc.get("occurrences", 0)
        if t is not None:
            occ_by_temp[round(float(t), 2)] = int(o)

    scenario_summaries: list[dict] = []
    yearly_electric_kwh = 0.0
    yearly_distance_km = 0.0
    yearly_auxiliary_kwh = 0.0
    yearly_drivetrain_kwh = 0.0
    yearly_diesel_fuel_kwh = 0.0
    yearly_diesel_liters = 0.0
    has_any_diesel = False

    for pr in pred_runs:
        summary = pr.summary or {}
        temp_c = float(pr.external_temp_celsius)
        occurrences = occ_by_temp.get(round(temp_c, 2), 0)

        daily_electric = float(summary.get("total_consumption_kwh", 0))
        daily_distance = float(summary.get("total_distance_km", 0))
        daily_aux = float(summary.get("total_auxiliary_kwh", 0))
        daily_dt = float(summary.get("total_drivetrain_kwh", 0))

        dh = summary.get("diesel_heating")
        daily_diesel_fuel = 0.0
        daily_diesel_liters = 0.0
        scenario_dh = None
        if dh:
            has_any_diesel = True
            daily_diesel_fuel = float(dh.get("diesel_fuel_kwh", 0))
            daily_diesel_liters = float(dh.get("diesel_liters", 0))
            scenario_dh = {
                "diesel_fuel_kwh": daily_diesel_fuel,
                "diesel_liters": daily_diesel_liters,
                "diesel_heater_efficiency": float(dh.get("diesel_heater_efficiency", 0)),
            }

        annual_electric = daily_electric * occurrences
        annual_distance = daily_distance * occurrences
        annual_aux = daily_aux * occurrences
        annual_dt = daily_dt * occurrences
        annual_diesel_fuel = daily_diesel_fuel * occurrences
        annual_diesel_liters = daily_diesel_liters * occurrences

        scenario_summaries.append({
            "prediction_run_id": pr.id,
            "temperature_celsius": temp_c,
            "occurrences": occurrences,
            "auxiliary_heating_type": pr.auxiliary_heating_type,
            "daily_electric_kwh": round(daily_electric, 4),
            "daily_distance_km": round(daily_distance, 4),
            "daily_auxiliary_kwh": round(daily_aux, 4),
            "daily_drivetrain_kwh": round(daily_dt, 4),
            "diesel_heating": scenario_dh,
            "annual_electric_kwh": round(annual_electric, 4),
            "annual_distance_km": round(annual_distance, 4),
            "annual_auxiliary_kwh": round(annual_aux, 4),
            "annual_drivetrain_kwh": round(annual_dt, 4),
            "annual_diesel_fuel_kwh": round(annual_diesel_fuel, 4),
            "annual_diesel_liters": round(annual_diesel_liters, 4),
        })

        yearly_electric_kwh += annual_electric
        yearly_distance_km += annual_distance
        yearly_auxiliary_kwh += annual_aux
        yearly_drivetrain_kwh += annual_dt
        yearly_diesel_fuel_kwh += annual_diesel_fuel
        yearly_diesel_liters += annual_diesel_liters

    yearly_totals: dict = {
        "distance_km": round(yearly_distance_km, 4),
        "electric_kwh": round(yearly_electric_kwh, 4),
        "auxiliary_kwh": round(yearly_auxiliary_kwh, 4),
        "drivetrain_kwh": round(yearly_drivetrain_kwh, 4),
    }
    if has_any_diesel:
        yearly_totals["diesel_fuel_kwh"] = round(yearly_diesel_fuel_kwh, 4)
        yearly_totals["diesel_liters"] = round(yearly_diesel_liters, 4)
        yearly_totals["combined_final_energy_kwh"] = round(
            yearly_electric_kwh + yearly_diesel_fuel_kwh, 4
        )

    yearly_diesel_heating = None
    if has_any_diesel:
        yearly_diesel_heating = {
            "diesel_fuel_kwh": round(yearly_diesel_fuel_kwh, 4),
            "diesel_liters": round(yearly_diesel_liters, 4),
        }

    return {
        "yearly_analysis_id": ya.id,
        "auxiliary_heating_type": global_aux_type,
        "scenarios": scenario_summaries,
        "yearly_totals": yearly_totals,
        "yearly_diesel_heating": yearly_diesel_heating,
    }


async def _load_prediction_runs(
    db: AsyncSession,
    yearly_analysis_id: UUID,
) -> list:
    result = await db.execute(
        select(PredictionRuns)
        .where(PredictionRuns.yearly_analysis_id == yearly_analysis_id)
        .order_by(PredictionRuns.external_temp_celsius)
    )
    return list(result.scalars().all())


async def get_fresh_energy_summary(
    db: AsyncSession,
    yearly_analysis_id: UUID,
) -> dict:
    """Return a freshly-computed energy summary — never reads the cached blob.

    This is the recommended entry point for any downstream module (costs,
    emissions, reports) that needs yearly energy aggregates.  It always
    re-aggregates from the current prediction runs so that consumers never
    operate on stale data.

    Raises ``HTTPException(404)`` if the yearly analysis or its prediction
    runs cannot be found.
    """
    ya = await db.get(YearlyAnalysis, yearly_analysis_id)
    if ya is None:
        raise HTTPException(status_code=404, detail="Yearly analysis not found")

    pred_runs = await _load_prediction_runs(db, yearly_analysis_id)
    if not pred_runs:
        raise HTTPException(
            status_code=404,
            detail="No prediction runs found for this yearly analysis.",
        )

    return await _build_energy_summary(ya, pred_runs)


@router.get(
    "/{yearly_analysis_id}/energy-summary",
    response_model=YearlyEnergySummaryResponse,
    summary="Aggregated yearly energy summary from prediction runs",
    description=(
        "Reads all prediction runs linked to a yearly analysis, pairs them "
        "with the scenario definitions stored in ``features.scenarios``, and "
        "returns per-scenario daily/annual energy quantities plus yearly "
        "totals. For diesel-heating runs the diesel quantities are included. "
        "This endpoint is read-only; use POST to compute and persist."
    ),
)
async def get_yearly_energy_summary(
    yearly_analysis_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    ya = await db.get(YearlyAnalysis, yearly_analysis_id)
    if ya is None:
        raise HTTPException(status_code=404, detail="Yearly analysis not found")

    pred_runs = await _load_prediction_runs(db, yearly_analysis_id)
    if not pred_runs:
        raise HTTPException(
            status_code=404,
            detail="No prediction runs found for this yearly analysis.",
        )

    return await _build_energy_summary(ya, pred_runs)


@router.post(
    "/{yearly_analysis_id}/energy-summary",
    response_model=YearlyEnergySummaryResponse,
    summary="Compute and persist yearly energy summary",
    description=(
        "Same aggregation as the GET variant, but additionally persists the "
        "result into ``features.energy_summary`` on the yearly analysis row. "
        "Later cost and emission modules can read these stored values "
        "directly without re-aggregation."
    ),
)
async def compute_and_store_yearly_energy_summary(
    yearly_analysis_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    ya = await db.get(YearlyAnalysis, yearly_analysis_id)
    if ya is None:
        raise HTTPException(status_code=404, detail="Yearly analysis not found")

    pred_runs = await _load_prediction_runs(db, yearly_analysis_id)
    if not pred_runs:
        raise HTTPException(
            status_code=404,
            detail="No prediction runs found for this yearly analysis.",
        )

    summary_data = await _build_energy_summary(ya, pred_runs)

    persistable = {
        "auxiliary_heating_type": summary_data["auxiliary_heating_type"],
        "yearly_totals": summary_data["yearly_totals"],
        "yearly_diesel_heating": summary_data["yearly_diesel_heating"],
        "scenarios": [
            {
                "prediction_run_id": str(s["prediction_run_id"]),
                "temperature_celsius": s["temperature_celsius"],
                "occurrences": s["occurrences"],
                "auxiliary_heating_type": s["auxiliary_heating_type"],
                "daily_electric_kwh": s["daily_electric_kwh"],
                "daily_distance_km": s["daily_distance_km"],
                "daily_auxiliary_kwh": s["daily_auxiliary_kwh"],
                "daily_drivetrain_kwh": s["daily_drivetrain_kwh"],
                "diesel_heating": s["diesel_heating"],
                "annual_electric_kwh": s["annual_electric_kwh"],
                "annual_distance_km": s["annual_distance_km"],
                "annual_auxiliary_kwh": s["annual_auxiliary_kwh"],
                "annual_drivetrain_kwh": s["annual_drivetrain_kwh"],
                "annual_diesel_fuel_kwh": s["annual_diesel_fuel_kwh"],
                "annual_diesel_liters": s["annual_diesel_liters"],
            }
            for s in summary_data["scenarios"]
        ],
    }

    updated_features = dict(ya.features or {})
    updated_features["energy_summary"] = persistable
    ya.features = updated_features
    await db.commit()
    await db.refresh(ya)

    logger.info(
        "Persisted energy_summary for yearly_analysis %s (diesel=%s)",
        yearly_analysis_id,
        summary_data["yearly_diesel_heating"] is not None,
    )

    return summary_data


# ---------------------------------------------------------------------------
# Yearly cost breakdown (mixed e-bus vs full-diesel comparator)
# ---------------------------------------------------------------------------

@router.get(
    "/{yearly_analysis_id}/costs",
    response_model=YearlyCostResponse,
    summary="Yearly cost breakdown — mixed e-bus vs full-diesel comparator",
    description=(
        "Computes annual OPEX (and optionally annualised CAPEX) for the "
        "yearly analysis using freshly-aggregated energy data from its "
        "prediction runs.  For ``auxiliary_heating_type = 'diesel'`` the "
        "e-bus branch includes diesel-heating fuel and maintenance OPEX "
        "derived from the real mixed-case technical outputs, **not** from "
        "legacy diesel-bus regressions.  The ``diesel_comparator`` branch "
        "uses the standard full-diesel-bus cost model."
    ),
)
async def get_yearly_costs(
    yearly_analysis_id: UUID,
    # --- Physical inputs ---
    bus_length_m: float = Query(
        ..., gt=0, description="Bus length [m] (required)."
    ),
    # --- CAPEX toggle ---
    include_capex: bool = Query(
        False,
        description=(
            "When true, the response includes CAPEX line items and "
            "annualised CAPEX. With capex_source=manual (default), requires "
            "battery_capacity_kwh and charger_power_kw. With "
            "capex_source=optimization, derives CAPEX from the linked completed "
            "feasible optimization run while OPEX remains based on the yearly "
            "energy summary."
        ),
    ),
    capex_source: Literal["manual", "optimization"] = Query(
        "manual",
        description=(
            "CAPEX source when include_capex=true. 'manual' preserves existing "
            "behavior and uses battery_capacity_kwh/charger_power_kw query "
            "inputs. 'optimization' derives battery and charging-infrastructure "
            "CAPEX from yearly_analysis.optimization_run_id and its completed "
            "feasible optimization results. Ignored when include_capex=false."
        ),
    ),
    # --- Optional economic parameters (fall back to config defaults) ---
    energy_price_per_kwh: Optional[float] = Query(
        None, gt=0, description="Electricity price [CHF/kWh]."
    ),
    fuel_cost_per_l: Optional[float] = Query(
        None, gt=0, description="Diesel fuel price [CHF/l]."
    ),
    interest_rate: Optional[float] = Query(
        None, ge=0, le=1.0, description="Discount / interest rate (0–1)."
    ),
    diesel_heating_maintenance_factor: Optional[float] = Query(
        None, ge=0, le=1.0,
        description=(
            "Fraction of electric maintenance OPEX applied as "
            "diesel-heating maintenance surcharge (default 0.10 = 10 %%)."
        ),
    ),
    # --- Electric maintenance model (a·m + b) [CHF/km] ---
    electric_maint_cost_per_m: Optional[float] = Query(
        None, description="Electric maintenance slope [CHF/km per m]."
    ),
    electric_maint_cost_const: Optional[float] = Query(
        None, description="Electric maintenance intercept [CHF/km]."
    ),
    # --- Diesel comparator maintenance model (a·m + b) [CHF/km] ---
    diesel_maint_cost_per_m: Optional[float] = Query(
        None, description="Diesel maintenance slope [CHF/km per m]."
    ),
    diesel_maint_cost_const: Optional[float] = Query(
        None, description="Diesel maintenance intercept [CHF/km]."
    ),
    # --- Diesel comparator consumption model (a·m + b) [l/km] ---
    diesel_consumption_per_m: Optional[float] = Query(
        None, description="Diesel consumption slope [l/km per m]."
    ),
    diesel_consumption_const: Optional[float] = Query(
        None, description="Diesel consumption intercept [l/km]."
    ),
    # --- CAPEX inputs (required when include_capex=true) ---
    battery_capacity_kwh: Optional[float] = Query(
        None, gt=0,
        description="Battery capacity [kWh]. Required when include_capex=true.",
    ),
    charger_power_kw: Optional[float] = Query(
        None, gt=0,
        description="Charger rated power [kW]. Required when include_capex=true.",
    ),
    # --- Optional CAPEX coefficients ---
    battery_cost_per_kwh: Optional[float] = Query(
        None, gt=0, description="Battery unit cost [CHF/kWh]."
    ),
    bus_no_batt_quad_coeff: Optional[float] = Query(
        None, description="Electric bus body quadratic coeff (a)."
    ),
    bus_no_batt_lin_coeff: Optional[float] = Query(
        None, description="Electric bus body linear coeff (b)."
    ),
    bus_no_batt_const: Optional[float] = Query(
        None, description="Electric bus body constant (c) [CHF]."
    ),
    charger_cost_per_kw: Optional[float] = Query(
        None, description="Charger cost slope [CHF/kW]."
    ),
    charger_cost_const: Optional[float] = Query(
        None, description="Charger cost intercept [CHF]."
    ),
    grid_connection_fee_per_kw: Optional[float] = Query(
        None, description="Grid connection fee slope [CHF/kW]."
    ),
    grid_connection_fee_const: Optional[float] = Query(
        None, description="Grid connection fee intercept [CHF]."
    ),
    diesel_bus_quad_coeff: Optional[float] = Query(
        None, description="Diesel bus quadratic coeff (a)."
    ),
    diesel_bus_lin_coeff: Optional[float] = Query(
        None, description="Diesel bus linear coeff (b)."
    ),
    diesel_bus_const: Optional[float] = Query(
        None, description="Diesel bus constant (c) [CHF]."
    ),
    # --- Lifetimes ---
    lifetime_bus: Optional[int] = Query(
        None, gt=0, le=200, description="Electric bus lifetime [years]."
    ),
    lifetime_battery: Optional[int] = Query(
        None, gt=0, le=200, description="Battery lifetime [years]."
    ),
    lifetime_charger: Optional[int] = Query(
        None, gt=0, le=200, description="Charger lifetime [years]."
    ),
    lifetime_connection: Optional[int] = Query(
        None, gt=0, le=200, description="Grid connection lifetime [years]."
    ),
    lifetime_diesel_bus: Optional[int] = Query(
        None, gt=0, le=200, description="Diesel bus lifetime [years]."
    ),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    # -- Validate CAPEX prerequisites -----------------------------------
    optimization_capex_inputs: dict[str, float] | None = None
    if include_capex and capex_source == "manual":
        if battery_capacity_kwh is None:
            raise HTTPException(
                status_code=422,
                detail="battery_capacity_kwh is required when include_capex=true.",
            )
        if charger_power_kw is None:
            raise HTTPException(
                status_code=422,
                detail="charger_power_kw is required when include_capex=true.",
            )
    elif include_capex and capex_source == "optimization":
        opt_run = await _load_optimization_run_for_capex(
            db=db,
            yearly_analysis_id=yearly_analysis_id,
            current_user=current_user,
        )
        optimization_capex_inputs = _extract_optimization_capex_inputs(opt_run.results)

    # -- 1. Fresh energy summary ----------------------------------------
    energy = await get_fresh_energy_summary(db, yearly_analysis_id)
    yt = energy["yearly_totals"]
    aux_type: str = energy["auxiliary_heating_type"]

    yearly_km = float(yt["distance_km"])
    yearly_electric_kwh = float(yt["electric_kwh"])
    yearly_diesel_liters = float(yt.get("diesel_liters", 0))
    yearly_diesel_fuel_kwh = float(yt.get("diesel_fuel_kwh", 0))

    # -- 2. Resolve economic parameters ---------------------------------
    epk = _econ_or(energy_price_per_kwh, "energy_price_per_kwh")
    fpl = _econ_or(fuel_cost_per_l, "fuel_cost_per_l")
    ir = _econ_or(interest_rate, "interest_rate")
    dhmf = _econ_or(
        diesel_heating_maintenance_factor,
        "diesel_heating_maintenance_factor",
    )

    em_a = _econ_or(electric_maint_cost_per_m, "electric_maint_cost_per_m")
    em_b = _econ_or(electric_maint_cost_const, "electric_maint_cost_const")
    dm_a = _econ_or(diesel_maint_cost_per_m, "diesel_maint_cost_per_m")
    dm_b = _econ_or(diesel_maint_cost_const, "diesel_maint_cost_const")
    dc_a = _econ_or(diesel_consumption_per_m, "diesel_consumption_per_m")
    dc_b = _econ_or(diesel_consumption_const, "diesel_consumption_const")

    e_maint_cpk = _maint_per_km(bus_length_m, em_a, em_b)
    d_maint_cpk = _maint_per_km(bus_length_m, dm_a, dm_b)
    d_cons_lpk = _consumption_l_per_km(bus_length_m, dc_a, dc_b)

    # -- 3. Mixed e-bus OPEX --------------------------------------------
    e_energy_year = epk * yearly_electric_kwh
    e_maint_year = e_maint_cpk * yearly_km

    dh_fuel_year = 0.0
    dh_maint_year = 0.0
    if aux_type == "diesel":
        dh_fuel_year = fpl * yearly_diesel_liters
        dh_maint_year = e_maint_year * dhmf

    ebus_opex = [
        OpexLineItem(name="Energy", cost_chf_per_year=round(e_energy_year, 2)),
        OpexLineItem(name="Maintenance", cost_chf_per_year=round(e_maint_year, 2)),
    ]
    if aux_type == "diesel":
        ebus_opex.append(OpexLineItem(
            name="Diesel heating fuel",
            cost_chf_per_year=round(dh_fuel_year, 2),
        ))
        ebus_opex.append(OpexLineItem(
            name="Diesel heating maintenance",
            cost_chf_per_year=round(dh_maint_year, 2),
        ))
    ebus_total_opex = sum(i.cost_chf_per_year for i in ebus_opex)

    # -- 4. Diesel comparator OPEX (legacy formulas) --------------------
    d_maint_year = d_maint_cpk * yearly_km
    d_fuel_year = yearly_km * d_cons_lpk * fpl

    diesel_opex = [
        OpexLineItem(name="Maintenance", cost_chf_per_year=round(d_maint_year, 2)),
        OpexLineItem(name="Fuel", cost_chf_per_year=round(d_fuel_year, 2)),
    ]
    diesel_total_opex = sum(i.cost_chf_per_year for i in diesel_opex)

    # -- 5. CAPEX (optional) --------------------------------------------
    ebus_capex: list[CapexLineItem] | None = None
    ebus_total_capex: float | None = None
    diesel_capex: list[CapexLineItem] | None = None
    diesel_total_capex: float | None = None

    if include_capex:
        lt_bus = _econ_or_int(lifetime_bus, "lifetime_bus")
        lt_batt = _econ_or_int(lifetime_battery, "lifetime_battery")
        lt_chg = _econ_or_int(lifetime_charger, "lifetime_charger")
        lt_conn = _econ_or_int(lifetime_connection, "lifetime_connection")
        lt_diesel = _econ_or_int(lifetime_diesel_bus, "lifetime_diesel_bus")

        batt_cpk = _econ_or(battery_cost_per_kwh, "battery_cost_per_kwh")
        eb_a = _econ_or(bus_no_batt_quad_coeff, "bus_no_batt_quad_coeff")
        eb_b = _econ_or(bus_no_batt_lin_coeff, "bus_no_batt_lin_coeff")
        eb_c = _econ_or(bus_no_batt_const, "bus_no_batt_const")
        ch_a = _econ_or(charger_cost_per_kw, "charger_cost_per_kw")
        ch_b = _econ_or(charger_cost_const, "charger_cost_const")
        gc_a = _econ_or(grid_connection_fee_per_kw, "grid_connection_fee_per_kw")
        gc_b = _econ_or(grid_connection_fee_const, "grid_connection_fee_const")
        db_a = _econ_or(diesel_bus_quad_coeff, "diesel_bus_quad_coeff")
        db_b = _econ_or(diesel_bus_lin_coeff, "diesel_bus_lin_coeff")
        db_c = _econ_or(diesel_bus_const, "diesel_bus_const")

        inv_bus_body = eb_a * bus_length_m ** 2 + eb_b * bus_length_m + eb_c

        if capex_source == "optimization":
            if optimization_capex_inputs is None:
                raise HTTPException(
                    status_code=500,
                    detail="Optimization CAPEX inputs were not prepared.",
                )
            inv_battery = (
                batt_cpk
                * optimization_capex_inputs["optimized_battery_capacity_kwh"]
            )
            inv_charging_infrastructure = optimization_capex_inputs["installation_cost_chf"]
            ebus_capex = [
                CapexLineItem(
                    name="Battery",
                    investment_chf=round(inv_battery, 2),
                    lifetime_years=lt_batt,
                    annualized_chf_per_year=round(_annualize(inv_battery, lt_batt, ir), 2),
                ),
                CapexLineItem(
                    name="Bus body (w/o battery)",
                    investment_chf=round(inv_bus_body, 2),
                    lifetime_years=lt_bus,
                    annualized_chf_per_year=round(_annualize(inv_bus_body, lt_bus, ir), 2),
                ),
                CapexLineItem(
                    name="Optimized charging infrastructure",
                    investment_chf=round(inv_charging_infrastructure, 2),
                    lifetime_years=lt_chg,
                    annualized_chf_per_year=round(
                        _annualize(inv_charging_infrastructure, lt_chg, ir), 2
                    ),
                ),
            ]
        else:
            inv_battery = batt_cpk * battery_capacity_kwh
            inv_charger = ch_a * charger_power_kw + ch_b
            inv_grid = max(gc_a * charger_power_kw + gc_b, 0.0)

            ebus_capex = [
                CapexLineItem(
                    name="Battery",
                    investment_chf=round(inv_battery, 2),
                    lifetime_years=lt_batt,
                    annualized_chf_per_year=round(_annualize(inv_battery, lt_batt, ir), 2),
                ),
                CapexLineItem(
                    name="Bus body (w/o battery)",
                    investment_chf=round(inv_bus_body, 2),
                    lifetime_years=lt_bus,
                    annualized_chf_per_year=round(_annualize(inv_bus_body, lt_bus, ir), 2),
                ),
                CapexLineItem(
                    name="Charger",
                    investment_chf=round(inv_charger, 2),
                    lifetime_years=lt_chg,
                    annualized_chf_per_year=round(_annualize(inv_charger, lt_chg, ir), 2),
                ),
                CapexLineItem(
                    name="Grid connection",
                    investment_chf=round(inv_grid, 2),
                    lifetime_years=lt_conn,
                    annualized_chf_per_year=round(_annualize(inv_grid, lt_conn, ir), 2),
                ),
            ]
        ebus_total_capex = sum(c.annualized_chf_per_year for c in ebus_capex)

        inv_diesel = db_a * bus_length_m ** 2 + db_b * bus_length_m + db_c
        diesel_capex = [
            CapexLineItem(
                name="Diesel bus",
                investment_chf=round(inv_diesel, 2),
                lifetime_years=lt_diesel,
                annualized_chf_per_year=round(
                    _annualize(inv_diesel, lt_diesel, ir), 2
                ),
            ),
        ]
        diesel_total_capex = sum(c.annualized_chf_per_year for c in diesel_capex)

    # -- 6. Total annual costs ------------------------------------------
    ebus_annual = round(ebus_total_opex + (ebus_total_capex or 0), 2)
    diesel_annual = round(diesel_total_opex + (diesel_total_capex or 0), 2)
    ebus_cpk = round(ebus_annual / yearly_km, 6) if yearly_km > 0 else 0.0
    diesel_cpk = round(diesel_annual / yearly_km, 6) if yearly_km > 0 else 0.0

    ebus_summary = CostSummary(
        capex_items=ebus_capex,
        total_annualized_capex_chf_per_year=(
            round(ebus_total_capex, 2) if ebus_total_capex is not None else None
        ),
        opex_items=ebus_opex,
        total_opex_chf_per_year=round(ebus_total_opex, 2),
        total_annual_cost_chf_per_year=ebus_annual,
    )
    diesel_summary = CostSummary(
        capex_items=diesel_capex,
        total_annualized_capex_chf_per_year=(
            round(diesel_total_capex, 2) if diesel_total_capex is not None else None
        ),
        opex_items=diesel_opex,
        total_opex_chf_per_year=round(diesel_total_opex, 2),
        total_annual_cost_chf_per_year=diesel_annual,
    )

    # -- 7. Per-scenario costs ------------------------------------------
    scenario_costs: list[YearlyCostScenario] = []
    for sc in energy["scenarios"]:
        sc_annual_electric = float(sc.get("annual_electric_kwh", 0))
        sc_annual_km = float(sc.get("annual_distance_km", 0))
        sc_daily_electric = float(sc.get("daily_electric_kwh", 0))
        sc_daily_km = float(sc.get("daily_distance_km", 0))

        dh = sc.get("diesel_heating") or {}
        sc_daily_dh_liters = float(dh.get("diesel_liters", 0))
        sc_annual_dh_liters = float(sc.get("annual_diesel_liters", 0))

        sc_e_energy_cost = epk * sc_annual_electric
        sc_e_maint_cost = e_maint_cpk * sc_annual_km
        sc_dh_fuel_cost = fpl * sc_annual_dh_liters if aux_type == "diesel" else 0.0
        sc_dh_maint_cost = sc_e_maint_cost * dhmf if aux_type == "diesel" else 0.0

        scenario_costs.append(YearlyCostScenario(
            temperature_celsius=sc["temperature_celsius"],
            occurrences=sc["occurrences"],
            daily_electric_kwh=round(sc_daily_electric, 4),
            daily_distance_km=round(sc_daily_km, 4),
            daily_diesel_heating_liters=round(sc_daily_dh_liters, 4),
            annual_electric_kwh=round(sc_annual_electric, 4),
            annual_distance_km=round(sc_annual_km, 4),
            annual_diesel_heating_liters=round(sc_annual_dh_liters, 4),
            annual_electric_energy_cost_chf=round(sc_e_energy_cost, 2),
            annual_electric_maint_cost_chf=round(sc_e_maint_cost, 2),
            annual_diesel_heating_fuel_cost_chf=round(sc_dh_fuel_cost, 2),
            annual_diesel_heating_maint_cost_chf=round(sc_dh_maint_cost, 2),
        ))

    # -- 8. Assemble response -------------------------------------------
    return YearlyCostResponse(
        yearly_analysis_id=yearly_analysis_id,
        auxiliary_heating_type=aux_type,
        annual_km=round(yearly_km, 3),
        ebus=ebus_summary,
        diesel_comparator=diesel_summary,
        annual_saving_chf=round(diesel_annual - ebus_annual, 2),
        assumptions=YearlyCostAssumptions(
            energy_price_per_kwh=epk,
            fuel_cost_per_l=fpl,
            interest_rate=ir,
            bus_length_m=bus_length_m,
            yearly_electric_kwh=round(yearly_electric_kwh, 4),
            yearly_distance_km=round(yearly_km, 4),
            yearly_diesel_heating_liters=round(yearly_diesel_liters, 4),
            yearly_diesel_heating_fuel_kwh=round(yearly_diesel_fuel_kwh, 4),
            diesel_heating_maintenance_factor=dhmf,
            electric_maint_cost_per_km_chf=round(e_maint_cpk, 6),
            diesel_comparator_maint_cost_per_km_chf=round(d_maint_cpk, 6),
            diesel_comparator_consumption_l_per_km=round(d_cons_lpk, 6),
        ),
        scenarios=scenario_costs,
    )


# =========================================================================
# GET /{yearly_analysis_id}/emissions — Mixed e-bus vs diesel comparator
# =========================================================================

_INDICATORS = (
    "gwp100a",
    "nox",
    "pm10",
    "primaryEnergy",
    "primaryEnergyNonRenewable",
)

_INDICATOR_UNITS: dict[str, str] = {
    "gwp100a": "g CO\u2082-eq",
    "nox": "mg NOx",
    "pm10": "mg PM10",
    "primaryEnergy": "MJ oil-eq",
    "primaryEnergyNonRenewable": "MJ oil-eq",
}


def _ef(section: str, indicator: str) -> float:
    """Look up a single emission factor from emission_defaults.json."""
    ef = _load_emission_defaults()[section]
    suffix_map = {
        "gwp100a": "gwp100a_g",
        "nox": "nox_mg",
        "pm10": "pm10_mg",
        "primaryEnergy": "primaryEnergy_mj",
        "primaryEnergyNonRenewable": "primaryEnergyNonRenewable_mj",
    }
    prefix = suffix_map[indicator]
    per_unit = "per_kwh" if section == "electricity" else "per_liter"
    return float(ef[f"{prefix}_{per_unit}"])


@router.get(
    "/{yearly_analysis_id}/emissions",
    response_model=YearlyEmissionsResponse,
    operation_id="get_yearly_analysis_emissions",
    summary="Yearly emissions — mixed e-bus vs full-diesel comparator",
    description=textwrap.dedent(
        """
        Yearly environmental indicators for the yearly analysis, using a **fresh**
        energy aggregate (same source as ``GET …/energy-summary`` via
        ``get_fresh_energy_summary()`` — never a stale cached blob).

        **Mixed e-bus branch** (``ebus``): for each indicator, ``electric`` is
        ``yearly_electric_kwh ×`` electricity factors; ``diesel_heating`` is
        ``yearly_diesel_heating_liters ×`` diesel-heater factors when
        ``auxiliary_heating_type`` is ``diesel``, else zero; ``total`` is the sum.
        Factors are read from ``config/emission_defaults.json`` (electricity,
        diesel_heating sections).

        **Full-diesel comparator** (``diesel_comparator``): ``yearly_distance_km ×``
        diesel L/km regression (same as ``GET …/costs``, from economic defaults)
        ``×`` **diesel_bus** factors — legacy full-diesel vehicle, not heater-only.

        **Response units** (see each indicator's ``unit`` field): yearly totals are
        **g CO₂-eq**, **mg NOx**, **mg PM₁₀**, **MJ oil-eq** per year for the whole
        analysis. Per-scenario CO₂ fields (``gwp100a_*_kg``) are **kg/year** for that
        scenario only.

        **``annual_saving``**: per indicator, ``diesel_comparator.total − ebus.total``.
        Positive ⇒ the mixed e-bus emits **less** than the full-diesel comparator
        for that indicator (a “saving” vs the comparator).

        **Errors**: ``404`` if the yearly analysis does not exist or has no linked
        prediction runs (same as energy-summary).
        """
    ).strip(),
    responses={
        404: {
            "description": (
                "Yearly analysis not found, or no prediction runs linked "
                "(``detail`` explains which)."
            ),
        },
        401: {"description": "Missing or invalid bearer token."},
        422: {"description": "Validation error (e.g. missing ``bus_length_m``)."},
    },
)
async def get_yearly_emissions(
    yearly_analysis_id: UUID,
    bus_length_m: float = Query(
        ...,
        gt=0,
        description=(
            "Bus length [m] (required). Used only for the **full-diesel comparator** "
            "consumption model (L/km regression); mixed e-bus diesel-heating liters come "
            "from prediction summaries, not from this length."
        ),
    ),
    diesel_consumption_per_m: Optional[float] = Query(
        None,
        description=(
            "Optional override: diesel comparator consumption slope [L/km per m]. "
            "Default from ``config/economic_defaults.json`` (``diesel_consumption_per_m``)."
        ),
    ),
    diesel_consumption_const: Optional[float] = Query(
        None,
        description=(
            "Optional override: diesel comparator consumption intercept [L/km]. "
            "Default from ``config/economic_defaults.json`` (``diesel_consumption_const``)."
        ),
    ),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    # -- 1. Fresh energy summary ----------------------------------------
    energy = await get_fresh_energy_summary(db, yearly_analysis_id)
    yt = energy["yearly_totals"]
    aux_type: str = energy["auxiliary_heating_type"]

    yearly_km = float(yt["distance_km"])
    yearly_electric_kwh = float(yt["electric_kwh"])
    yearly_diesel_liters = float(yt.get("diesel_liters", 0))
    yearly_diesel_fuel_kwh = float(yt.get("diesel_fuel_kwh", 0))

    # -- 2. Diesel comparator consumption (same regression as costs) -----
    dc_a = _econ_or(diesel_consumption_per_m, "diesel_consumption_per_m")
    dc_b = _econ_or(diesel_consumption_const, "diesel_consumption_const")
    d_cons_lpk = _consumption_l_per_km(bus_length_m, dc_a, dc_b)
    yearly_diesel_bus_liters = yearly_km * d_cons_lpk

    # -- 3. Resolve LCA vehicles for phase-level data -------------------------
    ebus_lca = await _resolve_lca_vehicle(db, yearly_analysis_id)

    ebus_impact: Optional[dict] = None
    diesel_impact: Optional[dict] = None
    diesel_lca_meta: Optional[dict] = None
    diesel_lca_reason: Optional[str] = None

    if ebus_lca is not None:
        ebus_impact = await _lca_get_impact(str(ebus_lca.id))
        logger.info(
            "Emissions LCA: ebus vehicle=%s, impact_received=%s, yearly_km=%.1f, passengers=%s",
            ebus_lca.id, ebus_impact is not None, yearly_km,
            ebus_lca.passenger_capacity,
        )
    else:
        logger.info(
            "Emissions LCA: no ebus LCA vehicle resolved for yearly_analysis %s",
            yearly_analysis_id,
        )

    # Diesel comparator LCA: deterministic resolution from config
    size_class = _resolve_bus_size_class(bus_length_m)
    if size_class is not None:
        diesel_lca_cfg = _get_configured_diesel_lca_vehicle(size_class)
        if diesel_lca_cfg is not None:
            diesel_lca_vehicle_id = diesel_lca_cfg["id"]
            diesel_impact = await _lca_get_impact(diesel_lca_vehicle_id)
            if diesel_impact is not None:
                diesel_lca_meta = diesel_lca_cfg
                logger.info(
                    "Emissions LCA: diesel comparator vehicle=%s (size=%s, lca_size=%s)",
                    diesel_lca_cfg["name"], size_class, diesel_lca_cfg["lca_size"],
                )
            else:
                diesel_lca_reason = "external_lca_error"
                logger.warning(
                    "Emissions LCA: external LCA API failed for diesel comparator id=%s",
                    diesel_lca_vehicle_id,
                )
        else:
            diesel_lca_reason = "configured_diesel_lca_vehicle_not_found"
            logger.warning(
                "Emissions LCA: no diesel_comparator_lca_vehicles config for size_class=%s",
                size_class,
            )
    else:
        diesel_lca_reason = "no_diesel_lca_vehicle_mapping"
        logger.debug(
            "Emissions LCA: cannot determine size_class (bus_length_m=%s)",
            bus_length_m,
        )

    # -- 4. Build per-indicator values -----------------------------------
    ebus_indicators: dict[str, YearlyEmissionsIndicator] = {}
    diesel_indicators: dict[str, YearlyEmissionsComparatorIndicator] = {}
    annual_saving: dict[str, float] = {}

    for ind in _INDICATORS:
        unit = _INDICATOR_UNITS[ind]

        # Energy-based calculation (backward-compatible totals)
        el_factor = _ef("electricity", ind)
        el_val = yearly_electric_kwh * el_factor

        dh_val = 0.0
        if aux_type == "diesel" and yearly_diesel_liters > 0:
            dh_factor = _ef("diesel_heating", ind)
            dh_val = yearly_diesel_liters * dh_factor

        ebus_total = el_val + dh_val

        # LCA phase-share allocation: distribute the operational electric-side
        # value across lifecycle phases using Mobitool proportions.
        ebus_phases: Dict[str, Optional[float]] = {p: None for p in _LCA_PHASES}
        if ebus_impact is not None:
            indicator_data = ebus_impact.get(ind)
            if isinstance(indicator_data, dict):
                ebus_phases = _allocate_phases_by_share(indicator_data, el_val)
                # total = phase_sum (≈ el_val) + diesel_heating
                ebus_total = ebus_phases["total"] + dh_val

        ebus_indicators[ind] = YearlyEmissionsIndicator(
            unit=unit,
            electric=round(el_val, 4),
            diesel_heating=round(dh_val, 4),
            total=round(ebus_total, 4),
            direct=ebus_phases.get("direct"),
            directNonExhaust=ebus_phases.get("directNonExhaust"),
            energyChain=ebus_phases.get("energyChain"),
            maintenance=ebus_phases.get("maintenance"),
            vehicle=ebus_phases.get("vehicle"),
            endOfLife=ebus_phases.get("endOfLife"),
            infrastructure=ebus_phases.get("infrastructure"),
        )

        # Diesel comparator: use energy-factor total; phase-share allocation
        # from config-mapped diesel LCA vehicle.
        d_factor_ef = _ef("diesel_bus", ind)
        d_total_ef = yearly_diesel_bus_liters * d_factor_ef

        diesel_phases: Dict[str, Optional[float]] = {p: None for p in _LCA_PHASES}
        d_total = d_total_ef
        if diesel_impact is not None:
            d_indicator_data = diesel_impact.get(ind)
            if isinstance(d_indicator_data, dict):
                diesel_phases = _allocate_phases_by_share(d_indicator_data, d_total_ef)
                d_total = diesel_phases["total"]
            elif diesel_lca_reason is None:
                diesel_lca_reason = "empty_phase_response"

        diesel_indicators[ind] = YearlyEmissionsComparatorIndicator(
            unit=unit,
            total=round(d_total, 4),
            direct=diesel_phases.get("direct"),
            directNonExhaust=diesel_phases.get("directNonExhaust"),
            energyChain=diesel_phases.get("energyChain"),
            maintenance=diesel_phases.get("maintenance"),
            vehicle=diesel_phases.get("vehicle"),
            endOfLife=diesel_phases.get("endOfLife"),
            infrastructure=diesel_phases.get("infrastructure"),
        )

        annual_saving[ind] = round(d_total - ebus_total, 4)

    # -- 5. Per-scenario emissions (CO₂ only for compactness) ------------
    scenario_emissions: list[YearlyEmissionsScenario] = []
    el_gwp_factor = _ef("electricity", "gwp100a")
    dh_gwp_factor = _ef("diesel_heating", "gwp100a")
    g_to_kg = 1e-3

    for sc in energy["scenarios"]:
        sc_annual_electric = float(sc.get("annual_electric_kwh", 0))
        dh = sc.get("diesel_heating") or {}
        sc_daily_dh_liters = float(dh.get("diesel_liters", 0))
        sc_annual_dh_liters = float(sc.get("annual_diesel_liters", 0))

        sc_gwp_el_g = sc_annual_electric * el_gwp_factor
        sc_gwp_dh_g = sc_annual_dh_liters * dh_gwp_factor if aux_type == "diesel" else 0.0

        scenario_emissions.append(YearlyEmissionsScenario(
            temperature_celsius=sc["temperature_celsius"],
            occurrences=sc["occurrences"],
            annual_electric_kwh=round(sc_annual_electric, 4),
            annual_diesel_heating_liters=round(sc_annual_dh_liters, 4),
            gwp100a_electric_kg=round(sc_gwp_el_g * g_to_kg, 6),
            gwp100a_diesel_heating_kg=round(sc_gwp_dh_g * g_to_kg, 6),
            gwp100a_total_kg=round((sc_gwp_el_g + sc_gwp_dh_g) * g_to_kg, 6),
        ))

    # -- 6. Build complete payload sections ----------------------------------

    # 6a. Indicator summaries (flat list with delta, percent, normalized)
    _INDICATOR_LABELS = {
        "gwp100a": "CO₂ equivalent",
        "nox": "NOx emissions",
        "pm10": "PM₁₀ emissions",
        "primaryEnergy": "Total primary energy",
        "primaryEnergyNonRenewable": "Non-renewable primary energy",
    }
    _DISPLAY_UNITS = {
        "gwp100a": "t/year",
        "nox": "kg/year",
        "pm10": "kg/year",
        "primaryEnergy": "GJ/year",
        "primaryEnergyNonRenewable": "GJ/year",
    }
    _NORMALIZED_UNITS = {
        "gwp100a": "g/km",
        "nox": "mg/km",
        "pm10": "mg/km",
        "primaryEnergy": "MJ/km",
        "primaryEnergyNonRenewable": "MJ/km",
    }

    indicator_summaries: List[IndicatorSummary] = []
    for ind in _INDICATORS:
        ebus_t = ebus_indicators[ind].total
        diesel_t = diesel_indicators[ind].total
        delta = diesel_t - ebus_t
        pct = round((-delta / diesel_t) * 100, 2) if diesel_t != 0 else None
        norm_ebus = round(ebus_t / yearly_km, 4) if yearly_km > 0 else 0.0
        norm_diesel = round(diesel_t / yearly_km, 4) if yearly_km > 0 else 0.0

        indicator_summaries.append(IndicatorSummary(
            key=ind,
            label=_INDICATOR_LABELS[ind],
            unit=_INDICATOR_UNITS[ind] + "/year",
            display_unit=_DISPLAY_UNITS[ind],
            ebus_total=round(ebus_t, 4),
            diesel_comparator=round(diesel_t, 4),
            delta_vs_diesel=round(delta, 4),
            change_vs_diesel_percent=pct,
            normalized_ebus_per_km=norm_ebus,
            normalized_diesel_per_km=norm_diesel,
            normalized_unit=_NORMALIZED_UNITS[ind],
        ))

    # 6b. Mixed-case decomposition
    mixed_available = aux_type == "diesel" and yearly_diesel_liters > 0
    mixed_indicators: Dict[str, MixedCaseIndicator] = {}
    for ind in _INDICATORS:
        el_v = ebus_indicators[ind].electric
        dh_v = ebus_indicators[ind].diesel_heating
        mixed_indicators[ind] = MixedCaseIndicator(
            unit=_INDICATOR_UNITS[ind] + "/year",
            electric_side=round(el_v, 4),
            diesel_heating=round(dh_v, 4),
            total=round(el_v + dh_v, 4),
        )

    electric_kwh_per_100km = (
        round((yearly_electric_kwh / yearly_km) * 100, 4) if yearly_km > 0 else 0.0
    )
    mixed_decomposition = MixedCaseDecomposition(
        available=mixed_available,
        yearly_electric_kwh=round(yearly_electric_kwh, 4),
        electric_kwh_per_100km=electric_kwh_per_100km,
        yearly_diesel_heating_liters=round(yearly_diesel_liters, 4) if mixed_available else 0.0,
        indicators=mixed_indicators,
    )

    # 6c. Lifecycle breakdown (gwp100a)
    gwp_ebus = ebus_indicators["gwp100a"]
    gwp_diesel = diesel_indicators["gwp100a"]

    ebus_phase_sum: Optional[float] = None
    if gwp_ebus.energyChain is not None:
        ebus_phase_sum = round(sum(
            getattr(gwp_ebus, p) for p in (
                "direct", "directNonExhaust", "energyChain",
                "maintenance", "vehicle", "endOfLife", "infrastructure",
            ) if getattr(gwp_ebus, p) is not None
        ), 4)

    ebus_label = (
        "E-bus (with diesel heating)" if mixed_available else "E-bus"
    )

    diesel_phases_available = gwp_diesel.energyChain is not None
    diesel_phase_sum: Optional[float] = None
    if diesel_phases_available:
        diesel_phase_sum = round(sum(
            getattr(gwp_diesel, p) for p in (
                "direct", "directNonExhaust", "energyChain",
                "maintenance", "vehicle", "endOfLife", "infrastructure",
            ) if getattr(gwp_diesel, p) is not None
        ), 4)

    lifecycle = LifecycleBreakdown(
        indicator="gwp100a",
        unit=_INDICATOR_UNITS["gwp100a"] + "/year",
        method="mobitool_phase_share" if ebus_impact is not None else None,
        ebus=LifecycleEbus(
            label=ebus_label,
            electric_side=round(gwp_ebus.electric, 4),
            diesel_heating=round(gwp_ebus.diesel_heating, 4),
            total=round(gwp_ebus.total, 4),
            phases=LifecyclePhases(
                direct=gwp_ebus.direct,
                directNonExhaust=gwp_ebus.directNonExhaust,
                energyChain=gwp_ebus.energyChain,
                maintenance=gwp_ebus.maintenance,
                vehicle=gwp_ebus.vehicle,
                endOfLife=gwp_ebus.endOfLife,
                infrastructure=gwp_ebus.infrastructure,
            ),
            phase_sum=ebus_phase_sum,
            phase_sum_represents="electric_side_only",
        ),
        diesel_comparator=LifecycleDieselComparator(
            available=diesel_phases_available,
            total=round(gwp_diesel.total, 4),
            phases=LifecyclePhases(
                direct=gwp_diesel.direct,
                directNonExhaust=gwp_diesel.directNonExhaust,
                energyChain=gwp_diesel.energyChain,
                maintenance=gwp_diesel.maintenance,
                vehicle=gwp_diesel.vehicle,
                endOfLife=gwp_diesel.endOfLife,
                infrastructure=gwp_diesel.infrastructure,
            ),
            phase_sum=diesel_phase_sum,
            reason=diesel_lca_reason if not diesel_phases_available else None,
            lca_vehicle_id=diesel_lca_meta["id"] if diesel_lca_meta else None,
            size=diesel_lca_meta["size"] if diesel_lca_meta else None,
            lca_size=diesel_lca_meta["lca_size"] if diesel_lca_meta else None,
            source_id=diesel_lca_meta["source_id"] if diesel_lca_meta else None,
            name=diesel_lca_meta["name"] if diesel_lca_meta else None,
        ),
    )

    # 6d. Primary energy breakdown (renewable / non-renewable)
    pe_ebus_total = ebus_indicators["primaryEnergy"].total
    pe_ebus_nr = ebus_indicators["primaryEnergyNonRenewable"].total
    pe_ebus_r = pe_ebus_total - pe_ebus_nr

    pe_diesel_total = diesel_indicators["primaryEnergy"].total
    pe_diesel_nr = diesel_indicators["primaryEnergyNonRenewable"].total
    pe_diesel_r = pe_diesel_total - pe_diesel_nr

    def _pct(part: float, whole: float) -> float:
        return round((part / whole) * 100, 2) if whole != 0 else 0.0

    primary_energy = PrimaryEnergyBreakdown(
        unit="MJ/year",
        display_unit="GJ/year",
        ebus=PrimaryEnergySide(
            renewable=round(pe_ebus_r, 4),
            non_renewable=round(pe_ebus_nr, 4),
            total=round(pe_ebus_total, 4),
            renewable_percent=_pct(pe_ebus_r, pe_ebus_total),
            non_renewable_percent=_pct(pe_ebus_nr, pe_ebus_total),
        ),
        diesel_comparator=PrimaryEnergySide(
            renewable=round(pe_diesel_r, 4),
            non_renewable=round(pe_diesel_nr, 4),
            total=round(pe_diesel_total, 4),
            renewable_percent=_pct(pe_diesel_r, pe_diesel_total),
            non_renewable_percent=_pct(pe_diesel_nr, pe_diesel_total),
        ),
    )

    # 6e. Savings chart (CO₂, NOx, PM₁₀, primary energy)
    _SAVINGS_CONFIG = [
        ("gwp100a", "CO₂", "t/year", 1e-6),                       # g → t
        ("nox", "NOx", "kg/year", 1e-6),                           # mg → kg
        ("pm10", "PM₁₀", "kg/year", 1e-6),                        # mg → kg
        ("primaryEnergy", "Primary energy", "GJ/year", 1e-3),      # MJ → GJ
    ]
    savings_items: List[SavingsItem] = []
    for s_key, s_label, s_unit, s_factor in _SAVINGS_CONFIG:
        e_val = ebus_indicators[s_key].total * s_factor
        d_val = diesel_indicators[s_key].total * s_factor
        saved = d_val - e_val
        s_pct = round((saved / d_val) * 100, 1) if d_val != 0 else None
        savings_items.append(SavingsItem(
            key=s_key,
            label=s_label,
            unit=s_unit,
            ebus_display=round(e_val, 4),
            diesel_display=round(d_val, 4),
            saved_display=round(saved, 4),
            saved_percent=s_pct,
        ))

    savings_block = SavingsBlock(items=savings_items)

    # -- 7. Assemble response -------------------------------------------
    lca_phase_status = "available" if ebus_impact is not None and ebus_phase_sum is not None else "unavailable"
    lca_phase_reason: Optional[str] = None
    if lca_phase_status == "unavailable":
        if ebus_lca is None:
            lca_phase_reason = "no_lca_vehicle_match"
        elif ebus_impact is None:
            lca_phase_reason = "external_lca_error"
        else:
            lca_phase_reason = "unexpected_lca_response"

    return YearlyEmissionsResponse(
        yearly_analysis_id=yearly_analysis_id,
        auxiliary_heating_type=aux_type,
        annual_km=round(yearly_km, 3),
        ebus=ebus_indicators,
        diesel_comparator=diesel_indicators,
        annual_saving=annual_saving,
        assumptions=YearlyEmissionsAssumptions(
            auxiliary_heating_type=aux_type,
            yearly_electric_kwh=round(yearly_electric_kwh, 4),
            yearly_diesel_heating_liters=round(yearly_diesel_liters, 4),
            yearly_diesel_heating_fuel_kwh=round(yearly_diesel_fuel_kwh, 4),
            yearly_distance_km=round(yearly_km, 4),
            electricity_gwp100a_g_per_kwh=_ef("electricity", "gwp100a"),
            diesel_heating_gwp100a_g_per_liter=_ef("diesel_heating", "gwp100a"),
            diesel_bus_gwp100a_g_per_liter=_ef("diesel_bus", "gwp100a"),
            diesel_comparator_consumption_l_per_km=round(d_cons_lpk, 6),
            lca_phase_method=(
                "mobitool_phase_share" if ebus_impact is not None else None
            ),
            lca_source_functional_unit=(
                ebus_lca.functional_unit
                if ebus_lca is not None and ebus_impact is not None
                else None
            ),
            bus_length_m=bus_length_m,
            electric_kwh_per_100km=electric_kwh_per_100km,
            lca_vehicle_id=(
                str(ebus_lca.id) if ebus_lca is not None else None
            ),
            lca_phase_status=lca_phase_status,
            lca_phase_reason=lca_phase_reason,
        ),
        scenarios=scenario_emissions,
        indicators=indicator_summaries,
        mixed_case_decomposition=mixed_decomposition,
        lifecycle_breakdown=lifecycle,
        primary_energy_breakdown=primary_energy,
        savings=savings_block,
    )
