"""Tests for the prediction pipeline.

Run with:  ./run_tests.sh tests/test_prediction.py
"""

import json
import os
import pathlib
import time
import uuid

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

__report_module__ = "prediction"

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
        "diesel_heating": {
            "temperature_celsius": [-20, -10, 0, 10, 15, 20, 25],
            "consumption_kw": [8, 8, 8, 8, 9, 10, 16],
        },
    },
}


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
            "name": f"Test Bus Pred {unique}",
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
    client: TestClient, token: str, bus_id: str, trip_ids: list[str]
) -> str:
    unique = uuid.uuid4().hex[:8]
    r = client.post(
        f"{API_BASE}/shifts/",
        json={"name": f"Pred Shift {unique}", "bus_id": bus_id, "trip_ids": trip_ids},
        headers=auth_headers(token),
    )
    assert r.status_code == 200, f"create shift failed: {r.text}"
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def prediction_env(client: TestClient):
    """Create bus model, bus, shift from real 01_40102 data – return dict with IDs and tear down after."""
    token = get_auth_token(client)
    assert token, "Authentication failed"
    hdrs = auth_headers(token)
    user_id = current_user_id(client, token)

    bus_model_id = create_bus_model(client, token, user_id)
    bus_id = create_bus(client, token, user_id, bus_model_id)
    trip_ids = load_real_shift_trip_ids()
    shift_id = create_shift(client, token, bus_id, trip_ids)

    env = {
        "token": token,
        "user_id": user_id,
        "bus_model_id": bus_model_id,
        "bus_id": bus_id,
        "trip_ids": trip_ids,
        "shift_id": shift_id,
    }

    yield env

    # Teardown: shift delete cascades to prediction_runs (DB-level CASCADE + passive_deletes)
    client.delete(f"{API_BASE}/shifts/{shift_id}", headers=hdrs)
    client.delete(f"{API_BASE}/buses/{bus_id}", headers=hdrs)
    client.delete(f"{API_BASE}/bus-models/{bus_model_id}", headers=hdrs)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_SKIP_REASON = "Requires TEST_ROUTE_ID and auth env vars"
_skip_cond = not (
    os.getenv("TEST_ROUTE_ID")
    and (
        os.getenv("TEST_API_TOKEN")
        or (os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD"))
    )
)


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_predict_endpoint_auth_required(client: TestClient, record):
    """POST without auth token returns 403."""
    r = client.post(
        f"{SIM_BASE}/prediction-runs/",
        json={
            "shift_ids": [str(uuid.uuid4())],
            "bus_model_id": str(uuid.uuid4()),
            "model_name": "test",
            "external_temp_celsius": 10,
        },
    )
    record("predict_auth_required", r.status_code == 403, f"status={r.status_code}")


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_predict_endpoint_invalid_bus_model(client: TestClient, prediction_env, record):
    """POST with non-existent bus_model_id returns 404."""
    token = prediction_env["token"]
    fake_id = str(uuid.uuid4())
    r = client.post(
        f"{SIM_BASE}/prediction-runs/",
        json={
            "shift_ids": [prediction_env["shift_id"]],
            "bus_model_id": fake_id,
            "model_name": "greybox_qrf_production_crps_optimized_3",
            "external_temp_celsius": 10,
        },
        headers=auth_headers(token),
    )
    record("predict_invalid_bus_model_404", r.status_code == 404, f"status={r.status_code} body={r.text}")


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_predict_endpoint_invalid_shift(client: TestClient, prediction_env, record):
    """POST with non-existent shift_id returns 404."""
    token = prediction_env["token"]
    fake_id = str(uuid.uuid4())
    r = client.post(
        f"{SIM_BASE}/prediction-runs/",
        json={
            "shift_ids": [fake_id],
            "bus_model_id": prediction_env["bus_model_id"],
            "model_name": "greybox_qrf_production_crps_optimized_3",
            "external_temp_celsius": 10,
        },
        headers=auth_headers(token),
    )
    record("predict_invalid_shift_404", r.status_code == 404, f"status={r.status_code} body={r.text}")


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_predict_endpoint_creates_runs(client: TestClient, prediction_env, record):
    """POST to /prediction-runs/ returns run IDs with status=pending."""
    token = prediction_env["token"]
    r = client.post(
        f"{SIM_BASE}/prediction-runs/",
        json={
            "shift_ids": [prediction_env["shift_id"]],
            "bus_model_id": prediction_env["bus_model_id"],
            "model_name": "greybox_qrf_production_crps_optimized_3",
            "external_temp_celsius": 10,
            "occupancy_percent": 50,
            "auxiliary_heating_type": "default",
            "quantiles": [0.05, 0.5, 0.95],
        },
        headers=auth_headers(token),
    )
    record("predict_create_200", r.status_code == 200, f"status={r.status_code} body={r.text}")
    if r.status_code != 200:
        return

    data = r.json()
    run_ids = data.get("prediction_run_ids", [])
    record("predict_create_has_ids", len(run_ids) == 1, f"ids={run_ids}")

    # Store for subsequent tests
    prediction_env["prediction_run_id"] = run_ids[0]


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_predict_endpoint_completes(client: TestClient, prediction_env, record):
    """Poll GET /prediction-runs/{id} until completed or timeout."""
    token = prediction_env["token"]
    run_id = prediction_env.get("prediction_run_id")
    if not run_id:
        record("predict_poll_skipped", False, "no run_id from previous test")
        return

    status = "pending"
    max_wait = 120  # seconds
    elapsed = 0
    poll_interval = 2

    while status in ("pending", "running") and elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        r = client.get(
            f"{SIM_BASE}/prediction-runs/{run_id}",
            headers=auth_headers(token),
        )
        if r.status_code == 200:
            status = r.json().get("status", "unknown")

    record(
        "predict_completed",
        status == "completed",
        f"status={status} after {elapsed}s",
    )

    if status == "completed":
        run_data = r.json()
        record(
            "predict_summary_populated",
            run_data.get("summary") is not None and "total_consumption_kwh" in (run_data.get("summary") or {}),
            f"summary keys={list((run_data.get('summary') or {}).keys())}",
        )
        record(
            "predict_contextual_params",
            run_data.get("contextual_parameters") is not None,
            f"ctx keys={list((run_data.get('contextual_parameters') or {}).keys())}",
        )


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_predict_endpoint_returns_trip_predictions(client: TestClient, prediction_env, record):
    """GET /prediction-runs/{id}/predictions returns per-trip results."""
    token = prediction_env["token"]
    run_id = prediction_env.get("prediction_run_id")
    if not run_id:
        record("predict_trips_skipped", False, "no run_id from previous test")
        return

    r = client.get(
        f"{SIM_BASE}/prediction-runs/{run_id}/predictions",
        headers=auth_headers(token),
    )
    record("predict_trips_200", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return

    predictions = r.json()
    record("predict_trips_count", len(predictions) > 0, f"count={len(predictions)}")

    if predictions:
        first = predictions[0]
        record(
            "predict_trip_has_fields",
            all(k in first for k in ("prediction_kwh", "trip_id", "sequence_number")),
            f"keys={list(first.keys())}",
        )
        has_quantiles = first.get("quantiles") is not None and len(first["quantiles"]) > 0
        record("predict_trip_has_quantiles", has_quantiles, f"quantiles={first.get('quantiles')}")

        has_sensitivity = first.get("mass_sensitivity_kwh_per_kwh_batt") is not None
        record("predict_trip_has_sensitivity", has_sensitivity, f"sensitivity={first.get('mass_sensitivity_kwh_per_kwh_batt')}")


# ---------------------------------------------------------------------------
# (b) Service-level unit tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_build_aux_energy_fn(record):
    """Verify auxiliary energy interpolation at known temperatures."""
    from app.services.prediction import build_aux_energy_fn

    fn = build_aux_energy_fn(BUS_MODEL_SPECS, "default")
    assert fn is not None, "build_aux_energy_fn returned None"

    df = pd.DataFrame({
        "avg_temp_outside_celsius": [-5.0, 10.0, 25.0],
        "total_duration_minutes": [60.0, 60.0, 60.0],
    })
    result = fn(df)
    record(
        "aux_fn_at_minus5",
        abs(float(result.iloc[0]) - 24.0) < 0.01,
        f"expected ~24 kWh, got {result.iloc[0]}",
    )
    record(
        "aux_fn_at_10",
        abs(float(result.iloc[1]) - 8.0) < 0.01,
        f"expected ~8 kWh, got {result.iloc[1]}",
    )
    record(
        "aux_fn_at_25",
        abs(float(result.iloc[2]) - 16.0) < 0.01,
        f"expected ~16 kWh, got {result.iloc[2]}",
    )


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_build_aux_energy_fn_missing_type(record):
    """Non-existent heating type falls back to 'default'."""
    from app.services.prediction import build_aux_energy_fn

    fn = build_aux_energy_fn(BUS_MODEL_SPECS, "heat_pump")
    record("aux_fn_fallback_not_none", fn is not None, "should fall back to default")

    if fn:
        df = pd.DataFrame({
            "avg_temp_outside_celsius": [10.0],
            "total_duration_minutes": [60.0],
        })
        result = fn(df)
        record(
            "aux_fn_fallback_value",
            abs(float(result.iloc[0]) - 8.0) < 0.01,
            f"expected ~8 kWh from default, got {result.iloc[0]}",
        )


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_build_aux_energy_fn_no_data(record):
    """Specs with no auxiliary data returns None."""
    from app.services.prediction import build_aux_energy_fn

    fn = build_aux_energy_fn({}, "default")
    record("aux_fn_empty_specs_none", fn is None, f"got {fn}")


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_model_caching(record):
    """Verify the ConsumptionPredictor singleton is reused across calls."""
    from app.services.prediction import _predictor_cache, get_predictor

    model_name = "greybox_qrf_production_crps_optimized_3"
    _predictor_cache.pop(model_name, None)

    p1 = get_predictor(model_name)
    p2 = get_predictor(model_name)
    record("model_cache_same_object", p1 is p2, f"id(p1)={id(p1)} id(p2)={id(p2)}")

    _predictor_cache.pop(model_name, None)


@pytest.mark.asyncio
@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
async def test_predict_shift_loads_trips_from_db(prediction_env, record):
    """Given a shift_id, verify load_shift_trip_statistics returns trips in sequence order."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.database import get_database_url
    from app.services.prediction import load_shift_trip_statistics

    engine = create_async_engine(get_database_url(), future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    shift_id = uuid.UUID(prediction_env["shift_id"])
    async with Session() as session:
        stats = await load_shift_trip_statistics(session, shift_id)
    await engine.dispose()

    record("load_trips_count", len(stats) > 0, f"count={len(stats)}")

    if len(stats) >= 2:
        seqs = [s["sequence_number"] for s in stats]
        record("load_trips_ordered", seqs == sorted(seqs), f"seqs={seqs}")

    if stats:
        first = stats[0]
        record(
            "load_trips_has_statistics",
            "statistics" in first and "statistics" in first["statistics"],
            f"keys={list(first.keys())}",
        )
        inner = first["statistics"]["statistics"]
        record(
            "load_trips_has_distance",
            "total_distance_m" in inner and inner["total_distance_m"] > 0,
            f"total_distance_m={inner.get('total_distance_m')}",
        )


# ---------------------------------------------------------------------------
# (a) DB model tests
# ---------------------------------------------------------------------------

def _make_test_session():
    """Create a fresh async engine + session factory bound to the current event loop."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.database import get_database_url
    engine = create_async_engine(get_database_url(), future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    return engine, Session


@pytest.mark.asyncio
@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
async def test_prediction_run_crud(prediction_env, record):
    """Create a prediction_run row and verify it persists."""
    from app.models import PredictionRuns

    engine, Session = _make_test_session()
    async with Session() as session:
        run = PredictionRuns(
            user_id=uuid.UUID(prediction_env["user_id"]),
            shift_id=uuid.UUID(prediction_env["shift_id"]),
            bus_model_id=uuid.UUID(prediction_env["bus_model_id"]),
            model_name="test_model",
            external_temp_celsius=15.0,
            auxiliary_heating_type="default",
            occupancy_percent=50,
            status="pending",
        )
        session.add(run)
        await session.flush()

        fetched = await session.get(PredictionRuns, run.id)
        record("db_run_persisted", fetched is not None, f"id={run.id}")
        record("db_run_status", fetched.status == "pending", f"status={fetched.status}")
        record("db_run_model_name", fetched.model_name == "test_model", f"model={fetched.model_name}")

        await session.delete(run)
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
async def test_trip_prediction_fk(prediction_env, record):
    """Create trip_predictions linked to a run and verify FK integrity."""
    from app.models import PredictionRuns, TripPredictions

    engine, Session = _make_test_session()
    async with Session() as session:
        run = PredictionRuns(
            user_id=uuid.UUID(prediction_env["user_id"]),
            shift_id=uuid.UUID(prediction_env["shift_id"]),
            bus_model_id=uuid.UUID(prediction_env["bus_model_id"]),
            model_name="test_fk",
            external_temp_celsius=10.0,
            auxiliary_heating_type="default",
            occupancy_percent=50,
            status="completed",
        )
        session.add(run)
        await session.flush()

        trip_id = uuid.UUID(prediction_env["trip_ids"][0])
        tp = TripPredictions(
            prediction_run_id=run.id,
            trip_id=trip_id,
            sequence_number=0,
            prediction_kwh=42.5,
            prediction_median_kwh=41.0,
            quantiles={"0.50": 41.0},
        )
        session.add(tp)
        await session.flush()

        fetched = await session.get(TripPredictions, tp.id)
        record("db_tp_persisted", fetched is not None, f"id={tp.id}")
        record("db_tp_run_fk", str(fetched.prediction_run_id) == str(run.id), "FK matches")
        record("db_tp_prediction_kwh", float(fetched.prediction_kwh) == 42.5, f"kwh={fetched.prediction_kwh}")

        await session.delete(tp)
        await session.delete(run)
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
async def test_cascade_delete(prediction_env, record):
    """Deleting a prediction_run cascades to its trip_predictions."""
    from app.models import PredictionRuns, TripPredictions

    engine, Session = _make_test_session()
    async with Session() as session:
        run = PredictionRuns(
            user_id=uuid.UUID(prediction_env["user_id"]),
            shift_id=uuid.UUID(prediction_env["shift_id"]),
            bus_model_id=uuid.UUID(prediction_env["bus_model_id"]),
            model_name="test_cascade",
            external_temp_celsius=5.0,
            auxiliary_heating_type="default",
            occupancy_percent=50,
            status="completed",
        )
        session.add(run)
        await session.flush()
        run_id = run.id

        trip_id = uuid.UUID(prediction_env["trip_ids"][0])
        tp = TripPredictions(
            prediction_run_id=run_id,
            trip_id=trip_id,
            sequence_number=0,
            prediction_kwh=10.0,
        )
        session.add(tp)
        await session.flush()
        tp_id = tp.id

        await session.delete(run)
        await session.commit()

        orphan = await session.get(TripPredictions, tp_id)
        record("cascade_delete_tp_gone", orphan is None, f"orphan={orphan}")
    await engine.dispose()


# ---------------------------------------------------------------------------
# (d) Error handling tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_predict_handles_model_load_failure(client: TestClient, prediction_env, record):
    """Non-existent ML model name results in status=failed."""
    token = prediction_env["token"]
    r = client.post(
        f"{SIM_BASE}/prediction-runs/",
        json={
            "shift_ids": [prediction_env["shift_id"]],
            "bus_model_id": prediction_env["bus_model_id"],
            "model_name": "nonexistent_model_xyz_999",
            "external_temp_celsius": 10,
        },
        headers=auth_headers(token),
    )
    record("model_fail_create_200", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return

    run_id = r.json()["prediction_run_ids"][0]

    max_wait, elapsed, poll = 30, 0, 2
    status = "pending"
    while status in ("pending", "running") and elapsed < max_wait:
        time.sleep(poll)
        elapsed += poll
        rr = client.get(
            f"{SIM_BASE}/prediction-runs/{run_id}",
            headers=auth_headers(token),
        )
        if rr.status_code == 200:
            status = rr.json().get("status", "unknown")

    record("model_fail_status_failed", status == "failed", f"status={status} after {elapsed}s")
    if status == "failed":
        summary = rr.json().get("summary", {})
        record(
            "model_fail_has_error",
            "error" in summary,
            f"summary keys={list(summary.keys())}",
        )


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_predict_sensitivity_differs_by_direction(client: TestClient, prediction_env, record):
    """Verify mass_sensitivity differs between direction 0 and direction 1 trips."""
    token = prediction_env["token"]
    run_id = prediction_env.get("prediction_run_id")
    if not run_id:
        record("sensitivity_dir_skipped", False, "no run_id from previous test")
        return

    r = client.get(
        f"{SIM_BASE}/prediction-runs/{run_id}/predictions",
        headers=auth_headers(token),
    )
    record("sensitivity_dir_fetch_200", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return

    predictions = r.json()

    # Build trip_id → direction_id mapping from the shift JSON
    with open(REAL_SHIFT_JSON) as f:
        shift_trips = json.load(f)
    dir_map = {t["id"]: t.get("direction_id") for t in shift_trips if t.get("status") == "gtfs"}

    sens_by_dir: dict[int, set[float]] = {}
    for pred in predictions:
        tid = pred.get("trip_id")
        direction = dir_map.get(tid)
        sens = pred.get("mass_sensitivity_kwh_per_kwh_batt")
        if direction is not None and sens is not None:
            sens_by_dir.setdefault(direction, set()).add(round(float(sens), 10))

    record(
        "sensitivity_dir_has_both_dirs",
        len(sens_by_dir) >= 2,
        f"directions={list(sens_by_dir.keys())}",
    )

    if len(sens_by_dir) >= 2:
        dir_values = list(sens_by_dir.values())
        all_unique = dir_values[0] != dir_values[1]
        record(
            "sensitivity_dir_differs",
            all_unique,
            f"dir0={dir_values[0]}, dir1={dir_values[1]}",
        )

    record(
        "sensitivity_trip_count",
        len(predictions) >= 40,
        f"count={len(predictions)} (expected ~42 GTFS trips)",
    )


# ---------------------------------------------------------------------------
# Helper: submit prediction, poll until done, return (run_data, predictions)
# ---------------------------------------------------------------------------

def _submit_and_wait(client, token, shift_id, bus_model_id, **overrides):
    """Submit a prediction run and poll until completed. Returns (run_data, trip_predictions)."""
    payload = {
        "shift_ids": [shift_id],
        "bus_model_id": bus_model_id,
        "model_name": "greybox_qrf_production_crps_optimized_3",
        "external_temp_celsius": 10,
        "occupancy_percent": 50,
        "auxiliary_heating_type": "default",
        "quantiles": [0.05, 0.5, 0.95],
    }
    payload.update(overrides)
    hdrs = auth_headers(token)

    r = client.post(f"{SIM_BASE}/prediction-runs/", json=payload, headers=hdrs)
    assert r.status_code == 200, f"submit failed: {r.text}"
    run_id = r.json()["prediction_run_ids"][0]

    status, max_wait, elapsed, poll = "pending", 120, 0, 2
    run_data = {}
    while status in ("pending", "running") and elapsed < max_wait:
        time.sleep(poll)
        elapsed += poll
        rr = client.get(f"{SIM_BASE}/prediction-runs/{run_id}", headers=hdrs)
        if rr.status_code == 200:
            run_data = rr.json()
            status = run_data.get("status", "unknown")
    assert status == "completed", f"run {run_id} ended with status={status} after {elapsed}s"

    rp = client.get(f"{SIM_BASE}/prediction-runs/{run_id}/predictions", headers=hdrs)
    assert rp.status_code == 200
    return run_data, rp.json()


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_weight_override_battery_packs(client: TestClient, prediction_env, record):
    """Verify that changing num_battery_packs changes total_weight and predictions."""
    token = prediction_env["token"]
    sid = prediction_env["shift_id"]
    bmid = prediction_env["bus_model_id"]

    run_lo, preds_lo = _submit_and_wait(client, token, sid, bmid, num_battery_packs=4)
    run_hi, preds_hi = _submit_and_wait(client, token, sid, bmid, num_battery_packs=8)

    ctx_lo = run_lo.get("contextual_parameters", {})
    ctx_hi = run_hi.get("contextual_parameters", {})

    weight_lo = ctx_lo.get("total_weight_kg", 0)
    weight_hi = ctx_hi.get("total_weight_kg", 0)
    batt_lo = ctx_lo.get("battery_capacity_kwh", 0)
    batt_hi = ctx_hi.get("battery_capacity_kwh", 0)
    packs_lo = ctx_lo.get("num_battery_packs", 0)
    packs_hi = ctx_hi.get("num_battery_packs", 0)

    pack_weight = BUS_MODEL_SPECS["battery_pack_weight_kg"]
    record("weight_packs_lo_ctx", packs_lo == 4, f"packs={packs_lo}")
    record("weight_packs_hi_ctx", packs_hi == 8, f"packs={packs_hi}")

    # 4 extra packs × 603 kg = 2412 kg difference
    expected_delta_kg = 4 * pack_weight
    actual_delta_kg = weight_hi - weight_lo
    record(
        "weight_delta_correct",
        abs(actual_delta_kg - expected_delta_kg) < 1,
        f"delta={actual_delta_kg:.1f} kg (expected {expected_delta_kg})",
    )

    record(
        "weight_battery_capacity",
        batt_hi > batt_lo,
        f"4 packs={batt_lo} kWh, 8 packs={batt_hi} kWh",
    )

    # Heavier bus -> higher consumption
    total_lo = run_lo.get("summary", {}).get("total_consumption_kwh", 0)
    total_hi = run_hi.get("summary", {}).get("total_consumption_kwh", 0)
    record(
        "weight_consumption_increases",
        total_hi > total_lo,
        f"4 packs={total_lo:.2f} kWh, 8 packs={total_hi:.2f} kWh, diff={total_hi - total_lo:.2f} kWh",
    )

    # Per-trip: every trip should have higher consumption with more packs
    kwh_lo = {p["trip_id"]: float(p["prediction_kwh"]) for p in preds_lo}
    kwh_hi = {p["trip_id"]: float(p["prediction_kwh"]) for p in preds_hi}
    common = set(kwh_lo) & set(kwh_hi)
    all_higher = all(kwh_hi[tid] > kwh_lo[tid] for tid in common)
    record(
        "weight_all_trips_higher",
        all_higher,
        f"checked {len(common)} trips",
    )

    # Log the drivetrain breakdown for insight
    dt_lo = run_lo.get("summary", {}).get("total_drivetrain_kwh", 0)
    dt_hi = run_hi.get("summary", {}).get("total_drivetrain_kwh", 0)
    aux_lo = run_lo.get("summary", {}).get("total_auxiliary_kwh", 0)
    aux_hi = run_hi.get("summary", {}).get("total_auxiliary_kwh", 0)
    record(
        "weight_drivetrain_increases",
        dt_hi > dt_lo,
        f"4 packs drivetrain={dt_lo:.2f} kWh, 8 packs drivetrain={dt_hi:.2f} kWh",
    )
    record(
        "weight_auxiliary_unchanged",
        abs(aux_hi - aux_lo) < 0.01,
        f"4 packs aux={aux_lo:.2f} kWh, 8 packs aux={aux_hi:.2f} kWh",
    )


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_weight_override_occupancy(client: TestClient, prediction_env, record):
    """Verify that changing occupancy_percent changes total_weight and predictions."""
    token = prediction_env["token"]
    sid = prediction_env["shift_id"]
    bmid = prediction_env["bus_model_id"]

    run_0, preds_0 = _submit_and_wait(client, token, sid, bmid, occupancy_percent=0)
    run_100, preds_100 = _submit_and_wait(client, token, sid, bmid, occupancy_percent=100)

    ctx_0 = run_0.get("contextual_parameters", {})
    ctx_100 = run_100.get("contextual_parameters", {})

    weight_0 = ctx_0.get("total_weight_kg", 0)
    weight_100 = ctx_100.get("total_weight_kg", 0)

    max_pax = BUS_MODEL_SPECS["max_passengers"]
    expected_delta_kg = max_pax * 70
    actual_delta_kg = weight_100 - weight_0
    record(
        "occupancy_delta_correct",
        abs(actual_delta_kg - expected_delta_kg) < 1,
        f"delta={actual_delta_kg:.1f} kg (expected {expected_delta_kg})",
    )

    total_0 = run_0.get("summary", {}).get("total_consumption_kwh", 0)
    total_100 = run_100.get("summary", {}).get("total_consumption_kwh", 0)
    record(
        "occupancy_consumption_increases",
        total_100 > total_0,
        f"0%={total_0:.2f} kWh, 100%={total_100:.2f} kWh, diff={total_100 - total_0:.2f} kWh",
    )

    # Per-trip check
    kwh_0 = {p["trip_id"]: float(p["prediction_kwh"]) for p in preds_0}
    kwh_100 = {p["trip_id"]: float(p["prediction_kwh"]) for p in preds_100}
    common = set(kwh_0) & set(kwh_100)
    all_higher = all(kwh_100[tid] > kwh_0[tid] for tid in common)
    record(
        "occupancy_all_trips_higher",
        all_higher,
        f"checked {len(common)} trips",
    )


# ---------------------------------------------------------------------------
# (f) Auxiliary heating model & temperature tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_auxiliary_model_selection(client: TestClient, prediction_env, record):
    """At -5°C: default draws 24 kW (electric heating), diesel_heating draws 8 kW (diesel heater
    takes over heating, only base electrical load remains). Both curves converge above ~10°C."""
    token = prediction_env["token"]
    sid = prediction_env["shift_id"]
    bmid = prediction_env["bus_model_id"]

    run_def, _ = _submit_and_wait(
        client, token, sid, bmid,
        auxiliary_heating_type="default", external_temp_celsius=-5,
    )
    run_dsl, _ = _submit_and_wait(
        client, token, sid, bmid,
        auxiliary_heating_type="diesel_heating", external_temp_celsius=-5,
    )

    aux_def = run_def["summary"]["total_auxiliary_kwh"]
    aux_dsl = run_dsl["summary"]["total_auxiliary_kwh"]

    # Default at -5°C: 24 kW → high aux
    record(
        "aux_model_default_at_minus5",
        aux_def > 0,
        f"default aux={aux_def:.2f} kWh (24 kW curve)",
    )
    # Diesel at -5°C: 8 kW → lower aux (diesel heater handles heating)
    record(
        "aux_model_diesel_lower_at_minus5",
        aux_dsl > 0 and aux_dsl < aux_def,
        f"diesel aux={aux_dsl:.2f} kWh (8 kW curve), default={aux_def:.2f} kWh",
    )
    # The saving should be significant: 24 vs 8 kW → default is 3× higher
    ratio = aux_def / aux_dsl if aux_dsl > 0 else float("inf")
    record(
        "aux_model_default_3x_higher",
        ratio > 2.5,
        f"default/diesel ratio={ratio:.1f}x",
    )

    # Drivetrain should be identical (same weight, same route)
    dt_def = run_def["summary"]["total_drivetrain_kwh"]
    dt_dsl = run_dsl["summary"]["total_drivetrain_kwh"]
    record(
        "aux_model_drivetrain_same",
        abs(dt_def - dt_dsl) < 0.01,
        f"default dt={dt_def:.2f} kWh, diesel dt={dt_dsl:.2f} kWh",
    )

    # Total consumption must reflect the aux difference
    total_def = run_def["summary"]["total_consumption_kwh"]
    total_dsl = run_dsl["summary"]["total_consumption_kwh"]
    record(
        "aux_model_total_reflects_aux",
        total_def > total_dsl,
        f"default total={total_def:.2f} kWh, diesel total={total_dsl:.2f} kWh, diff={total_def - total_dsl:.2f} kWh",
    )


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_auxiliary_diesel_nonzero_above_threshold(client: TestClient, prediction_env, record):
    """diesel_heating at 20°C draws 10 kW (same as default) → non-zero auxiliary."""
    token = prediction_env["token"]
    sid = prediction_env["shift_id"]
    bmid = prediction_env["bus_model_id"]

    run_warm, _ = _submit_and_wait(
        client, token, sid, bmid,
        auxiliary_heating_type="diesel_heating", external_temp_celsius=20,
    )

    aux = run_warm["summary"]["total_auxiliary_kwh"]
    record(
        "aux_diesel_nonzero_at_20c",
        aux > 0,
        f"diesel aux at 20°C = {aux:.2f} kWh",
    )


@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
def test_temperature_effect_on_consumption(client: TestClient, prediction_env, record):
    """Compare cold (-5°C), mild (15°C), and hot (25°C) temperatures with default heating."""
    token = prediction_env["token"]
    sid = prediction_env["shift_id"]
    bmid = prediction_env["bus_model_id"]

    run_cold, _ = _submit_and_wait(client, token, sid, bmid, external_temp_celsius=-5)
    run_mild, _ = _submit_and_wait(client, token, sid, bmid, external_temp_celsius=15)
    run_hot,  _ = _submit_and_wait(client, token, sid, bmid, external_temp_celsius=25)

    aux_cold = run_cold["summary"]["total_auxiliary_kwh"]
    aux_mild = run_mild["summary"]["total_auxiliary_kwh"]
    aux_hot  = run_hot["summary"]["total_auxiliary_kwh"]

    # Default curve: -5°C → 24 kW, 15°C → 9 kW, 25°C → 16 kW (A/C)
    record(
        "temp_aux_cold_highest",
        aux_cold > aux_mild and aux_cold > aux_hot,
        f"cold={aux_cold:.2f}, mild={aux_mild:.2f}, hot={aux_hot:.2f} kWh",
    )
    record(
        "temp_aux_mild_lowest",
        aux_mild < aux_cold and aux_mild < aux_hot,
        f"mild={aux_mild:.2f} kWh is minimum",
    )
    record(
        "temp_aux_hot_above_mild",
        aux_hot > aux_mild,
        f"hot={aux_hot:.2f} > mild={aux_mild:.2f} kWh (A/C kicks in)",
    )

    # Drivetrain also varies with temperature because the QRF residual model
    # captures temperature-dependent effects beyond HVAC (tire resistance,
    # battery/motor efficiency, etc.).  Cold → higher drivetrain.
    dt_cold = run_cold["summary"]["total_drivetrain_kwh"]
    dt_mild = run_mild["summary"]["total_drivetrain_kwh"]
    dt_hot  = run_hot["summary"]["total_drivetrain_kwh"]
    record(
        "temp_drivetrain_cold_highest",
        dt_cold > dt_mild and dt_cold > dt_hot,
        f"cold dt={dt_cold:.2f}, mild dt={dt_mild:.2f}, hot dt={dt_hot:.2f} kWh",
    )

    # Total consumption: cold is highest (both aux and drivetrain peak in cold)
    total_cold = run_cold["summary"]["total_consumption_kwh"]
    total_mild = run_mild["summary"]["total_consumption_kwh"]
    total_hot  = run_hot["summary"]["total_consumption_kwh"]
    record(
        "temp_total_cold_highest",
        total_cold > total_mild and total_cold > total_hot,
        f"cold={total_cold:.2f}, mild={total_mild:.2f}, hot={total_hot:.2f} kWh",
    )
    record(
        "temp_total_mild_lowest",
        total_mild < total_cold and total_mild < total_hot,
        f"mild={total_mild:.2f} kWh is minimum total consumption",
    )
    record(
        "temp_consumption_per_km",
        True,
        (f"cold={run_cold['summary'].get('consumption_per_km_kwh', 0):.3f}, "
         f"mild={run_mild['summary'].get('consumption_per_km_kwh', 0):.3f}, "
         f"hot={run_hot['summary'].get('consumption_per_km_kwh', 0):.3f} kWh/km"),
    )


@pytest.mark.asyncio
@pytest.mark.skipif(_skip_cond, reason=_SKIP_REASON)
async def test_predict_handles_empty_shift(client: TestClient, prediction_env, record):
    """Shift with no trips results in status=failed with a meaningful error."""
    from app.models import Shifts

    token = prediction_env["token"]
    hdrs = auth_headers(token)

    engine, Session = _make_test_session()
    async with Session() as session:
        empty_shift = Shifts(
            name=f"Empty Shift {uuid.uuid4().hex[:8]}",
            bus_id=uuid.UUID(prediction_env["bus_id"]),
        )
        session.add(empty_shift)
        await session.commit()
        await session.refresh(empty_shift)
        empty_shift_id = str(empty_shift.id)

    try:
        r2 = client.post(
            f"{SIM_BASE}/prediction-runs/",
            json={
                "shift_ids": [empty_shift_id],
                "bus_model_id": prediction_env["bus_model_id"],
                "model_name": "greybox_qrf_production_crps_optimized_3",
                "external_temp_celsius": 10,
            },
            headers=hdrs,
        )
        record("empty_shift_submit_200", r2.status_code == 200, f"status={r2.status_code}")
        if r2.status_code != 200:
            return

        run_id = r2.json()["prediction_run_ids"][0]
        max_wait, elapsed, poll = 30, 0, 2
        status = "pending"
        while status in ("pending", "running") and elapsed < max_wait:
            time.sleep(poll)
            elapsed += poll
            rr = client.get(
                f"{SIM_BASE}/prediction-runs/{run_id}",
                headers=hdrs,
            )
            if rr.status_code == 200:
                status = rr.json().get("status", "unknown")

        record("empty_shift_status_failed", status == "failed", f"status={status} after {elapsed}s")
        if status == "failed":
            summary = rr.json().get("summary", {})
            record(
                "empty_shift_has_error",
                "error" in summary,
                f"summary={summary}",
            )
    finally:
        async with Session() as session:
            shift = await session.get(Shifts, uuid.UUID(empty_shift_id))
            if shift:
                await session.delete(shift)
                await session.commit()
        await engine.dispose()
