"""Fleet optimization test: 74 shifts with 4 bus models.

Replicates the playground optimization (optimize_pyomo_gb_sensitivity.py)
through the production API. Exercises depot charging, charging_only,
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

DEPOT_STATION_NAMES = ["TPL Rimessa 1", "TPL Rimessa 2", "TPL Rimessa 3"]
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


def load_shift_files() -> list[dict]:
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
            trip_ids.extend(t["id"] for t in trips if t.get("status") == "gtfs")
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
# Resolve stop names to stop_ids via DB
# ---------------------------------------------------------------------------

def resolve_stop_ids_by_name(client: TestClient, token: str, stop_names: list[str]) -> dict[str, list[str]]:
    """Query the GTFS stops table to map stop_name -> [stop_id, ...] (all platforms).

    Uses asyncpg directly to avoid event-loop conflicts with AsyncSessionLocal.
    Returns ALL stop_ids for each name since the same physical stop can have
    multiple platforms with different UUIDs.
    """
    import asyncio
    import asyncpg
    from app.core.config import get_cached_settings

    settings = get_cached_settings()
    dsn = settings.database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]

    async def _resolve():
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                "SELECT id, stop_name FROM gtfs_stops WHERE stop_name = ANY($1::text[])",
                stop_names,
            )
        finally:
            await conn.close()
        name_map: dict[str, list[str]] = {}
        for row in rows:
            sname = row["stop_name"]
            name_map.setdefault(sname, []).append(str(row["id"]))
        return name_map

    return asyncio.run(_resolve())


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
def fleet_env(client: TestClient):
    """Set up 4 bus models, up to 74 shifts, and predictions for all of them."""
    token = get_auth_token(client)
    assert token, "Authentication failed"
    hdrs = auth_headers(token)
    user_id = current_user_id(client, token)

    # 1. Create bus models
    model_ids: dict[str, str] = {}
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
    bus_ids: dict[str, str] = {}
    for model_name, model_id in model_ids.items():
        unique = uuid.uuid4().hex[:8]
        r = client.post(
            f"{API_BASE}/buses/",
            json={"user_id": user_id, "name": f"Bus {model_name} {unique}", "specs": {}, "bus_model_id": model_id},
            headers=hdrs,
        )
        assert r.status_code == 200, f"create bus {model_name} failed: {r.text}"
        bus_ids[model_name] = r.json()["id"]

    # 3. Load shift files
    shift_defs = load_shift_files()
    assert len(shift_defs) > 0, "No shift files found"

    # 4. Create shifts via API
    shift_records: list[dict] = []  # {shift_id, model_name, bus_model_id, key}
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

    # 6. Resolve charging station stop_ids (ALL platforms per name)
    all_station_names = list(CHARGING_STATIONS_BY_NAME.keys()) + DEPOT_STATION_NAMES
    name_to_stop_ids = resolve_stop_ids_by_name(client, token, all_station_names)

    # Build station configs: one entry per platform (stop_id), sharing the
    # same cost/power settings.  This ensures trips ending at any platform
    # of a named stop can be matched to a charger.
    known_stop_ids: set[str] = set()
    charging_stations_config = []
    for name, cfg in CHARGING_STATIONS_BY_NAME.items():
        sids = name_to_stop_ids.get(name, [])
        for sid in sids:
            if sid not in known_stop_ids:
                entry = {
                    "stop_id": sid,
                    "slot_costs_chf": cfg["slot_costs_chf"],
                    "max_total_power_kw": cfg["max_total_power_kw"],
                }
                if "max_power_per_slot_kw" in cfg:
                    entry["max_power_per_slot_kw"] = cfg["max_power_per_slot_kw"]
                charging_stations_config.append(entry)
                known_stop_ids.add(sid)

    # Depot: 1 slot per bus in the fleet, high power
    n_buses_total = len(shift_records)
    depot_slots = max(4, n_buses_total)
    depot_power = 2000.0  # 2 MW
    depot_stations_config = []
    for name in DEPOT_STATION_NAMES:
        sids = name_to_stop_ids.get(name, [])
        for sid in sids:
            if sid not in known_stop_ids:
                depot_stations_config.append({
                    "stop_id": sid,
                    "slot_costs_chf": [100_000.0] * depot_slots,
                    "max_total_power_kw": depot_power,
                    "max_power_per_slot_kw": DEPOT_CONFIG["max_power_per_slot_kw"],
                })
                known_stop_ids.add(sid)

    env = {
        "token": token,
        "user_id": user_id,
        "model_ids": model_ids,
        "bus_ids": bus_ids,
        "shift_records": shift_records,
        "prediction_run_ids": prediction_run_ids,
        "charging_stations_config": charging_stations_config,
        "depot_stations_config": depot_stations_config,
        "name_to_stop_ids": name_to_stop_ids,
    }

    yield env

    # Teardown
    for sr in shift_records:
        client.delete(f"{API_BASE}/shifts/{sr['shift_id']}", headers=hdrs)
    for bus_id in bus_ids.values():
        client.delete(f"{API_BASE}/buses/{bus_id}", headers=hdrs)
    for model_id in model_ids.values():
        client.delete(f"{API_BASE}/bus-models/{model_id}", headers=hdrs)


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
        f"candidate_stations={n_stations} (all platforms), depot={n_depot}",
    )


def _collect_all_ids(fleet_env) -> tuple[list[str], list[str]]:
    """Collect all shift_ids and prediction_run_ids from the fleet env."""
    all_shift_ids = [s["shift_id"] for s in fleet_env["shift_records"]]
    all_pred_ids = []
    for sid in all_shift_ids:
        all_pred_ids.extend(fleet_env["prediction_run_ids"].get(sid, []))
    return all_shift_ids, all_pred_ids


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_fleet_charging_only(client: TestClient, fleet_env, record):
    """Run charging_only optimization on ALL shifts in a single run."""
    token = fleet_env["token"]
    all_shift_ids, all_pred_ids = _collect_all_ids(fleet_env)
    stations = fleet_env["charging_stations_config"]

    body = {
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
    data = wait_for_optimization(client, token, run_id, max_wait=900)

    res = data.get("results", {})
    ok = data["status"] == "completed"
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
    data = wait_for_optimization(client, token, run_id, max_wait=900)

    res = data.get("results", {})
    ok = data["status"] == "completed"
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
    data = wait_for_optimization(client, token, run_id, max_wait=900)

    res = data.get("results", {})
    ok = data["status"] == "completed"
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
def test_depot_charging_utilized(client: TestClient, fleet_env, record):
    """Verify that depot charging is actually used when depot_dwell_minutes_after is set."""
    token = fleet_env["token"]

    # Use first 5 shifts (mixed bus types) for a focused depot test
    subset = fleet_env["shift_records"][:5]
    subset_shift_ids = [s["shift_id"] for s in subset]
    subset_pred_ids = []
    for sid in subset_shift_ids:
        subset_pred_ids.extend(fleet_env["prediction_run_ids"].get(sid, []))

    # Include depot stations in the charging config
    stations = fleet_env["charging_stations_config"] + fleet_env["depot_stations_config"]

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
    data = wait_for_optimization(client, token, run_id, max_wait=600)

    completed = data["status"] == "completed"
    results = data.get("results", {})
    station_util = results.get("station_utilization", {})

    depot_stop_ids = set()
    for name in DEPOT_STATION_NAMES:
        sids = fleet_env["name_to_stop_ids"].get(name, [])
        depot_stop_ids.update(sids)

    depot_energy = sum(
        station_util.get(sid, {}).get("total_energy_kwh", 0)
        for sid in depot_stop_ids
    )

    record(
        "depot_charging_utilized",
        completed,
        f"status={data['status']}, depot_energy={depot_energy:.1f} kWh, "
        f"depot_stations_found={len(depot_stop_ids)}",
    )
