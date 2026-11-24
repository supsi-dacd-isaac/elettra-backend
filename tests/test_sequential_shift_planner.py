from datetime import date
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token
from app.database import engine, get_async_session
from app.models import (Buses, Depots, ElevationProfileJobs, GtfsAgencies, GtfsCalendar,
                        GtfsRoutes, GtfsStops, GtfsStopsTimes, GtfsTrips, ShiftsStructures, Users)
from app.routers import gtfs, shift_planner, user
from app.services.shift_planner import Movement, Programme, compile_programme


def test_operating_arrival_platform_continuity_and_midnight():
    depot, base, a, b, c, trip1, trip2, route = [uuid4() for _ in range(8)]
    times = {
        trip1: dict(route_id=route, from_stop_id=a, to_stop_id=b, from_parent=None,
                    to_parent="station", departure_time="23:50", arrival_time="24:00", first_arrival=None),
        trip2: dict(route_id=route, from_stop_id=c, to_stop_id=a, from_parent="station",
                    to_parent=None, departure_time="24:00", arrival_time="24:10", first_arrival=None),
    }
    data = dict(version=1, weekday="monday", depot_id=depot, start_time="23:25", steps=[
        dict(kind="stay", depot_id=depot, transfer_departure="23:25", arrival="23:25", departure="23:30"),
        dict(kind="service", trip_id=trip1, transfer_arrival="23:45"),
        dict(kind="service", trip_id=trip2),
        dict(kind="return", transfer_departure="24:10", arrival="24:20"),
    ])
    def compile_case():
        return compile_programme(Programme(**data), times, {depot: base})
    assert compile_case() == [
        Movement("depot", base, a, "23:30:00", "23:45:00", route), trip1, trip2,
        Movement("depot", a, base, "24:10:00", "24:20:00", route),
    ]
    data["steps"][2]["force_transfer"] = True
    with pytest.raises(ValueError, match="positive travel time"):
        compile_case()
    data["steps"][2].pop("force_transfer")
    times[trip2].update(from_parent=None, departure_time="24:05")
    assert compile_case()[2] == Movement("transfer", b, c, "24:00:00", "24:05:00", route)
    data["steps"][2]["transfer_arrival"] = "24:06"
    with pytest.raises(ValueError, match="published departure"):
        compile_case()
    data["steps"].pop()
    with pytest.raises(ValueError, match="original depot"):
        compile_case()


def test_empty_movements_around_a_stay_belong_to_the_next_scheduled_line():
    depot, base, parking, bay, terminus, first, second, line16, line15 = [uuid4() for _ in range(9)]
    services = {
        first: dict(route_id=line16, from_stop_id=terminus, to_stop_id=terminus, from_parent=None,
                    to_parent=None, departure_time="20:00", arrival_time="20:31", first_arrival=None),
        second: dict(route_id=line15, from_stop_id=terminus, to_stop_id=terminus, from_parent=None,
                     to_parent=None, departure_time="21:00", arrival_time="24:31", first_arrival=None),
    }
    programme = Programme(version=1, weekday="monday", depot_id=depot, start_time="19:50", steps=[
        dict(kind="service", trip_id=first),
        dict(kind="stay", depot_id=parking, transfer_departure="20:31", arrival="20:32", departure="20:58"),
        dict(kind="service", trip_id=second),
        dict(kind="return", transfer_departure="24:31", arrival="24:43"),
    ])
    assert compile_programme(programme, services, {depot: base, parking: bay}) == [
        Movement("depot", base, terminus, "19:50:00", "20:00:00", line16), first,
        Movement("depot", terminus, bay, "20:31:00", "20:32:00", line15),
        Movement("depot", bay, terminus, "20:58:00", "21:00:00", line15), second,
        Movement("depot", terminus, base, "24:31:00", "24:43:00", line15),
    ]


async def seed_programme(db):
    agency = GtfsAgencies(id=uuid4(), gtfs_agency_id=str(uuid4()), agency_name="Planner QA",
                          agency_url="https://example.invalid", agency_timezone="Europe/Zurich")
    db.add(agency)
    await db.flush()
    owner = Users(id=uuid4(), email=f"{uuid4()}@example.invalid", company_id=agency.id,
                  full_name="Planner QA", role="analyst", password_hash="unused")
    route = GtfsRoutes(id=uuid4(), route_id=str(uuid4()), agency_id=agency.id, route_short_name="15",
                       gtfs_year=2027, gtfs_file_date=date(2026, 8, 22), route_type=3)
    days = dict(monday=1, tuesday=0, wednesday=0, thursday=0, friday=0, saturday=0, sunday=0,
                start_date=date(2026, 12, 13), end_date=date(2027, 12, 11))
    calendar = GtfsCalendar(id=uuid4(), service_id=str(uuid4()), **days)
    if not await db.scalar(select(GtfsCalendar.id).where(GtfsCalendar.service_id == "auxiliary_mon").limit(1)):
        db.add(GtfsCalendar(id=uuid4(), service_id="auxiliary_mon", **days))
    stops = [GtfsStops(id=uuid4(), stop_id=str(uuid4()), stop_name=f"Stop {n}", stop_lat=46, stop_lon=9+n/100)
             for n in range(3)]
    db.add_all([owner, route, calendar, *stops])
    await db.flush()
    depot = Depots(id=uuid4(), name="Depot", user_id=owner.id, stop_id=stops[0].id, features={"type": "depot"})
    bus = Buses(id=uuid4(), user_id=owner.id, name="Planner QA", specs={})
    trip = GtfsTrips(id=uuid4(), trip_id=str(uuid4()), service_id=calendar.id, route_id=route.id,
                     gtfs_service_id=calendar.service_id, status="gtfs")
    db.add_all([bus, depot, trip])
    await db.flush()
    db.add_all([GtfsStopsTimes(trip_id=trip.id, stop_id=stops[1].id, stop_sequence=1, departure_time="08:10:00"),
                GtfsStopsTimes(trip_id=trip.id, stop_id=stops[2].id, stop_sequence=2, arrival_time="08:25:00")])
    await db.flush()
    return owner, route.id, {
        "request_id": str(uuid4()), "name": "QA", "bus_id": str(bus.id),
        "gtfs_dataset": {"gtfs_year": 2027, "gtfs_file_date": "2026-08-22"},
        "programme": {"version": 1, "weekday": "monday", "depot_id": str(depot.id), "start_time": "08:00",
                      "steps": [{"kind": "service", "trip_id": str(trip.id)},
                                {"kind": "return", "transfer_departure": "08:25", "arrival": "08:30"}]},
    }


@pytest.mark.asyncio
async def test_catalogue_groups_only_equivalent_day_variants_and_preserves_saved_ids():
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                owner, route, body = await seed_programme(db)
                first = await db.get(GtfsTrips, UUID(body["programme"]["steps"][0]["trip_id"]))
                base_calendar = await db.get(GtfsCalendar, first.service_id)
                endpoints = (await db.scalars(select(GtfsStopsTimes.stop_id).where(
                    GtfsStopsTimes.trip_id == first.id).order_by(GtfsStopsTimes.stop_sequence))).all()
                middle = GtfsStops(id=uuid4(), stop_id=str(uuid4()), stop_name="Middle", stop_lat=46, stop_lon=9)
                db.add(middle)

                def calendar(monday):
                    value = GtfsCalendar(id=uuid4(), service_id=str(uuid4()), monday=monday, tuesday=1,
                                         wednesday=0, thursday=0, friday=0, saturday=0, sunday=0,
                                         start_date=base_calendar.start_date, end_date=base_calendar.end_date)
                    db.add(value)
                    return value

                alternate = calendar(1)
                tuesday = calendar(0)

                def trip(calendar, number="1001", intermediate="08:15:30", shape="line-1"):
                    record = GtfsTrips(id=uuid4(), trip_id=str(uuid4()), service_id=calendar.id,
                                       gtfs_service_id=calendar.service_id, status="gtfs", route_id=route,
                                       trip_short_name=number, direction_id=0, shape_id=shape)
                    db.add(record)
                    for seq, (stop, clock) in enumerate(zip([endpoints[0], middle.id, endpoints[1]],
                                                            ["08:10:00", intermediate, "08:25:00"]), 1):
                        db.add(GtfsStopsTimes(trip_id=record.id, stop_id=stop, stop_sequence=seq,
                                              arrival_time=clock, departure_time=clock))
                    return record.id

                a, b = trip(base_calendar), trip(alternate)
                different_time = trip(alternate, intermediate="08:15:31")
                different_shape = trip(alternate, shape="line-2")
                simultaneous = {trip(base_calendar, "1002"), trip(base_calendar, "1002")}
                inactive = trip(tuesday)
                await db.flush()
                rows = await shift_planner.catalogue(db, owner.company_id, "monday", route=route)
                primary, alias = sorted([a, b])
                grouped = next(row for row in rows if row["id"] == primary)
                assert grouped["equivalent_trip_ids"] == [alias] and grouped["stop_count"] == 3
                assert {row["id"] for row in rows} == {first.id, primary, different_time, different_shape, *simultaneous}
                assert inactive not in grouped["equivalent_trip_ids"]
                historical = await shift_planner.catalogue(db, owner.company_id, "monday", trips=[alias])
                assert [item["id"] for item in historical] == [alias]
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_atomic_save_retry_edit_and_duplicate(monkeypatch):
    app = FastAPI()
    app.include_router(shift_planner.router, prefix="/planner")
    app.include_router(user.router, prefix="/user")
    calls = []
    def road(request):
        calls.append(request.url.path)
        if len(calls) == 2:
            return httpx.Response(503, text="Test router unavailable")
        return httpx.Response(200, json={"code": "Ok", "routes": [
            {"geometry": "sample", "distance": 1380.2, "duration": 180}]})
    routing = SimpleNamespace(AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(road)),
                              HTTPError=httpx.HTTPError)
    monkeypatch.setattr(gtfs, "httpx", routing)

    async with engine.connect() as connection:
        outer = await connection.begin()
        try:
            def session():
                return AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
            async def request_db():
                async with session() as db:
                    yield db
            app.dependency_overrides[get_async_session] = request_db
            async with session() as db:
                owner, route_id, body = await seed_programme(db)
                await db.commit()
            headers = {"Authorization": "Bearer " + create_access_token({"sub": str(owner.id)})}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                         headers=headers) as client:
                url = "/planner/programmes"
                assert (await client.post(url, json=body)).status_code == 503
                async with session() as db:
                    n = await db.scalar(select(func.count()).select_from(GtfsTrips)
                                        .where(GtfsTrips.route_id == route_id, GtfsTrips.status == "depot"))
                    assert n == 0
                assert (await client.post(url, json={**body, "programme": {**body["programme"], "weekday": "saturday"}})).status_code == 422
                saved = await client.post(url, json=body)
                assert saved.status_code == 201, saved.text
                sid = saved.json()["id"]
                assert (await client.post(url, json=body)).json()["id"] == sid
                assert (await client.post(url, json={**body, "name": "different"})).status_code == 409
                assert (await client.put("/user/shifts/" + sid, json={"trip_ids": [body["programme"]["steps"][0]["trip_id"]]})).status_code == 409
                assert (await client.get(url + "/" + sid)).json()["programme"]["steps"] == saved.json()["programme"]["steps"]
                changed = await client.put(url + "/" + sid, json={**body, "name": "edited", "revision": 1})
                assert changed.status_code == 200 and changed.json()["revision"] == 2
                assert (await client.put(url + "/" + sid, json={**body, "revision": 1})).status_code == 409
                copy = await client.post(url, json={**body, "request_id": str(uuid4()), "copy_from": sid, "name": "copy"})
                assert copy.status_code == 201 and copy.json()["id"] != sid
                assert len(calls) == 4
                async with session() as db:
                    blocks = []
                    for block in (sid, copy.json()["id"]):
                        blocks.append((await db.scalars(select(ShiftsStructures.trip_id)
                            .where(ShiftsStructures.shift_id == UUID(block)).order_by(ShiftsStructures.sequence_number))).all())
                    assert blocks[0] == blocks[1]
                    assert len(blocks[0]) == 3
                    jobs = (await db.scalars(select(ElevationProfileJobs).where(ElevationProfileJobs.trip_id.in_(blocks[0])))).all()
                    assert len(jobs) == 2
                    distances = (await db.scalars(select(GtfsStopsTimes.shape_dist_traveled)
                        .where(GtfsStopsTimes.trip_id == blocks[0][0]).order_by(GtfsStopsTimes.stop_sequence))).all()
                    assert distances == [0.0, 1380.2]
        finally:
            await outer.rollback()
