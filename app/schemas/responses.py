# Custom response schemas with relationships and specialized data
from __future__ import annotations
from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict

from app.schemas.database import GtfsTripsRead


class PredictionSubmitResponse(BaseModel):
    prediction_run_ids: list[UUID]


class OptimizationSubmitResponse(BaseModel):
    optimization_run_id: UUID


class OptimizationDeleteResponse(BaseModel):
    deleted: bool
    id: UUID


class ElevationProfileJobResponse(BaseModel):
    id: UUID
    trip_id: UUID
    status: str
    attempts: int
    available_at: datetime
    lease_expires_at: Optional[datetime]
    worker_id: Optional[str]
    last_error: Optional[str]
    algorithm_version: Optional[str]
    roads_release: Optional[str]
    output_object_name: str
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]
    model_config = ConfigDict(from_attributes=True)


class AuxTripCreateResponse(BaseModel):
    trip: GtfsTripsRead
    elevation_job: ElevationProfileJobResponse


class GtfsStopsReadWithTimes(BaseModel):
    id: UUID
    stop_id: str
    stop_code: Optional[str]
    stop_name: Optional[str]
    stop_desc: Optional[str]
    stop_lat: Optional[float]
    stop_lon: Optional[float]
    zone_id: Optional[str]
    stop_url: Optional[str]
    location_type: Optional[int]
    parent_station: Optional[str]
    stop_timezone: Optional[str]
    wheelchair_boarding: Optional[int]
    platform_code: Optional[str]
    level_id: Optional[str]
    arrival_time: Optional[str]
    departure_time: Optional[str]
    model_config = ConfigDict(from_attributes=True)


class DepotCreateRequest(BaseModel):
    user_id: UUID
    name: str
    address: Optional[str] = None
    features: Optional[dict | list | None] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class DepotUpdateRequest(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    features: Optional[dict | list | None] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class DepotReadWithLocation(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    address: Optional[str]
    features: Optional[dict | list | None]
    stop_id: Optional[UUID]
    latitude: Optional[float]
    longitude: Optional[float]
    model_config = ConfigDict(from_attributes=True)


class VariantsReadWithRoute(BaseModel):
    id: UUID
    route_id: UUID
    variant_num: int
    created_at: datetime
    gtfs_route_id: str
    elevation_file_path: str
    elevation_data_fields: list[str]
    elevation_data: list[list]
    model_config = ConfigDict(from_attributes=True)


class GtfsRoutesReadWithVariant(BaseModel):
    id: UUID
    route_id: str
    agency_id: UUID
    route_short_name: Optional[str]
    route_long_name: Optional[str]
    route_desc: Optional[str]
    route_type: Optional[int]
    route_url: Optional[str]
    route_color: Optional[str]
    route_text_color: Optional[str]
    route_sort_order: Optional[int]
    continuous_pickup: Optional[int]
    continuous_drop_off: Optional[int]
    variant_elevation_file_path: str
    variant_elevation_data_fields: list[str]
    variant_elevation_data: list[list]
    model_config = ConfigDict(from_attributes=True)


class ShiftStructureItem(BaseModel):
    id: UUID
    trip_id: UUID
    shift_id: UUID
    sequence_number: int
    model_config = ConfigDict(from_attributes=True)


class ShiftReadWithStructure(BaseModel):
    id: UUID
    name: str
    bus_id: Optional[UUID]
    structure: list[ShiftStructureItem]
    model_config = ConfigDict(from_attributes=True)


class TripStatisticsResponse(BaseModel):
    """Response schema for trip statistics computation"""
    trip_id: UUID
    statistics: dict
    error: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class CombinedTripStatisticsResponse(BaseModel):
    """Single combined statistics for one or multiple trips"""
    trip_ids: list[UUID]
    statistics: dict
    error: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class RouteInfoBrief(BaseModel):
    """Brief route information for shift info response"""
    id: UUID
    name: str  # route_short_name or route_long_name
    model_config = ConfigDict(from_attributes=True)


class TripInfoBrief(BaseModel):
    """Brief trip information for shift info response"""
    id: UUID
    trip_id: str
    trip_headsign: Optional[str]
    departure_time: Optional[str]
    arrival_time: Optional[str]
    start_stop_name: Optional[str]
    end_stop_name: Optional[str]
    sequence_number: int
    model_config = ConfigDict(from_attributes=True)


class ShiftInfoResponse(BaseModel):
    """Detailed shift information including route, day of week, and trips"""
    id: UUID
    name: str
    bus_id: Optional[UUID]
    route: Optional[RouteInfoBrief]
    days_of_week: list[str]  # e.g., ["monday", "tuesday", ...]
    trips: list[TripInfoBrief]
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Yearly analysis energy summary
# ---------------------------------------------------------------------------

class ScenarioDieselHeating(BaseModel):
    """Diesel-heating quantities for a single temperature scenario."""
    diesel_fuel_kwh: float
    diesel_liters: float
    diesel_heater_efficiency: float


class ScenarioEnergySummary(BaseModel):
    """Per-scenario energy data (one prediction run = one scenario)."""
    prediction_run_id: UUID
    temperature_celsius: float
    occurrences: int
    auxiliary_heating_type: str
    daily_electric_kwh: float
    daily_distance_km: float
    daily_auxiliary_kwh: float
    daily_drivetrain_kwh: float
    diesel_heating: Optional[ScenarioDieselHeating] = None
    annual_electric_kwh: float
    annual_distance_km: float
    annual_auxiliary_kwh: float
    annual_drivetrain_kwh: float
    annual_diesel_fuel_kwh: float
    annual_diesel_liters: float


class YearlyDieselHeatingTotals(BaseModel):
    """Aggregated yearly diesel-heating quantities across all scenarios."""
    diesel_fuel_kwh: float
    diesel_liters: float


class YearlyEnergySummaryResponse(BaseModel):
    """Full yearly energy summary, with per-scenario breakdown and totals."""
    yearly_analysis_id: UUID
    auxiliary_heating_type: str
    scenarios: list[ScenarioEnergySummary]
    yearly_totals: dict
    yearly_diesel_heating: Optional[YearlyDieselHeatingTotals] = None


# ---------------------------------------------------------------------------
# Lightweight list-item schemas for paginated list endpoints
# ---------------------------------------------------------------------------


class BusesModelsListItemRead(BaseModel):
    """Lightweight bus-model row for list pages.

    Detail/edit pages should still use the full ``GET /bus-models/{id}``
    endpoint to fetch ``specs`` and other heavy fields.
    """
    id: UUID
    name: str
    user_id: UUID
    manufacturer: Optional[str] = None
    description: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class GtfsStopsListItemRead(BaseModel):
    """Lightweight GTFS stop row for list/table/map selectors."""
    id: UUID
    stop_id: str
    stop_code: Optional[str] = None
    stop_name: Optional[str] = None
    stop_lat: Optional[float] = None
    stop_lon: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)


class ShiftListItemRead(BaseModel):
    """Lightweight shift row for the main list page.

    Includes ``trip_count`` for the UI without forcing the full structure
    payload. The full shift detail endpoint (``GET /shifts/{id}``) still
    returns the complete structure.
    """
    id: UUID
    name: str
    bus_id: Optional[UUID] = None
    trip_count: int = 0
    model_config = ConfigDict(from_attributes=True)


class OptimizationRunListItemRead(BaseModel):
    """Lightweight optimization-run row for the list page.

    Excludes the heavy ``input_params`` and ``results`` blobs but exposes a
    handful of summary fields commonly shown in the list UI
    (``electrification_feasible``, ``solver_status``, ``objective_value``).
    Use the detail endpoint to fetch the full payload.
    """
    id: UUID
    user_id: UUID
    bus_model_id: Optional[UUID] = None
    name: Optional[str] = None
    mode: str
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    electrification_feasible: Optional[bool] = None
    solver_status: Optional[str] = None
    objective_value: Optional[float] = None
    model_config = ConfigDict(from_attributes=True)


class YearlyAnalysisListItemRead(BaseModel):
    """Lightweight yearly-analysis row for the list page.

    The heavy ``features`` blob is intentionally omitted; load it from the
    detail endpoint when needed.
    """
    id: UUID
    optimization_run_id: Optional[UUID] = None
    name: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
