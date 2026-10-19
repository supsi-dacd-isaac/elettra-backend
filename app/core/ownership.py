"""Central ownership rules for user-created ELETTRA resources.

The database schema intentionally remains unchanged.  Directly-owned records
use their existing ``user_id`` columns; shifts inherit ownership from their
bus; yearly analyses inherit it from their optimization/prediction runs and
carry a server-managed marker in their existing ``features`` JSON document;
auxiliary trips inherit it from their existing elevation-job payload or from
the shifts that reference them.

All lookups return 404 for missing and foreign records alike.  This prevents a
caller from using response differences to enumerate another user's UUIDs.
"""

from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, exists, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Buses,
    BusesModels,
    Depots,
    ElevationProfileJobs,
    GtfsRoutes,
    GtfsStops,
    GtfsStopsTimes,
    GtfsTrips,
    OptimizationRuns,
    PredictionRuns,
    Shifts,
    ShiftsStructures,
    YearlyAnalysis,
)


YEARLY_ANALYSIS_OWNER_KEY = "_owner_user_id"
AUXILIARY_TRIP_OWNER_KEY = "owner_user_id"

_T = TypeVar("_T")


def _not_found(resource: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{resource} not found")


def require_current_user_id(requested_user_id: UUID, current_user_id: UUID) -> None:
    """Reject attempts to create or transfer a record for another user."""

    if str(requested_user_id) != str(current_user_id):
        raise HTTPException(
            status_code=403,
            detail="A user can only create or update their own resources",
        )


async def _owned_by_user_id(
    db: AsyncSession,
    model: type[_T],
    object_id: UUID,
    user_id: UUID,
    resource: str,
) -> _T:
    result = await db.execute(
        select(model).where(model.id == object_id, model.user_id == user_id)
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise _not_found(resource)
    return obj


async def get_owned_bus_model(
    db: AsyncSession, model_id: UUID, user_id: UUID
) -> BusesModels:
    return await _owned_by_user_id(db, BusesModels, model_id, user_id, "Bus model")


async def get_owned_bus(db: AsyncSession, bus_id: UUID, user_id: UUID) -> Buses:
    return await _owned_by_user_id(db, Buses, bus_id, user_id, "Bus")


async def get_owned_depot(db: AsyncSession, depot_id: UUID, user_id: UUID) -> Depots:
    return await _owned_by_user_id(db, Depots, depot_id, user_id, "Depot")


async def get_owned_prediction_run(
    db: AsyncSession, run_id: UUID, user_id: UUID
) -> PredictionRuns:
    result = await db.execute(
        select(PredictionRuns).where(
            PredictionRuns.id == run_id,
            prediction_run_owned_filter(user_id),
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise _not_found("Prediction run")
    return run


async def get_owned_optimization_run(
    db: AsyncSession, run_id: UUID, user_id: UUID
) -> OptimizationRuns:
    return await _owned_by_user_id(
        db, OptimizationRuns, run_id, user_id, "Optimization run"
    )


async def get_owned_shift(
    db: AsyncSession,
    shift_id: UUID,
    user_id: UUID,
    company_id: UUID | None = None,
) -> Shifts:
    """Resolve shift ownership through the existing shift -> bus relation."""

    filters = [Shifts.id == shift_id, Buses.user_id == user_id]
    if company_id is not None:
        filters.append(shift_trips_accessible_filter(user_id, company_id))
    result = await db.execute(
        select(Shifts)
        .join(Buses, Shifts.bus_id == Buses.id)
        .where(*filters)
    )
    shift = result.scalar_one_or_none()
    if shift is None:
        raise _not_found("Shift")
    return shift


def stamp_yearly_analysis_owner(features: dict | None, user_id: UUID) -> dict:
    """Return a copied features document with its server-managed owner marker."""

    stamped = dict(features or {})
    stamped[YEARLY_ANALYSIS_OWNER_KEY] = str(user_id)
    return stamped


def prediction_run_owned_filter(user_id: UUID):
    """Require a prediction and both of its private parents to share one owner."""

    model_owned = exists(
        select(1).where(
            BusesModels.id == PredictionRuns.bus_model_id,
            BusesModels.user_id == user_id,
        )
    )
    shift_owned = exists(
        select(1)
        .select_from(Shifts)
        .join(Buses, Buses.id == Shifts.bus_id)
        .where(
            Shifts.id == PredictionRuns.shift_id,
            Buses.user_id == user_id,
        )
    )
    return and_(PredictionRuns.user_id == user_id, model_owned, shift_owned)


def yearly_analysis_owned_filter(user_id: UUID):
    """Build a fail-closed ownership predicate for yearly analyses.

    Legacy rows may not have the JSON marker, so ownership can also be proven by
    the linked optimization run or prediction runs.  Conflicting evidence makes
    the row inaccessible to every user until it is repaired deliberately.
    """

    user_text = str(user_id)
    marker = YearlyAnalysis.features[YEARLY_ANALYSIS_OWNER_KEY].astext

    optimization_owned = exists(
        select(1).where(
            OptimizationRuns.id == YearlyAnalysis.optimization_run_id,
            OptimizationRuns.user_id == user_id,
        )
    )
    optimization_foreign = exists(
        select(1).where(
            OptimizationRuns.id == YearlyAnalysis.optimization_run_id,
            OptimizationRuns.user_id != user_id,
        )
    )
    prediction_owned = exists(
        select(1).where(
            PredictionRuns.yearly_analysis_id == YearlyAnalysis.id,
            prediction_run_owned_filter(user_id),
        )
    )
    prediction_foreign = exists(
        select(1).where(
            PredictionRuns.yearly_analysis_id == YearlyAnalysis.id,
            not_(prediction_run_owned_filter(user_id)),
        )
    )
    marker_owned = marker == user_text
    marker_foreign = and_(marker.is_not(None), marker != user_text)

    return and_(
        or_(marker_owned, optimization_owned, prediction_owned),
        not_(or_(marker_foreign, optimization_foreign, prediction_foreign)),
    )


async def get_owned_yearly_analysis(
    db: AsyncSession, analysis_id: UUID, user_id: UUID
) -> YearlyAnalysis:
    result = await db.execute(
        select(YearlyAnalysis).where(
            YearlyAnalysis.id == analysis_id,
            yearly_analysis_owned_filter(user_id),
        )
    )
    analysis = result.scalar_one_or_none()
    if analysis is None:
        raise _not_found("Yearly analysis")
    return analysis


def gtfs_trip_accessible_filter(user_id: UUID, company_id: UUID):
    """Permit agency GTFS trips and private auxiliary trips only."""

    route_in_agency = exists(
        select(1).where(
            GtfsRoutes.id == GtfsTrips.route_id,
            GtfsRoutes.agency_id == company_id,
        )
    )
    marker = ElevationProfileJobs.payload[AUXILIARY_TRIP_OWNER_KEY].astext
    job_owned = exists(
        select(1).where(
            ElevationProfileJobs.trip_id == GtfsTrips.id,
            marker == str(user_id),
        )
    )
    job_foreign = exists(
        select(1).where(
            ElevationProfileJobs.trip_id == GtfsTrips.id,
            marker.is_not(None),
            marker != str(user_id),
        )
    )
    shift_owned = exists(
        select(1)
        .select_from(ShiftsStructures)
        .join(Shifts, Shifts.id == ShiftsStructures.shift_id)
        .join(Buses, Buses.id == Shifts.bus_id)
        .where(
            ShiftsStructures.trip_id == GtfsTrips.id,
            Buses.user_id == user_id,
        )
    )
    shift_foreign = exists(
        select(1)
        .select_from(ShiftsStructures)
        .join(Shifts, Shifts.id == ShiftsStructures.shift_id)
        .join(Buses, Buses.id == Shifts.bus_id)
        .where(
            ShiftsStructures.trip_id == GtfsTrips.id,
            Buses.user_id != user_id,
        )
    )

    shared_agency_trip = and_(GtfsTrips.status == "gtfs", route_in_agency)
    private_auxiliary_trip = and_(
        GtfsTrips.status != "gtfs",
        or_(job_owned, shift_owned),
        not_(or_(job_foreign, shift_foreign)),
    )
    return or_(shared_agency_trip, private_auxiliary_trip)


def shift_trips_accessible_filter(user_id: UUID, company_id: UUID):
    """Reject a shift if any of its trips is outside the user's scope."""

    inaccessible_trip = exists(
        select(1)
        .select_from(ShiftsStructures)
        .join(GtfsTrips, GtfsTrips.id == ShiftsStructures.trip_id)
        .where(
            ShiftsStructures.shift_id == Shifts.id,
            not_(gtfs_trip_accessible_filter(user_id, company_id)),
        )
    )
    return not_(inaccessible_trip)


async def get_accessible_gtfs_trip(
    db: AsyncSession,
    trip_id: UUID,
    user_id: UUID,
    company_id: UUID,
) -> GtfsTrips:
    result = await db.execute(
        select(GtfsTrips).where(
            GtfsTrips.id == trip_id,
            gtfs_trip_accessible_filter(user_id, company_id),
        )
    )
    trip = result.scalar_one_or_none()
    if trip is None:
        raise _not_found("Trip")
    return trip


async def require_accessible_gtfs_trips(
    db: AsyncSession,
    trip_ids: list[UUID],
    user_id: UUID,
    company_id: UUID,
) -> None:
    """Validate a complete trip list without leaking which UUID was foreign."""

    unique_ids = set(trip_ids)
    if not unique_ids:
        return
    result = await db.execute(
        select(GtfsTrips.id).where(
            GtfsTrips.id.in_(unique_ids),
            gtfs_trip_accessible_filter(user_id, company_id),
        )
    )
    accessible_ids = set(result.scalars().all())
    if accessible_ids != unique_ids:
        raise HTTPException(status_code=404, detail="One or more trips were not found")


def gtfs_stop_accessible_filter(user_id: UUID, company_id: UUID):
    """Permit agency GTFS stops and private depot stops only.

    ``gtfs_stops`` has no agency foreign key in the existing schema. Resolve
    that relationship through stop_times -> trips -> routes instead of making
    every imported stop globally visible.
    """

    agency_gtfs_stop = exists(
        select(1)
        .select_from(GtfsStopsTimes)
        .join(GtfsTrips, GtfsTrips.id == GtfsStopsTimes.trip_id)
        .join(GtfsRoutes, GtfsRoutes.id == GtfsTrips.route_id)
        .where(
            GtfsStopsTimes.stop_id == GtfsStops.id,
            GtfsTrips.status == "gtfs",
            GtfsRoutes.agency_id == company_id,
        )
    )
    owned_depot = exists(
        select(1).where(Depots.stop_id == GtfsStops.id, Depots.user_id == user_id)
    )
    foreign_depot = exists(
        select(1).where(Depots.stop_id == GtfsStops.id, Depots.user_id != user_id)
    )
    return and_(or_(agency_gtfs_stop, owned_depot), not_(foreign_depot))


async def get_accessible_gtfs_stop(
    db: AsyncSession,
    stop_id: UUID,
    user_id: UUID,
    company_id: UUID,
) -> GtfsStops:
    result = await db.execute(
        select(GtfsStops).where(
            GtfsStops.id == stop_id,
            gtfs_stop_accessible_filter(user_id, company_id),
        )
    )
    stop = result.scalar_one_or_none()
    if stop is None:
        raise _not_found("Stop")
    return stop


async def get_agency_route(
    db: AsyncSession, route_id: UUID, company_id: UUID
) -> GtfsRoutes:
    result = await db.execute(
        select(GtfsRoutes).where(
            GtfsRoutes.id == route_id,
            GtfsRoutes.agency_id == company_id,
        )
    )
    route = result.scalar_one_or_none()
    if route is None:
        raise _not_found("Route")
    return route
