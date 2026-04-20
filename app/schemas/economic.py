"""
Pydantic schemas for the Economic Evaluation endpoints.

Covers investment costs (CAPEX), annualised costs, operating expenses (OPEX),
and a full electric-vs-diesel comparison for public transport buses.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Default parameters
# ---------------------------------------------------------------------------

class EconomicDefaultsResponse(BaseModel):
    """All default economic parameters loaded from ``config/economic_defaults.json``."""

    # Scenario / operational parameters
    annual_km: float = Field(..., description="Reference annual mileage [km/year].")
    interest_rate: float = Field(..., description="Default discount / interest rate.")
    bus_length_m: float = Field(..., description="Default bus length [m].")
    battery_capacity_kwh: float = Field(..., description="Default battery capacity [kWh].")
    charger_power_kw: float = Field(..., description="Default charger rated power [kW].")
    annual_consumption_kwh: float = Field(..., description="Default annual electricity consumption [kWh/year].")
    energy_price_per_kwh: float = Field(..., description="Default electricity price [CHF/kWh].")
    fuel_cost_per_l: float = Field(..., description="Default diesel fuel price [CHF/l].")

    # Lifetimes
    lifetime_bus: int = Field(..., description="Electric bus lifetime [years].")
    lifetime_battery: int = Field(..., description="Battery lifetime [years].")
    lifetime_charger: int = Field(..., description="Charger lifetime [years].")
    lifetime_connection: int = Field(..., description="Grid connection lifetime [years].")
    lifetime_diesel_bus: int = Field(..., description="Diesel bus lifetime [years].")

    # Equation coefficients — Battery
    battery_cost_per_kwh: float = Field(..., description="Battery unit cost coefficient [CHF/kWh].")

    # Equation coefficients — Electric bus body (a·m² + b·m + c)
    bus_no_batt_quad_coeff: float = Field(..., description="Electric bus body quadratic coeff (a).")
    bus_no_batt_lin_coeff: float = Field(..., description="Electric bus body linear coeff (b).")
    bus_no_batt_const: float = Field(..., description="Electric bus body constant (c) [CHF].")

    # Equation coefficients — Charger (a·kW + b)
    charger_cost_per_kw: float = Field(..., description="Charger cost slope [CHF/kW].")
    charger_cost_const: float = Field(..., description="Charger cost intercept [CHF].")

    # Equation coefficients — Grid connection (a·kW + b)
    grid_connection_fee_per_kw: float = Field(..., description="Grid connection fee slope [CHF/kW].")
    grid_connection_fee_const: float = Field(..., description="Grid connection fee intercept [CHF].")

    # Equation coefficients — Diesel bus (a·m² + b·m + c)
    diesel_bus_quad_coeff: float = Field(..., description="Diesel bus quadratic coeff (a).")
    diesel_bus_lin_coeff: float = Field(..., description="Diesel bus linear coeff (b).")
    diesel_bus_const: float = Field(..., description="Diesel bus constant (c) [CHF].")

    # Equation coefficients — Electric maintenance (a·m + b) [CHF/km]
    electric_maint_cost_per_m: float = Field(..., description="Electric maintenance slope [CHF/km per m].")
    electric_maint_cost_const: float = Field(..., description="Electric maintenance intercept [CHF/km].")

    # Equation coefficients — Diesel maintenance (a·m + b) [CHF/km]
    diesel_maint_cost_per_m: float = Field(..., description="Diesel maintenance slope [CHF/km per m].")
    diesel_maint_cost_const: float = Field(..., description="Diesel maintenance intercept [CHF/km].")

    # Equation coefficients — Diesel consumption (a·m + b) [l/km]
    diesel_consumption_per_m: float = Field(..., description="Diesel consumption slope [l/km per m].")
    diesel_consumption_const: float = Field(..., description="Diesel consumption intercept [l/km].")

    # Diesel-heating maintenance surcharge (fraction of electric maintenance OPEX)
    diesel_heating_maintenance_factor: float = Field(
        ...,
        description=(
            "Fraction of electric maintenance OPEX applied as diesel-heating "
            "maintenance surcharge (e.g. 0.10 = 10 %%)."
        ),
    )


# ---------------------------------------------------------------------------
# Investment cost responses
# ---------------------------------------------------------------------------

class BatteryCostResponse(BaseModel):
    """Investment cost for a traction battery."""
    capacity_kwh: float = Field(..., description="Battery capacity [kWh].")
    cost_chf: float = Field(..., description="Battery investment cost [CHF].")


class ElectricBusBodyCostResponse(BaseModel):
    """Investment cost for an electric bus body (without battery)."""
    length_m: float = Field(..., description="Bus length [m].")
    cost_chf: float = Field(
        ..., description="Bus body (without battery) investment cost [CHF]."
    )


class ChargerCostResponse(BaseModel):
    """Investment cost for a charging station."""
    power_kw: float = Field(..., description="Charger rated power [kW].")
    cost_chf: float = Field(..., description="Charger investment cost [CHF].")


class GridConnectionCostResponse(BaseModel):
    """One-off grid connection fee."""
    power_kw: float = Field(..., description="Connection power [kW].")
    cost_chf: float = Field(..., description="Grid connection fee [CHF].")


class DieselBusCostResponse(BaseModel):
    """Investment cost for a diesel bus."""
    length_m: float = Field(..., description="Bus length [m].")
    cost_chf: float = Field(..., description="Diesel bus investment cost [CHF].")


# ---------------------------------------------------------------------------
# Annualisation
# ---------------------------------------------------------------------------

class AnnualizedCostResponse(BaseModel):
    """Result of annualising an investment cost via the Capital Recovery Factor."""
    investment_cost_chf: float = Field(
        ..., description="Original one-off investment [CHF]."
    )
    lifetime_years: int = Field(..., description="Asset lifetime [years].")
    interest_rate: float = Field(..., description="Discount / interest rate.")
    capital_recovery_factor: float = Field(
        ..., description="CRF = q^t * i / (q^t - 1)."
    )
    annualized_cost_chf_per_year: float = Field(
        ..., description="Equivalent annual cost [CHF/year]."
    )


# ---------------------------------------------------------------------------
# OPEX – Electric bus
# ---------------------------------------------------------------------------

class ElectricMaintenanceCostResponse(BaseModel):
    """Annual maintenance cost for an electric bus."""
    shift_id: UUID = Field(..., description="Shift used to derive annual distance.")
    length_m: float = Field(..., description="Bus length [m].")
    annual_km: float = Field(..., description="Yearly distance derived from shift [km/year].")
    cost_per_km_chf: float = Field(
        ..., description="Maintenance unit cost [CHF/km]."
    )
    cost_per_year_chf: float = Field(
        ..., description="Annual maintenance cost [CHF/year]."
    )


class ElectricEnergyCostResponse(BaseModel):
    """Annual energy cost for an electric bus."""
    annual_consumption_kwh: float = Field(
        ..., description="Annual electricity consumption [kWh/year]."
    )
    energy_price_per_kwh: float = Field(
        ..., description="Electricity price [CHF/kWh]."
    )
    cost_per_year_chf: float = Field(
        ..., description="Annual energy cost [CHF/year]."
    )


# ---------------------------------------------------------------------------
# OPEX – Diesel bus
# ---------------------------------------------------------------------------

class DieselMaintenanceCostResponse(BaseModel):
    """Annual maintenance cost for a diesel bus."""
    shift_id: UUID = Field(..., description="Shift used to derive annual distance.")
    length_m: float = Field(..., description="Bus length [m].")
    annual_km: float = Field(..., description="Yearly distance derived from shift [km/year].")
    cost_per_km_chf: float = Field(
        ..., description="Maintenance unit cost [CHF/km]."
    )
    cost_per_year_chf: float = Field(
        ..., description="Annual maintenance cost [CHF/year]."
    )


class DieselConsumptionResponse(BaseModel):
    """Diesel fuel consumption rate."""
    length_m: float = Field(..., description="Bus length [m].")
    consumption_l_per_km: float = Field(
        ..., description="Fuel consumption [l/km]."
    )


class DieselFuelCostResponse(BaseModel):
    """Annual diesel fuel cost."""
    shift_id: UUID = Field(..., description="Shift used to derive annual distance.")
    length_m: float = Field(..., description="Bus length [m].")
    annual_km: float = Field(..., description="Yearly distance derived from shift [km/year].")
    fuel_cost_per_l: float = Field(..., description="Fuel price [CHF/l].")
    consumption_l_per_km: float = Field(
        ..., description="Fuel consumption [l/km]."
    )
    cost_per_year_chf: float = Field(
        ..., description="Annual fuel cost [CHF/year]."
    )


# ---------------------------------------------------------------------------
# Full comparison
# ---------------------------------------------------------------------------

class CapexLineItem(BaseModel):
    """Single CAPEX component with its annualised equivalent."""
    name: str
    investment_chf: float = Field(..., description="One-off investment [CHF].")
    lifetime_years: int = Field(..., description="Asset lifetime [years].")
    annualized_chf_per_year: float = Field(
        ..., description="Annualised cost [CHF/year]."
    )


class OpexLineItem(BaseModel):
    """Single OPEX component."""
    name: str
    cost_chf_per_year: float = Field(..., description="Annual cost [CHF/year].")


class CostSummary(BaseModel):
    """Aggregated cost breakdown for one powertrain type.

    When CAPEX is excluded (``include_capex=false``), ``capex_items`` and
    ``total_annualized_capex_chf_per_year`` are ``null`` and
    ``total_annual_cost_chf_per_year`` reflects OPEX only.
    """

    capex_items: Optional[list[CapexLineItem]] = Field(
        default=None,
        description="CAPEX line items. Null when CAPEX is excluded.",
    )
    total_annualized_capex_chf_per_year: Optional[float] = Field(
        default=None,
        description="Sum of annualised CAPEX. Null when CAPEX is excluded.",
    )
    opex_items: list[OpexLineItem]
    total_opex_chf_per_year: float
    total_annual_cost_chf_per_year: float


class FullComparisonResponse(BaseModel):
    """Side-by-side annual cost comparison: electric vs diesel bus.

    When ``include_capex=false``, ``battery_capacity_kwh`` and
    ``charger_power_kw`` are ``null`` (they are only needed for CAPEX
    regressions) and ``annual_saving_chf`` reflects OPEX only.
    """

    shift_id: UUID = Field(..., description="Shift used to derive annual distance.")
    annual_km: float = Field(..., description="Yearly distance derived from shift [km/year].")
    interest_rate: float
    bus_length_m: float
    battery_capacity_kwh: Optional[float] = Field(
        default=None,
        description="Battery capacity [kWh]. Null when CAPEX is excluded.",
    )
    charger_power_kw: Optional[float] = Field(
        default=None,
        description="Charger rated power [kW]. Null when CAPEX is excluded.",
    )
    annual_consumption_kwh: float
    energy_price_per_kwh: float
    fuel_cost_per_l: float

    electric: CostSummary
    diesel: CostSummary

    annual_saving_chf: float = Field(
        ...,
        description=(
            "diesel total − electric total [CHF/year]. "
            "Positive means the electric option is cheaper. "
            "Reflects OPEX only when CAPEX is excluded."
        ),
    )


# ---------------------------------------------------------------------------
# Yearly-analysis cost breakdown (mixed e-bus vs full-diesel comparator)
# ---------------------------------------------------------------------------

class YearlyCostScenario(BaseModel):
    """Per-scenario cost breakdown within a yearly analysis."""

    temperature_celsius: float
    occurrences: int
    daily_electric_kwh: float
    daily_distance_km: float
    daily_diesel_heating_liters: float
    annual_electric_kwh: float
    annual_distance_km: float
    annual_diesel_heating_liters: float
    annual_electric_energy_cost_chf: float
    annual_electric_maint_cost_chf: float
    annual_diesel_heating_fuel_cost_chf: float
    annual_diesel_heating_maint_cost_chf: float


class YearlyCostAssumptions(BaseModel):
    """Economic assumptions used for a yearly cost calculation."""

    energy_price_per_kwh: float
    fuel_cost_per_l: float
    interest_rate: float
    bus_length_m: float
    yearly_electric_kwh: float
    yearly_distance_km: float
    yearly_diesel_heating_liters: float
    yearly_diesel_heating_fuel_kwh: float
    diesel_heating_maintenance_factor: float = Field(
        description=(
            "Fraction of electric maintenance OPEX applied as diesel-heating "
            "maintenance surcharge (e.g. 0.10 = 10 %%)."
        ),
    )
    electric_maint_cost_per_km_chf: float
    diesel_comparator_maint_cost_per_km_chf: float
    diesel_comparator_consumption_l_per_km: float


class YearlyCostResponse(BaseModel):
    """Yearly cost comparison: mixed e-bus vs full-diesel comparator.

    The ``ebus`` branch represents the real vehicle under analysis.
    For ``auxiliary_heating_type = "diesel"`` it includes both battery-side
    electric costs and diesel-heating costs.  For ``"default"`` the diesel-
    heating items are zero.

    The ``diesel_comparator`` branch is a legacy full-diesel-bus reference
    and is always computed from distance-based regression formulas.
    """

    yearly_analysis_id: UUID
    auxiliary_heating_type: str
    annual_km: float

    ebus: CostSummary
    diesel_comparator: CostSummary

    annual_saving_chf: float = Field(
        description=(
            "diesel_comparator.total − ebus.total [CHF/year]. "
            "Positive means the e-bus is cheaper."
        ),
    )
    assumptions: YearlyCostAssumptions
    scenarios: list[YearlyCostScenario]
