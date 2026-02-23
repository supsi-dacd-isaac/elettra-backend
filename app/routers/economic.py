"""
Economic Evaluation router.

Provides investment-cost (CAPEX), annualised-cost, operating-expense (OPEX),
and full electric-vs-diesel comparison endpoints for public transport buses.

Default parameter values are loaded from ``config/economic_defaults.json``.
Every input is overridable per-request via query parameters.
"""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.core.auth import get_current_user
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


# ---------------------------------------------------------------------------
# Pure-calculation helpers (no I/O, no DB)
# ---------------------------------------------------------------------------

def _battery_cost(capacity_kwh: float) -> float:
    """battery_cost [CHF] = battery_cost_per_kwh * capacity [kWh]"""
    return _d("battery_cost_per_kwh") * capacity_kwh


def _bus_no_batt_cost(length_m: float) -> float:
    """bus_no_batt [CHF] = a*m^2 + b*m + c"""
    return (
        _d("bus_no_batt_quad_coeff") * length_m ** 2
        + _d("bus_no_batt_lin_coeff") * length_m
        + _d("bus_no_batt_const")
    )


def _charger_cost(power_kw: float) -> float:
    """charger [CHF] = a*kW + b"""
    return _d("charger_cost_per_kw") * power_kw + _d("charger_cost_const")


def _fee_connection(power_kw: float) -> float:
    """fee_connect [CHF] = a*kW + b"""
    return _d("grid_connection_fee_per_kw") * power_kw + _d("grid_connection_fee_const")


def _diesel_bus_cost(length_m: float) -> float:
    """diesel_bus [CHF] = a*m^2 + b*m + c"""
    return (
        _d("diesel_bus_quad_coeff") * length_m ** 2
        + _d("diesel_bus_lin_coeff") * length_m
        + _d("diesel_bus_const")
    )


def _capital_recovery_factor(interest_rate: float, lifetime: int) -> float:
    """CRF = (q^t * i) / (q^t - 1), with q = 1 + i."""
    q = 1.0 + interest_rate
    q_t = q ** lifetime
    return (q_t * interest_rate) / (q_t - 1.0)


def _annualize(investment: float, lifetime: int, interest_rate: float) -> float:
    return investment * _capital_recovery_factor(interest_rate, lifetime)


def _electric_maint_cost_per_km(length_m: float) -> float:
    """CHF/km = a*m + b"""
    return _d("electric_maint_cost_per_m") * length_m + _d("electric_maint_cost_const")


def _electric_energy_cost(annual_kwh: float, price: float) -> float:
    return price * annual_kwh


def _diesel_maint_cost_per_km(length_m: float) -> float:
    """CHF/km = a*m + b"""
    return _d("diesel_maint_cost_per_m") * length_m + _d("diesel_maint_cost_const")


def _diesel_consumption_l_per_km(length_m: float) -> float:
    """l/km = a*m + b"""
    return _d("diesel_consumption_per_m") * length_m + _d("diesel_consumption_const")


def _diesel_fuel_cost(length_m: float, annual_km: float, fuel_per_l: float) -> float:
    return annual_km * _diesel_consumption_l_per_km(length_m) * fuel_per_l


# ---------------------------------------------------------------------------
# Utility: resolve Optional query → default
# ---------------------------------------------------------------------------

def _or(value: Optional[float], key: str) -> float:
    return value if value is not None else _d(key)


def _or_int(value: Optional[int], key: str) -> int:
    return value if value is not None else int(_d(key))


# ========================================================================== #
# CAPEX endpoints
# ========================================================================== #

@router.get(
    "/investment/battery",
    response_model=BatteryCostResponse,
    summary="Battery investment cost",
    description="``cost [CHF] = battery_cost_per_kwh × capacity [kWh]``",
)
async def get_battery_cost(
    capacity_kwh: Optional[float] = Query(
        None, gt=0, description="Battery capacity [kWh]."
    ),
    current_user: Users = Depends(get_current_user),
):
    cap = _or(capacity_kwh, "battery_capacity_kwh")
    return BatteryCostResponse(
        capacity_kwh=cap,
        cost_chf=round(_battery_cost(cap), 2),
    )


@router.get(
    "/investment/electric-bus-body",
    response_model=ElectricBusBodyCostResponse,
    summary="Electric bus body investment cost (without battery)",
    description="``cost [CHF] = a·m² + b·m + c``",
)
async def get_electric_bus_body_cost(
    length_m: Optional[float] = Query(
        None, gt=0, description="Bus length [m]."
    ),
    current_user: Users = Depends(get_current_user),
):
    lm = _or(length_m, "bus_length_m")
    return ElectricBusBodyCostResponse(
        length_m=lm,
        cost_chf=round(_bus_no_batt_cost(lm), 2),
    )


@router.get(
    "/investment/charger",
    response_model=ChargerCostResponse,
    summary="Charger investment cost",
    description="``cost [CHF] = a·kW + b``",
)
async def get_charger_cost(
    power_kw: Optional[float] = Query(
        None, gt=0, description="Charger rated power [kW]."
    ),
    current_user: Users = Depends(get_current_user),
):
    pw = _or(power_kw, "charger_power_kw")
    return ChargerCostResponse(
        power_kw=pw,
        cost_chf=round(_charger_cost(pw), 2),
    )


@router.get(
    "/investment/grid-connection",
    response_model=GridConnectionCostResponse,
    summary="Grid connection fee",
    description="``cost [CHF] = a·kW + b``",
)
async def get_grid_connection_cost(
    power_kw: Optional[float] = Query(
        None, gt=0, description="Connection power [kW]."
    ),
    current_user: Users = Depends(get_current_user),
):
    pw = _or(power_kw, "charger_power_kw")
    return GridConnectionCostResponse(
        power_kw=pw,
        cost_chf=round(_fee_connection(pw), 2),
    )


@router.get(
    "/investment/diesel-bus",
    response_model=DieselBusCostResponse,
    summary="Diesel bus investment cost",
    description="``cost [CHF] = a·m² + b·m + c``",
)
async def get_diesel_bus_cost(
    length_m: Optional[float] = Query(
        None, gt=0, description="Bus length [m]."
    ),
    current_user: Users = Depends(get_current_user),
):
    lm = _or(length_m, "bus_length_m")
    return DieselBusCostResponse(
        length_m=lm,
        cost_chf=round(_diesel_bus_cost(lm), 2),
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
        ..., gt=0, description="One-off investment [CHF]."
    ),
    lifetime_years: int = Query(
        ..., gt=0, description="Asset lifetime [years]."
    ),
    interest_rate: Optional[float] = Query(
        None, ge=0, description="Discount / interest rate."
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
    description="``CHF/km = a·m + b``",
)
async def get_electric_maintenance_cost(
    length_m: Optional[float] = Query(
        None, gt=0, description="Bus length [m]."
    ),
    annual_km: Optional[float] = Query(
        None, gt=0, description="Annual mileage [km/year]."
    ),
    current_user: Users = Depends(get_current_user),
):
    lm = _or(length_m, "bus_length_m")
    akm = _or(annual_km, "annual_km")
    cpk = _electric_maint_cost_per_km(lm)
    return ElectricMaintenanceCostResponse(
        length_m=lm,
        annual_km=akm,
        cost_per_km_chf=round(cpk, 6),
        cost_per_year_chf=round(cpk * akm, 2),
    )


@router.get(
    "/opex/electric-energy",
    response_model=ElectricEnergyCostResponse,
    summary="Electric bus annual energy cost",
    description="``cost [CHF/year] = price [CHF/kWh] × consumption [kWh/year]``",
)
async def get_electric_energy_cost(
    annual_consumption_kwh: Optional[float] = Query(
        None, gt=0, description="Annual electricity consumption [kWh/year]."
    ),
    energy_price_per_kwh: Optional[float] = Query(
        None, gt=0, description="Electricity price [CHF/kWh]."
    ),
    current_user: Users = Depends(get_current_user),
):
    akwh = _or(annual_consumption_kwh, "annual_consumption_kwh")
    price = _or(energy_price_per_kwh, "energy_price_per_kwh")
    return ElectricEnergyCostResponse(
        annual_consumption_kwh=akwh,
        energy_price_per_kwh=price,
        cost_per_year_chf=round(_electric_energy_cost(akwh, price), 2),
    )


# ========================================================================== #
# OPEX – Diesel bus
# ========================================================================== #

@router.get(
    "/opex/diesel-maintenance",
    response_model=DieselMaintenanceCostResponse,
    summary="Diesel bus maintenance cost",
    description="``CHF/km = a·m + b``",
)
async def get_diesel_maintenance_cost(
    length_m: Optional[float] = Query(
        None, gt=0, description="Bus length [m]."
    ),
    annual_km: Optional[float] = Query(
        None, gt=0, description="Annual mileage [km/year]."
    ),
    current_user: Users = Depends(get_current_user),
):
    lm = _or(length_m, "bus_length_m")
    akm = _or(annual_km, "annual_km")
    cpk = _diesel_maint_cost_per_km(lm)
    return DieselMaintenanceCostResponse(
        length_m=lm,
        annual_km=akm,
        cost_per_km_chf=round(cpk, 6),
        cost_per_year_chf=round(cpk * akm, 2),
    )


@router.get(
    "/opex/diesel-consumption",
    response_model=DieselConsumptionResponse,
    summary="Diesel fuel consumption rate",
    description="``l/km = a·m + b``",
)
async def get_diesel_consumption(
    length_m: Optional[float] = Query(
        None, gt=0, description="Bus length [m]."
    ),
    current_user: Users = Depends(get_current_user),
):
    lm = _or(length_m, "bus_length_m")
    return DieselConsumptionResponse(
        length_m=lm,
        consumption_l_per_km=round(_diesel_consumption_l_per_km(lm), 6),
    )


@router.get(
    "/opex/diesel-fuel",
    response_model=DieselFuelCostResponse,
    summary="Diesel bus annual fuel cost",
    description=(
        "``cost [CHF/year] = annual_km × (a·m + b) × fuel_cost_per_l``"
    ),
)
async def get_diesel_fuel_cost(
    length_m: Optional[float] = Query(
        None, gt=0, description="Bus length [m]."
    ),
    annual_km: Optional[float] = Query(
        None, gt=0, description="Annual mileage [km/year]."
    ),
    fuel_cost_per_l: Optional[float] = Query(
        None, gt=0, description="Fuel price [CHF/l]."
    ),
    current_user: Users = Depends(get_current_user),
):
    lm = _or(length_m, "bus_length_m")
    akm = _or(annual_km, "annual_km")
    fpl = _or(fuel_cost_per_l, "fuel_cost_per_l")
    cons = _diesel_consumption_l_per_km(lm)
    return DieselFuelCostResponse(
        length_m=lm,
        annual_km=akm,
        fuel_cost_per_l=fpl,
        consumption_l_per_km=round(cons, 6),
        cost_per_year_chf=round(akm * cons * fpl, 2),
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
        "yearly saving (positive = electric is cheaper)."
    ),
)
async def get_full_comparison(
    annual_km: Optional[float] = Query(
        None, gt=0, description="Annual mileage [km/year]."
    ),
    interest_rate: Optional[float] = Query(
        None, ge=0, description="Discount / interest rate."
    ),
    bus_length_m: Optional[float] = Query(
        None, gt=0, description="Bus length [m]."
    ),
    battery_capacity_kwh: Optional[float] = Query(
        None, gt=0, description="Battery capacity [kWh]."
    ),
    charger_power_kw: Optional[float] = Query(
        None, gt=0, description="Charger rated power [kW]."
    ),
    annual_consumption_kwh: Optional[float] = Query(
        None, gt=0, description="Annual electricity consumption [kWh/year]."
    ),
    energy_price_per_kwh: Optional[float] = Query(
        None, gt=0, description="Electricity price [CHF/kWh]."
    ),
    fuel_cost_per_l: Optional[float] = Query(
        None, gt=0, description="Diesel price [CHF/l]."
    ),
    lifetime_bus: Optional[int] = Query(
        None, gt=0, description="Electric bus lifetime [years]."
    ),
    lifetime_battery: Optional[int] = Query(
        None, gt=0, description="Battery lifetime [years]."
    ),
    lifetime_charger: Optional[int] = Query(
        None, gt=0, description="Charger lifetime [years]."
    ),
    lifetime_connection: Optional[int] = Query(
        None, gt=0, description="Grid connection lifetime [years]."
    ),
    lifetime_diesel_bus: Optional[int] = Query(
        None, gt=0, description="Diesel bus lifetime [years]."
    ),
    current_user: Users = Depends(get_current_user),
):
    akm = _or(annual_km, "annual_km")
    ir = _or(interest_rate, "interest_rate")
    lm = _or(bus_length_m, "bus_length_m")
    bcap = _or(battery_capacity_kwh, "battery_capacity_kwh")
    cpw = _or(charger_power_kw, "charger_power_kw")
    akwh = _or(annual_consumption_kwh, "annual_consumption_kwh")
    epk = _or(energy_price_per_kwh, "energy_price_per_kwh")
    fpl = _or(fuel_cost_per_l, "fuel_cost_per_l")

    lt_bus = _or_int(lifetime_bus, "lifetime_bus")
    lt_batt = _or_int(lifetime_battery, "lifetime_battery")
    lt_chg = _or_int(lifetime_charger, "lifetime_charger")
    lt_conn = _or_int(lifetime_connection, "lifetime_connection")
    lt_diesel = _or_int(lifetime_diesel_bus, "lifetime_diesel_bus")

    # --- Electric CAPEX ---
    inv_battery = _battery_cost(bcap)
    inv_bus_body = _bus_no_batt_cost(lm)
    inv_charger = _charger_cost(cpw)
    inv_grid = _fee_connection(cpw)

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

    # --- Electric OPEX ---
    e_maint_per_km = _electric_maint_cost_per_km(lm)
    e_maint_year = e_maint_per_km * akm
    e_energy_year = _electric_energy_cost(akwh, epk)

    e_opex = [
        OpexLineItem(name="Maintenance", cost_chf_per_year=round(e_maint_year, 2)),
        OpexLineItem(name="Energy", cost_chf_per_year=round(e_energy_year, 2)),
    ]
    e_total_opex = sum(item.cost_chf_per_year for item in e_opex)

    electric = CostSummary(
        capex_items=e_capex,
        total_annualized_capex_chf_per_year=round(e_total_capex, 2),
        opex_items=e_opex,
        total_opex_chf_per_year=round(e_total_opex, 2),
        total_annual_cost_chf_per_year=round(e_total_capex + e_total_opex, 2),
    )

    # --- Diesel CAPEX ---
    inv_diesel = _diesel_bus_cost(lm)

    d_capex = [
        CapexLineItem(
            name="Diesel bus",
            investment_chf=round(inv_diesel, 2),
            lifetime_years=lt_diesel,
            annualized_chf_per_year=round(_annualize(inv_diesel, lt_diesel, ir), 2),
        ),
    ]
    d_total_capex = sum(item.annualized_chf_per_year for item in d_capex)

    # --- Diesel OPEX ---
    d_maint_per_km = _diesel_maint_cost_per_km(lm)
    d_maint_year = d_maint_per_km * akm
    d_fuel_year = _diesel_fuel_cost(lm, akm, fpl)

    d_opex = [
        OpexLineItem(name="Maintenance", cost_chf_per_year=round(d_maint_year, 2)),
        OpexLineItem(name="Fuel", cost_chf_per_year=round(d_fuel_year, 2)),
    ]
    d_total_opex = sum(item.cost_chf_per_year for item in d_opex)

    diesel = CostSummary(
        capex_items=d_capex,
        total_annualized_capex_chf_per_year=round(d_total_capex, 2),
        opex_items=d_opex,
        total_opex_chf_per_year=round(d_total_opex, 2),
        total_annual_cost_chf_per_year=round(d_total_capex + d_total_opex, 2),
    )

    return FullComparisonResponse(
        annual_km=akm,
        interest_rate=ir,
        bus_length_m=lm,
        battery_capacity_kwh=bcap,
        charger_power_kw=cpw,
        annual_consumption_kwh=akwh,
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
