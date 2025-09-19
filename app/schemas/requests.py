from __future__ import annotations

from uuid import UUID
from pydantic import BaseModel
from typing import Optional
from app.schemas.trip_status import TripStatus


class AuxTripCreate(BaseModel):
    departure_stop_id: UUID
    arrival_stop_id: UUID
    departure_time: str
    arrival_time: str
    route_id: UUID
    status: TripStatus = TripStatus.DEPOT
    calendar_service_key: Optional[str] = None


# Shift creation/update requests
class ShiftCreateRequest(BaseModel):
    name: str
    bus_id: Optional[UUID] = None
    trip_ids: list[UUID]


class ShiftUpdateRequest(BaseModel):
    name: Optional[str] = None
    bus_id: Optional[UUID] = None
    trip_ids: Optional[list[UUID]] = None

