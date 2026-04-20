"""
Pydantic schemas for the Energie Schweiz LCA (Life Cycle Analysis) API
and the Elettra environmental calculation endpoints.

Remote API docs: https://d2pqfjzfn7r7rw.cloudfront.net/index.html
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.shift_distance import RecurrenceType


# ---------------------------------------------------------------------------
# Vehicle schemas
# ---------------------------------------------------------------------------

class ApiParameter(BaseModel):
    """A tuneable parameter exposed by a vehicle (numeric, text, or boolean)."""
    type: str
    defaultValue: Any = None
    minValue: Optional[float] = None
    maxValue: Optional[float] = None
    allowedValues: Optional[List[str]] = None


class VehicleMinimal(BaseModel):
    """Compact vehicle representation returned by GET /vehicle."""
    id: str
    sourceId: Optional[str] = None
    name: Optional[str] = None
    vehicleType: Optional[str] = None
    vehicleSubtype: Optional[str] = None
    powertrain: Optional[str] = None
    description: Optional[str] = None
    filteringDescription: Optional[str] = None
    geography: Optional[str] = None
    trafficCharacteristics: Optional[str] = None
    functionalUnit: Optional[str] = None
    filter1: Optional[str] = None
    filter2: Optional[str] = None
    filter3: Optional[str] = None
    speed: Optional[str] = None
    powerClass: Optional[str] = None
    size: Optional[str] = None
    year: Optional[str] = None
    emissionStandard: Optional[str] = None

    model_config = {"extra": "allow"}


class VehicleComplete(BaseModel):
    """
    Full vehicle object returned by GET /vehicle/{id}.

    Includes all static properties and tuneable ApiParameter fields.
    Because different vehicle types expose different parameter sets we use
    ``extra = "allow"`` so unknown fields are preserved in the response.
    """
    id: str
    sourceId: Optional[str] = None
    name: Optional[str] = None
    vehicleType: Optional[str] = None
    vehicleSubtype: Optional[str] = None
    powertrain: Optional[str] = None
    description: Optional[str] = None
    filteringDescription: Optional[str] = None
    geography: Optional[str] = None
    trafficCharacteristics: Optional[str] = None
    functionalUnit: Optional[str] = None
    filter1: Optional[str] = None
    filter2: Optional[str] = None
    filter3: Optional[str] = None
    speed: Optional[str] = None
    powerClass: Optional[str] = None
    size: Optional[str] = None
    year: Optional[str] = None
    emissionStandard: Optional[str] = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Vehicle mass
# ---------------------------------------------------------------------------

class VehicleMass(BaseModel):
    """Response from GET /vehicle/{id}/mass."""
    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Vehicle impact (emissions)
# ---------------------------------------------------------------------------

class EmissionBreakdown(BaseModel):
    """
    Breakdown of a single environmental indicator across lifecycle phases.
    """
    direct: Optional[float] = None
    directNonExhaust: Optional[float] = None
    energyChain: Optional[float] = None
    maintenance: Optional[float] = None
    vehicle: Optional[float] = None
    endOfLife: Optional[float] = None
    infrastructure: Optional[float] = None

    model_config = {"extra": "allow"}


class VehicleImpact(BaseModel):
    """
    Response from GET /vehicle/{id}/impact.

    Keys are environmental indicator names (e.g. gwp100a, primaryEnergy, …).
    """
    primaryEnergy: Optional[EmissionBreakdown] = None
    primaryEnergyNonRenewable: Optional[EmissionBreakdown] = None
    gwp100a: Optional[EmissionBreakdown] = None
    pm10: Optional[EmissionBreakdown] = None
    pm25: Optional[EmissionBreakdown] = None
    nmvoc: Optional[EmissionBreakdown] = None
    nox: Optional[EmissionBreakdown] = None
    ubp21: Optional[EmissionBreakdown] = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Electricity mix
# ---------------------------------------------------------------------------

class ElectricityMix(BaseModel):
    """An electricity mix as returned by GET /electricitymix."""
    id: str
    sourceId: Optional[str] = None
    name: Optional[str] = None
    hydro: Optional[float] = None
    solar: Optional[float] = None
    wind: Optional[float] = None
    biomass: Optional[float] = None
    biogas: Optional[float] = None
    waste: Optional[float] = None
    nuclear: Optional[float] = None
    oil: Optional[float] = None
    naturalGas: Optional[float] = None
    coal: Optional[float] = None
    imported: Optional[float] = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Fuel blend
# ---------------------------------------------------------------------------

class FuelComponent(BaseModel):
    """A single fuel component within a blend."""
    name: Optional[str] = None
    density: Optional[float] = None
    lowerHeatingValueKiloWattHour: Optional[float] = None
    lowerHeatingValueMegaJoule: Optional[float] = None
    co2Fossil: Optional[float] = None
    co2Biogenic: Optional[float] = None
    so2: Optional[float] = None

    model_config = {"extra": "allow"}


class FuelProperty(BaseModel):
    """A proportion + component pair inside a fuel blend."""
    proportion: Optional[float] = None
    component: Optional[FuelComponent] = None

    model_config = {"extra": "allow"}


class FuelBlend(BaseModel):
    """A fuel blend as returned by GET /fuelblend."""
    id: str
    sourceId: Optional[str] = None
    name: Optional[str] = None
    components: Optional[List[FuelProperty]] = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Data version
# ---------------------------------------------------------------------------

class DataVersion(BaseModel):
    """A data version entry as returned by GET /dataversion."""
    id: str
    dataVersion: Optional[int] = None
    name: Optional[str] = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Shift yearly distance
# ---------------------------------------------------------------------------

class ShiftTripDistance(BaseModel):
    """Distance breakdown for a single trip inside a shift."""
    trip_id: UUID
    gtfs_trip_id: Optional[str] = None
    sequence_number: int
    distance_m: Optional[float] = Field(
        None,
        description="Trip distance in metres (from shape_dist_traveled). "
                    "Null when the trip has no shape data (e.g. depot trips).",
    )


class ShiftYearlyDistanceResponse(BaseModel):
    """Response for GET /shifts/{shift_id}/yearly-distance."""
    shift_id: UUID
    shift_name: str
    daily_distance_m: float = Field(
        ..., description="Sum of all trip distances in the shift (metres)."
    )
    daily_distance_km: float = Field(
        ..., description="Same value expressed in kilometres."
    )
    recurrence: RecurrenceType
    recurrence_days: int = Field(
        ..., description="Number of operating days in a year used for the calculation."
    )
    yearly_distance_m: float
    yearly_distance_km: float
    trips: List[ShiftTripDistance] = Field(
        ..., description="Per-trip distance breakdown."
    )


# ---------------------------------------------------------------------------
# Yearly environmental impact
# ---------------------------------------------------------------------------

class LcaVehicleInfo(BaseModel):
    """Matched LCA vehicle used for the impact calculation."""
    lca_vehicle_id: UUID
    lca_vehicle_name: str
    lca_size: Optional[str] = None
    powertrain: Optional[str] = None
    passenger_capacity: Optional[float] = None


class YearlyEmissionBreakdown(BaseModel):
    """
    Yearly emissions for a single environmental indicator, broken down by
    lifecycle phase.  Each value = impact_per_pkm × yearly_distance_km × passengers.
    """
    unit: str = Field(
        ..., description="Unit of measure for every numeric value in this breakdown."
    )
    direct: Optional[float] = None
    directNonExhaust: Optional[float] = None
    energyChain: Optional[float] = None
    maintenance: Optional[float] = None
    vehicle: Optional[float] = None
    endOfLife: Optional[float] = None
    infrastructure: Optional[float] = None
    total: Optional[float] = Field(
        None, description="Sum of all lifecycle phases for this indicator."
    )

    model_config = {"extra": "allow"}


class YearlyImpactResponse(BaseModel):
    """Response for GET /shifts/{shift_id}/yearly-impact."""
    shift_id: UUID
    shift_name: str
    lca_vehicle: LcaVehicleInfo
    bus_model_name: str
    bus_model_size: Optional[str] = None
    passengers: float
    recurrence: RecurrenceType
    recurrence_days: int
    daily_distance_km: float
    yearly_distance_km: float
    functional_unit: str = Field(
        ..., description="Unit of the upstream impact data (e.g. pkm)."
    )
    impact_per_unit: VehicleImpact = Field(
        ..., description="Raw per-pkm impact from the LCA API."
    )
    yearly_impact: Dict[str, YearlyEmissionBreakdown] = Field(
        ...,
        description=(
            "Yearly absolute impact for each indicator. "
            "Computed as impact_per_pkm × yearly_distance_km × passengers."
        ),
    )


# ---------------------------------------------------------------------------
# Yearly-analysis emissions (mixed e-bus vs full-diesel comparator)
# ---------------------------------------------------------------------------

class YearlyEmissionsIndicator(BaseModel):
    """Breakdown of one indicator for the mixed e-bus branch."""

    unit: str
    electric: float = Field(
        description="Contribution from battery-side electricity consumption.",
    )
    diesel_heating: float = Field(
        description="Contribution from diesel-heating fuel (zero for default mode).",
    )
    total: float


class YearlyEmissionsComparatorIndicator(BaseModel):
    """Single indicator for the full-diesel comparator."""

    unit: str
    total: float


class YearlyEmissionsScenario(BaseModel):
    """Per-scenario emissions for a yearly analysis."""

    temperature_celsius: float
    occurrences: int
    annual_electric_kwh: float
    annual_diesel_heating_liters: float
    gwp100a_electric_kg: float = Field(
        description="CO₂-eq from electricity [kg/year].",
    )
    gwp100a_diesel_heating_kg: float = Field(
        description="CO₂-eq from diesel-heating fuel [kg/year].",
    )
    gwp100a_total_kg: float = Field(
        description="Total CO₂-eq for this scenario [kg/year].",
    )


class YearlyEmissionsAssumptions(BaseModel):
    """Emission factor assumptions used for a yearly emissions calculation."""

    auxiliary_heating_type: str
    yearly_electric_kwh: float
    yearly_diesel_heating_liters: float
    yearly_diesel_heating_fuel_kwh: float
    yearly_distance_km: float
    electricity_gwp100a_g_per_kwh: float
    diesel_heating_gwp100a_g_per_liter: float
    diesel_bus_gwp100a_g_per_liter: float
    diesel_comparator_consumption_l_per_km: float


class YearlyEmissionsResponse(BaseModel):
    """Yearly emissions comparison: mixed e-bus vs full-diesel comparator.

    For ``auxiliary_heating_type = "diesel"`` the ``ebus`` indicators
    include both an electric contribution (from battery-side kWh) and a
    diesel-heating contribution (from heater fuel liters).  For
    ``"default"`` the diesel-heating contribution is zero.

    The ``diesel_comparator`` uses legacy distance-based diesel-bus
    factors and is always separate from the mixed e-bus branch.
    """

    yearly_analysis_id: UUID
    auxiliary_heating_type: str
    annual_km: float

    ebus: Dict[str, YearlyEmissionsIndicator]
    diesel_comparator: Dict[str, YearlyEmissionsComparatorIndicator]

    annual_saving: Dict[str, float] = Field(
        description=(
            "Per-indicator saving: diesel_comparator.total − ebus.total. "
            "Positive means the e-bus emits less."
        ),
    )
    assumptions: YearlyEmissionsAssumptions
    scenarios: List[YearlyEmissionsScenario]
