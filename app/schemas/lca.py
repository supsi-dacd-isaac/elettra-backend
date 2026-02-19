"""
Pydantic schemas for the Energie Schweiz LCA (Life Cycle Analysis) API
and the Elettra environmental calculation endpoints.

Remote API docs: https://d2pqfjzfn7r7rw.cloudfront.net/index.html
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


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

class RecurrenceType(str, Enum):
    """How often a shift repeats within a week."""
    weekly_once = "weekly_once"   # 1 day/week  → ×52
    weekdays = "weekdays"        # 5 days/week → ×260
    daily = "daily"              # 7 days/week → ×364
    custom = "custom"            # N days/year (user-supplied)


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
