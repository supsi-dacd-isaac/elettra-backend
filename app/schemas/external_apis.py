# External API schemas
from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# PVGIS TMY schemas
# ---------------------------------------------------------------------------

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


class PvgisTmyMetadataResponse(BaseModel):
    """Lightweight response when download=false: only availability info."""
    latitude: float
    longitude: float
    available_in_db: bool
    records_count: int
    source: Optional[str] = None


# ---------------------------------------------------------------------------
# Weather temperature clustering schemas
# ---------------------------------------------------------------------------

class WeatherClusteringRequest(BaseModel):
    """Request body for POST /weather-temperature-clusters/."""
    latitude: float
    longitude: float
    k: int = Field(default=8, ge=1, description="Number of clusters (>= 1)")
    start_time: str = Field(default="05:00", description="Daily window start (HH:MM). Default 05:00")
    end_time: str = Field(default="24:00", description="Daily window end (HH:MM). '24:00' is accepted as end-of-day. Default 24:00")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "latitude": 46.172,
                    "longitude": 8.799,
                    "k": 8,
                    "start_time": "05:00",
                    "end_time": "24:00",
                }
            ]
        }
    }


class ClusterItem(BaseModel):
    cluster_id: int
    centroid_daily_avg_temp: float
    occurrences: int


class WeatherClusteringResponse(BaseModel):
    """Response for both POST and GET /weather-temperature-clusters/."""
    latitude: float
    longitude: float
    k: int
    start_time: str
    end_time: str
    n_days_used: Optional[int] = None
    clusters: list[ClusterItem]


# ---------------------------------------------------------------------------
# Elevation profile schemas
# ---------------------------------------------------------------------------

class ElevationProfileResponse(BaseModel):
    shape_id: str
    records: list[dict]
    model_config = {"from_attributes": True}
