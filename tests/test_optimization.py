"""Tests for the optimization pipeline.

Run with:  ./run_tests.sh tests/test_optimization.py
"""

import json
import os
import pathlib
import time
import uuid

import numpy as np
import pytest
from fastapi.testclient import TestClient

__report_module__ = "optimization"

API_BASE = "/api/v1/user"
SIM_BASE = "/api/v1/simulation"
AUTH_BASE = "/auth"

REAL_SHIFT_JSON = pathlib.Path(__file__).resolve().parent / "fixtures/shift_01_40102.json"

BUS_MODEL_SPECS = {
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
        },
    },
}


# ---------------------------------------------------------------------------
# Auth helpers (shared with test_prediction.py)
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
# Test data helpers
# ---------------------------------------------------------------------------

def create_bus_model(client: TestClient, token: str, user_id: str) -> str:
    unique = uuid.uuid4().hex[:8]
    r = client.post(
        f"{API_BASE}/bus-models/",
        json={
            "name": f"AA_NF_test_{unique}",
            "specs": BUS_MODEL_SPECS,
            "user_id": user_id,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"create bus model failed: {r.text}"
    return r.json()["id"]


def create_bus(client: TestClient, token: str, user_id: str, bus_model_id: str) -> str:
    unique = uuid.uuid4().hex[:8]
    r = client.post(
        f"{API_BASE}/buses/",
        json={
            "user_id": user_id,
            "name": f"Test Bus Opt {unique}",
            "specs": {},
            "bus_model_id": bus_model_id,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"create bus failed: {r.text}"
    return r.json()["id"]


def load_real_shift_trip_ids() -> list[str]:
    """Load GTFS trip IDs from the real shift JSON (01_40102.json)."""
    assert REAL_SHIFT_JSON.exists(), f"Shift JSON not found: {REAL_SHIFT_JSON}"
    with open(REAL_SHIFT_JSON) as f:
        trips = json.load(f)
    return [t["id"] for t in trips if t.get("status") == "gtfs"]


def create_shift(
    client: TestClient, token: str, bus_id: str, trip_ids: list[str], name_prefix: str = "Opt Shift"
) -> str:
    unique = uuid.uuid4().hex[:8]
    r = client.post(
        f"{API_BASE}/shifts/",
        json={"name": f"{name_prefix} {unique}", "bus_id": bus_id, "trip_ids": trip_ids},
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"create shift failed: {r.text}"
    return r.json()["id"]


def get_shift_end_stops(client: TestClient, token: str, shift_id: str) -> list[str]:
    """Get the end-stop IDs from the trips in a shift, for use as charging station candidates."""
    r = client.get(f"{API_BASE}/shifts/{shift_id}", headers=auth_headers(token))
    assert r.status_code == 200
    structures = r.json().get("structures") or []
    end_stop_ids = set()
    for s in structures:
        trip_id = s.get("trip_id")
        if not trip_id:
            continue
        tr = client.get(f"{API_BASE}/trips/{trip_id}/schedule", headers=auth_headers(token))
        if tr.status_code == 200:
            stops = tr.json()
            if stops:
                end_stop_ids.add(stops[-1]["id"])
    return list(end_stop_ids)


def run_prediction_and_wait(
    client: TestClient, token: str, shift_ids: list[str],
    bus_model_id: str, max_wait: int = 120,
) -> list[str]:
    """Submit a prediction and poll until completed. Return prediction_run_ids."""
    r = client.post(
        f"{SIM_BASE}/prediction-runs/",
        json={
            "shift_ids": shift_ids,
            "bus_model_id": bus_model_id,
            "model_name": "greybox_qrf_production_crps_optimized_3",
            "external_temp_celsius": 15.0,
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
            status_r = client.get(f"{SIM_BASE}/prediction-runs/{rid}", headers=auth_headers(token))
            if status_r.status_code == 200 and status_r.json()["status"] in ("completed", "failed"):
                continue
            all_done = False
            break
        if all_done:
            break
    else:
        pytest.fail("Prediction did not complete in time")

    for rid in run_ids:
        status_r = client.get(f"{SIM_BASE}/prediction-runs/{rid}", headers=auth_headers(token))
        assert status_r.json()["status"] == "completed", f"Prediction {rid} failed"

    return run_ids


def wait_for_optimization(
    client: TestClient, token: str, run_id: str, max_wait: int = 300,
) -> dict:
    """Poll optimization run until completed or failed. Return full run data."""
    for i in range(max_wait):
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

_SKIP_REASON = "Requires TEST_ROUTE_ID and auth env vars"
_skip_cond = not (
    os.getenv("TEST_ROUTE_ID")
    and (
        os.getenv("TEST_API_TOKEN")
        or (os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD"))
    )
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def opt_env(client: TestClient):
    """Create bus model, bus, shift + pre-computed prediction – return dict with IDs."""
    token = get_auth_token(client)
    assert token, "Authentication failed"
    hdrs = auth_headers(token)
    user_id = current_user_id(client, token)

    bus_model_id = create_bus_model(client, token, user_id)
    bus_id = create_bus(client, token, user_id, bus_model_id)
    trip_ids = load_real_shift_trip_ids()

    shift_id = create_shift(client, token, bus_id, trip_ids, "Opt Shift A")
    shift_id_b = create_shift(client, token, bus_id, trip_ids, "Opt Shift B")

    # Run predictions for both shifts
    pred_run_ids_a = run_prediction_and_wait(client, token, [shift_id], bus_model_id)
    pred_run_ids_b = run_prediction_and_wait(client, token, [shift_id_b], bus_model_id)

    # Get candidate charging stations (end-stop IDs of the shift's trips)
    end_stop_ids = get_shift_end_stops(client, token, shift_id)

    env = {
        "token": token,
        "user_id": user_id,
        "bus_model_id": bus_model_id,
        "bus_id": bus_id,
        "trip_ids": trip_ids,
        "shift_id": shift_id,
        "shift_id_b": shift_id_b,
        "prediction_run_ids_a": pred_run_ids_a,
        "prediction_run_ids_b": pred_run_ids_b,
        "end_stop_ids": end_stop_ids,
    }

    yield env

    # Teardown
    client.delete(f"{API_BASE}/shifts/{shift_id}", headers=hdrs)
    client.delete(f"{API_BASE}/shifts/{shift_id_b}", headers=hdrs)
    client.delete(f"{API_BASE}/buses/{bus_id}", headers=hdrs)
    client.delete(f"{API_BASE}/bus-models/{bus_model_id}", headers=hdrs)


# ---------------------------------------------------------------------------
# Basic API tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_optimization_auth_required(client: TestClient, record):
    """POST without auth token returns 403."""
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "battery_only",
            "shift_ids": [str(uuid.uuid4())],
            "bus_model_id": str(uuid.uuid4()),
            "prediction_params": {
                "model_name": "test",
                "external_temp_celsius": 15.0,
            },
            "charging_stations": [{
                "stop_id": str(uuid.uuid4()),
                "num_slots": 2,
                "max_total_power_kw": 450,
            }],
        },
    )
    record("optimization_auth_required", r.status_code == 403, f"status={r.status_code}")


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_optimization_invalid_bus_model(client: TestClient, opt_env, record):
    """POST with non-existent bus_model_id returns 404."""
    token = opt_env["token"]
    fake_id = str(uuid.uuid4())
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "battery_only",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": fake_id,
            "prediction_params": {
                "model_name": "test",
                "external_temp_celsius": 15.0,
            },
            "charging_stations": [{
                "stop_id": str(uuid.uuid4()),
                "num_slots": 2,
                "max_total_power_kw": 450,
            }],
        },
        headers=auth_headers(token),
    )
    record("optimization_invalid_bus_model", r.status_code == 404, f"status={r.status_code}")


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_optimization_invalid_shift(client: TestClient, opt_env, record):
    """POST with non-existent shift_id returns 404."""
    token = opt_env["token"]
    fake_id = str(uuid.uuid4())
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "battery_only",
            "shift_ids": [fake_id],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_params": {
                "model_name": "test",
                "external_temp_celsius": 15.0,
            },
            "charging_stations": [{
                "stop_id": str(uuid.uuid4()),
                "num_slots": 2,
                "max_total_power_kw": 450,
            }],
        },
        headers=auth_headers(token),
    )
    record("optimization_invalid_shift", r.status_code == 404, f"status={r.status_code}")


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_optimization_invalid_mode(client: TestClient, opt_env, record):
    """POST with invalid mode returns 422."""
    token = opt_env["token"]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "invalid_mode",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_params": {
                "model_name": "test",
                "external_temp_celsius": 15.0,
            },
        },
        headers=auth_headers(token),
    )
    record("optimization_invalid_mode", r.status_code == 422, f"status={r.status_code}")


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_optimization_missing_prediction_params(client: TestClient, opt_env, record):
    """POST without prediction_run_ids or prediction_params returns 422."""
    token = opt_env["token"]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "battery_only",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "charging_stations": [{
                "stop_id": opt_env["end_stop_ids"][0] if opt_env["end_stop_ids"] else str(uuid.uuid4()),
                "num_slots": 2,
                "max_total_power_kw": 450,
            }],
        },
        headers=auth_headers(token),
    )
    record("optimization_missing_prediction_params", r.status_code == 422, f"status={r.status_code}")


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_optimization_charging_only_missing_slot_costs(client: TestClient, opt_env, record):
    """charging_only mode without slot_costs_chf returns 422."""
    token = opt_env["token"]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "charging_only",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_run_ids": opt_env["prediction_run_ids_a"],
            "charging_stations": [{
                "stop_id": opt_env["end_stop_ids"][0] if opt_env["end_stop_ids"] else str(uuid.uuid4()),
                "max_total_power_kw": 450,
            }],
        },
        headers=auth_headers(token),
    )
    record("charging_only_missing_slot_costs", r.status_code == 422, f"status={r.status_code}")


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_optimization_joint_missing_battery_cost(client: TestClient, opt_env, record):
    """joint mode without battery_cost_per_kwh returns 422."""
    token = opt_env["token"]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "joint",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_run_ids": opt_env["prediction_run_ids_a"],
            "charging_stations": [{
                "stop_id": opt_env["end_stop_ids"][0] if opt_env["end_stop_ids"] else str(uuid.uuid4()),
                "slot_costs_chf": [350000, 150000],
                "max_total_power_kw": 450,
            }],
        },
        headers=auth_headers(token),
    )
    record("joint_missing_battery_cost", r.status_code == 422, f"status={r.status_code}")


# ---------------------------------------------------------------------------
# Mode-specific tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_battery_only_mode(client: TestClient, opt_env, record):
    """battery_only: fixed chargers at end stations, optimizer finds minimum battery size."""
    token = opt_env["token"]
    charging_stations = [
        {
            "stop_id": sid,
            "num_slots": 2,
            "max_total_power_kw": 450,
        }
        for sid in opt_env["end_stop_ids"]
    ]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "battery_only",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_run_ids": opt_env["prediction_run_ids_a"],
            "charging_stations": charging_stations,
            "min_soc": 0.1,
            "max_soc": 0.95,
            "lock_entire_dwell": True,
            "solver_name": "highs",
            "max_solver_time_seconds": 120,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"submit failed: {r.text}"
    run_id = r.json()["optimization_run_id"]

    data = wait_for_optimization(client, token, run_id)
    status = data["status"]
    results = data.get("results", {})

    passed = status == "completed"
    details = f"status={status}"
    if passed:
        solver_status = results.get("solver_status", "")
        battery = results.get("battery_results", {})
        details += f", solver={solver_status}, battery_results={json.dumps(battery, default=str)[:200]}"
        passed = solver_status in ("optimal", "feasible") and len(battery) > 0
    record("battery_only_mode", passed, details)


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_charging_only_mode(client: TestClient, opt_env, record):
    """charging_only: fixed battery, optimizer decides where to install charging stations."""
    token = opt_env["token"]
    charging_stations = [
        {
            "stop_id": sid,
            "slot_costs_chf": [350000, 150000, 150000],
            "max_total_power_kw": 450,
        }
        for sid in opt_env["end_stop_ids"]
    ]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "charging_only",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_run_ids": opt_env["prediction_run_ids_a"],
            "charging_stations": charging_stations,
            "min_soc": 0.1,
            "max_soc": 0.95,
            "lock_entire_dwell": True,
            "solver_name": "highs",
            "max_solver_time_seconds": 120,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"submit failed: {r.text}"
    run_id = r.json()["optimization_run_id"]

    data = wait_for_optimization(client, token, run_id)
    status = data["status"]
    results = data.get("results", {})

    passed = status == "completed"
    details = f"status={status}"
    if passed:
        solver_status = results.get("solver_status", "")
        chargers = results.get("installed_chargers", {})
        cost = results.get("total_installation_cost_chf", 0)
        details += f", solver={solver_status}, cost={cost}, stations_with_chargers={sum(1 for c in chargers.values() if c.get('num_slots', 0) > 0)}"
        passed = solver_status in ("optimal", "feasible")
    record("charging_only_mode", passed, details)


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_joint_mode(client: TestClient, opt_env, record):
    """joint: optimize both battery size and charging station locations."""
    token = opt_env["token"]
    charging_stations = [
        {
            "stop_id": sid,
            "slot_costs_chf": [350000, 150000, 150000],
            "max_total_power_kw": 450,
        }
        for sid in opt_env["end_stop_ids"]
    ]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "joint",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_run_ids": opt_env["prediction_run_ids_a"],
            "charging_stations": charging_stations,
            "min_soc": 0.1,
            "max_soc": 0.95,
            "battery_cost_per_kwh": 300.0,
            "max_battery_penalty_per_kwh": 1e6,
            "lock_entire_dwell": True,
            "solver_name": "highs",
            "max_solver_time_seconds": 120,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"submit failed: {r.text}"
    run_id = r.json()["optimization_run_id"]

    data = wait_for_optimization(client, token, run_id)
    status = data["status"]
    results = data.get("results", {})

    passed = status == "completed"
    details = f"status={status}"
    if passed:
        solver_status = results.get("solver_status", "")
        batt_cost = results.get("total_battery_cost_chf", 0)
        inst_cost = results.get("total_installation_cost_chf", 0)
        details += f", solver={solver_status}, battery_cost={batt_cost}, install_cost={inst_cost}"
        passed = solver_status in ("optimal", "feasible")
    record("joint_mode", passed, details)


# ---------------------------------------------------------------------------
# Constraint validation tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_soc_bounds_respected(client: TestClient, opt_env, record):
    """Verify that SOC respects min/max bounds in per_bus_summary."""
    token = opt_env["token"]
    min_soc_frac = 0.1
    max_soc_frac = 0.95
    pack_size = BUS_MODEL_SPECS["battery_pack_size_kwh"]
    max_packs = BUS_MODEL_SPECS["max_battery_packs"]

    charging_stations = [
        {
            "stop_id": sid,
            "num_slots": 2,
            "max_total_power_kw": 450,
        }
        for sid in opt_env["end_stop_ids"]
    ]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "battery_only",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_run_ids": opt_env["prediction_run_ids_a"],
            "charging_stations": charging_stations,
            "min_soc": min_soc_frac,
            "max_soc": max_soc_frac,
            "lock_entire_dwell": True,
            "solver_name": "highs",
            "max_solver_time_seconds": 120,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    run_id = r.json()["optimization_run_id"]
    data = wait_for_optimization(client, token, run_id)

    results = data.get("results", {})
    battery = results.get("battery_results", {})
    per_bus = results.get("per_bus_summary", [])

    passed = data["status"] == "completed" and len(per_bus) > 0
    details = f"status={data['status']}"
    if passed:
        for bus in per_bus:
            shift_id = bus["shift_id"]
            batt_info = battery.get(shift_id, {})
            opt_kwh = batt_info.get("optimized_kwh", max_packs * pack_size)
            min_allowed = opt_kwh * min_soc_frac - 1.0  # small tolerance
            max_allowed = opt_kwh * max_soc_frac + 1.0
            min_soc_kwh = bus["min_soc_kwh"]
            max_soc_kwh = bus["max_soc_kwh"]
            if min_soc_kwh < min_allowed:
                details += f", VIOLATION: bus {shift_id} min_soc={min_soc_kwh:.1f} < {min_allowed:.1f}"
                passed = False
            if max_soc_kwh > max_allowed:
                details += f", VIOLATION: bus {shift_id} max_soc={max_soc_kwh:.1f} > {max_allowed:.1f}"
                passed = False
        if passed:
            details += ", all SOC bounds respected"
    record("soc_bounds_respected", passed, details)


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_slot_exclusivity(client: TestClient, opt_env, record):
    """Verify station utilization is within slot capacity."""
    token = opt_env["token"]
    charging_stations = [
        {
            "stop_id": sid,
            "slot_costs_chf": [350000, 150000],
            "max_total_power_kw": 450,
        }
        for sid in opt_env["end_stop_ids"]
    ]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "charging_only",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_run_ids": opt_env["prediction_run_ids_a"],
            "charging_stations": charging_stations,
            "min_soc": 0.1,
            "max_soc": 0.95,
            "lock_entire_dwell": True,
            "solver_name": "highs",
            "max_solver_time_seconds": 120,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    run_id = r.json()["optimization_run_id"]
    data = wait_for_optimization(client, token, run_id)

    results = data.get("results", {})
    chargers = results.get("installed_chargers", {})
    utilization = results.get("station_utilization", {})

    passed = data["status"] == "completed"
    details = f"status={data['status']}"
    if passed:
        for stop_id, util in utilization.items():
            peak = util.get("peak_concurrent_buses", 0)
            installed = chargers.get(stop_id, {}).get("num_slots", 0)
            if peak > installed:
                details += f", VIOLATION: station {stop_id} peak={peak} > slots={installed}"
                passed = False
        if passed:
            details += ", all slot exclusivity constraints met"
    record("slot_exclusivity", passed, details)


# ---------------------------------------------------------------------------
# Multi-bus optimization with two shifts
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_multi_shift_optimization(client: TestClient, opt_env, record):
    """Optimize two shifts simultaneously in charging_only mode."""
    token = opt_env["token"]
    charging_stations = [
        {
            "stop_id": sid,
            "slot_costs_chf": [350000, 150000, 150000],
            "max_total_power_kw": 900,
        }
        for sid in opt_env["end_stop_ids"]
    ]
    all_pred_ids = opt_env["prediction_run_ids_a"] + opt_env["prediction_run_ids_b"]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "charging_only",
            "shift_ids": [opt_env["shift_id"], opt_env["shift_id_b"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_run_ids": all_pred_ids,
            "charging_stations": charging_stations,
            "min_soc": 0.1,
            "max_soc": 0.95,
            "lock_entire_dwell": True,
            "solver_name": "highs",
            "max_solver_time_seconds": 120,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"submit failed: {r.text}"
    run_id = r.json()["optimization_run_id"]

    data = wait_for_optimization(client, token, run_id)
    status = data["status"]
    results = data.get("results", {})
    per_bus = results.get("per_bus_summary", [])

    passed = status == "completed" and len(per_bus) == 2
    details = f"status={status}, num_buses={len(per_bus)}"
    if passed:
        solver_status = results.get("solver_status", "")
        details += f", solver={solver_status}"
        passed = solver_status in ("optimal", "feasible")
    record("multi_shift_optimization", passed, details)


# ---------------------------------------------------------------------------
# Auto-prediction tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_auto_prediction_creates_predictions(client: TestClient, opt_env, record):
    """Submitting without prediction_run_ids but with prediction_params should auto-create predictions."""
    token = opt_env["token"]
    charging_stations = [
        {
            "stop_id": sid,
            "num_slots": 2,
            "max_total_power_kw": 450,
        }
        for sid in opt_env["end_stop_ids"]
    ]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "battery_only",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_params": {
                "model_name": "greybox_qrf_production_crps_optimized_3",
                "external_temp_celsius": 15.0,
                "occupancy_percent": 50.0,
            },
            "charging_stations": charging_stations,
            "min_soc": 0.1,
            "max_soc": 0.95,
            "lock_entire_dwell": True,
            "solver_name": "highs",
            "max_solver_time_seconds": 180,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"submit failed: {r.text}"
    run_id = r.json()["optimization_run_id"]

    data = wait_for_optimization(client, token, run_id, max_wait=300)
    status = data["status"]
    pred_ids = data.get("prediction_run_ids")

    passed = status == "completed" and pred_ids is not None and len(pred_ids) > 0
    details = f"status={status}, prediction_run_ids={pred_ids}"
    record("auto_prediction_creates", passed, details)


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_auto_prediction_reuses_existing(client: TestClient, opt_env, record):
    """Auto-prediction should reuse existing completed predictions with matching parameters."""
    token = opt_env["token"]
    charging_stations = [
        {
            "stop_id": sid,
            "num_slots": 2,
            "max_total_power_kw": 450,
        }
        for sid in opt_env["end_stop_ids"]
    ]

    # The opt_env already has completed predictions for shift_id with temp=15 and occ=50
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "battery_only",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_params": {
                "model_name": "greybox_qrf_production_crps_optimized_3",
                "external_temp_celsius": 15.0,
                "occupancy_percent": 50.0,
            },
            "charging_stations": charging_stations,
            "min_soc": 0.1,
            "max_soc": 0.95,
            "lock_entire_dwell": True,
            "solver_name": "highs",
            "max_solver_time_seconds": 180,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"submit failed: {r.text}"
    run_id = r.json()["optimization_run_id"]

    data = wait_for_optimization(client, token, run_id, max_wait=300)
    status = data["status"]
    pred_ids = data.get("prediction_run_ids", [])

    # The reused prediction IDs should match the ones created in opt_env
    existing = opt_env["prediction_run_ids_a"]
    reused = set(pred_ids or []) & set(existing)
    passed = status == "completed" and len(reused) > 0
    details = f"status={status}, reused={len(reused)}/{len(existing)}"
    record("auto_prediction_reuses", passed, details)


# ---------------------------------------------------------------------------
# Solver parameter tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_solver_time_limit(client: TestClient, opt_env, record):
    """Setting a very short time limit should result in graceful termination."""
    token = opt_env["token"]
    charging_stations = [
        {
            "stop_id": sid,
            "num_slots": 2,
            "max_total_power_kw": 450,
        }
        for sid in opt_env["end_stop_ids"]
    ]
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json={
            "mode": "battery_only",
            "shift_ids": [opt_env["shift_id"]],
            "bus_model_id": opt_env["bus_model_id"],
            "prediction_run_ids": opt_env["prediction_run_ids_a"],
            "charging_stations": charging_stations,
            "min_soc": 0.1,
            "max_soc": 0.95,
            "lock_entire_dwell": True,
            "solver_name": "highs",
            "max_solver_time_seconds": 1,
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"submit failed: {r.text}"
    run_id = r.json()["optimization_run_id"]

    data = wait_for_optimization(client, token, run_id, max_wait=30)
    status = data["status"]
    # Should either complete or fail gracefully, not hang
    passed = status in ("completed", "failed")
    details = f"status={status}"
    if status == "completed":
        solver_status = data.get("results", {}).get("solver_status", "")
        details += f", solver_status={solver_status}"
    record("solver_time_limit", passed, details)


# ---------------------------------------------------------------------------
# GET endpoint test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_get_optimization_run_not_found(client: TestClient, opt_env, record):
    """GET for non-existent run returns 404."""
    token = opt_env["token"]
    r = client.get(
        f"{SIM_BASE}/optimization-runs/{uuid.uuid4()}",
        headers=auth_headers(token),
    )
    record("get_run_not_found", r.status_code == 404, f"status={r.status_code}")
