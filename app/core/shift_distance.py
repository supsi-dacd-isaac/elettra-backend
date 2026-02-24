"""
Shared logic for computing the yearly distance driven by a shift.

Used by:
- ``/api/v1/user/shifts/{shift_id}/yearly-distance``  (full detailed response)
- ``/api/v1/economic/*``  endpoints that need the annual km figure
- ``/api/v1/environmental/shifts/{shift_id}/yearly-impact``  (inline variant)
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import List, NamedTuple, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GtfsStopsTimes, GtfsTrips, Shifts, ShiftsStructures

logger = logging.getLogger(__name__)


class RecurrenceType(str, Enum):
    """How often a shift repeats within a week."""
    weekly_once = "weekly_once"   # 1 day/week  -> x52
    weekdays = "weekdays"         # 5 days/week -> x260
    daily = "daily"               # 7 days/week -> x364
    custom = "custom"             # N days/year (user-supplied)


RECURRENCE_DAYS = {
    RecurrenceType.weekly_once: 52,
    RecurrenceType.weekdays: 260,
    RecurrenceType.daily: 364,
}


class TripDistanceInfo(NamedTuple):
    trip_id: UUID
    gtfs_trip_id: Optional[str]
    sequence_number: int
    distance_m: Optional[float]


class ShiftYearlyDistance(NamedTuple):
    shift_id: UUID
    shift_name: str
    daily_distance_m: float
    daily_distance_km: float
    recurrence_days: int
    yearly_distance_m: float
    yearly_distance_km: float
    trips: List[TripDistanceInfo]


def resolve_recurrence_days(
    recurrence: RecurrenceType,
    custom_days: Optional[int],
) -> int:
    """Return the number of operating days/year for the given recurrence."""
    if recurrence == RecurrenceType.custom:
        if custom_days is None:
            raise HTTPException(
                status_code=422,
                detail="custom_days is required when recurrence=custom",
            )
        return custom_days
    return RECURRENCE_DAYS[recurrence]


async def compute_shift_yearly_distance(
    shift_id: UUID,
    recurrence: RecurrenceType,
    custom_days: Optional[int],
    db: AsyncSession,
) -> ShiftYearlyDistance:
    """Compute daily and yearly distance for a shift from GTFS shape data."""

    days_per_year = resolve_recurrence_days(recurrence, custom_days)

    shift = await db.get(Shifts, shift_id)
    if shift is None:
        raise HTTPException(status_code=404, detail="Shift not found")

    stmt = (
        select(
            ShiftsStructures.trip_id,
            ShiftsStructures.sequence_number,
            GtfsTrips.trip_id.label("gtfs_trip_id"),
            func.max(GtfsStopsTimes.shape_dist_traveled).label("max_dist"),
        )
        .join(GtfsTrips, GtfsTrips.id == ShiftsStructures.trip_id)
        .outerjoin(GtfsStopsTimes, GtfsStopsTimes.trip_id == GtfsTrips.id)
        .where(ShiftsStructures.shift_id == shift_id)
        .group_by(
            ShiftsStructures.trip_id,
            ShiftsStructures.sequence_number,
            GtfsTrips.trip_id,
        )
        .order_by(ShiftsStructures.sequence_number)
    )

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="Shift has no trips in its structure",
        )

    trips: List[TripDistanceInfo] = []
    daily_distance_m = 0.0

    for trip_uuid, seq, gtfs_tid, max_dist in rows:
        dist = float(max_dist) if max_dist is not None else None
        trips.append(TripDistanceInfo(
            trip_id=trip_uuid,
            gtfs_trip_id=gtfs_tid,
            sequence_number=seq,
            distance_m=dist,
        ))
        if dist is not None:
            daily_distance_m += dist

    yearly_distance_m = daily_distance_m * days_per_year

    return ShiftYearlyDistance(
        shift_id=shift_id,
        shift_name=shift.name,
        daily_distance_m=round(daily_distance_m, 2),
        daily_distance_km=round(daily_distance_m / 1000.0, 3),
        recurrence_days=days_per_year,
        yearly_distance_m=round(yearly_distance_m, 2),
        yearly_distance_km=round(yearly_distance_m / 1000.0, 3),
        trips=trips,
    )
