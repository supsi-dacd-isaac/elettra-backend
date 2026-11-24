"""Recurring weekday compatibility for already authorized vehicle blocks."""

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GtfsCalendar, GtfsTrips, Shifts, ShiftsStructures
from app.schemas.service_weekday import SERVICE_WEEKDAYS, ServiceWeekday


_ALL_DAYS = frozenset(SERVICE_WEEKDAYS)


@dataclass(frozen=True)
class ShiftWeekdays:
    planner_version: int | None = None
    service_weekday: ServiceWeekday | None = None
    known_weekdays: frozenset[ServiceWeekday] = _ALL_DAYS
    complete: bool = False

    @property
    def compatible_weekdays(self) -> list[ServiceWeekday] | None:
        if not self.complete and self.known_weekdays:
            return None
        return [day for day in SERVICE_WEEKDAYS if day in self.known_weekdays]


async def weekdays_for_shifts(
    db: AsyncSession, shifts: list[Shifts]
) -> dict[UUID, ShiftWeekdays]:
    """Load distinct legacy service calendars for a page or study in one query.

    Depot trips, transfers and other auxiliary movements do not define a service
    day. School and service trips contribute a specific calendar when present.
    The generic `auxiliary` calendar is an all-days placeholder for those trips.
    """
    weekdays = {}
    legacy_days: dict[UUID, frozenset[ServiceWeekday]] = {}
    for shift in shifts:
        programme = shift.programme
        if isinstance(programme, dict) and programme.get("version") == 1:
            day = shift.service_weekday
            weekdays[shift.id] = ShiftWeekdays(
                planner_version=1,
                service_weekday=day,
                known_weekdays=frozenset({day}) if day in _ALL_DAYS else _ALL_DAYS,
                complete=day in _ALL_DAYS,
            )
        else:
            legacy_days[shift.id] = _ALL_DAYS

    if not legacy_days:
        return weekdays

    rows = await db.execute(
        select(
            ShiftsStructures.shift_id,
            GtfsTrips.status,
            GtfsCalendar.service_id,
            *(getattr(GtfsCalendar, day) for day in SERVICE_WEEKDAYS),
        )
        .join(GtfsTrips, GtfsTrips.id == ShiftsStructures.trip_id)
        .outerjoin(GtfsCalendar, GtfsCalendar.id == GtfsTrips.service_id)
        .where(
            ShiftsStructures.shift_id.in_(legacy_days),
            GtfsTrips.status.in_(("gtfs", "school", "service")),
        )
        .distinct()
    )
    seen = set()
    missing = set()
    for shift_id, trip_status, service_id, *flags in rows:
        if trip_status != "gtfs" and service_id == "auxiliary":
            continue
        if service_id is None or any(flag not in (0, 1) for flag in flags):
            missing.add(shift_id)
            continue
        seen.add(shift_id)
        legacy_days[shift_id] &= frozenset(
            day for day, flag in zip(SERVICE_WEEKDAYS, flags) if flag == 1
        )

    for shift_id, days in legacy_days.items():
        weekdays[shift_id] = ShiftWeekdays(
            known_weekdays=days, complete=shift_id in seen and shift_id not in missing,
        )
    return weekdays


def resolve_study_weekdays(
    requested_day: ServiceWeekday | None, weekdays: dict[UUID, ShiftWeekdays]
) -> dict:
    common = _ALL_DAYS.intersection(*(value.known_weekdays for value in weekdays.values()))
    if not common:
        raise HTTPException(422, "The selected vehicle blocks have no common service weekday")
    if requested_day is not None and requested_day not in common:
        raise HTTPException(422, "The selected vehicle blocks do not all run on the requested service weekday")

    if any(not value.complete for value in weekdays.values()):
        raise HTTPException(422, "The service weekday could not be verified for every selected vehicle block")

    compatible = [day for day in SERVICE_WEEKDAYS if day in common]
    service_weekday = requested_day
    if service_weekday is None and len(compatible) == 1:
        service_weekday = compatible[0]

    return {
        "service_weekday": service_weekday,
        "compatible_weekdays": compatible,
        "service_weekday_provenance": {
            "source": "selected" if requested_day is not None else "historical_compatibility",
            "complete": True,
            "unverified_shift_ids": [],
        },
    }
