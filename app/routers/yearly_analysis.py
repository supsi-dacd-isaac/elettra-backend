from __future__ import annotations

import json
import logging
import math
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import List, Optional
from uuid import UUID

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
)
from app.schemas.responses import (
    YearlyEnergySummaryResponse,
    YearlyAnalysisListItemRead,
)
from app.models import YearlyAnalysis, OptimizationRuns, PredictionRuns, Users
from app.core.auth import get_current_user

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
    await db.commit()
    await db.refresh(obj)
    return obj


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

    await db.commit()
    await db.refresh(obj)
    return obj


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
            "annualised CAPEX.  Requires battery_capacity_kwh and "
            "charger_power_kw."
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
    if include_capex:
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

        inv_battery = batt_cpk * battery_capacity_kwh
        inv_bus_body = eb_a * bus_length_m ** 2 + eb_b * bus_length_m + eb_c
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

    # -- 3. Build per-indicator values -----------------------------------
    ebus_indicators: dict[str, YearlyEmissionsIndicator] = {}
    diesel_indicators: dict[str, YearlyEmissionsComparatorIndicator] = {}
    annual_saving: dict[str, float] = {}

    for ind in _INDICATORS:
        unit = _INDICATOR_UNITS[ind]

        el_factor = _ef("electricity", ind)
        el_val = yearly_electric_kwh * el_factor

        dh_val = 0.0
        if aux_type == "diesel" and yearly_diesel_liters > 0:
            dh_factor = _ef("diesel_heating", ind)
            dh_val = yearly_diesel_liters * dh_factor

        ebus_total = el_val + dh_val
        ebus_indicators[ind] = YearlyEmissionsIndicator(
            unit=unit,
            electric=round(el_val, 4),
            diesel_heating=round(dh_val, 4),
            total=round(ebus_total, 4),
        )

        d_factor = _ef("diesel_bus", ind)
        d_total = yearly_diesel_bus_liters * d_factor
        diesel_indicators[ind] = YearlyEmissionsComparatorIndicator(
            unit=unit, total=round(d_total, 4),
        )

        annual_saving[ind] = round(d_total - ebus_total, 4)

    # -- 4. Per-scenario emissions (CO₂ only for compactness) ------------
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

    # -- 5. Assemble response -------------------------------------------
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
        ),
        scenarios=scenario_emissions,
    )
