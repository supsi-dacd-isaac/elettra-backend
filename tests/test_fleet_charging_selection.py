"""Regressions for the fleet integration fixtures, without simulation writes."""
import json
from types import SimpleNamespace

import pytest

import test_fleet_optimization as fleet


def candidate(sid, name="Manno, Uovo di Manno", *, depot=False, buses=0):
    return dict(stop_id=sid, stop_name=name, is_depot=depot, depot_bus_count=buses)


def test_distinct_platforms_survive_repeated_terminal_occurrences():
    rows = [candidate("platform-a"), candidate("platform-b"), candidate("platform-a")]
    stations, depots = fleet.configure_charging_stations(rows)
    assert [s["stop_id"] for s in stations] == ["platform-a", "platform-b"]
    assert not depots
    for station in stations:
        assert station["slot_costs_chf"] == [350_000.]
        assert station["max_total_power_kw"] == 450
        assert station["max_power_per_slot_kw"] == 450


def test_unconfigured_terminal_is_not_assigned_an_invented_charger():
    assert fleet.configure_charging_stations([candidate("x", "Unconfigured")]) == ([], [])


@pytest.mark.parametrize("include,buses,expected", [(False, 2, 0), (True, 0, 1), (True, 2, 1)])
def test_reached_depot_includes_intermediate_returns_when_enabled(include, buses, expected):
    stations, depots = fleet.configure_charging_stations(
        [candidate("depot", "TPL Rimessa 1", depot=True, buses=buses)], include_depots=include)
    assert not stations
    assert len(depots) == expected


def test_depot_capacity_uses_returning_subset_not_entire_fleet():
    _, depots = fleet.configure_charging_stations(
        [candidate("depot", "TPL Rimessa 2", depot=True, buses=5)], include_depots=True)
    assert depots[0]["slot_costs_chf"] == [100_000.] * 5
    assert depots[0]["max_power_per_slot_kw"] == 80


def test_no_silent_truncation_at_api_limit():
    rows = [candidate(str(i)) for i in range(101)]
    stations, _ = fleet.configure_charging_stations(rows)
    assert len(stations) == 101


def test_selection_requests_only_selected_shifts_and_propagates_auth():
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: [candidate("selected")])

    stations, _ = fleet.select_charging_stations(SimpleNamespace(post=post), "token", ["s2", "s2"])
    assert [s["stop_id"] for s in stations] == ["selected"]
    assert calls == [("/api/v1/simulation/charging-stops/candidates", {
        "json": {"shift_ids": ["s2"]}, "headers": {"Authorization": "Bearer token"}})]


@pytest.mark.parametrize("status", [403, 404, 500])
def test_candidate_failure_is_not_silently_replaced_with_global_name_lookup(status):
    client = SimpleNamespace(post=lambda *a, **kw: SimpleNamespace(status_code=status, text="unavailable"))
    with pytest.raises(AssertionError, match="unavailable"):
        fleet.select_charging_stations(client, "token", ["shift"])


def test_empty_shift_selection_fails_before_request():
    with pytest.raises(ValueError, match="At least one shift"):
        fleet.select_charging_stations(None, "token", [])


@pytest.mark.parametrize("job,solver,verification,expected", [
    ("completed", "optimal", "verified", True),
    ("completed", "optimal", "physical_limits_exceeded", True),
    ("completed", "infeasible", "infeasible", True),
    ("completed", "maxTimeLimit", "solver_not_optimal", False),
    ("completed", "optimal", "time_budget_exhausted", False),
    ("completed", "optimal", "cycle_detected", False),
    ("failed", "optimal", "verified", False),
    ("running", None, "running", False),
    ("completed", "optimal", None, False),
])
def test_completion_does_not_hide_inconclusive_solver_results(job, solver, verification, expected):
    assert fleet.optimization_is_conclusive(dict(status=job, results=dict(
        solver_status=solver, forecast_verification=dict(status=verification)))) is expected


def test_split_duties_preserve_depot_movements_in_original_order(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet, "SHIFT_DIR", tmp_path)
    for part, trips in enumerate([
        [("out-a", "depot"), ("trip-a", "gtfs"), ("in-a", "depot")],
        [("out-b", "depot"), ("trip-b", "gtfs"), ("in-b", "depot")],
    ], 1):
        (tmp_path / f"00_40101__part{part}.json").write_text(json.dumps(
            [dict(id=tid, status=status) for tid, status in trips]))
    assert fleet.load_shift_files()[0]["trip_ids"] == ["trip-a", "trip-b"]
    complete = fleet.load_shift_files(include_depots=True)
    assert len(complete) == 1
    assert complete[0]["trip_ids"] == ["out-a", "trip-a", "in-a", "out-b", "trip-b", "in-b"]


@pytest.mark.skipif(fleet._skip_cond, reason=fleet._SKIP_REASON)
def test_owned_depot_copies_preserve_sources_and_cleanup_exact_ids(client, request):
    import asyncio
    from copy import deepcopy
    from uuid import UUID
    import asyncpg
    from app.core.config import get_cached_settings
    from test_support.fleet_depot_fixture import owned_depot_shift_copies
    from test_support.staging_targets import validate_test_targets

    settings = get_cached_settings()
    validate_test_targets(settings.database_url)
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    source = fleet.load_shift_files(include_depots=True)[:1]
    original = deepcopy(source)
    original_ids = [UUID(t) for t in source[0]["trip_ids"]]
    token = fleet.get_auth_token(client)
    assert token
    owner = fleet.current_user_id(client, token)
    cleanup_callbacks = []

    def register(callback):
        cleanup_callbacks.append(callback)
        request.addfinalizer(callback)

    async def inspect(ids):
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetch("""
                SELECT t.id, to_jsonb(t)::text AS trip,
                       to_jsonb(j)::text AS job, j.payload->>'owner_user_id' AS owner,
                       j.output_object_name AS profile,
                       (SELECT jsonb_agg(to_jsonb(st) ORDER BY st.stop_sequence)::text
                        FROM gtfs_stops_times st WHERE st.trip_id=t.id) AS stops
                FROM gtfs_trips t LEFT JOIN elevation_profile_jobs j ON j.trip_id=t.id
                WHERE t.id=ANY($1::uuid[]) ORDER BY t.id
            """, ids)
        finally:
            await conn.close()

    before = asyncio.run(inspect(original_ids))
    copies = owned_depot_shift_copies(SimpleNamespace(addfinalizer=register), source, owner)
    assert source == original
    copied_ids = [UUID(t) for t in copies[0]["trip_ids"] if UUID(t) not in original_ids]
    assert copied_ids
    copied_rows = asyncio.run(inspect(copied_ids))
    assert len(copied_rows) == len(copied_ids)
    source_profiles = {row["profile"] for row in before if row["profile"]}
    for row in copied_rows:
        assert row["owner"] == owner
        assert row["profile"] in source_profiles
        assert row["stops"]
    assert asyncio.run(inspect(original_ids)) == before
    cleanup_callbacks[0]()
    assert asyncio.run(inspect(copied_ids)) == []
    assert asyncio.run(inspect(original_ids)) == before
