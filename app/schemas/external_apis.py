# External API schemas
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel


# PVGIS TMY schemas
class PvgisTmyRequest(BaseModel):
    latitude: float
    longitude: float


class PvgisTmyResponse(BaseModel):
    data: dict
    metadata: dict
    latitude: float
    longitude: float
    coerce_year: int
    generated_at: datetime


# Elevation profile schemas
class ElevationProfileResponse(BaseModel):
    shape_id: str
    records: list[dict]
    model_config = {"from_attributes": True}
