"""Owned temporary depot movements; source rows and elevation blobs stay untouched."""
import asyncio
from copy import deepcopy
import json
from uuid import UUID, uuid4

import asyncpg

from app.core.config import get_cached_settings
from test_support.staging_targets import validate_test_targets


def owned_depot_shift_copies(request, shift_defs, owner):
    settings = get_cached_settings()
    validate_test_targets(settings.database_url)
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    copies = deepcopy(shift_defs)
    trips, stops, depots = {}, {}, []

    async def cleanup():
        conn = await asyncpg.connect(dsn)
        try:
            async with conn.transaction():
                ids = list(trips.values())
                await conn.execute("DELETE FROM elevation_profile_jobs WHERE trip_id=ANY($1::uuid[])", ids)
                await conn.execute("DELETE FROM gtfs_stops_times WHERE trip_id=ANY($1::uuid[])", ids)
                await conn.execute("DELETE FROM gtfs_trips WHERE id=ANY($1::uuid[])", ids)
                await conn.execute("DELETE FROM depots WHERE id=ANY($1::uuid[])", depots)
                await conn.execute("DELETE FROM gtfs_stops WHERE id=ANY($1::uuid[])", list(stops.values()))
        finally:
            await conn.close()

    # Registered first: the fleet graph finalizer removes shifts before these trips.
    request.addfinalizer(lambda: asyncio.run(cleanup()))

    async def create():
        conn = await asyncpg.connect(dsn)
        try:
            async with conn.transaction():
                ids = list({UUID(t) for s in copies for t in s["trip_ids"]})
                source = await conn.fetch("""
                    SELECT t.id FROM gtfs_trips t
                    JOIN elevation_profile_jobs j ON j.trip_id=t.id
                    WHERE t.id=ANY($1::uuid[]) AND t.status='depot'
                      AND j.status='succeeded' AND j.output_object_name IS NOT NULL
                """, ids)
                needed = await conn.fetchval(
                    "SELECT count(*) FROM gtfs_trips WHERE id=ANY($1::uuid[]) AND status='depot'", ids)
                assert needed and len(source) == needed, "Source depot movements need ready elevation profiles"
                for row in source:
                    old, new = row["id"], uuid4()
                    trips[old] = new
                    await conn.execute("""
                        INSERT INTO gtfs_trips
                        SELECT (jsonb_populate_record(NULL::gtfs_trips, to_jsonb(t) || $2::jsonb)).*
                        FROM gtfs_trips t WHERE t.id=$1
                    """, old, json.dumps(dict(id=str(new), trip_id=f"fleet_depot_{new}")))
                    await conn.execute("""
                        INSERT INTO elevation_profile_jobs
                        SELECT (jsonb_populate_record(NULL::elevation_profile_jobs,
                            to_jsonb(j) || $2::jsonb)).*
                        FROM elevation_profile_jobs j WHERE j.trip_id=$1
                    """, old, json.dumps(dict(id=str(uuid4()), trip_id=str(new),
                                              payload={"owner_user_id": owner})))
                    depot_rows = await conn.fetch("""
                        SELECT DISTINCT st.stop_id FROM gtfs_stops_times st
                        WHERE st.trip_id=$1 AND EXISTS (
                            SELECT 1 FROM depots d WHERE d.stop_id=st.stop_id)
                    """, old)
                    for endpoint in depot_rows:
                        sid = endpoint["stop_id"]
                        if sid in stops:
                            continue
                        copied_stop, copied_depot = uuid4(), uuid4()
                        stops[sid] = copied_stop
                        depots.append(copied_depot)
                        await conn.execute("""
                            INSERT INTO gtfs_stops
                            SELECT (jsonb_populate_record(NULL::gtfs_stops, to_jsonb(s) || $2::jsonb)).*
                            FROM gtfs_stops s WHERE s.id=$1
                        """, sid, json.dumps(dict(id=str(copied_stop), stop_id=f"fleet_depot_{copied_stop}")))
                        await conn.execute("""
                            INSERT INTO depots (id,user_id,name,stop_id)
                            VALUES ($1,$2,$3,$4)
                        """, copied_depot, UUID(owner), f"fleet_depot_{copied_depot}", copied_stop)
                    times = await conn.fetch("SELECT * FROM gtfs_stops_times WHERE trip_id=$1", old)
                    for st in times:
                        await conn.execute("""
                            INSERT INTO gtfs_stops_times
                            SELECT (jsonb_populate_record(NULL::gtfs_stops_times, to_jsonb(st) || $2::jsonb)).*
                            FROM gtfs_stops_times st WHERE st.id=$1
                        """, st["id"], json.dumps(dict(id=str(uuid4()), trip_id=str(new),
                            stop_id=str(stops.get(st["stop_id"], st["stop_id"])))))
                for shift in copies:
                    shift["trip_ids"] = [str(trips.get(UUID(t), UUID(t))) for t in shift["trip_ids"]]
        finally:
            await conn.close()

    asyncio.run(create())
    return copies
