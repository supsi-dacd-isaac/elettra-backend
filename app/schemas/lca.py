"""
Pydantic schemas for the Energie Schweiz LCA (Life Cycle Analysis) API.

Remote API docs: https://d2pqfjzfn7r7rw.cloudfront.net/index.html
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
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
