"""Fleet optimization tests using the available TPL shifts and four bus models.

Replicates the playground optimization (optimize_pyomo_gb_sensitivity.py)
through the checked-out API with staging data. Exercises depot charging, charging_only,
battery_only, and joint optimization modes.

Run with:  ./run_tests.sh tests/test_fleet_optimization.py
"""

import json
import os
import pathlib
import re
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from test_support.staging_cleanup import cleanup_staging_simulation_graph

__report_module__ = "fleet_optimization"

CAPTURED_BODIES_DIR = pathlib.Path(__file__).resolve().parent / "captured_bodies"


def _save_post_body(mode: str, body: dict) -> None:
    CAPTURED_BODIES_DIR.mkdir(exist_ok=True)
    dest = CAPTURED_BODIES_DIR / f"optimization_run_{mode}.json"
    with open(dest, "w") as f:
        json.dump(body, f, indent=2, default=str)


API_BASE = "/api/v1/user"
SIM_BASE = "/api/v1/simulation"
AUTH_BASE = "/auth"

SHIFT_DIR = (
    pathlib.Path(__file__).resolve().parent.parent
    / "playground" / "tpl" / "turni_macchina_2026" / "2026-TM_15f_lu-ve_TM_json"
)

ROUTE_TO_MODEL = {
    "401": "AA_NF", "402": "AU_NF", "403": "AA_NF",
    "404": "AA_NF", "405": "AA_NF", "406": "AA_NF",
    "407": "AA_NF", "408": "AU_NF",
    "409": "MB_NF", "410": "MB_NF",
    "412": "AL_NF", "415": "AL_NF", "416": "AL_NF",
    "417": "MB_NF", "418": "MB_NF",
}

BUS_MODEL_SPECS = {
    "AA_NF": {
        "bus_length_m": 18,
        "battery_pack_size_kwh": 88,
        "min_battery_packs": 3,
        "max_battery_packs": 8,
        "battery_pack_weight_kg": 603,
        "max_passengers": 131,
        "empty_weight_kg": 16493,
        "max_charging_power_kw": 450,
        "auxiliary_consumption_kw": {
            "default": {
                "temperature_celsius": [-5, 0, 5, 10, 15, 20, 25],
                "consumption_kw": [24, 16, 12, 8, 9, 10, 16],
            }
        },
    },
    "AU_NF": {
        "bus_length_m": 12,
        "battery_pack_size_kwh": 88,
        "min_battery_packs": 2,
        "max_battery_packs": 6,
        "battery_pack_weight_kg": 603,
        "max_passengers": 77,
        "empty_weight_kg": 11530,
        "max_charging_power_kw": 450,
        "auxiliary_consumption_kw": {
            "default": {
                "temperature_celsius": [-5, 0, 5, 10, 15, 20, 25],
                "consumption_kw": [17, 12, 9, 6, 7, 8, 12],
            }
        },
    },
    "AL_NF": {
        "bus_length_m": 10,
        "battery_pack_size_kwh": 88,
        "min_battery_packs": 2,
        "max_battery_packs": 4,
        "battery_pack_weight_kg": 603,
        "max_passengers": 53,
        "empty_weight_kg": 10200,
        "max_charging_power_kw": 450,
        "auxiliary_consumption_kw": {
            "default": {
                "temperature_celsius": [-5, 0, 5, 10, 15, 20, 25],
                "consumption_kw": [15, 10, 8, 6, 6, 7, 10],
            }
        },
    },
    "MB_NF": {
        "bus_length_m": 6,
        "battery_pack_size_kwh": 88,
        "min_battery_packs": 1,
        "max_battery_packs": 3,
        "battery_pack_weight_kg": 603,
        "max_passengers": 30,
        "empty_weight_kg": 6000,
        "max_charging_power_kw": 250,
        "auxiliary_consumption_kw": {
            "default": {
                "temperature_celsius": [-5, 0, 5, 10, 15, 20, 25],
                "consumption_kw": [8, 6, 5, 4, 4, 4, 6],
            }
        },
    },
}

CHARGING_STATIONS_BY_NAME = {
    "Albonago, Paese":            {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Breganzona, Posta":          {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Brè, Paese":                 {"slot_costs_chf": [350e3],                     "max_total_power_kw": 138.5,"max_power_per_slot_kw": 138.5},
    "Canobbio, Ganna":            {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Canobbio, Mercato Resega":   {"slot_costs_chf": [1e9],                       "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Castagnola, Capolinea":      {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Comano, Studio TV":          {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Lugano, Centro":             {"slot_costs_chf": [350e3, 150e3, 150e3, 150e3],"max_total_power_kw": 1000, "max_power_per_slot_kw": 300},
    "Lugano, Cornaredo":          {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Lugano, Pista Ghiaccio":     {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Lugano, Stazione":           {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Lugano, Stazione/Via Basilea": {"slot_costs_chf": [350e3],                   "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Manno, Uovo di Manno":       {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Muzzano, Paese":             {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Paradiso, Carzo":            {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Pazzallo, P+R Fornaci":      {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Piano Stampa, Capolinea":    {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Pregassona, Piazza di Giro": {"slot_costs_chf": [450e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
    "Viganello, S. Siro":         {"slot_costs_chf": [350e3],                     "max_total_power_kw": 450,  "max_power_per_slot_kw": 450},
}

DEPOT_CONFIG = {"slot_costs_chf": [100e3, 100e3, 100e3, 100e3], "max_total_power_kw": 1000, "max_power_per_slot_kw": 80}


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def get_auth_token(client: TestClient) -> str | None:
    token = os.getenv("TEST_API_TOKEN")
    if token:
        return token
    email = os.getenv("TEST_LOGIN_EMAIL")
    password = os.getenv("TEST_LOGIN_PASSWORD")
    if not email or not password:
        return None
    r = client.post(f"{AUTH_BASE}/login", json={"email": email, "password": password})
    if r.status_code != 200:
        return None
    return r.json().get("access_token")


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def current_user_id(client: TestClient, token: str) -> str:
    r = client.get(f"{AUTH_BASE}/me", headers=auth_headers(token))
    assert r.status_code == 200, f"fetch /me failed: {r.text}"
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Shift loading helpers
# ---------------------------------------------------------------------------

def _route_from_filename(fname: str) -> str:
    """Extract 3-digit route number from filename like '06_40201.json' -> '402'."""
    base = fname.replace("__part1", "").replace("__part2", "").replace("__part3", "")
    m = re.match(r"\d+_(\d{3})\d+\.json$", base)
    if m:
        return m.group(1)
    return "000"


def _shift_key(fname: str) -> str:
    """Group key for merging split files: '00_40101__part1.json' -> '00_40101'."""
    base = fname.split("__")[0]
    if base.endswith(".json"):
        base = base[:-5]
    return base


def load_shift_files(*, include_depots: bool = False) -> list[dict]:
    """
    Load all shift JSONs, merging __part1/__part2/__part3 into single shifts.
    Returns list of {key, route, model_name, trip_ids, name}.
    """
    if not SHIFT_DIR.exists():
        return []
    files = sorted(SHIFT_DIR.glob("*.json"))
    groups: dict[str, list[pathlib.Path]] = {}
    for f in files:
        k = _shift_key(f.name)
        groups.setdefault(k, []).append(f)

    shifts = []
    for key, paths in sorted(groups.items()):
        trip_ids: list[str] = []
        for p in sorted(paths):
            with open(p) as fh:
                trips = json.load(fh)
            trip_ids.extend(t["id"] for t in trips
                            if t.get("status") == "gtfs"
                            or (include_depots and t.get("status") == "depot"))
        if not trip_ids:
            continue
        route = _route_from_filename(paths[0].name)
        model_name = ROUTE_TO_MODEL.get(route, "AA_NF")
        shifts.append({
            "key": key,
            "route": route,
            "model_name": model_name,
            "trip_ids": trip_ids,
            "name": f"fleet_{key}",
        })
    return shifts


# ---------------------------------------------------------------------------
# Resolve charging opportunities from the selected shifts
# ---------------------------------------------------------------------------

def select_charging_stations(client, token, shift_ids, *, include_depots=False):
    """Use the same owned-shift termini endpoint as the frontend, never name lookup."""
    if not shift_ids:
        raise ValueError("At least one shift is required")
    response = client.post(
        f"{SIM_BASE}/charging-stops/candidates",
        json={"shift_ids": list(dict.fromkeys(shift_ids))},
        headers=auth_headers(token),
    )
    assert response.status_code == 200, response.text
    return configure_charging_stations(response.json(), include_depots=include_depots)


def configure_charging_stations(candidates, *, include_depots=False):
    """Apply fixture prices only to actual terminal IDs; retain distinct platforms."""
    stations, depots, seen = [], [], set()
    for candidate in candidates:
        sid, name = candidate["stop_id"], candidate["stop_name"]
        if sid in seen:
            continue
        seen.add(sid)
        if candidate["is_depot"]:
            # An intermediate return is also a charging opportunity, even if
            # no vehicle block finishes there (depot_bus_count == 0).
            if include_depots:
                slots = max(4, candidate.get("depot_bus_count", 0))
                depots.append(dict(stop_id=sid, slot_costs_chf=[100_000.] * slots,
                                   max_total_power_kw=2000.,
                                   max_power_per_slot_kw=DEPOT_CONFIG["max_power_per_slot_kw"]))
        elif name in CHARGING_STATIONS_BY_NAME:
            stations.append({"stop_id": sid, **CHARGING_STATIONS_BY_NAME[name]})
    return stations, depots


def discover_all_end_stops(trip_ids: list[str]) -> dict[str, str]:
    """Find all unique last-stop (stop_id, stop_name) pairs for the given trip IDs.

    Returns {stop_id_str: stop_name}.
    """
    import asyncio
    import asyncpg
    from app.core.config import get_cached_settings

    settings = get_cached_settings()
    dsn = settings.database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]

    async def _query():
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch("""
                SELECT DISTINCT ON (st.stop_id) st.stop_id, gs.stop_name
                FROM gtfs_stops_times st
                JOIN gtfs_stops gs ON gs.id = st.stop_id
                WHERE st.trip_id = ANY($1::uuid[])
                AND st.stop_sequence = (
                    SELECT MAX(st2.stop_sequence)
                    FROM gtfs_stops_times st2
                    WHERE st2.trip_id = st.trip_id
                )
            """, [uuid.UUID(tid) for tid in trip_ids])
        finally:
            await conn.close()
        return {str(row["stop_id"]): row["stop_name"] for row in rows}

    return asyncio.run(_query())


# ---------------------------------------------------------------------------
# Prediction + optimization helpers
# ---------------------------------------------------------------------------

def run_prediction_and_wait(
    client: TestClient, token: str, shift_ids: list[str],
    bus_model_id: str, max_wait: int = 180,
) -> list[str]:
    r = client.post(
        f"{SIM_BASE}/prediction-runs/",
        json={
            "shift_ids": shift_ids,
            "bus_model_id": bus_model_id,
            "model_name": "greybox_qrf_production_crps_optimized_3",
            "external_temp_celsius": -5.0,
            "occupancy_percent": 50.0,
            "quantiles": [0.05, 0.5, 0.95],
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"prediction submit failed: {r.text}"
    run_ids = r.json()["prediction_run_ids"]

    for _ in range(max_wait):
        time.sleep(1)
        all_done = True
        for rid in run_ids:
            rr = client.get(f"{SIM_BASE}/prediction-runs/{rid}", headers=auth_headers(token))
            if rr.status_code == 200 and rr.json()["status"] in ("completed", "failed"):
                continue
            all_done = False
            break
        if all_done:
            break

    for rid in run_ids:
        rr = client.get(f"{SIM_BASE}/prediction-runs/{rid}", headers=auth_headers(token))
        assert rr.json()["status"] == "completed", f"Prediction {rid} failed: {rr.json()}"

    return run_ids


def wait_for_optimization(
    client: TestClient, token: str, run_id: str, max_wait: int = 600,
) -> dict:
    for _ in range(max_wait):
        time.sleep(1)
        r = client.get(f"{SIM_BASE}/optimization-runs/{run_id}", headers=auth_headers(token))
        assert r.status_code == 200
        data = r.json()
        if data["status"] in ("completed", "failed"):
            return data
    pytest.fail(f"Optimization did not complete in {max_wait}s")


# ---------------------------------------------------------------------------
# Skip condition
# ---------------------------------------------------------------------------

_SKIP_REASON = "Requires TEST_ROUTE_ID and auth env vars and shift data"
_skip_cond = not (
    os.getenv("TEST_ROUTE_ID")
    and (
        os.getenv("TEST_API_TOKEN")
        or (os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD"))
    )
    and SHIFT_DIR.exists()
)


# ---------------------------------------------------------------------------
# Module-scoped fixture: create bus models, shifts, run predictions
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fleet_env(client: TestClient, request):
    return build_fleet_env(client, request, load_shift_files())


@pytest.fixture(scope="module")
def depot_env(client: TestClient, request):
    # Preserve the source depot movements, including returns between split duties.
    from test_support.fleet_depot_fixture import owned_depot_shift_copies
    token = get_auth_token(client)
    assert token, "Authentication failed"
    shifts = owned_depot_shift_copies(
        request, load_shift_files(include_depots=True)[:5], current_user_id(client, token))
    return build_fleet_env(client, request, shifts)


def build_fleet_env(client, request, shift_defs):
    """Create only temporary models/buses/shifts and clean them even after setup errors."""
    token = get_auth_token(client)
    assert token, "Authentication failed"
    hdrs = auth_headers(token)
    user_id = current_user_id(client, token)

    model_ids: dict[str, str] = {}
    bus_ids: dict[str, str] = {}
    shift_records: list[dict] = []
    request.addfinalizer(lambda: cleanup_staging_simulation_graph(
        model_ids=list(model_ids.values()), bus_ids=list(bus_ids.values()),
        shift_ids=[s["shift_id"] for s in shift_records]))

    # 1. Create bus models
    for model_name, specs in BUS_MODEL_SPECS.items():
        unique = uuid.uuid4().hex[:8]
        r = client.post(
            f"{API_BASE}/bus-models/",
            json={"name": f"{model_name}_fleet_{unique}", "specs": specs, "user_id": user_id},
            headers=hdrs,
        )
        assert r.status_code == 200, f"create bus model {model_name} failed: {r.text}"
        model_ids[model_name] = r.json()["id"]

    # 2. Create one bus per model
    for model_name, model_id in model_ids.items():
        unique = uuid.uuid4().hex[:8]
        r = client.post(
            f"{API_BASE}/buses/",
            json={"user_id": user_id, "name": f"Bus {model_name} {unique}", "specs": {}, "bus_model_id": model_id},
            headers=hdrs,
        )
        assert r.status_code == 200, f"create bus {model_name} failed: {r.text}"
        bus_ids[model_name] = r.json()["id"]

    # 3. Validate the requested fixture scope
    assert len(shift_defs) > 0, "No shift files found"

    # 4. Create shifts via API
    for sd in shift_defs:
        model_name = sd["model_name"]
        bus_id = bus_ids[model_name]
        r = client.post(
            f"{API_BASE}/shifts/",
            json={"name": sd["name"], "bus_id": bus_id, "trip_ids": sd["trip_ids"]},
            headers=hdrs,
        )
        assert r.status_code == 200, f"create shift {sd['name']} failed: {r.text}"
        shift_records.append({
            "shift_id": r.json()["id"],
            "model_name": model_name,
            "bus_model_id": model_ids[model_name],
            "key": sd["key"],
            "route": sd["route"],
            "trip_ids": sd["trip_ids"],
        })

    # 5. Run predictions in batches (by bus model)
    prediction_run_ids: dict[str, list[str]] = {}  # shift_id -> [pred_run_id]
    for model_name in BUS_MODEL_SPECS:
        model_shifts = [s for s in shift_records if s["model_name"] == model_name]
        if not model_shifts:
            continue
        model_shift_ids = [s["shift_id"] for s in model_shifts]
        bus_model_id = model_ids[model_name]
        pred_ids = run_prediction_and_wait(client, token, model_shift_ids, bus_model_id)
        for sid, pid in zip(model_shift_ids, pred_ids):
            prediction_run_ids[sid] = [pid]

    # 6. Scope candidates to this fixture's trips and the logged-in user's depots.
    charging_stations_config, depot_stations_config = select_charging_stations(
        client, token, [s["shift_id"] for s in shift_records], include_depots=True)

    env = {
        "token": token,
        "user_id": user_id,
        "model_ids": model_ids,
        "bus_ids": bus_ids,
        "shift_records": shift_records,
        "prediction_run_ids": prediction_run_ids,
        "charging_stations_config": charging_stations_config,
        "depot_stations_config": depot_stations_config,
        "optimization_run_ids": [],
    }

    return env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_fleet_setup_complete(client: TestClient, fleet_env, record):
    """Verify that all shifts were created and predictions completed."""
    n_shifts = len(fleet_env["shift_records"])
    n_predictions = len(fleet_env["prediction_run_ids"])
    n_stations = len(fleet_env["charging_stations_config"])
    n_depot = len(fleet_env["depot_stations_config"])
    ok = n_shifts > 0 and n_predictions == n_shifts and n_stations > 0
    record(
        "fleet_setup_complete",
        ok,
        f"shifts={n_shifts}, predictions={n_predictions}, "
        f"candidate_stations={n_stations} (selected trip termini), depot={n_depot}",
    )


def _collect_all_ids(fleet_env) -> tuple[list[str], list[str]]:
    """Collect all shift_ids and prediction_run_ids from the fleet env."""
    all_shift_ids = [s["shift_id"] for s in fleet_env["shift_records"]]
    all_pred_ids = []
    for sid in all_shift_ids:
        all_pred_ids.extend(fleet_env["prediction_run_ids"].get(sid, []))
    return all_shift_ids, all_pred_ids


def optimization_is_conclusive(data):
    """A completed API job may still contain a timeout or unverified forecast."""
    result = data.get("results") or {}
    verification = result.get("forecast_verification") or {}
    return (
        data.get("status") == "completed"
        and result.get("solver_status") in {"optimal", "infeasible"}
        and verification.get("status") in {"verified", "physical_limits_exceeded", "infeasible"}
    )


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
@pytest.mark.parametrize("subset_size", [1, 5, None])
def test_fleet_candidates_match_selected_trip_termini(client, fleet_env, subset_size):
    """Independent stop-times oracle excludes intermediate/other-edition/parent rows."""
    selected = fleet_env["shift_records"][:subset_size]
    trip_ids = list(dict.fromkeys(tid for shift in selected for tid in shift["trip_ids"]))
    expected = discover_all_end_stops(trip_ids)
    response = client.post(
        f"{SIM_BASE}/charging-stops/candidates",
        json={"shift_ids": [s["shift_id"] for s in selected]},
        headers=auth_headers(fleet_env["token"]),
    )
    assert response.status_code == 200, response.text
    candidates = response.json()
    assert len(candidates) == len(expected)
    assert {c["stop_id"]: c["stop_name"] for c in candidates} == expected
    stations, depots = configure_charging_stations(candidates)
    assert not depots
    assert {s["stop_id"] for s in stations} == {
        sid for sid, name in expected.items() if name in CHARGING_STATIONS_BY_NAME
    }


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_fleet_charging_only(client: TestClient, fleet_env, record):
    """Run charging_only optimization on ALL shifts in a single run."""
    token = fleet_env["token"]
    all_shift_ids, all_pred_ids = _collect_all_ids(fleet_env)
    stations = fleet_env["charging_stations_config"]

    body = {
        "name": "Fleet charging-only integration test",
        "mode": "charging_only",
        "shift_ids": all_shift_ids,
        "prediction_run_ids": all_pred_ids,
        "charging_stations": stations,
        "min_soc": 0.4,
        "max_soc": 0.9,
        "lock_entire_dwell": True,
        "solver_name": "highs",
        "max_solver_time_seconds": 600,
    }
    _save_post_body("charging_only", body)
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json=body,
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"charging_only submit failed: {r.text}"
    run_id = r.json()["optimization_run_id"]
    fleet_env["optimization_run_ids"].append(run_id)
    data = wait_for_optimization(client, token, run_id, max_wait=900)

    res = data.get("results", {})
    ok = optimization_is_conclusive(data)
    record(
        "fleet_charging_only", ok,
        f"status={data['status']}, buses={len(all_shift_ids)}, "
        f"obj={res.get('objective_value', 'N/A')}, "
        f"install_cost={res.get('total_installation_cost_chf', 'N/A')}, "
        f"solver={res.get('solver_status', 'N/A')}, "
        f"time={res.get('solve_time_seconds', 'N/A')}s",
    )
    assert ok, f"Optimization failed: {res.get('solver_status')}"


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_fleet_battery_only(client: TestClient, fleet_env, record):
    """Run battery_only optimization on ALL shifts: fix chargers, optimize battery size."""
    token = fleet_env["token"]
    all_shift_ids, all_pred_ids = _collect_all_ids(fleet_env)

    stations_fixed = [
        {
            "stop_id": s["stop_id"],
            "num_slots": 2,
            "max_total_power_kw": s["max_total_power_kw"],
            **({"max_power_per_slot_kw": s["max_power_per_slot_kw"]} if "max_power_per_slot_kw" in s else {}),
        }
        for s in fleet_env["charging_stations_config"]
    ]

    body = {
        "name": "Fleet battery-only integration test",
        "mode": "battery_only",
        "shift_ids": all_shift_ids,
        "prediction_run_ids": all_pred_ids,
        "charging_stations": stations_fixed,
        "min_soc": 0.4,
        "max_soc": 0.9,
        "lock_entire_dwell": True,
        "solver_name": "highs",
        "max_solver_time_seconds": 600,
    }
    _save_post_body("battery_only", body)
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json=body,
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"battery_only submit failed: {r.text}"
    run_id = r.json()["optimization_run_id"]
    fleet_env["optimization_run_ids"].append(run_id)
    data = wait_for_optimization(client, token, run_id, max_wait=900)

    res = data.get("results", {})
    ok = optimization_is_conclusive(data)
    batt = res.get("battery_results", {})
    excess = sum(1 for v in batt.values() if v.get("excess_packs", 0) > 0)
    record(
        "fleet_battery_only", ok,
        f"status={data['status']}, buses={len(all_shift_ids)}, "
        f"buses_needing_excess_packs={excess}/{len(batt)}, "
        f"solver={res.get('solver_status', 'N/A')}, "
        f"time={res.get('solve_time_seconds', 'N/A')}s",
    )
    assert ok, f"Optimization failed: {res.get('solver_status')}"


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_fleet_joint(client: TestClient, fleet_env, record):
    """Run joint optimization on ALL shifts: charger installation + battery sizing."""
    token = fleet_env["token"]
    all_shift_ids, all_pred_ids = _collect_all_ids(fleet_env)
    stations = fleet_env["charging_stations_config"]

    body = {
        "name": "Fleet joint integration test",
        "mode": "joint",
        "shift_ids": all_shift_ids,
        "prediction_run_ids": all_pred_ids,
        "charging_stations": stations,
        "min_soc": 0.4,
        "max_soc": 0.9,
        "battery_cost_per_kwh": 300.0,
        "max_battery_penalty_per_kwh": 1e6,
        "battery_sizing_mode": "per_route",
        "lock_entire_dwell": True,
        "solver_name": "highs",
        "max_solver_time_seconds": 600,
    }
    _save_post_body("joint", body)
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json=body,
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"joint submit failed: {r.text}"
    run_id = r.json()["optimization_run_id"]
    fleet_env["optimization_run_ids"].append(run_id)
    data = wait_for_optimization(client, token, run_id, max_wait=900)

    res = data.get("results", {})
    ok = optimization_is_conclusive(data)
    record(
        "fleet_joint", ok,
        f"status={data['status']}, buses={len(all_shift_ids)}, "
        f"obj={res.get('objective_value', 'N/A')}, "
        f"install_cost={res.get('total_installation_cost_chf', 'N/A')}, "
        f"batt_cost={res.get('total_battery_cost_chf', 'N/A')}, "
        f"solver={res.get('solver_status', 'N/A')}, "
        f"time={res.get('solve_time_seconds', 'N/A')}s",
    )
    assert ok, f"Optimization failed: {res.get('solver_status')}"


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_depot_charging_utilized(client: TestClient, depot_env, record):
    """Verify actual depot energy with real return trips and post-shift dwell enabled."""
    token = depot_env["token"]

    # Separate five-shift fixture retaining its actual depot movements.
    subset = depot_env["shift_records"][:5]
    subset_shift_ids = [s["shift_id"] for s in subset]
    subset_pred_ids = []
    for sid in subset_shift_ids:
        subset_pred_ids.extend(depot_env["prediction_run_ids"].get(sid, []))

    assert depot_env["depot_stations_config"], "No selected shift actually returns to an owned depot"

    # Include only depots reached by this subset
    stations = depot_env["charging_stations_config"] + depot_env["depot_stations_config"]

    # Fixed chargers for battery_only mode
    stations_fixed = [
        {
            "stop_id": s["stop_id"],
            "num_slots": max(len(s.get("slot_costs_chf", [1])), 2),
            "max_total_power_kw": s["max_total_power_kw"],
            **({"max_power_per_slot_kw": s["max_power_per_slot_kw"]} if "max_power_per_slot_kw" in s else {}),
        }
        for s in stations
    ]

    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "name": "Fleet depot-charging integration test",
            "mode": "battery_only",
            "shift_ids": subset_shift_ids,
            "prediction_run_ids": subset_pred_ids,
            "charging_stations": stations_fixed,
            "min_soc": 0.4,
            "max_soc": 0.9,
            "depot_dwell_minutes_after": 120,
            "lock_entire_dwell": True,
            "solver_name": "highs",
            "max_solver_time_seconds": 300,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"depot charging submit failed: {r.text}"
    run_id = r.json()["optimization_run_id"]
    depot_env["optimization_run_ids"].append(run_id)
    data = wait_for_optimization(client, token, run_id, max_wait=600)

    completed = optimization_is_conclusive(data)
    results = data.get("results", {})
    station_util = results.get("station_utilization", {})

    depot_stop_ids = {s["stop_id"] for s in depot_env["depot_stations_config"]}

    depot_energy = sum(
        station_util.get(sid, {}).get("total_energy_kwh", 0)
        for sid in depot_stop_ids
    )

    record(
        "depot_charging_utilized",
        completed and depot_energy > 0,
        f"status={data['status']}, depot_energy={depot_energy:.1f} kWh, "
        f"shifts={len(subset)}, candidate_stations={len(stations)}, "
        f"depot_stations_found={len(depot_stop_ids)}",
    )

    assert completed, f"Depot optimization failed: {results}"
    assert depot_energy > 0, "The solver completed without charging at any selected depot"
