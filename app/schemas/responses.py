# Custom response schemas with relationships and specialized data
from __future__ import annotations
from typing import Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class SimulationRunResults(BaseModel):
    """Schema for returning simulation run output results, either complete or filtered"""
    run_id: UUID
    status: str
    output_results: Optional[dict | list | None]
    completed_at: Optional[datetime]
    requested_keys: Optional[list[str]] = None  # Shows which keys were requested if filtered
    model_config = ConfigDict(from_attributes=True)


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
