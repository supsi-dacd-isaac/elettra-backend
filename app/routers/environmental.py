"""
Environmental Calculations router.

Proxies the Energie Schweiz LCA (Life Cycle Analysis) API to provide
emission and environmental data for passenger and freight vehicles in
Switzerland.  Also provides shift-level yearly distance and yearly
environmental impact calculations.

Remote API docs: https://d2pqfjzfn7r7rw.cloudfront.net/index.html
"""

import logging
from typing import Dict, List, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import get_cached_settings
from app.database import get_async_session
from app.models import (
    Buses, BusesLcaData, BusesModels,
    GtfsStopsTimes, GtfsTrips, Shifts, ShiftsStructures, Users,
)
from app.schemas.lca import (
    DataVersion,
    ElectricityMix,
    FuelBlend,
    LcaVehicleInfo,
    RecurrenceType,
    ShiftTripDistance,
    ShiftYearlyDistanceResponse,
    VehicleComplete,
    VehicleImpact,
    VehicleMass,
    VehicleMinimal,
    YearlyEmissionBreakdown,
    YearlyImpactResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Timeout (seconds) for requests to the upstream LCA API
_LCA_TIMEOUT = 30.0


def _lca_base_url() -> str:
    """Return the configured LCA API base URL (without trailing slash)."""
    return get_cached_settings().lca_api_base_url.rstrip("/")


async def _lca_get(path: str, params: Optional[Dict[str, str]] = None) -> httpx.Response:
    """
    Perform an async GET against the upstream LCA API and return the raw
    ``httpx.Response``.  Raises ``HTTPException`` on network or HTTP errors.
    """
    url = f"{_lca_base_url()}{path}"
    try:
        async with httpx.AsyncClient(timeout=_LCA_TIMEOUT) as client:
            resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp
    except httpx.TimeoutException:
        logger.error("LCA API timeout: GET %s", url)
        raise HTTPException(status_code=504, detail="Upstream LCA API request timed out")
    except httpx.HTTPStatusError as exc:
        logger.error("LCA API HTTP error: %s %s", exc.response.status_code, url)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Upstream LCA API error: {exc.response.text[:500]}",
        )
    except httpx.RequestError as exc:
        logger.error("LCA API request error: %s – %s", url, exc)
        raise HTTPException(status_code=502, detail=f"Cannot reach upstream LCA API: {exc}")


def _extract_query_params(request: Request) -> Dict[str, str]:
    """
    Extract all query parameters from the incoming request so they can be
    forwarded transparently to the upstream LCA API.  This allows the
    frontend to pass any vehicle-specific calculation parameter (e.g.
    ``lifetimeKilometers``, ``passengers``, ``dataVersion``, …) without
    requiring explicit FastAPI ``Query`` declarations for every possible key.
    """
    return dict(request.query_params)


_RECURRENCE_DAYS = {
    RecurrenceType.weekly_once: 52,
    RecurrenceType.weekdays: 260,     # 52 × 5
    RecurrenceType.daily: 364,        # 52 × 7
}


# ========================================================================== #
# Shift yearly distance  (GET2)
# ========================================================================== #

@router.get(
    "/shifts/{shift_id}/yearly-distance",
    response_model=ShiftYearlyDistanceResponse,
    summary="Calculate shift yearly distance",
    description=(
        "Computes the total daily distance of a shift (sum of its trip "
        "distances derived from GTFS ``shape_dist_traveled``) and projects "
        "it to a yearly figure based on the chosen recurrence pattern.\n\n"
        "**Recurrence options:**\n"
        "- ``weekly_once`` – shift runs 1 day/week → 52 days/year\n"
        "- ``weekdays`` – shift runs Mon–Fri → 260 days/year\n"
        "- ``daily`` – shift runs every day → 364 days/year\n"
        "- ``custom`` – provide ``custom_days`` (number of operating days "
        "per year)\n\n"
        "Trips without shape data (e.g. depot trips) are included in the "
        "breakdown with ``distance_m = null`` but do not count towards the "
        "total."
    ),
)
async def get_shift_yearly_distance(
    shift_id: UUID,
    recurrence: RecurrenceType = Query(
        ...,
        description="How often the shift repeats.",
    ),
    custom_days: Optional[int] = Query(
        None,
        ge=1,
        le=366,
        description="Number of operating days/year (required when recurrence=custom).",
    ),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    # --- validate recurrence -------------------------------------------------
    if recurrence == RecurrenceType.custom:
        if custom_days is None:
            raise HTTPException(
                status_code=422,
                detail="custom_days is required when recurrence=custom",
            )
        days_per_year = custom_days
    else:
        days_per_year = _RECURRENCE_DAYS[recurrence]

    # --- fetch shift ---------------------------------------------------------
    shift = await db.get(Shifts, shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")

    # --- trip distances via shape_dist_traveled ------------------------------
    stmt = (
        select(
            ShiftsStructures.trip_id,
            ShiftsStructures.sequence_number,
            GtfsTrips.trip_id.label("gtfs_trip_id"),
            func.max(GtfsStopsTimes.shape_dist_traveled).label("max_dist"),
        )
        .join(GtfsTrips, GtfsTrips.id == ShiftsStructures.trip_id)
        .outerjoin(GtfsStopsTimes, GtfsStopsTimes.trip_id == GtfsTrips.id)
        .where(ShiftsStructures.shift_id == shift_id)
        .group_by(
            ShiftsStructures.trip_id,
            ShiftsStructures.sequence_number,
            GtfsTrips.trip_id,
        )
        .order_by(ShiftsStructures.sequence_number)
    )

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Shift has no trips in its structure",
        )

    # --- build response ------------------------------------------------------
    trips: List[ShiftTripDistance] = []
    daily_distance_m = 0.0

    for trip_uuid, seq, gtfs_tid, max_dist in rows:
        dist = float(max_dist) if max_dist is not None else None
        trips.append(
            ShiftTripDistance(
                trip_id=trip_uuid,
                gtfs_trip_id=gtfs_tid,
                sequence_number=seq,
                distance_m=dist,
            )
        )
        if dist is not None:
            daily_distance_m += dist

    yearly_distance_m = daily_distance_m * days_per_year

    return ShiftYearlyDistanceResponse(
        shift_id=shift_id,
        shift_name=shift.name,
        daily_distance_m=round(daily_distance_m, 2),
        daily_distance_km=round(daily_distance_m / 1000.0, 3),
        recurrence=recurrence,
        recurrence_days=days_per_year,
        yearly_distance_m=round(yearly_distance_m, 2),
        yearly_distance_km=round(yearly_distance_m / 1000.0, 3),
        trips=trips,
    )


# ========================================================================== #
# Shift yearly impact  (GET1)
# ========================================================================== #

_SIZE_PREFIXES = ("9m", "13m", "18m")


def _extract_size_prefix(model_size: str | None) -> str | None:
    """
    Return the canonical LCA size prefix (``9m``, ``13m``, ``18m``) from a
    ``buses_models.specs.size`` value, or ``None`` when no match is found.
    """
    if not model_size or model_size in ("None", "N/A"):
        return None
    for prefix in _SIZE_PREFIXES:
        if model_size.startswith(prefix):
            return prefix
    return None


_INDICATOR_UNITS: Dict[str, str] = {
    "primaryEnergy": "MJ oil-eq",
    "primaryEnergyNonRenewable": "MJ oil-eq",
    "gwp100a": "g CO\u2082-eq",
    "pm10": "mg PM10",
    "pm25": "mg PM2.5",
    "nmvoc": "mg NMVOC",
    "nox": "mg NOx",
    "ubp21": "UBP",
}


def _scale_breakdown(
    indicator: str, breakdown: dict, factor: float
) -> YearlyEmissionBreakdown:
    """Multiply every phase value by *factor* and return a YearlyEmissionBreakdown."""
    phases = (
        "direct", "directNonExhaust", "energyChain",
        "maintenance", "vehicle", "endOfLife", "infrastructure",
    )
    scaled: dict[str, float | None] = {}
    total = 0.0
    for phase in phases:
        raw = breakdown.get(phase)
        if raw is not None:
            val = float(raw) * factor
            scaled[phase] = round(val, 6)
            total += val
        else:
            scaled[phase] = None
    scaled["total"] = round(total, 6)
    unit = _INDICATOR_UNITS.get(indicator, "unknown")
    return YearlyEmissionBreakdown(unit=unit, **scaled)


@router.get(
    "/shifts/{shift_id}/yearly-impact",
    response_model=YearlyImpactResponse,
    summary="Calculate shift yearly environmental impact",
    description=(
        "End-to-end yearly environmental impact for a bus shift.\n\n"
        "**Steps performed internally:**\n"
        "1. Resolve ``shift → bus → bus_model → specs.size``.\n"
        "2. Match the size prefix (9m / 13m / 18m) against the active "
        "``buses_lca_data`` row.\n"
        "3. Call the upstream LCA API ``/vehicle/{lca_id}/impact`` to get "
        "per-pkm impact values.\n"
        "4. Compute the yearly distance from the shift's trips (same logic "
        "as the ``/yearly-distance`` endpoint).\n"
        "5. Multiply: ``yearly_impact = impact_per_pkm × yearly_distance_km "
        "× passengers``.\n\n"
        "Depot legs are excluded from the distance (0 passengers on board)."
    ),
)
async def get_shift_yearly_impact(
    shift_id: UUID,
    recurrence: RecurrenceType = Query(
        ..., description="How often the shift repeats."
    ),
    passengers: float = Query(
        ..., gt=0, description="Average number of passengers on the bus."
    ),
    custom_days: int | None = Query(
        None,
        ge=1,
        le=366,
        description="Number of operating days/year (required when recurrence=custom).",
    ),
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
):
    # --- recurrence -----------------------------------------------------------
    if recurrence == RecurrenceType.custom:
        if custom_days is None:
            raise HTTPException(
                status_code=422,
                detail="custom_days is required when recurrence=custom",
            )
        days_per_year = custom_days
    else:
        days_per_year = _RECURRENCE_DAYS[recurrence]

    # --- shift → bus → bus_model → specs.size --------------------------------
    shift = await db.get(Shifts, shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")
    if shift.bus_id is None:
        raise HTTPException(status_code=422, detail="Shift has no bus assigned")

    bus = await db.get(Buses, shift.bus_id)
    if bus is None:
        raise HTTPException(status_code=404, detail="Bus not found")
    if bus.bus_model_id is None:
        raise HTTPException(status_code=422, detail="Bus has no model assigned")

    bus_model = await db.get(BusesModels, bus.bus_model_id)
    if bus_model is None:
        raise HTTPException(status_code=404, detail="Bus model not found")

    model_size = (bus_model.specs or {}).get("size")
    size_prefix = _extract_size_prefix(str(model_size) if model_size is not None else None)
    if size_prefix is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot determine LCA size category from bus model "
                f"'{bus_model.name}' (specs.size='{model_size}'). "
                f"Expected a value starting with one of: {', '.join(_SIZE_PREFIXES)}."
            ),
        )

    # --- match active buses_lca_data row -------------------------------------
    lca_stmt = (
        select(BusesLcaData)
        .where(
            BusesLcaData.active.is_(True),
            BusesLcaData.size.startswith(size_prefix),
        )
    )
    lca_result = await db.execute(lca_stmt)
    lca_row = lca_result.scalars().first()

    if lca_row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No active buses_lca_data entry found for size prefix "
                f"'{size_prefix}'."
            ),
        )

    # --- yearly distance (same logic as GET2) --------------------------------
    dist_stmt = (
        select(
            func.max(GtfsStopsTimes.shape_dist_traveled).label("max_dist"),
        )
        .select_from(ShiftsStructures)
        .join(GtfsTrips, GtfsTrips.id == ShiftsStructures.trip_id)
        .outerjoin(GtfsStopsTimes, GtfsStopsTimes.trip_id == GtfsTrips.id)
        .where(ShiftsStructures.shift_id == shift_id)
        .group_by(ShiftsStructures.trip_id)
    )
    dist_result = await db.execute(dist_stmt)
    daily_distance_m = sum(
        float(row.max_dist) for row in dist_result.all() if row.max_dist is not None
    )
    daily_distance_km = daily_distance_m / 1000.0
    yearly_distance_km = daily_distance_km * days_per_year

    if yearly_distance_km == 0:
        raise HTTPException(
            status_code=422,
            detail="Shift has zero computable distance (no shape_dist_traveled data).",
        )

    # --- call upstream LCA API for per-pkm impact ----------------------------
    lca_vehicle_id = str(lca_row.id)
    resp = await _lca_get(f"/vehicle/{lca_vehicle_id}/impact")
    impact_per_pkm: dict = resp.json()

    # --- compute yearly impact -----------------------------------------------
    factor = yearly_distance_km * passengers

    yearly_impact: dict[str, YearlyEmissionBreakdown] = {}
    for indicator, breakdown in impact_per_pkm.items():
        if isinstance(breakdown, dict):
            yearly_impact[indicator] = _scale_breakdown(indicator, breakdown, factor)

    return YearlyImpactResponse(
        shift_id=shift_id,
        shift_name=shift.name,
        lca_vehicle=LcaVehicleInfo(
            lca_vehicle_id=lca_row.id,
            lca_vehicle_name=lca_row.name,
            lca_size=lca_row.size,
            powertrain=lca_row.powertrain,
            passenger_capacity=(
                float(lca_row.passenger_capacity)
                if lca_row.passenger_capacity is not None
                else None
            ),
        ),
        bus_model_name=bus_model.name,
        bus_model_size=str(model_size) if model_size is not None else None,
        passengers=passengers,
        recurrence=recurrence,
        recurrence_days=days_per_year,
        daily_distance_km=round(daily_distance_km, 3),
        yearly_distance_km=round(yearly_distance_km, 3),
        functional_unit=lca_row.functional_unit,
        impact_per_unit=VehicleImpact(**impact_per_pkm),
        yearly_impact=yearly_impact,
    )


# ========================================================================== #
# Vehicle endpoints  (LCA proxy)
# ========================================================================== #

@router.get(
    "/vehicles",
    response_model=List[VehicleMinimal],
    summary="List all LCA vehicles",
    description=(
        "Returns the full catalogue of vehicles available in the Energie "
        "Schweiz LCA database. Supports an optional ``dataVersion`` query "
        "parameter to pin a specific data snapshot."
    ),
)
async def list_vehicles(
    request: Request,
    current_user: Users = Depends(get_current_user),
):
    params = _extract_query_params(request)
    resp = await _lca_get("/vehicle", params=params or None)
    return resp.json()


@router.get(
    "/vehicles/{vehicle_id}",
    response_model=VehicleComplete,
    summary="Get a single LCA vehicle",
    description=(
        "Returns complete information for a vehicle including all tuneable "
        "calculation parameters with their default, min, and max values."
    ),
)
async def get_vehicle(
    vehicle_id: str,
    request: Request,
    current_user: Users = Depends(get_current_user),
):
    params = _extract_query_params(request)
    resp = await _lca_get(f"/vehicle/{vehicle_id}", params=params or None)
    return resp.json()


@router.get(
    "/vehicles/{vehicle_id}/mass",
    response_model=VehicleMass,
    summary="Calculate vehicle mass",
    description=(
        "Calculates the mass composition of a vehicle. All calculation "
        "parameters (e.g. ``lifetimeKilometers``, ``batteryChemistry``, …) "
        "are optional query parameters; default values are used when omitted."
    ),
)
async def get_vehicle_mass(
    vehicle_id: str,
    request: Request,
    current_user: Users = Depends(get_current_user),
):
    params = _extract_query_params(request)
    resp = await _lca_get(f"/vehicle/{vehicle_id}/mass", params=params or None)
    return resp.json()


@router.get(
    "/vehicles/{vehicle_id}/impact",
    response_model=VehicleImpact,
    summary="Calculate vehicle environmental impact",
    description=(
        "Calculates the life-cycle environmental impact of a vehicle across "
        "multiple indicators (GWP, primary energy, particulate matter, NOx, "
        "NMVOC, UBP'21). All calculation parameters are optional query "
        "parameters; default values from the vehicle data are used when "
        "omitted.\n\n"
        "**Common parameters**: ``lifetimeKilometers``, ``kilometersPerYear``, "
        "``passengers``, ``fuelConsumption``, ``fuelBlend``, "
        "``electricityConsumption``, ``electricityMix``, "
        "``electricEnergyStored``, ``batteryLifetimeReplacements``, "
        "``batteryChemistry``, ``distance``, ``vkm``, ``dataVersion``."
    ),
)
async def get_vehicle_impact(
    vehicle_id: str,
    request: Request,
    current_user: Users = Depends(get_current_user),
):
    params = _extract_query_params(request)
    resp = await _lca_get(f"/vehicle/{vehicle_id}/impact", params=params or None)
    return resp.json()


# ========================================================================== #
# Electricity mix
# ========================================================================== #

@router.get(
    "/electricity-mixes",
    response_model=List[ElectricityMix],
    summary="List electricity mixes",
    description=(
        "Returns all available Swiss electricity mixes (consumer physical, "
        "consumer with GO, renewables)."
    ),
)
async def list_electricity_mixes(
    request: Request,
    current_user: Users = Depends(get_current_user),
):
    params = _extract_query_params(request)
    resp = await _lca_get("/electricitymix", params=params or None)
    return resp.json()


# ========================================================================== #
# Fuel blends
# ========================================================================== #

@router.get(
    "/fuel-blends",
    response_model=List[FuelBlend],
    summary="List fuel blends",
    description=(
        "Returns all available fuel blends with their component composition "
        "(e.g. diesel average, gasoline average, E10, E85, hydrogen, …)."
    ),
)
async def list_fuel_blends(
    request: Request,
    current_user: Users = Depends(get_current_user),
):
    params = _extract_query_params(request)
    resp = await _lca_get("/fuelblend", params=params or None)
    return resp.json()


# ========================================================================== #
# Data versions
# ========================================================================== #

@router.get(
    "/data-versions",
    response_model=List[DataVersion],
    summary="List data versions",
    description=(
        "Returns all available data versions. A data version can be passed "
        "as a ``dataVersion`` query parameter to any other endpoint to "
        "retrieve historical data."
    ),
)
async def list_data_versions(
    request: Request,
    current_user: Users = Depends(get_current_user),
):
    params = _extract_query_params(request)
    resp = await _lca_get("/dataversion", params=params or None)
    return resp.json()
