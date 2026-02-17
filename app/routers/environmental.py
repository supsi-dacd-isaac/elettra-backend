"""
Environmental Calculations router.

Proxies the Energie Schweiz LCA (Life Cycle Analysis) API to provide
emission and environmental data for passenger and freight vehicles in
Switzerland.

Remote API docs: https://d2pqfjzfn7r7rw.cloudfront.net/index.html
"""

import logging
from typing import Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.auth import get_current_user
from app.core.config import get_cached_settings
from app.models import Users
from app.schemas.lca import (
    DataVersion,
    ElectricityMix,
    FuelBlend,
    VehicleComplete,
    VehicleImpact,
    VehicleMass,
    VehicleMinimal,
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


# ========================================================================== #
# Vehicle endpoints
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
