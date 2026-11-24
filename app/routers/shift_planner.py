"""Sequential vehicle blocks. Published GTFS times stay immutable."""
import hashlib
from collections import defaultdict
from dataclasses import astuple
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user, require_analyst
from app.core.ownership import get_accessible_gtfs_stop, get_agency_route, get_owned_bus, get_owned_shift
from app.database import get_async_session
from app.models import Depots, Shifts, ShiftsStructures, Users
from app.routers.gtfs import add_auxiliary_trip, get_driving_distance_to_stop
from app.schemas.requests import AuxTripCreate
from app.schemas.service_weekday import ServiceWeekday
from app.services.shift_planner import Movement, ProgrammeRequest, Service, Stay, compile_programme

router = APIRouter()
# Resolve the actual first/last stop in the same order as the prediction engine.
_CATALOG = """
SELECT t.id, t.route_id, coalesce(r.route_short_name,r.route_long_name,r.route_id) AS line,
       f.stop_id AS from_stop_id, l.stop_id AS to_stop_id,
       NULLIF(fs.parent_station,'') AS from_parent, NULLIF(ls.parent_station,'') AS to_parent,
       fs.stop_name AS from_name, ls.stop_name AS to_name,
       coalesce(nullif(f.departure_time,''), f.arrival_time) AS departure_time,
       coalesce(nullif(l.arrival_time,''), l.departure_time) AS arrival_time,
       nullif(f.arrival_time,'') AS first_arrival, nullif(l.departure_time,'') AS last_departure,
       r.gtfs_year, r.gtfs_file_date, t.trip_short_name {extra_columns}
FROM gtfs_trips t JOIN gtfs_routes r ON r.id=t.route_id
JOIN gtfs_calendar c ON c.id=t.service_id
JOIN LATERAL (SELECT stop_id,departure_time,arrival_time FROM gtfs_stops_times
              WHERE trip_id=t.id ORDER BY stop_sequence LIMIT 1) f ON true
JOIN LATERAL (SELECT stop_id,departure_time,arrival_time FROM gtfs_stops_times
              WHERE trip_id=t.id ORDER BY stop_sequence DESC LIMIT 1) l ON true
JOIN gtfs_stops fs ON fs.id=f.stop_id JOIN gtfs_stops ls ON ls.id=l.stop_id
{extra_join}
WHERE r.agency_id=:agency AND t.status='gtfs'
"""

# The public catalogue groups calendar records. Reads and writes of a saved
# programme always resolve the exact trip IDs selected by that programme.
_PATTERN_COLUMNS = """, c.id AS calendar_id, calls.stop_count,
    CASE WHEN nullif(trim(t.trip_short_name), '') IS NOT NULL AND nullif(t.shape_id, '') IS NOT NULL
         THEN ROW(t.route_id, t.trip_short_name, t.direction_id, t.shape_id, t.trip_headsign,
                  t.block_id, t.wheelchair_accessible, t.bikes_allowed, calls.schedule)::text
    END AS pattern"""
_PATTERN_JOIN = """
JOIN LATERAL (
    SELECT count(*) AS stop_count,
           encode(sha256(convert_to(array_agg(ROW(s.stop_id, s.arrival_time, s.departure_time,
               s.pickup_type, s.drop_off_type, s.continuous_pickup, s.continuous_drop_off,
               s.stop_headsign, s.timepoint, s.shape_dist_traveled) ORDER BY s.stop_sequence)::text,
               'UTF8')), 'hex') AS schedule
    FROM gtfs_stops_times s WHERE s.trip_id=t.id
) calls ON true"""


def group_calendar_variants(records):
    groups = defaultdict(list)
    for trip in records:
        # A missing public run number or path cannot establish equivalence.
        key = trip.pop("pattern") or trip["id"]
        groups[key].append(trip)
    candidates = []
    for members in groups.values():
        calendars = {trip.pop("calendar_id") for trip in members}
        if len(calendars) == len(members):
            first, *alternatives = members
            first["equivalent_trip_ids"] = [trip["id"] for trip in alternatives]
            candidates.append(first)
        else:
            # Trips sharing a calendar can actually run alongside each other.
            candidates.extend({**trip, "equivalent_trip_ids": []} for trip in members)
    return sorted(candidates, key=lambda trip: (trip["departure_time"] is None, trip["departure_time"], trip["id"]))


async def catalogue(db, agency, weekday, *, route=None, trips=None, edition=None):
    # weekday is a validated Literal. All data and identifiers from clients are bound.
    listing = route is not None and trips is None
    query = _CATALOG.format(extra_columns=_PATTERN_COLUMNS if listing else "",
                            extra_join=_PATTERN_JOIN if listing else "")
    query += " AND (to_jsonb(c) ->> CAST(:weekday AS text)) = '1'"
    params = {"agency": agency, "weekday": weekday}
    if route is not None:
        query += " AND r.id=:route"
        params["route"] = route
    if trips is not None:
        query += " AND t.id=ANY(CAST(:trips AS uuid[]))"
        params["trips"] = list(trips)
    if edition is not None:
        query += " AND r.gtfs_year=:year AND r.gtfs_file_date=:stamp"
        params.update(year=edition.gtfs_year, stamp=edition.gtfs_file_date)
    rows = await db.execute(text(query + " ORDER BY departure_time,t.id"), params)
    records = [dict(row) for row in rows.mappings()]
    return group_calendar_variants(records) if listing else records


@router.get("/catalog")
async def list_candidates(route_id: UUID, weekday: ServiceWeekday, db: AsyncSession = Depends(get_async_session),
                          user: Users = Depends(get_current_user)):
    await get_agency_route(db, route_id, user.company_id)
    return await catalogue(db, user.company_id, weekday, route=route_id)


@router.get("/route")
async def routing(from_stop_id: UUID, to_stop_id: UUID, db: AsyncSession = Depends(get_async_session),
                  user: Users = Depends(get_current_user)):
    origin = await get_accessible_gtfs_stop(db, from_stop_id, user.id, user.company_id)
    if origin.stop_lat is None or origin.stop_lon is None:
        raise HTTPException(422, "The origin has no coordinates")
    result = await get_driving_distance_to_stop(to_stop_id, float(origin.stop_lat), float(origin.stop_lon),
                                                "to_stop", "wgs84", "false", db, user)
    return {"distance_m": result["distance_meters"], "duration_seconds": result["duration_seconds"]}


def public_record(shift, trips):
    programme = dict(shift.programme)
    programme.pop("connections", None)
    dataset = programme.pop("dataset")
    revision = programme.pop("revision")
    programme.pop("creation_payload", None)
    return {"id": shift.id, "name": shift.name, "bus_id": shift.bus_id, "gtfs_dataset": dataset,
            "programme": programme, "revision": revision, "trips": trips}


@router.get("/programmes/{shift_id}")
async def read_programme(shift_id: UUID, db: AsyncSession = Depends(get_async_session),
                         user: Users = Depends(get_current_user)):
    shift = await get_owned_shift(db, shift_id, user.id, user.company_id)
    if not shift.programme:
        raise HTTPException(404, "This block uses the historical editor")
    trips = [UUID(s["trip_id"]) for s in shift.programme["steps"] if s["kind"] == "service"]
    return public_record(shift, await catalogue(db, user.company_id, shift.service_weekday, trips=trips))


async def save_programme(payload, db, user, shift_id=None):
    await get_owned_bus(db, payload.bus_id, user.id)
    creating = shift_id is None
    if creating:
        shift_id = uuid5(NAMESPACE_URL, f"elettra/planner/{user.id}/{payload.request_id}") if payload.request_id else uuid4()
    # Lock creates too: an HTTP retry can arrive before its original transaction commits.
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": int.from_bytes(shift_id.bytes[:8], "big", signed=True)})
    if creating:
        shift = await db.get(Shifts, shift_id)
    else:
        shift = await get_owned_shift(db, shift_id, user.id, user.company_id)
        if not shift.programme or shift.programme["revision"] != payload.revision:
            raise HTTPException(409, "The saved block changed; reopen it before saving")
        if shift.programme["dataset"] != payload.gtfs_dataset.model_dump(mode="json"):
            raise HTTPException(409, "The saved block belongs to another GTFS edition")
    programme = payload.programme
    selected = [s.trip_id for s in programme.steps if isinstance(s, Service)]
    trips = await catalogue(db, user.company_id, programme.weekday, trips=selected, edition=payload.gtfs_dataset)
    # Match retries against the original creation even after later edits.
    creation_hash = hashlib.sha256(payload.model_dump_json(exclude={"request_id", "revision"}).encode()).hexdigest()
    if creating and shift is not None:
        if (shift.programme or {}).get("creation_payload") != creation_hash:
            raise HTTPException(409, "The creation identifier has already been used")
        return await read_programme(shift.id, db, user)
    ids = {programme.depot_id, *(s.depot_id for s in programme.steps if isinstance(s, Stay))}
    depots = (await db.execute(select(Depots.id, Depots.stop_id, Depots.features).where(
        Depots.id.in_(ids), Depots.user_id == user.id, Depots.stop_id.is_not(None)
    ))).all()
    places = {depot.id: depot.stop_id for depot in depots}
    if set(places) != ids:
        raise HTTPException(422, "Unknown custom stop")
    if any(d.id == programme.depot_id and (d.features or {}).get("type") not in (None, "", "depot") for d in depots):
        raise HTTPException(422, "Start the block at a depot")
    try:
        movements = compile_programme(programme, {t["id"]: t for t in trips}, places)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    previous = (shift.programme or {}) if shift else {}
    connection_source = previous
    if payload.copy_from is not None:
        if not creating:
            raise HTTPException(422, "A copy source is only accepted when creating a block")
        source = await get_owned_shift(db, payload.copy_from, user.id, user.company_id)
        connection_source = source.programme or {}
        if connection_source.get("dataset") != payload.gtfs_dataset.model_dump(mode="json"):
            raise HTTPException(422, "Select a programme in the same GTFS edition to copy")
    reusable = {tuple(entry["key"]): UUID(entry["id"]) for entry in connection_source.get("connections", [])}
    trip_ids, connections = [], []
    for move in movements:
        if not isinstance(move, Movement):
            trip_ids.append(move)
            continue
        key = (programme.weekday, *(str(v) for v in astuple(move)))
        trip_id = reusable.get(key)
        if trip_id is None:
            aux = AuxTripCreate(departure_stop_id=move.origin, arrival_stop_id=move.destination,
                                departure_time=move.departure, arrival_time=move.arrival,
                                route_id=move.route_id, status=move.status, day_of_week=programme.weekday)
            trip, _ = await add_auxiliary_trip(aux, db, user)
            trip_id = trip.id
        trip_ids.append(trip_id)
        connections.append({"key": key, "id": str(trip_id)})
    if shift is None:
        shift = Shifts(id=shift_id)
        db.add(shift)
    shift.name, shift.bus_id, shift.service_weekday = payload.name, payload.bus_id, programme.weekday
    shift.programme = {**programme.model_dump(mode="json"), "connections": connections,
                       "creation_payload": previous.get("creation_payload", creation_hash),
                       "dataset": payload.gtfs_dataset.model_dump(mode="json"), "revision": previous.get("revision", 0) + 1}
    await db.flush()
    await db.execute(delete(ShiftsStructures).where(ShiftsStructures.shift_id == shift.id))
    db.add_all(ShiftsStructures(shift_id=shift.id, sequence_number=n, trip_id=trip_id)
               for n, trip_id in enumerate(trip_ids, 1))
    await db.commit()
    return public_record(shift, trips)


@router.post("/programmes", status_code=201)
async def create_programme(payload: ProgrammeRequest, db: AsyncSession = Depends(get_async_session),
                           user: Users = Depends(require_analyst)):
    return await save_programme(payload, db, user)


@router.put("/programmes/{shift_id}")
async def edit_programme(shift_id: UUID, payload: ProgrammeRequest, db: AsyncSession = Depends(get_async_session),
                         user: Users = Depends(require_analyst)):
    return await save_programme(payload, db, user, shift_id)
