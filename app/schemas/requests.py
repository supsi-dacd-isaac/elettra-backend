from __future__ import annotations

from uuid import UUID
from pydantic import BaseModel


class DepotTripCreate(BaseModel):
    departure_stop_id: UUID
    arrival_stop_id: UUID
    departure_time: str
    arrival_time: str
    route_id: UUID


