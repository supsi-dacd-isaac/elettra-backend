"""
Pydantic schemas for the Economic Evaluation endpoints.

Covers investment costs (CAPEX), annualised costs, operating expenses (OPEX),
and a full electric-vs-diesel comparison for public transport buses.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
    length_m: float = Field(..., description="Bus length [m].")
    annual_km: float = Field(..., description="Annual mileage [km/year].")
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
    length_m: float = Field(..., description="Bus length [m].")
    annual_km: float = Field(..., description="Annual mileage [km/year].")
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
    length_m: float = Field(..., description="Bus length [m].")
    annual_km: float = Field(..., description="Annual mileage [km/year].")
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
    """Aggregated cost breakdown for one powertrain type."""
    capex_items: list[CapexLineItem]
    total_annualized_capex_chf_per_year: float
    opex_items: list[OpexLineItem]
    total_opex_chf_per_year: float
    total_annual_cost_chf_per_year: float


class FullComparisonResponse(BaseModel):
    """Side-by-side annual cost comparison: electric vs diesel bus."""

    # Echoed input parameters
    annual_km: float
    interest_rate: float
    bus_length_m: float
    battery_capacity_kwh: float
    charger_power_kw: float
    annual_consumption_kwh: float
    energy_price_per_kwh: float
    fuel_cost_per_l: float

    electric: CostSummary
    diesel: CostSummary

    annual_saving_chf: float = Field(
        ...,
        description=(
            "diesel total − electric total [CHF/year]. "
            "Positive means the electric option is cheaper."
        ),
    )
