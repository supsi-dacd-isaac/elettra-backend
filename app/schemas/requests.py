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


