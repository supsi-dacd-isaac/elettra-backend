"""GTFS edition identity; derived metadata, with no database schema changes."""
from datetime import date
from pydantic import BaseModel, ConfigDict, Field


class GtfsDatasetReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gtfs_year: int = Field(ge=1900, le=9999)
    gtfs_file_date: date


class GtfsDatasetRead(GtfsDatasetReference):
    valid_from: date | None = None
    valid_to: date | None = None

