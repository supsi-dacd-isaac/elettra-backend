"""Company-scoped GTFS catalog and edition consistency for vehicle blocks."""
from copy import deepcopy
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GtfsCalendar, GtfsRoutes, GtfsTrips, ShiftsStructures
from app.schemas.gtfs_dataset import GtfsDatasetRead, GtfsDatasetReference


async def list_datasets(db: AsyncSession, company_id: UUID) -> list[GtfsDatasetRead]:
    # Each service is aggregated only once per edition (not once per trip).
    services = (
        select(GtfsRoutes.gtfs_year, GtfsRoutes.gtfs_file_date, GtfsTrips.service_id)
        .join(GtfsTrips, GtfsTrips.route_id == GtfsRoutes.id)
        .where(GtfsRoutes.agency_id == company_id, GtfsTrips.status == "gtfs")
        .distinct().subquery()
    )
    rows = await db.execute(
        select(services.c.gtfs_year, services.c.gtfs_file_date,
               func.min(GtfsCalendar.start_date), func.max(GtfsCalendar.end_date))
        .outerjoin(GtfsCalendar, GtfsCalendar.id == services.c.service_id)
        .group_by(services.c.gtfs_year, services.c.gtfs_file_date)
        .order_by(services.c.gtfs_year.desc(), services.c.gtfs_file_date.desc())
    )
    return [GtfsDatasetRead(gtfs_year=y, gtfs_file_date=d, valid_from=start, valid_to=end)
            for y, d, start, end in rows]


def dataset_key(dataset):
    return dataset.gtfs_year, dataset.gtfs_file_date


async def datasets_for_trips(db, trip_ids, company_id):
    if not trip_ids:
        return []
    rows = await db.execute(
        select(GtfsRoutes.gtfs_year, GtfsRoutes.gtfs_file_date)
        .join(GtfsTrips, GtfsTrips.route_id == GtfsRoutes.id)
        .where(GtfsTrips.id.in_(trip_ids), GtfsTrips.status == "gtfs",
               GtfsRoutes.agency_id == company_id).distinct()
    )
    keys = set(rows.all())
    return [d for d in await list_datasets(db, company_id) if dataset_key(d) in keys]


async def datasets_for_shifts(db, shift_ids, company_id):
    result = {sid: [] for sid in shift_ids}
    if not shift_ids:
        return result
    rows = await db.execute(
        select(ShiftsStructures.shift_id, GtfsRoutes.gtfs_year, GtfsRoutes.gtfs_file_date)
        .join(GtfsTrips, GtfsTrips.id == ShiftsStructures.trip_id)
        .join(GtfsRoutes, GtfsRoutes.id == GtfsTrips.route_id)
        .where(ShiftsStructures.shift_id.in_(shift_ids), GtfsTrips.status == "gtfs",
               GtfsRoutes.agency_id == company_id).distinct()
    )
    by_shift = {sid: set() for sid in shift_ids}
    for sid, year, file_date in rows:
        by_shift[sid].add((year, file_date))
    catalog = await list_datasets(db, company_id)
    return {sid: [d for d in catalog if dataset_key(d) in keys]
            for sid, keys in by_shift.items()}


def validate_shift_dataset(datasets, requested: GtfsDatasetReference | None = None,
                           previous=None):
    keys = {dataset_key(d) for d in datasets}
    if len(keys) > 1:
        raise HTTPException(422, "A vehicle block must use one GTFS dataset only")
    if requested is not None and keys != {dataset_key(requested)}:
        raise HTTPException(422, "Selected GTFS dataset does not match the vehicle block trips")
    if previous:
        old_keys = {dataset_key(d) for d in previous}
        if len(old_keys) > 1 or keys != old_keys:
            raise HTTPException(422, "Create a new vehicle block to use a different GTFS dataset")


async def capture_provenance(db, shift_ids, company_id):
    datasets = await datasets_for_shifts(db, shift_ids, company_id)
    return {"version": 1, "source": "recorded", "shifts": {
        str(sid): [d.model_dump(mode="json") for d in values]
        for sid, values in datasets.items()
    }}


def inherit_provenance(features, source):
    """Do not trust client-supplied provenance or rewrite it on feature edits."""
    result = dict(features or {})
    result.pop("gtfs_provenance", None)
    if source is not None:
        result["gtfs_provenance"] = deepcopy(source)
    return result
