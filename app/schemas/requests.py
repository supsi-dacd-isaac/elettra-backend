from __future__ import annotations

from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional
from app.schemas.trip_status import TripStatus


class PredictionRequest(BaseModel):
    shift_ids: list[UUID]
    bus_model_id: UUID
    model_name: str = Field(examples=["greybox_qrf_production_crps_optimized_3"])
    external_temp_celsius: float = Field(examples=[15.0])
    occupancy_percent: float = Field(default=50.0, examples=[50.0])
    auxiliary_heating_type: str = Field(default="default", examples=["default"])
    quantiles: list[float] = Field(default_factory=lambda: [0.05, 0.25, 0.5, 0.75, 0.95])
    num_battery_packs: Optional[int] = Field(default=None, examples=[12])


class AuxTripCreate(BaseModel):
    departure_stop_id: UUID
    arrival_stop_id: UUID
    departure_time: str
    arrival_time: str
    route_id: UUID
    status: TripStatus = TripStatus.DEPOT
    calendar_service_key: Optional[str] = None
    day_of_week: Optional[str] = None  # monday..sunday; when set, overrides calendar_service_key


# Shift creation/update requests
class ShiftCreateRequest(BaseModel):
    name: str
    bus_id: Optional[UUID] = None
    trip_ids: list[UUID]


class ShiftUpdateRequest(BaseModel):
    name: Optional[str] = None
    bus_id: Optional[UUID] = None
    trip_ids: Optional[list[UUID]] = None


class TripStatisticsRequest(BaseModel):
    trip_ids: list[UUID]

