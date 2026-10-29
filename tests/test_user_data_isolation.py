"""End-to-end authorization tests for private user resources.

These tests are intentionally allowed to write only to the dedicated staging
database or the ephemeral PostgreSQL service created by GitHub Actions.  They
never run DDL: temporary rows are inserted into the existing schema and removed
in dependency order at teardown.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import asyncpg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.core.config import get_cached_settings


AUTH = "/auth"
USER = "/api/v1/user"
GTFS = "/api/v1/gtfs"
SIM = "/api/v1/simulation"
YEARLY = "/api/v1/yearly-analysis"
ECONOMIC = "/api/v1/economic"
ENVIRONMENTAL = "/api/v1/environmental"

PASSWORD = "Isolation!Passw0rd"
BUS_MODEL_SPECS = {
    "size": "12m",
    "bus_length_m": 12,
    "empty_weight_kg": 12_000,
    "battery_pack_size_kwh": 50,
    "battery_pack_weight_kg": 300,
    "min_battery_packs": 4,
    "max_battery_packs": 8,
    "max_passengers": 80,
}


def _dsn() -> str:
    dsn = get_cached_settings().database_url
    if dsn.startswith("postgresql+asyncpg://"):
        return "postgresql://" + dsn.split("://", 1)[1]
    return dsn


def _assert_staging_database() -> None:
    url = make_url(get_cached_settings().database_url)
    assert url.host in {"127.0.0.1", "localhost"}
    is_local_staging = url.port == 9004
    is_ephemeral_ci = (
        os.environ.get("GITHUB_ACTIONS") == "true"
        and url.port == 5432
        and url.username == "admin"
        and url.database == "elettra"
    )
    is_isolated_local = (
        os.environ.get("ELETTRA_EPHEMERAL_TEST_DATABASE") == url.database
        and url.database.startswith("elettra_test_")
        and url.username == "admin"
    )
    assert is_local_staging or is_ephemeral_ci or is_isolated_local, (
        "User isolation integration tests may write only to staging on port "
        "9004 or the ephemeral GitHub Actions PostgreSQL service; got "
        f"{url.render_as_string(hide_password=True)}"
    )


def _run(coro):
    return asyncio.run(coro)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_login(
    client: TestClient,
    *,
    agency_id: str,
    label: str,
    role: str,
) -> dict[str, str]:
    email = f"tmp_isolation_{label}_{uuid.uuid4().hex}@example.com"
    response = client.post(
        f"{AUTH}/register",
        json={
            "company_id": agency_id,
            "email": email,
            "full_name": f"Isolation {label}",
            "password": PASSWORD,
            "role": role,
        },
    )
    assert response.status_code == 200, response.text
    user_id = response.json()["id"]
    response = client.post(
        f"{AUTH}/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {
        "id": user_id,
        "email": email,
        "token": response.json()["access_token"],
    }


def _create_primary_resources(
    client: TestClient,
    *,
    user: dict[str, str],
    label: str,
) -> dict[str, str]:
    headers = _headers(user["token"])
    prefix = f"000 Isolation {label} {uuid.uuid4().hex[:8]}"

    response = client.post(
        f"{USER}/bus-models/",
        headers=headers,
        json={
            "user_id": user["id"],
            "name": f"{prefix} model",
            "specs": BUS_MODEL_SPECS,
        },
    )
    assert response.status_code == 200, response.text
    model_id = response.json()["id"]

    response = client.post(
        f"{USER}/buses/",
        headers=headers,
        json={
            "user_id": user["id"],
            "name": f"{prefix} bus",
            "specs": {},
            "bus_model_id": model_id,
        },
    )
    assert response.status_code == 200, response.text
    bus_id = response.json()["id"]

    response = client.post(
        f"{USER}/depots/",
        headers=headers,
        json={
            "user_id": user["id"],
            "name": f"{prefix} depot",
            "address": "Staging only",
            "latitude": 46.0,
            "longitude": 8.9,
        },
    )
    assert response.status_code == 200, response.text
    depot = response.json()

    return {
        "model": model_id,
        "bus": bus_id,
        "depot": depot["id"],
        "depot_stop": depot["stop_id"],
        "prefix": prefix,
    }


async def _set_user_role(user_id: str, role: str) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(
            "UPDATE users SET role = $1 WHERE id = $2::uuid",
            role,
            uuid.UUID(user_id),
        )
    finally:
        await conn.close()


async def _find_shared_gtfs(agency_id: str) -> dict[str, object]:
    conn = await asyncpg.connect(_dsn())
    try:
        row = await conn.fetchrow(
            """
            SELECT t.id AS trip_id, t.route_id, t.service_id, st.stop_id
            FROM gtfs_trips t
            JOIN gtfs_routes r ON r.id = t.route_id
            JOIN gtfs_stops_times st ON st.trip_id = t.id
            WHERE r.agency_id = $1::uuid AND t.status = 'gtfs'
            ORDER BY t.id, st.stop_sequence NULLS LAST
            LIMIT 1
            """,
            uuid.UUID(agency_id),
        )
        if row is not None:
            return {
                **{key: str(row[key]) for key in row.keys()},
                "_created": None,
            }

        async with conn.transaction():
            suffix = uuid.uuid4().hex
            route_id = await conn.fetchval(
                """
                INSERT INTO gtfs_routes (route_id, agency_id, route_short_name)
                VALUES ($1, $2::uuid, 'ISO-SHARED')
                RETURNING id
                """,
                f"isolation_shared_route_{suffix}",
                agency_id,
            )
            service_id = await conn.fetchval(
                """
                INSERT INTO gtfs_calendar (
                    service_id, monday, tuesday, wednesday, thursday,
                    friday, saturday, sunday, start_date, end_date
                ) VALUES ($1, 1, 1, 1, 1, 1, 1, 1, DATE '2025-01-01',
                          DATE '2030-12-31')
                RETURNING id
                """,
                f"isolation_shared_service_{suffix}",
            )
            stop_id = await conn.fetchval(
                """
                INSERT INTO gtfs_stops (stop_id, stop_name, stop_lat, stop_lon)
                VALUES ($1, 'Isolation shared stop', 46.0, 8.9)
                RETURNING id
                """,
                f"isolation_shared_stop_{suffix}",
            )
            trip_id = await conn.fetchval(
                """
                INSERT INTO gtfs_trips (
                    route_id, service_id, gtfs_service_id, trip_id, status,
                    shape_id, departure_time, arrival_time
                ) VALUES ($1, $2, $3, $4, 'gtfs', $5, '08:00:00', '08:10:00')
                RETURNING id
                """,
                route_id,
                service_id,
                f"isolation_shared_service_{suffix}",
                f"isolation_shared_trip_{suffix}",
                f"isolation_shared_shape_{suffix}",
            )
            await conn.execute(
                """
                INSERT INTO gtfs_stops_times (
                    trip_id, stop_id, arrival_time, departure_time,
                    stop_sequence, shape_dist_traveled
                ) VALUES ($1, $2, '08:00:00', '08:00:00', 1, 0)
                """,
                trip_id,
                stop_id,
            )

        return {
            "trip_id": str(trip_id),
            "route_id": str(route_id),
            "service_id": str(service_id),
            "stop_id": str(stop_id),
            "_created": {
                "trip": str(trip_id),
                "route": str(route_id),
                "service": str(service_id),
                "stop": str(stop_id),
            },
        }
    finally:
        await conn.close()


async def _yearly_features(analysis_id: str) -> dict:
    conn = await asyncpg.connect(_dsn())
    try:
        features = await conn.fetchval(
            "SELECT features FROM yearly_analysis WHERE id = $1::uuid",
            uuid.UUID(analysis_id),
        )
        if isinstance(features, str):
            features = json.loads(features)
        return dict(features or {})
    finally:
        await conn.close()


def _create_shift(
    client: TestClient,
    *,
    user: dict[str, str],
    bus_id: str,
    trip_id: str,
    label: str,
) -> str:
    response = client.post(
        f"{USER}/shifts/",
        headers=_headers(user["token"]),
        json={
            "name": f"000 Isolation {label} shift {uuid.uuid4().hex[:8]}",
            "bus_id": bus_id,
            "trip_ids": [trip_id],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _insert_derived_and_gtfs_rows(ctx: dict) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        for side in ("a", "b"):
            user_id = uuid.UUID(ctx[side]["id"])
            resource = ctx[side]["resource"]
            prediction_id = await conn.fetchval(
                """
                INSERT INTO prediction_runs (
                    user_id, shift_id, bus_model_id, model_name,
                    prediction_stack, external_temp_celsius,
                    auxiliary_heating_type, occupancy_percent, status
                ) VALUES (
                    $1, $2, $3, 'greybox_qrf_production_crps_optimized_3',
                    'legacy', 10, 'default', 50, 'completed'
                ) RETURNING id
                """,
                user_id,
                uuid.UUID(resource["shift"]),
                uuid.UUID(resource["model"]),
            )
            optimization_id = await conn.fetchval(
                """
                INSERT INTO optimization_runs (
                    user_id, bus_model_id, mode, status, input_params,
                    prediction_run_ids, results, name
                ) VALUES (
                    $1, $2, 'battery_only', 'completed', $3::jsonb,
                    $4::jsonb, $5::jsonb, $6
                ) RETURNING id
                """,
                user_id,
                uuid.UUID(resource["model"]),
                json.dumps({"shift_ids": [resource["shift"]]}),
                json.dumps([str(prediction_id)]),
                json.dumps({"status": "completed"}),
                f"Isolation {side.upper()} optimization",
            )
            analysis_id = await conn.fetchval(
                """
                INSERT INTO yearly_analysis (optimization_run_id, name, features)
                VALUES ($1, $2, $3::jsonb)
                RETURNING id
                """,
                optimization_id,
                f"Isolation {side.upper()} yearly",
                json.dumps({"_owner_user_id": str(user_id)}),
            )

            aux_trip_id = await conn.fetchval(
                """
                INSERT INTO gtfs_trips (
                    route_id, service_id, gtfs_service_id, trip_id, status,
                    shape_id, trip_headsign, departure_time, arrival_time
                ) VALUES ($1, $2, 'isolation-service', $3, 'depot', $4,
                          $5, '08:00:00', '08:10:00')
                RETURNING id
                """,
                uuid.UUID(ctx["shared"]["route_id"]),
                uuid.UUID(ctx["shared"]["service_id"]),
                f"isolation_aux_{side}_{uuid.uuid4().hex}",
                f"isolation_shape_{side}_{uuid.uuid4().hex}",
                f"Isolation auxiliary {side.upper()}",
            )
            await conn.execute(
                """
                INSERT INTO elevation_profile_jobs (
                    trip_id, payload, status, output_object_name
                ) VALUES ($1, $2::jsonb, 'succeeded', $3)
                """,
                aux_trip_id,
                json.dumps({"owner_user_id": str(user_id)}),
                f"isolation/{aux_trip_id}.parquet",
            )
            await conn.execute(
                """
                INSERT INTO gtfs_stops_times (
                    trip_id, stop_id, arrival_time, departure_time,
                    stop_sequence, shape_dist_traveled
                ) VALUES
                    ($1, $2, '08:00:00', '08:00:00', 1, 0),
                    ($1, $2, '08:10:00', '08:10:00', 2, 1000)
                """,
                aux_trip_id,
                uuid.UUID(resource["depot_stop"]),
            )
            resource.update(
                prediction=str(prediction_id),
                optimization=str(optimization_id),
                yearly=str(analysis_id),
                aux_trip=str(aux_trip_id),
            )

        conflict_prediction = await conn.fetchval(
            """
            INSERT INTO prediction_runs (
                user_id, shift_id, bus_model_id, model_name,
                prediction_stack, external_temp_celsius,
                auxiliary_heating_type, occupancy_percent, status
            ) VALUES ($1, $2, $3, 'greybox_qrf_production_crps_optimized_3',
                      'legacy', 10, 'default', 50, 'completed')
            RETURNING id
            """,
            uuid.UUID(ctx["a"]["id"]),
            uuid.UUID(ctx["a"]["resource"]["shift"]),
            uuid.UUID(ctx["b"]["resource"]["model"]),
        )
        conflict_yearly = await conn.fetchval(
            """
            INSERT INTO yearly_analysis (optimization_run_id, name, features)
            VALUES ($1, 'Isolation conflicting ownership', $2::jsonb)
            RETURNING id
            """,
            uuid.UUID(ctx["b"]["resource"]["optimization"]),
            json.dumps({"_owner_user_id": ctx["a"]["id"]}),
        )
        ctx["conflict_prediction"] = str(conflict_prediction)
        ctx["conflict_yearly"] = str(conflict_yearly)

        foreign_agency = await conn.fetchval(
            """
            INSERT INTO gtfs_agencies (
                gtfs_agency_id, agency_name, agency_url, agency_timezone
            ) VALUES ($1, 'Isolation foreign agency', 'https://invalid.example',
                      'Europe/Zurich')
            RETURNING id
            """,
            f"isolation_foreign_{uuid.uuid4().hex}",
        )
        foreign_user = await conn.fetchval(
            """
            INSERT INTO users (
                company_id, email, full_name, password_hash, role
            ) VALUES ($1, $2, 'Isolation foreign user', 'not-used', 'analyst')
            RETURNING id
            """,
            foreign_agency,
            f"tmp_isolation_foreign_{uuid.uuid4().hex}@example.com",
        )
        foreign_route = await conn.fetchval(
            """
            INSERT INTO gtfs_routes (route_id, agency_id, route_short_name)
            VALUES ($1, $2, 'ISO-X') RETURNING id
            """,
            f"isolation_route_{uuid.uuid4().hex}",
            foreign_agency,
        )
        foreign_stop = await conn.fetchval(
            """
            INSERT INTO gtfs_stops (stop_id, stop_name, stop_lat, stop_lon)
            VALUES ($1, 'Isolation foreign stop', 47.0, 9.0)
            RETURNING id
            """,
            f"isolation_stop_{uuid.uuid4().hex}",
        )
        foreign_trip = await conn.fetchval(
            """
            INSERT INTO gtfs_trips (
                route_id, service_id, gtfs_service_id, trip_id, status, shape_id
            ) VALUES ($1, $2, 'isolation-foreign-service', $3, 'gtfs', $4)
            RETURNING id
            """,
            foreign_route,
            uuid.UUID(ctx["shared"]["service_id"]),
            f"isolation_foreign_trip_{uuid.uuid4().hex}",
            f"isolation_foreign_shape_{uuid.uuid4().hex}",
        )
        await conn.execute(
            """
            INSERT INTO gtfs_stops_times (trip_id, stop_id, stop_sequence)
            VALUES ($1, $2, 1)
            """,
            foreign_trip,
            foreign_stop,
        )
        ctx["foreign"] = {
            "agency": str(foreign_agency),
            "user": str(foreign_user),
            "route": str(foreign_route),
            "trip": str(foreign_trip),
            "stop": str(foreign_stop),
        }
    finally:
        await conn.close()


async def _cleanup(ctx: dict) -> None:
    conn = await asyncpg.connect(_dsn())
    try:
        user_ids = [
            uuid.UUID(side["id"])
            for key in ("a", "b")
            if (side := ctx.get(key)) and side.get("id")
        ]
        resources = [
            side["resource"]
            for key in ("a", "b")
            if (side := ctx.get(key)) and side.get("resource")
        ]
        yearly_ids = [
            uuid.UUID(resource["yearly"])
            for resource in resources
            if resource.get("yearly")
        ]
        if ctx.get("conflict_yearly"):
            yearly_ids.append(uuid.UUID(ctx["conflict_yearly"]))
        prediction_ids = [
            uuid.UUID(resource["prediction"])
            for resource in resources
            if resource.get("prediction")
        ]
        if ctx.get("conflict_prediction"):
            prediction_ids.append(uuid.UUID(ctx["conflict_prediction"]))
        optimization_ids = [
            uuid.UUID(resource["optimization"])
            for resource in resources
            if resource.get("optimization")
        ]
        aux_trip_ids = [
            uuid.UUID(resource["aux_trip"])
            for resource in resources
            if resource.get("aux_trip")
        ]

        if yearly_ids:
            await conn.execute(
                "DELETE FROM yearly_analysis WHERE id = ANY($1::uuid[])", yearly_ids
            )
        if prediction_ids:
            await conn.execute(
                "DELETE FROM prediction_runs WHERE id = ANY($1::uuid[])",
                prediction_ids,
            )
        if optimization_ids:
            await conn.execute(
                "DELETE FROM optimization_runs WHERE id = ANY($1::uuid[])",
                optimization_ids,
            )
        if aux_trip_ids:
            await conn.execute(
                "DELETE FROM gtfs_stops_times WHERE trip_id = ANY($1::uuid[])",
                aux_trip_ids,
            )
            await conn.execute(
                "DELETE FROM gtfs_trips WHERE id = ANY($1::uuid[])", aux_trip_ids
            )

        foreign = ctx.get("foreign") or {}
        if foreign.get("trip"):
            await conn.execute(
                "DELETE FROM gtfs_stops_times WHERE trip_id = $1::uuid",
                foreign["trip"],
            )
            await conn.execute(
                "DELETE FROM gtfs_trips WHERE id = $1::uuid", foreign["trip"]
            )
        if foreign.get("stop"):
            await conn.execute(
                "DELETE FROM gtfs_stops WHERE id = $1::uuid", foreign["stop"]
            )
        if foreign.get("route"):
            await conn.execute(
                "DELETE FROM gtfs_routes WHERE id = $1::uuid", foreign["route"]
            )
        if foreign.get("user"):
            await conn.execute(
                "DELETE FROM users WHERE id = $1::uuid", foreign["user"]
            )
        if foreign.get("agency"):
            await conn.execute(
                "DELETE FROM gtfs_agencies WHERE id = $1::uuid", foreign["agency"]
            )

        if user_ids:
            stop_ids = await conn.fetch(
                "SELECT stop_id FROM depots WHERE user_id = ANY($1::uuid[])",
                user_ids,
            )
            await conn.execute(
                "DELETE FROM users WHERE id = ANY($1::uuid[])", user_ids
            )
            orphan_stops = [row["stop_id"] for row in stop_ids if row["stop_id"]]
            if orphan_stops:
                await conn.execute(
                    "DELETE FROM gtfs_stops WHERE id = ANY($1::uuid[])",
                    orphan_stops,
                )

        shared_created = (ctx.get("shared") or {}).get("_created")
        if shared_created:
            await conn.execute(
                "DELETE FROM gtfs_stops_times WHERE trip_id = $1::uuid",
                shared_created["trip"],
            )
            await conn.execute(
                "DELETE FROM gtfs_trips WHERE id = $1::uuid",
                shared_created["trip"],
            )
            await conn.execute(
                "DELETE FROM gtfs_routes WHERE id = $1::uuid",
                shared_created["route"],
            )
            await conn.execute(
                "DELETE FROM gtfs_stops WHERE id = $1::uuid",
                shared_created["stop"],
            )
            await conn.execute(
                "DELETE FROM gtfs_calendar WHERE id = $1::uuid",
                shared_created["service"],
            )
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def isolation_context(client: TestClient) -> Iterator[dict]:
    _assert_staging_database()
    agency_id = os.environ.get("TEST_AGENCY_ID")
    assert agency_id, "TEST_AGENCY_ID is required for staging isolation tests"
    ctx: dict = {"agency_id": agency_id}
    try:
        ctx["a"] = _register_and_login(
            client, agency_id=agency_id, label="a", role="admin"
        )
        _run(_set_user_role(ctx["a"]["id"], "admin"))
        ctx["b"] = _register_and_login(
            client, agency_id=agency_id, label="b", role="viewer"
        )
        ctx["a"]["resource"] = _create_primary_resources(
            client, user=ctx["a"], label="A"
        )
        ctx["b"]["resource"] = _create_primary_resources(
            client, user=ctx["b"], label="B"
        )
        ctx["shared"] = _run(_find_shared_gtfs(agency_id))
        for side in ("a", "b"):
            ctx[side]["resource"]["shift"] = _create_shift(
                client,
                user=ctx[side],
                bus_id=ctx[side]["resource"]["bus"],
                trip_id=ctx["shared"]["trip_id"],
                label=side.upper(),
            )
        _run(_insert_derived_and_gtfs_rows(ctx))
        yield ctx
    finally:
        _run(_cleanup(ctx))


def _ids(payload: list[dict] | dict) -> set[str]:
    items = payload.get("items", []) if isinstance(payload, dict) else payload
    return {str(item["id"]) for item in items}


def test_same_agency_users_receive_only_their_private_rows(
    client: TestClient, isolation_context: dict
) -> None:
    ctx = isolation_context
    for side, other in (("a", "b"), ("b", "a")):
        headers = _headers(ctx[side]["token"])
        own = ctx[side]["resource"]
        foreign = ctx[other]["resource"]
        checks = (
            (f"{USER}/bus-models/?skip=0&limit=100", own["model"], foreign["model"]),
            (f"{USER}/buses/?skip=0&limit=100", own["bus"], foreign["bus"]),
            (f"{USER}/depots/?skip=0&limit=100", own["depot"], foreign["depot"]),
            (f"{USER}/shifts/?skip=0&limit=100", own["shift"], foreign["shift"]),
            (f"{SIM}/prediction-runs/", own["prediction"], foreign["prediction"]),
            (f"{SIM}/optimization-runs/?skip=0&limit=100", own["optimization"], foreign["optimization"]),
            (f"{YEARLY}/?skip=0&limit=100", own["yearly"], foreign["yearly"]),
            (f"{GTFS}/gtfs-stops/?skip=0&limit=100", own["depot_stop"], foreign["depot_stop"]),
        )
        for url, own_id, foreign_id in checks:
            response = client.get(url, headers=headers)
            assert response.status_code == 200, (url, response.text)
            returned = _ids(response.json())
            assert own_id in returned, (url, own_id, returned)
            assert foreign_id not in returned, (url, foreign_id, returned)

        response = client.get(
            f"{GTFS}/gtfs-trips/by-route/{ctx['shared']['route_id']}?status=depot",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        returned = _ids(response.json())
        assert own["aux_trip"] in returned
        assert foreign["aux_trip"] not in returned


@pytest.mark.parametrize("side", ["a", "b"])
def test_agency_gtfs_is_shared_but_other_agencies_are_hidden(
    client: TestClient, isolation_context: dict, side: str
) -> None:
    ctx = isolation_context
    headers = _headers(ctx[side]["token"])

    for url in (
        f"{GTFS}/gtfs-routes/{ctx['shared']['route_id']}",
        f"{GTFS}/gtfs-stops/{ctx['shared']['stop_id']}",
        f"{GTFS}/gtfs-calendar/by-trip/{ctx['shared']['trip_id']}",
        f"{GTFS}/gtfs-stops/by-trip/{ctx['shared']['trip_id']}",
    ):
        assert client.get(url, headers=headers).status_code == 200, url

    for url in (
        f"{GTFS}/gtfs-routes/{ctx['foreign']['route']}",
        f"{GTFS}/gtfs-stops/{ctx['foreign']['stop']}",
        f"{GTFS}/gtfs-calendar/by-trip/{ctx['foreign']['trip']}",
        f"{GTFS}/gtfs-stops/by-trip/{ctx['foreign']['trip']}",
    ):
        assert client.get(url, headers=headers).status_code == 404, url


def test_agency_admin_can_manage_accounts_only_inside_own_agency(
    client: TestClient, isolation_context: dict
) -> None:
    ctx = isolation_context
    headers = _headers(ctx["a"]["token"])

    response = client.get("/api/v1/agency/users/", headers=headers)
    assert response.status_code == 200, response.text
    returned = _ids(response.json())
    assert ctx["a"]["id"] in returned
    assert ctx["b"]["id"] in returned
    assert ctx["foreign"]["user"] not in returned
    assert all("password_hash" not in user for user in response.json())

    response = client.get(
        f"/api/v1/agency/users/{ctx['b']['id']}", headers=headers
    )
    assert response.status_code == 200, response.text
    assert "password_hash" not in response.json()
    assert client.get(
        f"/api/v1/agency/users/{ctx['foreign']['user']}", headers=headers
    ).status_code == 404
    assert client.put(
        f"/api/v1/agency/users/{ctx['foreign']['user']}",
        headers=headers,
        json={"full_name": "forbidden"},
    ).status_code == 404

    response = client.post(
        "/api/v1/agency/users/",
        headers=headers,
        json={
            "company_id": ctx["foreign"]["agency"],
            "email": f"forbidden_{uuid.uuid4().hex}@example.com",
            "full_name": "Forbidden foreign user",
            "password_hash": "not-used",
            "role": "analyst",
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 403, response.text
    assert client.get(
        f"/api/v1/agency/agencies/{ctx['foreign']['agency']}", headers=headers
    ).status_code == 404


def test_direct_foreign_reads_and_derived_calculations_are_404(
    client: TestClient, isolation_context: dict
) -> None:
    ctx = isolation_context
    headers = _headers(ctx["a"]["token"])
    foreign = ctx["b"]["resource"]
    urls = (
        f"{USER}/bus-models/{foreign['model']}",
        f"{USER}/buses/{foreign['bus']}",
        f"{USER}/depots/{foreign['depot']}",
        f"{USER}/shifts/{foreign['shift']}",
        f"{USER}/shifts/{foreign['shift']}/info",
        f"{USER}/shifts/{foreign['shift']}/yearly-distance?recurrence=daily",
        f"{SIM}/prediction-runs/{foreign['prediction']}",
        f"{SIM}/prediction-runs/{foreign['prediction']}/predictions",
        f"{SIM}/optimization-runs/{foreign['optimization']}",
        f"{SIM}/optimization-runs/{foreign['optimization']}/basis/aggregate_q95",
        f"{YEARLY}/{foreign['yearly']}",
        f"{YEARLY}/{foreign['yearly']}/energy-summary",
        f"{YEARLY}/{foreign['yearly']}/costs?bus_length_m=12",
        f"{YEARLY}/{foreign['yearly']}/lca",
        f"{YEARLY}/{foreign['yearly']}/emissions?bus_length_m=12",
        f"{GTFS}/gtfs-stops/{foreign['depot_stop']}",
        f"{GTFS}/gtfs-calendar/by-trip/{foreign['aux_trip']}",
        f"{GTFS}/gtfs-stops/by-trip/{foreign['aux_trip']}",
        f"{GTFS}/elevation-profile/by-trip/{foreign['aux_trip']}",
        f"{GTFS}/aux-trip/{foreign['aux_trip']}/elevation-status",
        f"{ECONOMIC}/opex/electric-maintenance?shift_id={foreign['shift']}&recurrence=daily&length_m=12",
        f"{ENVIRONMENTAL}/shifts/{foreign['shift']}/yearly-impact?recurrence=daily&passengers=20",
    )
    for url in urls:
        response = client.get(url, headers=headers)
        assert response.status_code == 404, (url, response.status_code, response.text)

    assert client.post(f"{SIM}/optimization-runs/{foreign['optimization']}/basis/aggregate_q95",
                       headers=headers).status_code == 404

    response = client.post(
        f"{SIM}/trip-statistics/",
        headers=headers,
        json={"trip_ids": [foreign["aux_trip"]]},
    )
    assert response.status_code == 404, response.text


def test_foreign_and_missing_ids_have_the_same_not_found_result(
    client: TestClient, isolation_context: dict
) -> None:
    headers = _headers(isolation_context["a"]["token"])
    foreign = isolation_context["b"]["resource"]
    missing = str(uuid.uuid4())
    for template, foreign_id in (
        (f"{USER}/bus-models/{{}}", foreign["model"]),
        (f"{USER}/buses/{{}}", foreign["bus"]),
        (f"{USER}/depots/{{}}", foreign["depot"]),
        (f"{USER}/shifts/{{}}", foreign["shift"]),
        (f"{SIM}/prediction-runs/{{}}", foreign["prediction"]),
        (f"{SIM}/optimization-runs/{{}}", foreign["optimization"]),
        (f"{YEARLY}/{{}}", foreign["yearly"]),
    ):
        foreign_response = client.get(template.format(foreign_id), headers=headers)
        missing_response = client.get(template.format(missing), headers=headers)
        assert foreign_response.status_code == missing_response.status_code == 404


def test_foreign_updates_and_deletes_are_denied_without_changing_rows(
    client: TestClient, isolation_context: dict
) -> None:
    ctx = isolation_context
    headers = _headers(ctx["a"]["token"])
    foreign = ctx["b"]["resource"]
    mutations = (
        ("put", f"{USER}/bus-models/{foreign['model']}", {"name": "forbidden"}),
        ("put", f"{USER}/buses/{foreign['bus']}", {"name": "forbidden"}),
        ("put", f"{USER}/depots/{foreign['depot']}", {"name": "forbidden"}),
        ("put", f"{USER}/shifts/{foreign['shift']}", {"name": "forbidden"}),
        ("patch", f"{YEARLY}/{foreign['yearly']}", {"name": "forbidden"}),
        ("put", f"{GTFS}/gtfs-trips/{foreign['aux_trip']}", {"trip_headsign": "forbidden"}),
    )
    for method, url, body in mutations:
        response = getattr(client, method)(url, headers=headers, json=body)
        assert response.status_code == 404, (method, url, response.text)

    # Even an agency administrator cannot mutate another user's depot stop.
    response = client.put(
        f"{GTFS}/gtfs-stops/{foreign['depot_stop']}",
        headers=headers,
        json={"stop_name": "forbidden"},
    )
    assert response.status_code == 404, response.text

    for url in (
        f"{USER}/bus-models/{foreign['model']}",
        f"{USER}/buses/{foreign['bus']}",
        f"{USER}/depots/{foreign['depot']}",
        f"{USER}/shifts/{foreign['shift']}",
        f"{SIM}/optimization-runs/{foreign['optimization']}",
        f"{YEARLY}/{foreign['yearly']}",
        f"{GTFS}/gtfs-trips/{foreign['aux_trip']}",
    ):
        response = client.delete(url, headers=headers)
        assert response.status_code == 404, (url, response.status_code, response.text)

    b_headers = _headers(ctx["b"]["token"])
    for url in (
        f"{USER}/bus-models/{foreign['model']}",
        f"{USER}/buses/{foreign['bus']}",
        f"{USER}/depots/{foreign['depot']}",
        f"{USER}/shifts/{foreign['shift']}",
        f"{SIM}/optimization-runs/{foreign['optimization']}",
        f"{YEARLY}/{foreign['yearly']}",
        f"{GTFS}/aux-trip/{foreign['aux_trip']}/elevation-status",
    ):
        response = client.get(url, headers=b_headers)
        assert response.status_code == 200, (url, response.status_code, response.text)


def test_create_on_behalf_and_cross_user_references_are_denied(
    client: TestClient, isolation_context: dict
) -> None:
    ctx = isolation_context
    headers = _headers(ctx["a"]["token"])
    own = ctx["a"]["resource"]
    foreign = ctx["b"]["resource"]

    response = client.post(
        f"{USER}/bus-models/",
        headers=headers,
        json={
            "user_id": ctx["b"]["id"],
            "name": f"forbidden-{uuid.uuid4().hex}",
            "specs": BUS_MODEL_SPECS,
        },
    )
    assert response.status_code == 403, response.text
    response = client.post(
        f"{USER}/buses/",
        headers=headers,
        json={
            "user_id": ctx["b"]["id"],
            "name": f"forbidden-{uuid.uuid4().hex}",
            "specs": {},
            "bus_model_id": own["model"],
        },
    )
    assert response.status_code == 403, response.text
    response = client.post(
        f"{USER}/depots/",
        headers=headers,
        json={
            "user_id": ctx["b"]["id"],
            "name": f"forbidden-{uuid.uuid4().hex}",
            "latitude": 46.0,
            "longitude": 8.9,
        },
    )
    assert response.status_code == 403, response.text

    response = client.post(
        f"{USER}/buses/",
        headers=headers,
        json={
            "user_id": ctx["a"]["id"],
            "name": f"forbidden-{uuid.uuid4().hex}",
            "specs": {},
            "bus_model_id": foreign["model"],
        },
    )
    assert response.status_code == 404, response.text
    response = client.put(
        f"{USER}/buses/{own['bus']}",
        headers=headers,
        json={"bus_model_id": foreign["model"]},
    )
    assert response.status_code == 404, response.text

    response = client.post(
        f"{USER}/shifts/",
        headers=headers,
        json={
            "name": "forbidden foreign bus",
            "bus_id": foreign["bus"],
            "trip_ids": [ctx["shared"]["trip_id"]],
        },
    )
    assert response.status_code == 404, response.text
    response = client.post(
        f"{USER}/shifts/",
        headers=headers,
        json={
            "name": "forbidden foreign trip",
            "bus_id": own["bus"],
            "trip_ids": [foreign["aux_trip"]],
        },
    )
    assert response.status_code == 404, response.text
    response = client.put(
        f"{USER}/shifts/{own['shift']}",
        headers=headers,
        json={"bus_id": foreign["bus"]},
    )
    assert response.status_code == 404, response.text
    response = client.put(
        f"{USER}/shifts/{own['shift']}",
        headers=headers,
        json={"trip_ids": [foreign["aux_trip"]]},
    )
    assert response.status_code == 404, response.text

    prediction_base = {
        "model_name": "greybox_qrf_production_crps_optimized_3",
        "prediction_stack": "legacy",
        "external_temp_celsius": 10,
    }
    response = client.post(
        f"{SIM}/prediction-runs/",
        headers=headers,
        json={
            **prediction_base,
            "shift_ids": [own["shift"]],
            "bus_model_id": foreign["model"],
        },
    )
    assert response.status_code == 404, response.text
    response = client.post(
        f"{SIM}/prediction-runs/",
        headers=headers,
        json={
            **prediction_base,
            "shift_ids": [foreign["shift"]],
            "bus_model_id": own["model"],
        },
    )
    assert response.status_code == 404, response.text
    response = client.post(
        f"{SIM}/prediction-runs/",
        headers=headers,
        json={
            **prediction_base,
            "shift_ids": [own["shift"]],
            "bus_model_id": own["model"],
            "yearly_analysis_id": foreign["yearly"],
        },
    )
    assert response.status_code == 404, response.text

    optimization_base = {
        "name": "forbidden optimization",
        "mode": "battery_only",
        "charging_stations": [],
    }
    response = client.post(
        f"{SIM}/optimization-runs/",
        headers=headers,
        json={
            **optimization_base,
            "shift_ids": [own["shift"]],
            "bus_model_id": foreign["model"],
            "prediction_run_ids": [own["prediction"]],
        },
    )
    assert response.status_code == 404, response.text
    response = client.post(
        f"{SIM}/optimization-runs/",
        headers=headers,
        json={
            **optimization_base,
            "shift_ids": [foreign["shift"]],
            "bus_model_id": own["model"],
            "prediction_run_ids": [own["prediction"]],
        },
    )
    assert response.status_code == 404, response.text
    response = client.post(
        f"{SIM}/optimization-runs/",
        headers=headers,
        json={
            **optimization_base,
            "shift_ids": [own["shift"]],
            "bus_model_id": own["model"],
            "prediction_run_ids": [foreign["prediction"]],
        },
    )
    assert response.status_code == 404, response.text

    response = client.post(
        f"{YEARLY}/",
        headers=headers,
        json={
            "name": "forbidden yearly",
            "optimization_run_id": foreign["optimization"],
            "features": {},
        },
    )
    assert response.status_code == 404, response.text
    response = client.patch(
        f"{YEARLY}/{own['yearly']}",
        headers=headers,
        json={"optimization_run_id": foreign["optimization"]},
    )
    assert response.status_code == 404, response.text


def test_conflicting_ownership_evidence_fails_closed_for_both_users(
    client: TestClient, isolation_context: dict
) -> None:
    ctx = isolation_context
    for side in ("a", "b"):
        headers = _headers(ctx[side]["token"])
        assert client.get(
            f"{SIM}/prediction-runs/{ctx['conflict_prediction']}",
            headers=headers,
        ).status_code == 404
        assert client.get(
            f"{YEARLY}/{ctx['conflict_yearly']}", headers=headers
        ).status_code == 404

        predictions = client.get(f"{SIM}/prediction-runs/", headers=headers)
        analyses = client.get(
            f"{YEARLY}/?skip=0&limit=100", headers=headers
        )
        assert predictions.status_code == analyses.status_code == 200
        assert ctx["conflict_prediction"] not in _ids(predictions.json())
        assert ctx["conflict_yearly"] not in _ids(analyses.json())


def test_own_crud_still_works_and_server_stamps_yearly_owner(
    client: TestClient, isolation_context: dict
) -> None:
    ctx = isolation_context
    user = ctx["a"]
    headers = _headers(user["token"])
    created: dict[str, str] = {}
    orphan_stop: str | None = None
    try:
        response = client.post(
            f"{USER}/bus-models/",
            headers=headers,
            json={
                "user_id": user["id"],
                "name": f"Isolation disposable {uuid.uuid4().hex}",
                "specs": BUS_MODEL_SPECS,
            },
        )
        assert response.status_code == 200, response.text
        created["model"] = response.json()["id"]

        response = client.put(
            f"{USER}/bus-models/{created['model']}",
            headers=headers,
            json={"description": "updated"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["description"] == "updated"

        response = client.post(
            f"{USER}/buses/",
            headers=headers,
            json={
                "user_id": user["id"],
                "name": f"Isolation disposable bus {uuid.uuid4().hex}",
                "specs": {},
                "bus_model_id": created["model"],
            },
        )
        assert response.status_code == 200, response.text
        created["bus"] = response.json()["id"]

        response = client.post(
            f"{USER}/shifts/",
            headers=headers,
            json={
                "name": "Isolation disposable shift",
                "bus_id": created["bus"],
                "trip_ids": [ctx["shared"]["trip_id"]],
            },
        )
        assert response.status_code == 200, response.text
        created["shift"] = response.json()["id"]
        assert client.put(
            f"{USER}/shifts/{created['shift']}",
            headers=headers,
            json={"name": "Isolation disposable shift updated"},
        ).status_code == 200

        response = client.post(
            f"{USER}/depots/",
            headers=headers,
            json={
                "user_id": user["id"],
                "name": "Isolation disposable depot",
                "latitude": 46.1,
                "longitude": 8.8,
            },
        )
        assert response.status_code == 200, response.text
        created["depot"] = response.json()["id"]
        orphan_stop = response.json()["stop_id"]
        assert client.put(
            f"{USER}/depots/{created['depot']}",
            headers=headers,
            json={"name": "Isolation disposable depot updated"},
        ).status_code == 200

        response = client.post(
            f"{YEARLY}/",
            headers=headers,
            json={"name": "Isolation disposable yearly", "features": {}},
        )
        assert response.status_code == 200, response.text
        created["yearly"] = response.json()["id"]
        assert "_owner_user_id" not in response.json()["features"]
        assert (
            _run(_yearly_features(created["yearly"]))["_owner_user_id"]
            == user["id"]
        )
        response = client.patch(
            f"{YEARLY}/{created['yearly']}",
            headers=headers,
            json={"name": "Isolation disposable yearly updated"},
        )
        assert response.status_code == 200, response.text
        assert "_owner_user_id" not in response.json()["features"]
        assert (
            _run(_yearly_features(created["yearly"]))["_owner_user_id"]
            == user["id"]
        )
    finally:
        if created.get("yearly"):
            client.delete(f"{YEARLY}/{created['yearly']}", headers=headers)
        if created.get("shift"):
            client.delete(f"{USER}/shifts/{created['shift']}", headers=headers)
        if created.get("depot"):
            client.delete(f"{USER}/depots/{created['depot']}", headers=headers)
        if created.get("bus"):
            client.delete(f"{USER}/buses/{created['bus']}", headers=headers)
        if created.get("model"):
            client.delete(f"{USER}/bus-models/{created['model']}", headers=headers)
        if orphan_stop:
            async def remove_orphan() -> None:
                conn = await asyncpg.connect(_dsn())
                try:
                    await conn.execute(
                        "DELETE FROM gtfs_stops WHERE id = $1::uuid", orphan_stop
                    )
                finally:
                    await conn.close()

            _run(remove_orphan())
