"""
Economic Evaluation router.

Provides investment-cost (CAPEX), annualised-cost, operating-expense (OPEX),
and full electric-vs-diesel comparison endpoints for public transport buses.

Default parameter values are loaded from ``config/economic_defaults.json``.
Physical inputs (kW, m, kWh, km …) are **mandatory**.
Equation coefficients and configuration parameters are **optional**; when
omitted the defaults from the JSON configuration file are used.
"""

import json
import logging
import math
from functools import lru_cache
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.shift_distance import RecurrenceType, compute_shift_yearly_distance
from app.database import get_async_session
from app.models import Users
from app.schemas.economic import (
    AnnualizedCostResponse,
    BatteryCostResponse,
    CapexLineItem,
    ChargerCostResponse,
    CostSummary,
    DieselBusCostResponse,
    DieselConsumptionResponse,
    DieselFuelCostResponse,
    DieselMaintenanceCostResponse,
    EconomicDefaultsResponse,
    ElectricBusBodyCostResponse,
    ElectricEnergyCostResponse,
    ElectricMaintenanceCostResponse,
    FullComparisonResponse,
    GridConnectionCostResponse,
    OpexLineItem,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Default-value loader
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "economic_defaults.json"


@lru_cache(maxsize=1)
def _load_defaults() -> dict:
    """Read and cache ``config/economic_defaults.json``."""
    with open(_CONFIG_PATH) as fh:
        return json.load(fh)


def _d(key: str):
    """Shorthand: return one default value by key."""
    return _load_defaults()[key]


def _or(value: Optional[float], key: str) -> float:
    """Resolve an optional query param: use the supplied value or fall back to the config default."""
    return value if value is not None else _d(key)


def _or_int(value: Optional[int], key: str) -> int:
    return value if value is not None else int(_d(key))


# ---------------------------------------------------------------------------
# Pure-calculation helpers — accept ALL parameters explicitly (no config I/O)
# ---------------------------------------------------------------------------

def _battery_cost(capacity_kwh: float, cost_per_kwh: float) -> float:
    return cost_per_kwh * capacity_kwh


def _bus_no_batt_cost(length_m: float, a: float, b: float, c: float) -> float:
    return a * length_m ** 2 + b * length_m + c


def _charger_cost(power_kw: float, a: float, b: float) -> float:
    return a * power_kw + b


def _fee_connection(power_kw: float, a: float, b: float) -> float:
    """Clamped to zero because a negative fee is nonsensical."""
    return max(a * power_kw + b, 0.0)


def _diesel_bus_cost(length_m: float, a: float, b: float, c: float) -> float:
    return a * length_m ** 2 + b * length_m + c


def _capital_recovery_factor(interest_rate: float, lifetime: int) -> float:
    """CRF = (q^t * i) / (q^t - 1), with q = 1 + i.

    Special cases handled to avoid runtime errors:
    - i = 0  -> CRF = 1 / t  (uniform distribution over lifetime)
    - q^t overflows -> CRF ~ i  (mathematical limit as t -> inf)
    """
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


def _electric_maint_cost_per_km(length_m: float, a: float, b: float) -> float:
    return a * length_m + b


def _diesel_maint_cost_per_km(length_m: float, a: float, b: float) -> float:
    return a * length_m + b


def _diesel_consumption_l_per_km(length_m: float, a: float, b: float) -> float:
    return a * length_m + b


# ========================================================================== #
# Defaults endpoint
# ========================================================================== #

@router.get(
    "/defaults",
    response_model=EconomicDefaultsResponse,
    summary="Default economic parameters",
    description=(
        "Returns every default value stored in "
        "``config/economic_defaults.json``.  These are used as fall-back "
        "whenever an optional coefficient / configuration parameter is "
        "omitted from any other endpoint in this section."
    ),
)
async def get_default_economic_parameters(
    current_user: Users = Depends(get_current_user),
):
    return EconomicDefaultsResponse(**_load_defaults())


# ========================================================================== #
# CAPEX endpoints
# ========================================================================== #

@router.get(
    "/investment/battery",
    response_model=BatteryCostResponse,
    summary="Battery investment cost",
    description="``cost [CHF] = cost_per_kwh × capacity [kWh]``",
)
async def get_battery_cost(
    capacity_kwh: float = Query(
        ..., gt=0, description="Battery capacity [kWh] (required)."
    ),
    cost_per_kwh: Optional[float] = Query(
        None, gt=0, description="Unit cost [CHF/kWh]. Default from config."
    ),
    current_user: Users = Depends(get_current_user),
):
    cpk = _or(cost_per_kwh, "battery_cost_per_kwh")
    return BatteryCostResponse(
        capacity_kwh=capacity_kwh,
        cost_chf=round(_battery_cost(capacity_kwh, cpk), 2),
    )


@router.get(
    "/investment/electric-bus-body",
    response_model=ElectricBusBodyCostResponse,
    summary="Electric bus body investment cost (without battery)",
    description="``cost [CHF] = quad_coeff·m² + lin_coeff·m + const_coeff``",
)
async def get_electric_bus_body_cost(
    length_m: float = Query(
        ..., gt=0, description="Bus length [m] (required)."
    ),
    quad_coeff: Optional[float] = Query(
        None, description="Quadratic coefficient (a). Default from config."
    ),
    lin_coeff: Optional[float] = Query(
        None, description="Linear coefficient (b). Default from config."
    ),
    const_coeff: Optional[float] = Query(
        None, description="Constant term (c) [CHF]. Default from config."
    ),
    current_user: Users = Depends(get_current_user),
):
    a = _or(quad_coeff, "bus_no_batt_quad_coeff")
    b = _or(lin_coeff, "bus_no_batt_lin_coeff")
    c = _or(const_coeff, "bus_no_batt_const")
    return ElectricBusBodyCostResponse(
        length_m=length_m,
        cost_chf=round(_bus_no_batt_cost(length_m, a, b, c), 2),
    )


@router.get(
    "/investment/charger",
    response_model=ChargerCostResponse,
    summary="Charger investment cost",
    description="``cost [CHF] = cost_per_kw·kW + cost_const``",
)
async def get_charger_cost(
    power_kw: float = Query(
        ..., gt=0, description="Charger rated power [kW] (required)."
    ),
    cost_per_kw: Optional[float] = Query(
        None, description="Slope coefficient [CHF/kW]. Default from config."
    ),
    cost_const: Optional[float] = Query(
        None, description="Intercept [CHF]. Default from config."
    ),
    current_user: Users = Depends(get_current_user),
):
    a = _or(cost_per_kw, "charger_cost_per_kw")
    b = _or(cost_const, "charger_cost_const")
    return ChargerCostResponse(
        power_kw=power_kw,
        cost_chf=round(_charger_cost(power_kw, a, b), 2),
    )


@router.get(
    "/investment/grid-connection",
    response_model=GridConnectionCostResponse,
    summary="Grid connection fee",
    description="``cost [CHF] = max(fee_per_kw·kW + fee_const, 0)``",
)
async def get_grid_connection_cost(
    power_kw: float = Query(
        ..., gt=0, description="Connection power [kW] (required)."
    ),
    fee_per_kw: Optional[float] = Query(
        None, description="Slope coefficient [CHF/kW]. Default from config."
    ),
    fee_const: Optional[float] = Query(
        None, description="Intercept [CHF]. Default from config."
    ),
    current_user: Users = Depends(get_current_user),
):
    a = _or(fee_per_kw, "grid_connection_fee_per_kw")
    b = _or(fee_const, "grid_connection_fee_const")
    return GridConnectionCostResponse(
        power_kw=power_kw,
        cost_chf=round(_fee_connection(power_kw, a, b), 2),
    )


@router.get(
    "/investment/diesel-bus",
    response_model=DieselBusCostResponse,
    summary="Diesel bus investment cost",
    description="``cost [CHF] = quad_coeff·m² + lin_coeff·m + const_coeff``",
)
async def get_diesel_bus_cost(
    length_m: float = Query(
        ..., gt=0, description="Bus length [m] (required)."
    ),
    quad_coeff: Optional[float] = Query(
        None, description="Quadratic coefficient (a). Default from config."
    ),
    lin_coeff: Optional[float] = Query(
        None, description="Linear coefficient (b). Default from config."
    ),
    const_coeff: Optional[float] = Query(
        None, description="Constant term (c) [CHF]. Default from config."
    ),
    current_user: Users = Depends(get_current_user),
):
    a = _or(quad_coeff, "diesel_bus_quad_coeff")
    b = _or(lin_coeff, "diesel_bus_lin_coeff")
    c = _or(const_coeff, "diesel_bus_const")
    return DieselBusCostResponse(
        length_m=length_m,
        cost_chf=round(_diesel_bus_cost(length_m, a, b, c), 2),
    )


# ========================================================================== #
# Annualisation
# ========================================================================== #

@router.get(
    "/annualize",
    response_model=AnnualizedCostResponse,
    summary="Annualise an investment cost",
    description=(
        "Converts a one-off investment into an equivalent annual cost "
        "using the Capital Recovery Factor: ``CRF = q^t·i / (q^t − 1)``."
    ),
)
async def get_annualized_cost(
    investment_cost_chf: float = Query(
        ..., gt=0, description="One-off investment [CHF] (required)."
    ),
    lifetime_years: int = Query(
        ..., gt=0, le=200, description="Asset lifetime [years] (required, max 200)."
    ),
    interest_rate: Optional[float] = Query(
        None, ge=0, le=1.0, description="Discount / interest rate (0–1). Default from config."
    ),
    current_user: Users = Depends(get_current_user),
):
    ir = _or(interest_rate, "interest_rate")
    crf = _capital_recovery_factor(ir, lifetime_years)
    return AnnualizedCostResponse(
        investment_cost_chf=investment_cost_chf,
        lifetime_years=lifetime_years,
        interest_rate=ir,
        capital_recovery_factor=round(crf, 8),
        annualized_cost_chf_per_year=round(investment_cost_chf * crf, 2),
    )


# ========================================================================== #
# OPEX – Electric bus
# ========================================================================== #

@router.get(
    "/opex/electric-maintenance",
    response_model=ElectricMaintenanceCostResponse,
    summary="Electric bus maintenance cost",
    description="``CHF/km = cost_per_m·m + cost_const``.  Annual km derived from shift yearly distance.",
)
async def get_electric_maintenance_cost(
    shift_id: UUID = Query(..., description="Shift UUID (required)."),
    recurrence: RecurrenceType = Query(..., description="How often the shift repeats."),
    length_m: float = Query(
        ..., gt=0, description="Bus length [m] (required)."
    ),
    custom_days: Optional[int] = Query(
        None, ge=1, le=366, description="Operating days/year (required when recurrence=custom)."
    ),
    cost_per_m: Optional[float] = Query(
        None, description="Slope coefficient [CHF/km per m]. Default from config."
    ),
    cost_const: Optional[float] = Query(
        None, description="Intercept [CHF/km]. Default from config."
    ),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    dist = await compute_shift_yearly_distance(shift_id, recurrence, custom_days, db)
    annual_km = dist.yearly_distance_km
    a = _or(cost_per_m, "electric_maint_cost_per_m")
    b = _or(cost_const, "electric_maint_cost_const")
    cpk = _electric_maint_cost_per_km(length_m, a, b)
    return ElectricMaintenanceCostResponse(
        shift_id=shift_id,
        length_m=length_m,
        annual_km=round(annual_km, 3),
        cost_per_km_chf=round(cpk, 6),
        cost_per_year_chf=round(cpk * annual_km, 2),
    )


@router.get(
    "/opex/electric-energy",
    response_model=ElectricEnergyCostResponse,
    summary="Electric bus annual energy cost",
    description="``cost [CHF/year] = energy_price_per_kwh × annual_consumption [kWh/year]``",
)
async def get_electric_energy_cost(
    annual_consumption_kwh: float = Query(
        ..., gt=0, description="Annual electricity consumption [kWh/year] (required)."
    ),
    energy_price_per_kwh: Optional[float] = Query(
        None, gt=0, description="Electricity price [CHF/kWh]. Default from config."
    ),
    current_user: Users = Depends(get_current_user),
):
    price = _or(energy_price_per_kwh, "energy_price_per_kwh")
    return ElectricEnergyCostResponse(
        annual_consumption_kwh=annual_consumption_kwh,
        energy_price_per_kwh=price,
        cost_per_year_chf=round(price * annual_consumption_kwh, 2),
    )


# ========================================================================== #
# OPEX – Diesel bus
# ========================================================================== #

@router.get(
    "/opex/diesel-maintenance",
    response_model=DieselMaintenanceCostResponse,
    summary="Diesel bus maintenance cost",
    description="``CHF/km = cost_per_m·m + cost_const``.  Annual km derived from shift yearly distance.",
)
async def get_diesel_maintenance_cost(
    shift_id: UUID = Query(..., description="Shift UUID (required)."),
    recurrence: RecurrenceType = Query(..., description="How often the shift repeats."),
    length_m: float = Query(
        ..., gt=0, description="Bus length [m] (required)."
    ),
    custom_days: Optional[int] = Query(
        None, ge=1, le=366, description="Operating days/year (required when recurrence=custom)."
    ),
    cost_per_m: Optional[float] = Query(
        None, description="Slope coefficient [CHF/km per m]. Default from config."
    ),
    cost_const: Optional[float] = Query(
        None, description="Intercept [CHF/km]. Default from config."
    ),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    dist = await compute_shift_yearly_distance(shift_id, recurrence, custom_days, db)
    annual_km = dist.yearly_distance_km
    a = _or(cost_per_m, "diesel_maint_cost_per_m")
    b = _or(cost_const, "diesel_maint_cost_const")
    cpk = _diesel_maint_cost_per_km(length_m, a, b)
    return DieselMaintenanceCostResponse(
        shift_id=shift_id,
        length_m=length_m,
        annual_km=round(annual_km, 3),
        cost_per_km_chf=round(cpk, 6),
        cost_per_year_chf=round(cpk * annual_km, 2),
    )


@router.get(
    "/opex/diesel-consumption",
    response_model=DieselConsumptionResponse,
    summary="Diesel fuel consumption rate",
    description="``l/km = coeff_per_m·m + coeff_const``",
)
async def get_diesel_consumption(
    length_m: float = Query(
        ..., gt=0, description="Bus length [m] (required)."
    ),
    coeff_per_m: Optional[float] = Query(
        None, description="Slope coefficient [l/km per m]. Default from config."
    ),
    coeff_const: Optional[float] = Query(
        None, description="Intercept [l/km]. Default from config."
    ),
    current_user: Users = Depends(get_current_user),
):
    a = _or(coeff_per_m, "diesel_consumption_per_m")
    b = _or(coeff_const, "diesel_consumption_const")
    return DieselConsumptionResponse(
        length_m=length_m,
        consumption_l_per_km=round(_diesel_consumption_l_per_km(length_m, a, b), 6),
    )


@router.get(
    "/opex/diesel-fuel",
    response_model=DieselFuelCostResponse,
    summary="Diesel bus annual fuel cost",
    description=(
        "``cost [CHF/year] = annual_km × (consumption_per_m·m + consumption_const) × fuel_cost_per_l``.  "
        "Annual km derived from shift yearly distance."
    ),
)
async def get_diesel_fuel_cost(
    shift_id: UUID = Query(..., description="Shift UUID (required)."),
    recurrence: RecurrenceType = Query(..., description="How often the shift repeats."),
    length_m: float = Query(
        ..., gt=0, description="Bus length [m] (required)."
    ),
    custom_days: Optional[int] = Query(
        None, ge=1, le=366, description="Operating days/year (required when recurrence=custom)."
    ),
    fuel_cost_per_l: Optional[float] = Query(
        None, gt=0, description="Fuel price [CHF/l]. Default from config."
    ),
    consumption_per_m: Optional[float] = Query(
        None, description="Consumption slope [l/km per m]. Default from config."
    ),
    consumption_const: Optional[float] = Query(
        None, description="Consumption intercept [l/km]. Default from config."
    ),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    dist = await compute_shift_yearly_distance(shift_id, recurrence, custom_days, db)
    annual_km = dist.yearly_distance_km
    fpl = _or(fuel_cost_per_l, "fuel_cost_per_l")
    ca = _or(consumption_per_m, "diesel_consumption_per_m")
    cb = _or(consumption_const, "diesel_consumption_const")
    cons = _diesel_consumption_l_per_km(length_m, ca, cb)
    return DieselFuelCostResponse(
        shift_id=shift_id,
        length_m=length_m,
        annual_km=round(annual_km, 3),
        fuel_cost_per_l=fpl,
        consumption_l_per_km=round(cons, 6),
        cost_per_year_chf=round(annual_km * cons * fpl, 2),
    )


# ========================================================================== #
# Full comparison: electric vs diesel
# ========================================================================== #

@router.get(
    "/comparison",
    response_model=FullComparisonResponse,
    summary="Full annual cost comparison — electric vs diesel bus",
    description=(
        "Computes annualised CAPEX and annual OPEX for both an electric and a "
        "diesel bus, then returns the side-by-side breakdown together with the "
        "yearly saving (positive = electric is cheaper).  Physical inputs are "
        "mandatory; all equation coefficients and configuration parameters are "
        "optional and fall back to the values from ``/defaults``."
    ),
)
async def get_full_comparison(
    # --- Shift-based distance ---
    shift_id: UUID = Query(..., description="Shift UUID (required)."),
    recurrence: RecurrenceType = Query(..., description="How often the shift repeats."),
    # --- Physical inputs ---
    bus_length_m: float = Query(
        ..., gt=0, description="Bus length [m] (required)."
    ),
    battery_capacity_kwh: Optional[float] = Query(
        None, gt=0,
        description=(
            "Battery capacity [kWh]. Required when include_capex=true "
            "(default); ignored when include_capex=false."
        ),
    ),
    charger_power_kw: Optional[float] = Query(
        None, gt=0,
        description=(
            "Charger rated power [kW]. Required when include_capex=true "
            "(default); ignored when include_capex=false."
        ),
    ),
    annual_consumption_kwh: float = Query(
        ..., gt=0, description="Annual electricity consumption [kWh/year] (required)."
    ),
    custom_days: Optional[int] = Query(
        None, ge=1, le=366, description="Operating days/year (required when recurrence=custom)."
    ),
    # --- CAPEX toggle ---
    include_capex: bool = Query(
        True,
        description=(
            "When true (default) the response includes full CAPEX + OPEX "
            "breakdown. When false, CAPEX items are omitted and "
            "battery_capacity_kwh / charger_power_kw become optional."
        ),
    ),
    # --- Optional configuration parameters ---
    interest_rate: Optional[float] = Query(
        None, ge=0, le=1.0, description="Discount / interest rate (0–1)."
    ),
    energy_price_per_kwh: Optional[float] = Query(
        None, gt=0, description="Electricity price [CHF/kWh]."
    ),
    fuel_cost_per_l: Optional[float] = Query(
        None, gt=0, description="Diesel price [CHF/l]."
    ),
    # --- Optional lifetimes ---
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
    # --- Optional equation coefficients: battery ---
    battery_cost_per_kwh: Optional[float] = Query(
        None, gt=0, description="Battery unit cost [CHF/kWh]."
    ),
    # --- Optional equation coefficients: electric bus body (a·m² + b·m + c) ---
    bus_no_batt_quad_coeff: Optional[float] = Query(
        None, description="Electric bus body quadratic coeff (a)."
    ),
    bus_no_batt_lin_coeff: Optional[float] = Query(
        None, description="Electric bus body linear coeff (b)."
    ),
    bus_no_batt_const: Optional[float] = Query(
        None, description="Electric bus body constant (c) [CHF]."
    ),
    # --- Optional equation coefficients: charger (a·kW + b) ---
    charger_cost_per_kw: Optional[float] = Query(
        None, description="Charger cost slope [CHF/kW]."
    ),
    charger_cost_const: Optional[float] = Query(
        None, description="Charger cost intercept [CHF]."
    ),
    # --- Optional equation coefficients: grid connection (a·kW + b) ---
    grid_connection_fee_per_kw: Optional[float] = Query(
        None, description="Grid connection fee slope [CHF/kW]."
    ),
    grid_connection_fee_const: Optional[float] = Query(
        None, description="Grid connection fee intercept [CHF]."
    ),
    # --- Optional equation coefficients: diesel bus (a·m² + b·m + c) ---
    diesel_bus_quad_coeff: Optional[float] = Query(
        None, description="Diesel bus quadratic coeff (a)."
    ),
    diesel_bus_lin_coeff: Optional[float] = Query(
        None, description="Diesel bus linear coeff (b)."
    ),
    diesel_bus_const: Optional[float] = Query(
        None, description="Diesel bus constant (c) [CHF]."
    ),
    # --- Optional equation coefficients: electric maintenance (a·m + b) ---
    electric_maint_cost_per_m: Optional[float] = Query(
        None, description="Electric maintenance slope [CHF/km per m]."
    ),
    electric_maint_cost_const: Optional[float] = Query(
        None, description="Electric maintenance intercept [CHF/km]."
    ),
    # --- Optional equation coefficients: diesel maintenance (a·m + b) ---
    diesel_maint_cost_per_m: Optional[float] = Query(
        None, description="Diesel maintenance slope [CHF/km per m]."
    ),
    diesel_maint_cost_const: Optional[float] = Query(
        None, description="Diesel maintenance intercept [CHF/km]."
    ),
    # --- Optional equation coefficients: diesel consumption (a·m + b) ---
    diesel_consumption_per_m: Optional[float] = Query(
        None, description="Diesel consumption slope [l/km per m]."
    ),
    diesel_consumption_const: Optional[float] = Query(
        None, description="Diesel consumption intercept [l/km]."
    ),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
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

    dist = await compute_shift_yearly_distance(shift_id, recurrence, custom_days, db)
    annual_km = dist.yearly_distance_km

    ir = _or(interest_rate, "interest_rate")
    epk = _or(energy_price_per_kwh, "energy_price_per_kwh")
    fpl = _or(fuel_cost_per_l, "fuel_cost_per_l")

    em_a = _or(electric_maint_cost_per_m, "electric_maint_cost_per_m")
    em_b = _or(electric_maint_cost_const, "electric_maint_cost_const")
    dm_a = _or(diesel_maint_cost_per_m, "diesel_maint_cost_per_m")
    dm_b = _or(diesel_maint_cost_const, "diesel_maint_cost_const")
    dc_a = _or(diesel_consumption_per_m, "diesel_consumption_per_m")
    dc_b = _or(diesel_consumption_const, "diesel_consumption_const")

    # --- Electric OPEX (always computed) ---
    e_maint_per_km = _electric_maint_cost_per_km(bus_length_m, em_a, em_b)
    e_maint_year = e_maint_per_km * annual_km
    e_energy_year = epk * annual_consumption_kwh

    e_opex = [
        OpexLineItem(name="Maintenance", cost_chf_per_year=round(e_maint_year, 2)),
        OpexLineItem(name="Energy", cost_chf_per_year=round(e_energy_year, 2)),
    ]
    e_total_opex = sum(item.cost_chf_per_year for item in e_opex)

    # --- Diesel OPEX (always computed) ---
    d_maint_per_km = _diesel_maint_cost_per_km(bus_length_m, dm_a, dm_b)
    d_maint_year = d_maint_per_km * annual_km
    d_cons = _diesel_consumption_l_per_km(bus_length_m, dc_a, dc_b)
    d_fuel_year = annual_km * d_cons * fpl

    d_opex = [
        OpexLineItem(name="Maintenance", cost_chf_per_year=round(d_maint_year, 2)),
        OpexLineItem(name="Fuel", cost_chf_per_year=round(d_fuel_year, 2)),
    ]
    d_total_opex = sum(item.cost_chf_per_year for item in d_opex)

    if include_capex:
        lt_bus = _or_int(lifetime_bus, "lifetime_bus")
        lt_batt = _or_int(lifetime_battery, "lifetime_battery")
        lt_chg = _or_int(lifetime_charger, "lifetime_charger")
        lt_conn = _or_int(lifetime_connection, "lifetime_connection")
        lt_diesel = _or_int(lifetime_diesel_bus, "lifetime_diesel_bus")

        batt_cpk = _or(battery_cost_per_kwh, "battery_cost_per_kwh")
        eb_a = _or(bus_no_batt_quad_coeff, "bus_no_batt_quad_coeff")
        eb_b = _or(bus_no_batt_lin_coeff, "bus_no_batt_lin_coeff")
        eb_c = _or(bus_no_batt_const, "bus_no_batt_const")
        ch_a = _or(charger_cost_per_kw, "charger_cost_per_kw")
        ch_b = _or(charger_cost_const, "charger_cost_const")
        gc_a = _or(grid_connection_fee_per_kw, "grid_connection_fee_per_kw")
        gc_b = _or(grid_connection_fee_const, "grid_connection_fee_const")
        db_a = _or(diesel_bus_quad_coeff, "diesel_bus_quad_coeff")
        db_b = _or(diesel_bus_lin_coeff, "diesel_bus_lin_coeff")
        db_c = _or(diesel_bus_const, "diesel_bus_const")

        # --- Electric CAPEX ---
        inv_battery = _battery_cost(battery_capacity_kwh, batt_cpk)
        inv_bus_body = _bus_no_batt_cost(bus_length_m, eb_a, eb_b, eb_c)
        inv_charger = _charger_cost(charger_power_kw, ch_a, ch_b)
        inv_grid = _fee_connection(charger_power_kw, gc_a, gc_b)

        e_capex = [
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
        e_total_capex = sum(item.annualized_chf_per_year for item in e_capex)

        # --- Diesel CAPEX ---
        inv_diesel = _diesel_bus_cost(bus_length_m, db_a, db_b, db_c)

        d_capex = [
            CapexLineItem(
                name="Diesel bus",
                investment_chf=round(inv_diesel, 2),
                lifetime_years=lt_diesel,
                annualized_chf_per_year=round(_annualize(inv_diesel, lt_diesel, ir), 2),
            ),
        ]
        d_total_capex = sum(item.annualized_chf_per_year for item in d_capex)

        electric = CostSummary(
            capex_items=e_capex,
            total_annualized_capex_chf_per_year=round(e_total_capex, 2),
            opex_items=e_opex,
            total_opex_chf_per_year=round(e_total_opex, 2),
            total_annual_cost_chf_per_year=round(e_total_capex + e_total_opex, 2),
        )
        diesel = CostSummary(
            capex_items=d_capex,
            total_annualized_capex_chf_per_year=round(d_total_capex, 2),
            opex_items=d_opex,
            total_opex_chf_per_year=round(d_total_opex, 2),
            total_annual_cost_chf_per_year=round(d_total_capex + d_total_opex, 2),
        )
    else:
        electric = CostSummary(
            opex_items=e_opex,
            total_opex_chf_per_year=round(e_total_opex, 2),
            total_annual_cost_chf_per_year=round(e_total_opex, 2),
        )
        diesel = CostSummary(
            opex_items=d_opex,
            total_opex_chf_per_year=round(d_total_opex, 2),
            total_annual_cost_chf_per_year=round(d_total_opex, 2),
        )

    return FullComparisonResponse(
        shift_id=shift_id,
        annual_km=round(annual_km, 3),
        interest_rate=ir,
        bus_length_m=bus_length_m,
        battery_capacity_kwh=battery_capacity_kwh,
        charger_power_kw=charger_power_kw,
        annual_consumption_kwh=annual_consumption_kwh,
        energy_price_per_kwh=epk,
        fuel_cost_per_l=fpl,
        electric=electric,
        diesel=diesel,
        annual_saving_chf=round(
            diesel.total_annual_cost_chf_per_year
            - electric.total_annual_cost_chf_per_year,
            2,
        ),
    )
