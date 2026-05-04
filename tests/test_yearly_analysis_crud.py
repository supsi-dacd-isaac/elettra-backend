"""Pytest-based yearly_analysis CRUD tests.
Run with: pytest tests/test_yearly_analysis_crud.py
Generates a human-readable report in tests/reports/ via report_collector fixture.
"""

import pytest
import asyncio
import json
import os
import uuid
from fastapi.testclient import TestClient

from app.core.config import get_cached_settings

__report_module__ = "yearly_analysis_crud"

API_BASE = "/api/v1/yearly-analysis"
AUTH_BASE = "/auth"

TEST_LOGIN_EMAIL = os.getenv("TEST_LOGIN_EMAIL", "test01.elettra@fart.ch")
TEST_LOGIN_PASSWORD = os.getenv("TEST_LOGIN_PASSWORD", "elettra")
TEST_AGENCY_ID = os.getenv("TEST_AGENCY_ID")
TMP_PASSWORD = "Tmp!Passw0rdXy"

settings = get_cached_settings()


def _dsn_for_asyncpg() -> str:
    dsn = settings.database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return dsn


def _register_temp_user(client: TestClient, *, prefix: str) -> dict:
    if not TEST_AGENCY_ID:
        pytest.skip("TEST_AGENCY_ID environment variable is required")

    email = f"{prefix}_{uuid.uuid4().hex[:10]}@example.com"
    response = client.post(
        f"{AUTH_BASE}/register",
        json={
            "company_id": TEST_AGENCY_ID,
            "email": email,
            "full_name": "Yearly Analysis Scope Test User",
            "password": TMP_PASSWORD,
            "role": "viewer",
        },
    )
    assert response.status_code == 200, (
        f"temp user register failed: status={response.status_code} body={response.text}"
    )
    data = response.json()
    return {"id": data["id"], "email": email, "password": TMP_PASSWORD}


def _login_as(client: TestClient, email: str, password: str) -> str | None:
    response = client.post(f"{AUTH_BASE}/login", json={"email": email, "password": password})
    if response.status_code == 200:
        return response.json().get("access_token")
    return None


async def _insert_owned_yearly_analysis_async(user_id: str, name: str) -> tuple[str, str]:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        optimization_run_id = await conn.fetchval(
            """
            INSERT INTO optimization_runs (user_id, mode, status, input_params)
            VALUES ($1::uuid, 'joint', 'completed', $2::jsonb)
            RETURNING id
            """,
            user_id,
            json.dumps({}),
        )
        yearly_analysis_id = await conn.fetchval(
            """
            INSERT INTO yearly_analysis (optimization_run_id, name, features)
            VALUES ($1::uuid, $2, $3::jsonb)
            RETURNING id
            """,
            optimization_run_id,
            name,
            json.dumps({}),
        )
        return str(optimization_run_id), str(yearly_analysis_id)
    finally:
        await conn.close()


async def _cleanup_owned_records_async(
    yearly_analysis_ids: list[str],
    optimization_run_ids: list[str],
    user_ids: list[str],
) -> None:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        if yearly_analysis_ids:
            await conn.execute(
                "DELETE FROM yearly_analysis WHERE id = ANY($1::uuid[])",
                yearly_analysis_ids,
            )
        if optimization_run_ids:
            await conn.execute(
                "DELETE FROM optimization_runs WHERE id = ANY($1::uuid[])",
                optimization_run_ids,
            )
        if user_ids:
            await conn.execute(
                "DELETE FROM users WHERE id = ANY($1::uuid[])",
                user_ids,
            )
    finally:
        await conn.close()


def _insert_owned_yearly_analysis(user_id: str, name: str) -> tuple[str, str]:
    return asyncio.run(_insert_owned_yearly_analysis_async(user_id, name))


def _cleanup_owned_records(
    yearly_analysis_ids: list[str],
    optimization_run_ids: list[str],
    user_ids: list[str],
) -> None:
    asyncio.run(_cleanup_owned_records_async(yearly_analysis_ids, optimization_run_ids, user_ids))


def get_auth_token(client: TestClient) -> str | None:
    response = client.post(f"{AUTH_BASE}/login", json={
        "email": TEST_LOGIN_EMAIL,
        "password": TEST_LOGIN_PASSWORD,
    })
    if response.status_code == 200:
        return response.json().get("access_token")
    return None


def get_auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def create_test_analysis(client: TestClient, token: str, name: str = "Test Analysis", features: dict | None = None) -> str | None:
    payload = {"name": name, "features": features or {}}
    response = client.post(f"{API_BASE}/", json=payload, headers=get_auth_headers(token))
    if response.status_code == 200:
        return response.json()["id"]
    return None


@pytest.fixture(autouse=True)
def cleanup_test_analyses(client):
    yield
    try:
        token = get_auth_token(client)
        if not token:
            return
        headers = get_auth_headers(token)
        # Paginated response: walk every page so cleanup also works when more
        # than one page exists.
        test_names = {
            "Test Analysis", "Minimal Analysis", "Get Analysis",
            "Update Analysis", "Delete Analysis", "Filter Analysis",
            "Workflow Analysis",
        }
        skip = 0
        limit = 100
        while True:
            response = client.get(
                f"{API_BASE}/?skip={skip}&limit={limit}",
                headers=headers,
            )
            if response.status_code != 200:
                break
            payload = response.json()
            items = payload.get("items", [])
            for item in items:
                if item.get("name") in test_names:
                    client.delete(f"{API_BASE}/{item['id']}", headers=headers)
            if not payload.get("has_next"):
                break
            skip += limit
    except Exception:
        pass


# ------------------------------------------------------------------
# Create
# ------------------------------------------------------------------

def test_create_yearly_analysis_success(client, record):
    token = get_auth_token(client)
    if not token:
        record("create_auth_failed", False, "Could not get auth token")
        return

    payload = {"name": "Test Analysis", "features": {"scenario": "baseline"}}
    headers = get_auth_headers(token)
    response = client.post(f"{API_BASE}/", json=payload, headers=headers)

    record("create_success", response.status_code == 200, f"status={response.status_code}")
    if response.status_code == 200:
        data = response.json()
        record("create_has_id", "id" in data, "id field missing")
        record("create_name", data.get("name") == "Test Analysis", f"name={data.get('name')}")
        record("create_features", data.get("features") == {"scenario": "baseline"}, f"features={data.get('features')}")
        record("create_has_created_at", "created_at" in data, "created_at missing")
        record("create_opt_run_null", data.get("optimization_run_id") is None, f"opt_run_id={data.get('optimization_run_id')}")

        client.delete(f"{API_BASE}/{data['id']}", headers=headers)


def test_create_yearly_analysis_minimal(client, record):
    token = get_auth_token(client)
    if not token:
        record("create_minimal_auth_failed", False, "Could not get auth token")
        return

    payload = {"name": "Minimal Analysis"}
    headers = get_auth_headers(token)
    response = client.post(f"{API_BASE}/", json=payload, headers=headers)

    record("create_minimal", response.status_code == 200, f"status={response.status_code}")
    if response.status_code == 200:
        data = response.json()
        record("create_minimal_features_default", data.get("features") == {}, f"features={data.get('features')}")
        client.delete(f"{API_BASE}/{data['id']}", headers=headers)


def test_create_yearly_analysis_missing_name(client, record):
    token = get_auth_token(client)
    if not token:
        record("create_missing_name_auth_failed", False, "Could not get auth token")
        return

    payload = {"features": {"a": 1}}
    headers = get_auth_headers(token)
    response = client.post(f"{API_BASE}/", json=payload, headers=headers)

    record("create_missing_name", response.status_code == 422, f"status={response.status_code}")


def test_create_yearly_analysis_invalid_opt_run(client, record):
    token = get_auth_token(client)
    if not token:
        record("create_invalid_opt_auth_failed", False, "Could not get auth token")
        return

    payload = {
        "name": "Test Analysis",
        "optimization_run_id": "00000000-0000-0000-0000-000000000999",
    }
    headers = get_auth_headers(token)
    response = client.post(f"{API_BASE}/", json=payload, headers=headers)

    record("create_invalid_opt_run", response.status_code == 404, f"status={response.status_code}")


def test_create_yearly_analysis_unauthorized(client, record):
    payload = {"name": "Test Analysis"}
    response = client.post(f"{API_BASE}/", json=payload)

    record("create_unauthorized", response.status_code == 403, f"status={response.status_code}")


# ------------------------------------------------------------------
# Get by ID
# ------------------------------------------------------------------

def test_get_yearly_analysis_success(client, record):
    token = get_auth_token(client)
    if not token:
        record("get_auth_failed", False, "Could not get auth token")
        return

    analysis_id = create_test_analysis(client, token, "Get Analysis")
    if not analysis_id:
        record("get_create_failed", False, "Could not create test analysis")
        return

    headers = get_auth_headers(token)
    response = client.get(f"{API_BASE}/{analysis_id}", headers=headers)

    record("get_success", response.status_code == 200, f"status={response.status_code}")
    if response.status_code == 200:
        data = response.json()
        record("get_correct_id", data.get("id") == analysis_id, f"expected={analysis_id}, got={data.get('id')}")
        record("get_correct_name", data.get("name") == "Get Analysis", f"name={data.get('name')}")

    client.delete(f"{API_BASE}/{analysis_id}", headers=headers)


def test_get_yearly_analysis_not_found(client, record):
    token = get_auth_token(client)
    if not token:
        record("get_not_found_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    response = client.get(f"{API_BASE}/00000000-0000-0000-0000-000000000999", headers=headers)

    record("get_not_found", response.status_code == 404, f"status={response.status_code}")


# ------------------------------------------------------------------
# List
# ------------------------------------------------------------------

def test_list_yearly_analyses(client, record):
    token = get_auth_token(client)
    if not token:
        record("list_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    response = client.get(f"{API_BASE}/", headers=headers)

    record("list_success", response.status_code == 200, f"status={response.status_code}")
    if response.status_code == 200:
        body = response.json()
        record(
            "list_is_paginated",
            isinstance(body, dict) and isinstance(body.get("items"), list),
            f"type={type(body)} keys={list(body.keys()) if isinstance(body, dict) else None}",
        )
        for key in ("items", "total", "skip", "limit", "count", "has_next", "has_previous"):
            record(f"list_has_{key}", key in body, f"keys={list(body.keys())}")
        record("list_default_limit_20", body.get("limit") == 20, f"limit={body.get('limit')}")


def test_list_yearly_analyses_filter_by_optimization_run(client, record):
    token = get_auth_token(client)
    if not token:
        record("list_filter_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    a_id = create_test_analysis(client, token, "Filter Analysis")
    if not a_id:
        record("list_filter_create_failed", False, "Could not create test analysis")
        return

    fake_opt_id = "00000000-0000-0000-0000-000000000001"
    response = client.get(f"{API_BASE}/?optimization_run_id={fake_opt_id}", headers=headers)

    record("list_filter_success", response.status_code == 200, f"status={response.status_code}")
    if response.status_code == 200:
        data = response.json()
        items = data.get("items", [])
        all_match = all(
            item.get("optimization_run_id") == fake_opt_id
            for item in items
        )
        record("list_filter_correct", all_match, f"items={len(items)}")

    client.delete(f"{API_BASE}/{a_id}", headers=headers)


def test_list_yearly_analyses_scoped_to_current_user(client, record):
    user_ids: list[str] = []
    optimization_run_ids: list[str] = []
    yearly_analysis_ids: list[str] = []

    try:
        user_a = _register_temp_user(client, prefix="tmp_yearly_scope_a")
        user_b = _register_temp_user(client, prefix="tmp_yearly_scope_b")
        user_ids.extend([user_a["id"], user_b["id"]])

        token_a = _login_as(client, user_a["email"], user_a["password"])
        record("list_scope_user_a_login", token_a is not None, "Could not login as user A")

        token_b = _login_as(client, user_b["email"], user_b["password"])
        record("list_scope_user_b_login", token_b is not None, "Could not login as user B")

        opt_a_id, analysis_a_id = _insert_owned_yearly_analysis(user_a["id"], "Scoped Analysis A")
        opt_b_id, analysis_b_id = _insert_owned_yearly_analysis(user_b["id"], "Scoped Analysis B")
        optimization_run_ids.extend([opt_a_id, opt_b_id])
        yearly_analysis_ids.extend([analysis_a_id, analysis_b_id])

        response_a = client.get(f"{API_BASE}/", headers=get_auth_headers(token_a))
        record("list_scope_user_a_status", response_a.status_code == 200, f"status={response_a.status_code}")
        ids_a = {item["id"] for item in response_a.json().get("items", [])}
        record("list_scope_user_a_sees_own", analysis_a_id in ids_a, f"ids={sorted(ids_a)}")
        record("list_scope_user_a_hides_other", analysis_b_id not in ids_a, f"ids={sorted(ids_a)}")

        response_b = client.get(f"{API_BASE}/", headers=get_auth_headers(token_b))
        record("list_scope_user_b_status", response_b.status_code == 200, f"status={response_b.status_code}")
        ids_b = {item["id"] for item in response_b.json().get("items", [])}
        record("list_scope_user_b_sees_own", analysis_b_id in ids_b, f"ids={sorted(ids_b)}")
        record("list_scope_user_b_hides_other", analysis_a_id not in ids_b, f"ids={sorted(ids_b)}")
    finally:
        _cleanup_owned_records(yearly_analysis_ids, optimization_run_ids, user_ids)


def test_list_yearly_analyses_empty_for_user_without_analyses(client, record):
    user_ids: list[str] = []

    try:
        user = _register_temp_user(client, prefix="tmp_yearly_empty")
        user_ids.append(user["id"])

        token = _login_as(client, user["email"], user["password"])
        record("list_empty_user_login", token is not None, "Could not login as empty user")

        response = client.get(f"{API_BASE}/", headers=get_auth_headers(token))
        record("list_empty_user_status", response.status_code == 200, f"status={response.status_code}")
        body = response.json()
        record(
            "list_empty_user_returns_empty",
            body.get("items") == [] and body.get("total") == 0,
            f"body={body}",
        )
    finally:
        _cleanup_owned_records([], [], user_ids)


def test_list_yearly_analyses_unauthorized(client, record):
    response = client.get(f"{API_BASE}/")
    record("list_unauthorized", response.status_code == 403, f"status={response.status_code}")


# ------------------------------------------------------------------
# Update (PATCH)
# ------------------------------------------------------------------

def test_update_yearly_analysis_success(client, record):
    token = get_auth_token(client)
    if not token:
        record("update_auth_failed", False, "Could not get auth token")
        return

    analysis_id = create_test_analysis(client, token, "Update Analysis")
    if not analysis_id:
        record("update_create_failed", False, "Could not create test analysis")
        return

    headers = get_auth_headers(token)
    response = client.patch(
        f"{API_BASE}/{analysis_id}",
        json={"name": "Updated Name"},
        headers=headers,
    )

    record("update_success", response.status_code == 200, f"status={response.status_code}")
    if response.status_code == 200:
        data = response.json()
        record("update_name_changed", data.get("name") == "Updated Name", f"name={data.get('name')}")

    client.delete(f"{API_BASE}/{analysis_id}", headers=headers)


def test_update_yearly_analysis_not_found(client, record):
    token = get_auth_token(client)
    if not token:
        record("update_not_found_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    response = client.patch(
        f"{API_BASE}/00000000-0000-0000-0000-000000000999",
        json={"name": "Does not exist"},
        headers=headers,
    )

    record("update_not_found", response.status_code == 404, f"status={response.status_code}")


# ------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------

def test_delete_yearly_analysis_success(client, record):
    token = get_auth_token(client)
    if not token:
        record("delete_auth_failed", False, "Could not get auth token")
        return

    analysis_id = create_test_analysis(client, token, "Delete Analysis")
    if not analysis_id:
        record("delete_create_failed", False, "Could not create test analysis")
        return

    headers = get_auth_headers(token)
    response = client.delete(f"{API_BASE}/{analysis_id}", headers=headers)

    record("delete_success", response.status_code == 200, f"status={response.status_code}")
    if response.status_code == 200:
        data = response.json()
        record("delete_message", "message" in data, f"response={data}")

        verify = client.get(f"{API_BASE}/{analysis_id}", headers=headers)
        record("delete_verify", verify.status_code == 404, f"verify_status={verify.status_code}")


def test_delete_yearly_analysis_not_found(client, record):
    token = get_auth_token(client)
    if not token:
        record("delete_not_found_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    response = client.delete(f"{API_BASE}/00000000-0000-0000-0000-000000000999", headers=headers)

    record("delete_not_found", response.status_code == 404, f"status={response.status_code}")


# ------------------------------------------------------------------
# Energy summary endpoint
# ------------------------------------------------------------------

def test_energy_summary_not_found(client, record):
    token = get_auth_token(client)
    if not token:
        record("energy_summary_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    response = client.get(
        f"{API_BASE}/00000000-0000-0000-0000-000000000999/energy-summary",
        headers=headers,
    )
    record("energy_summary_not_found", response.status_code == 404, f"status={response.status_code}")


def test_energy_summary_no_predictions(client, record):
    token = get_auth_token(client)
    if not token:
        record("energy_summary_no_pred_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    analysis_id = create_test_analysis(client, token, "Test Analysis", {"scenarios": []})
    if not analysis_id:
        record("energy_summary_no_pred_create_failed", False, "Could not create analysis")
        return

    response = client.get(f"{API_BASE}/{analysis_id}/energy-summary", headers=headers)
    record(
        "energy_summary_no_predictions_404",
        response.status_code == 404,
        f"status={response.status_code}",
    )

    client.delete(f"{API_BASE}/{analysis_id}", headers=headers)


def test_energy_summary_with_real_diesel_data(client, record):
    """Test energy summary GET on the existing diesel yearly analysis (if present)."""
    token = get_auth_token(client)
    if not token:
        record("energy_summary_diesel_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    diesel_ya = _find_yearly_analysis_by_heating_type(client, headers, "diesel")
    if diesel_ya is None:
        record("energy_summary_diesel_skip", True, "No diesel yearly analysis found (skipped)")
        return

    ya_id = diesel_ya["id"]
    resp = client.get(f"{API_BASE}/{ya_id}/energy-summary", headers=headers)
    record("energy_summary_diesel_200", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code != 200:
        return

    data = resp.json()
    record(
        "energy_summary_has_scenarios",
        len(data.get("scenarios", [])) > 0,
        f"scenario_count={len(data.get('scenarios', []))}",
    )
    record(
        "energy_summary_has_yearly_totals",
        "electric_kwh" in data.get("yearly_totals", {}),
        f"yearly_totals_keys={list(data.get('yearly_totals', {}).keys())}",
    )
    record(
        "energy_summary_has_diesel_totals",
        data.get("yearly_diesel_heating") is not None,
        f"yearly_diesel_heating={data.get('yearly_diesel_heating')}",
    )
    if data.get("yearly_diesel_heating"):
        dht = data["yearly_diesel_heating"]
        record(
            "energy_summary_diesel_fuel_positive",
            dht.get("diesel_fuel_kwh", 0) > 0,
            f"diesel_fuel_kwh={dht.get('diesel_fuel_kwh')}",
        )
        record(
            "energy_summary_diesel_liters_positive",
            dht.get("diesel_liters", 0) > 0,
            f"diesel_liters={dht.get('diesel_liters')}",
        )

    cold_scenario = None
    for sc in data.get("scenarios", []):
        if sc.get("temperature_celsius", 999) < 0:
            cold_scenario = sc
            break

    if cold_scenario:
        record(
            "energy_summary_cold_has_diesel",
            cold_scenario.get("diesel_heating") is not None
            and cold_scenario["diesel_heating"].get("diesel_fuel_kwh", 0) > 0,
            f"cold_scenario_diesel={cold_scenario.get('diesel_heating')}",
        )


def test_energy_summary_post_persists(client, record):
    """Test POST energy-summary stores energy_summary in features."""
    token = get_auth_token(client)
    if not token:
        record("energy_summary_post_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    # Walk every page; load the detail to inspect ``features``.
    target_ya = None
    skip = 0
    limit = 100
    while target_ya is None:
        response = client.get(
            f"{API_BASE}/?skip={skip}&limit={limit}",
            headers=headers,
        )
        if response.status_code != 200:
            record("energy_summary_post_list_failed", False, f"status={response.status_code}")
            return
        payload = response.json()
        for item in payload.get("items", []):
            detail = client.get(f"{API_BASE}/{item['id']}", headers=headers)
            if detail.status_code != 200:
                continue
            full = detail.json()
            features = full.get("features", {}) or {}
            if features.get("scenarios"):
                target_ya = full
                break
        if target_ya or not payload.get("has_next"):
            break
        skip += limit

    if target_ya is None:
        record("energy_summary_post_skip", True, "No yearly analysis with scenarios found (skipped)")
        return

    ya_id = target_ya["id"]
    resp = client.post(f"{API_BASE}/{ya_id}/energy-summary", headers=headers)
    record("energy_summary_post_200", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code != 200:
        return

    verify = client.get(f"{API_BASE}/{ya_id}", headers=headers)
    if verify.status_code == 200:
        features = verify.json().get("features", {})
        record(
            "energy_summary_persisted",
            "energy_summary" in features,
            f"features_keys={list(features.keys())}",
        )
        if "energy_summary" in features:
            es = features["energy_summary"]
            record(
                "energy_summary_persisted_has_totals",
                "yearly_totals" in es,
                f"energy_summary_keys={list(es.keys())}",
            )


# ------------------------------------------------------------------
# Yearly cost endpoint
# ------------------------------------------------------------------

def test_costs_not_found(client, record):
    token = get_auth_token(client)
    if not token:
        record("costs_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    response = client.get(
        f"{API_BASE}/00000000-0000-0000-0000-000000000999/costs",
        params={"bus_length_m": 12},
        headers=headers,
    )
    record("costs_not_found", response.status_code == 404, f"status={response.status_code}")


def test_costs_no_predictions(client, record):
    token = get_auth_token(client)
    if not token:
        record("costs_no_pred_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    analysis_id = create_test_analysis(client, token, "Test Analysis", {"scenarios": []})
    if not analysis_id:
        record("costs_no_pred_create_failed", False, "Could not create analysis")
        return

    response = client.get(
        f"{API_BASE}/{analysis_id}/costs",
        params={"bus_length_m": 12},
        headers=headers,
    )
    record(
        "costs_no_predictions_404",
        response.status_code == 404,
        f"status={response.status_code}",
    )

    client.delete(f"{API_BASE}/{analysis_id}", headers=headers)


def _find_yearly_analysis_by_heating_type(client, headers, heating_type):
    """Helper: find the first yearly analysis with the given heating type.

    The list endpoint is paginated and excludes ``features``, so we walk the
    pages and load each candidate's detail to inspect features.
    """
    skip = 0
    limit = 100
    while True:
        response = client.get(
            f"{API_BASE}/?skip={skip}&limit={limit}",
            headers=headers,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        for item in payload.get("items", []):
            detail = client.get(f"{API_BASE}/{item['id']}", headers=headers)
            if detail.status_code != 200:
                continue
            full = detail.json()
            features = full.get("features", {}) or {}
            config = features.get("config", {})
            ht = config.get("auxiliary_heating_type", "default")
            has_scenarios = bool(features.get("scenarios"))
            if ht == heating_type and has_scenarios:
                return full
        if not payload.get("has_next"):
            return None
        skip += limit


def test_costs_default_no_diesel_heating(client, record):
    """For auxiliary_heating_type=default, diesel heating OPEX must be zero."""
    token = get_auth_token(client)
    if not token:
        record("costs_default_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    ya = _find_yearly_analysis_by_heating_type(client, headers, "default")
    if ya is None:
        record("costs_default_skip", True, "No default yearly analysis found (skipped)")
        return

    resp = client.get(
        f"{API_BASE}/{ya['id']}/costs",
        params={"bus_length_m": 12},
        headers=headers,
    )
    record("costs_default_200", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code != 200:
        return

    data = resp.json()
    record(
        "costs_default_aux_type",
        data.get("auxiliary_heating_type") == "default",
        f"aux_type={data.get('auxiliary_heating_type')}",
    )

    ebus_opex_names = [i["name"] for i in data["ebus"]["opex_items"]]
    record(
        "costs_default_no_dh_opex",
        "Diesel heating fuel" not in ebus_opex_names,
        f"opex_names={ebus_opex_names}",
    )

    assumptions = data.get("assumptions", {})
    record(
        "costs_default_zero_dh_liters",
        assumptions.get("yearly_diesel_heating_liters", 0) == 0,
        f"dh_liters={assumptions.get('yearly_diesel_heating_liters')}",
    )


def test_costs_diesel_mixed_case(client, record):
    """For auxiliary_heating_type=diesel, mixed e-bus must have non-zero diesel heating OPEX."""
    token = get_auth_token(client)
    if not token:
        record("costs_diesel_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    ya = _find_yearly_analysis_by_heating_type(client, headers, "diesel")
    if ya is None:
        record("costs_diesel_skip", True, "No diesel yearly analysis found (skipped)")
        return

    resp = client.get(
        f"{API_BASE}/{ya['id']}/costs",
        params={"bus_length_m": 12},
        headers=headers,
    )
    record("costs_diesel_200", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code != 200:
        return

    data = resp.json()
    record(
        "costs_diesel_aux_type",
        data.get("auxiliary_heating_type") == "diesel",
        f"aux_type={data.get('auxiliary_heating_type')}",
    )

    # Validate 1: mixed e-bus has non-zero diesel heating fuel OPEX
    ebus_opex = {i["name"]: i["cost_chf_per_year"] for i in data["ebus"]["opex_items"]}
    record(
        "costs_diesel_has_dh_fuel",
        "Diesel heating fuel" in ebus_opex,
        f"opex_names={list(ebus_opex.keys())}",
    )
    record(
        "costs_diesel_dh_fuel_positive",
        ebus_opex.get("Diesel heating fuel", 0) > 0,
        f"dh_fuel_chf={ebus_opex.get('Diesel heating fuel')}",
    )

    # Validate 2: mixed e-bus has non-zero diesel heating maintenance OPEX
    record(
        "costs_diesel_has_dh_maint",
        "Diesel heating maintenance" in ebus_opex,
        f"opex_names={list(ebus_opex.keys())}",
    )
    record(
        "costs_diesel_dh_maint_positive",
        ebus_opex.get("Diesel heating maintenance", 0) > 0,
        f"dh_maint_chf={ebus_opex.get('Diesel heating maintenance')}",
    )

    # Validate 3: assumptions show non-zero diesel heating liters
    assumptions = data.get("assumptions", {})
    record(
        "costs_diesel_yearly_dh_liters_positive",
        assumptions.get("yearly_diesel_heating_liters", 0) > 0,
        f"dh_liters={assumptions.get('yearly_diesel_heating_liters')}",
    )
    record(
        "costs_diesel_dhmf",
        assumptions.get("diesel_heating_maintenance_factor", 0) == 0.10,
        f"dhmf={assumptions.get('diesel_heating_maintenance_factor')}",
    )

    # Validate 4: mixed e-bus total includes diesel heating costs
    ebus_total = data["ebus"]["total_annual_cost_chf_per_year"]
    electric_only = ebus_opex.get("Energy", 0) + ebus_opex.get("Maintenance", 0)
    record(
        "costs_diesel_total_gt_electric_only",
        ebus_total > electric_only,
        f"total={ebus_total}, electric_only={electric_only}",
    )

    # Validate 5: diesel comparator is separate (full-diesel, no DH items)
    diesel_comp_opex_names = [i["name"] for i in data["diesel_comparator"]["opex_items"]]
    record(
        "costs_diesel_comparator_separate",
        "Diesel heating fuel" not in diesel_comp_opex_names,
        f"diesel_opex_names={diesel_comp_opex_names}",
    )
    record(
        "costs_diesel_comparator_has_fuel",
        "Fuel" in diesel_comp_opex_names,
        f"diesel_opex_names={diesel_comp_opex_names}",
    )

    # Validate 6: annual saving uses mixed e-bus vs diesel comparator
    expected_saving = round(
        data["diesel_comparator"]["total_annual_cost_chf_per_year"] - ebus_total, 2,
    )
    record(
        "costs_diesel_saving_correct",
        abs(data["annual_saving_chf"] - expected_saving) < 0.01,
        f"saving={data['annual_saving_chf']}, expected={expected_saving}",
    )


def test_costs_diesel_cold_scenarios(client, record):
    """Cold scenarios in diesel mode must have non-zero diesel-heating costs."""
    token = get_auth_token(client)
    if not token:
        record("costs_cold_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    ya = _find_yearly_analysis_by_heating_type(client, headers, "diesel")
    if ya is None:
        record("costs_cold_skip", True, "No diesel yearly analysis found (skipped)")
        return

    resp = client.get(
        f"{API_BASE}/{ya['id']}/costs",
        params={"bus_length_m": 12},
        headers=headers,
    )
    if resp.status_code != 200:
        record("costs_cold_fetch_failed", False, f"status={resp.status_code}")
        return

    scenarios = resp.json().get("scenarios", [])
    cold = [s for s in scenarios if s["temperature_celsius"] < 10]

    if not cold:
        record("costs_cold_no_cold_scenarios", True, "No cold scenarios (skipped)")
        return

    coldest = min(cold, key=lambda s: s["temperature_celsius"])
    record(
        "costs_cold_daily_dh_liters_positive",
        coldest["daily_diesel_heating_liters"] > 0,
        f"temp={coldest['temperature_celsius']}, daily_dh_L={coldest['daily_diesel_heating_liters']}",
    )
    record(
        "costs_cold_annual_dh_liters_positive",
        coldest["annual_diesel_heating_liters"] > 0,
        f"annual_dh_L={coldest['annual_diesel_heating_liters']}",
    )
    record(
        "costs_cold_dh_fuel_cost_positive",
        coldest["annual_diesel_heating_fuel_cost_chf"] > 0,
        f"annual_dh_fuel_chf={coldest['annual_diesel_heating_fuel_cost_chf']}",
    )
    record(
        "costs_cold_dh_maint_cost_positive",
        coldest["annual_diesel_heating_maint_cost_chf"] > 0,
        f"annual_dh_maint_chf={coldest['annual_diesel_heating_maint_cost_chf']}",
    )


def test_costs_with_custom_params(client, record):
    """Verify that custom economic parameters override defaults."""
    token = get_auth_token(client)
    if not token:
        record("costs_custom_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    ya = _find_yearly_analysis_by_heating_type(client, headers, "diesel")
    if ya is None:
        ya = _find_yearly_analysis_by_heating_type(client, headers, "default")
    if ya is None:
        record("costs_custom_skip", True, "No yearly analysis found (skipped)")
        return

    resp_default = client.get(
        f"{API_BASE}/{ya['id']}/costs",
        params={"bus_length_m": 12},
        headers=headers,
    )
    resp_custom = client.get(
        f"{API_BASE}/{ya['id']}/costs",
        params={
            "bus_length_m": 12,
            "energy_price_per_kwh": 0.50,
            "fuel_cost_per_l": 3.0,
        },
        headers=headers,
    )
    if resp_default.status_code != 200 or resp_custom.status_code != 200:
        record("costs_custom_fetch_failed", False, "Could not fetch costs")
        return

    d_default = resp_default.json()
    d_custom = resp_custom.json()

    record(
        "costs_custom_energy_price_applied",
        d_custom["assumptions"]["energy_price_per_kwh"] == 0.50,
        f"epk={d_custom['assumptions']['energy_price_per_kwh']}",
    )
    record(
        "costs_custom_fuel_price_applied",
        d_custom["assumptions"]["fuel_cost_per_l"] == 3.0,
        f"fpl={d_custom['assumptions']['fuel_cost_per_l']}",
    )
    record(
        "costs_custom_ebus_total_differs",
        d_custom["ebus"]["total_opex_chf_per_year"]
        != d_default["ebus"]["total_opex_chf_per_year"],
        f"custom={d_custom['ebus']['total_opex_chf_per_year']}, default={d_default['ebus']['total_opex_chf_per_year']}",
    )


# ------------------------------------------------------------------
# Yearly emissions endpoint
# ------------------------------------------------------------------

def test_emissions_not_found(client, record):
    token = get_auth_token(client)
    if not token:
        record("emissions_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    response = client.get(
        f"{API_BASE}/00000000-0000-0000-0000-000000000999/emissions",
        params={"bus_length_m": 12},
        headers=headers,
    )
    record("emissions_not_found", response.status_code == 404, f"status={response.status_code}")


def test_emissions_no_predictions(client, record):
    token = get_auth_token(client)
    if not token:
        record("emissions_no_pred_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    analysis_id = create_test_analysis(client, token, "Test Analysis", {"scenarios": []})
    if not analysis_id:
        record("emissions_no_pred_create_failed", False, "Could not create analysis")
        return

    response = client.get(
        f"{API_BASE}/{analysis_id}/emissions",
        params={"bus_length_m": 12},
        headers=headers,
    )
    record(
        "emissions_no_predictions_404",
        response.status_code == 404,
        f"status={response.status_code}",
    )
    client.delete(f"{API_BASE}/{analysis_id}", headers=headers)


def test_emissions_default_no_diesel_heating(client, record):
    """For auxiliary_heating_type=default, diesel-heating emission contribution must be zero."""
    token = get_auth_token(client)
    if not token:
        record("emissions_default_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    ya = _find_yearly_analysis_by_heating_type(client, headers, "default")
    if ya is None:
        record("emissions_default_skip", True, "No default yearly analysis found (skipped)")
        return

    resp = client.get(
        f"{API_BASE}/{ya['id']}/emissions",
        params={"bus_length_m": 12},
        headers=headers,
    )
    record("emissions_default_200", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code != 200:
        return

    data = resp.json()
    record(
        "emissions_default_aux_type",
        data.get("auxiliary_heating_type") == "default",
        f"aux_type={data.get('auxiliary_heating_type')}",
    )

    gwp = data.get("ebus", {}).get("gwp100a", {})
    record(
        "emissions_default_zero_dh_gwp",
        gwp.get("diesel_heating", 0) == 0,
        f"gwp_dh={gwp.get('diesel_heating')}",
    )
    record(
        "emissions_default_electric_positive",
        gwp.get("electric", 0) > 0,
        f"gwp_el={gwp.get('electric')}",
    )
    record(
        "emissions_default_total_eq_electric",
        abs(gwp.get("total", 0) - gwp.get("electric", 0)) < 0.01,
        f"total={gwp.get('total')}, electric={gwp.get('electric')}",
    )

    assumptions = data.get("assumptions", {})
    record(
        "emissions_default_zero_dh_liters",
        assumptions.get("yearly_diesel_heating_liters", 0) == 0,
        f"dh_liters={assumptions.get('yearly_diesel_heating_liters')}",
    )


def test_emissions_diesel_mixed_case(client, record):
    """For auxiliary_heating_type=diesel, mixed e-bus must have non-zero diesel-heating emissions."""
    token = get_auth_token(client)
    if not token:
        record("emissions_diesel_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    ya = _find_yearly_analysis_by_heating_type(client, headers, "diesel")
    if ya is None:
        record("emissions_diesel_skip", True, "No diesel yearly analysis found (skipped)")
        return

    resp = client.get(
        f"{API_BASE}/{ya['id']}/emissions",
        params={"bus_length_m": 12},
        headers=headers,
    )
    record("emissions_diesel_200", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code != 200:
        return

    data = resp.json()
    record(
        "emissions_diesel_aux_type",
        data.get("auxiliary_heating_type") == "diesel",
        f"aux_type={data.get('auxiliary_heating_type')}",
    )

    # 1: mixed e-bus has non-zero diesel-heating CO2
    gwp = data.get("ebus", {}).get("gwp100a", {})
    record(
        "emissions_diesel_dh_gwp_positive",
        gwp.get("diesel_heating", 0) > 0,
        f"gwp_dh={gwp.get('diesel_heating')}",
    )
    record(
        "emissions_diesel_electric_positive",
        gwp.get("electric", 0) > 0,
        f"gwp_el={gwp.get('electric')}",
    )
    record(
        "emissions_diesel_total_gt_electric",
        gwp.get("total", 0) > gwp.get("electric", 0),
        f"total={gwp.get('total')}, electric={gwp.get('electric')}",
    )

    # 2: NOx also has diesel-heating contribution
    nox = data.get("ebus", {}).get("nox", {})
    record(
        "emissions_diesel_dh_nox_positive",
        nox.get("diesel_heating", 0) > 0,
        f"nox_dh={nox.get('diesel_heating')}",
    )

    # 3: PM10 also has diesel-heating contribution
    pm10 = data.get("ebus", {}).get("pm10", {})
    record(
        "emissions_diesel_dh_pm10_positive",
        pm10.get("diesel_heating", 0) > 0,
        f"pm10_dh={pm10.get('diesel_heating')}",
    )

    # 4: assumptions show non-zero diesel-heating liters
    assumptions = data.get("assumptions", {})
    record(
        "emissions_diesel_yearly_dh_liters_positive",
        assumptions.get("yearly_diesel_heating_liters", 0) > 0,
        f"dh_liters={assumptions.get('yearly_diesel_heating_liters')}",
    )

    # 5: diesel comparator is separate (different total from mixed e-bus)
    dc_gwp = data.get("diesel_comparator", {}).get("gwp100a", {})
    record(
        "emissions_diesel_comparator_different",
        dc_gwp.get("total", 0) != gwp.get("total", 0),
        f"ebus_total={gwp.get('total')}, diesel_total={dc_gwp.get('total')}",
    )

    # 6: annual saving is correctly computed
    expected_saving = round(dc_gwp.get("total", 0) - gwp.get("total", 0), 4)
    record(
        "emissions_diesel_saving_correct",
        abs(data.get("annual_saving", {}).get("gwp100a", 0) - expected_saving) < 1,
        f"saving={data.get('annual_saving', {}).get('gwp100a')}, expected={expected_saving}",
    )


def test_emissions_diesel_cold_scenarios(client, record):
    """Cold scenarios in diesel mode must have non-zero diesel-heating CO2 emissions."""
    token = get_auth_token(client)
    if not token:
        record("emissions_cold_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    ya = _find_yearly_analysis_by_heating_type(client, headers, "diesel")
    if ya is None:
        record("emissions_cold_skip", True, "No diesel yearly analysis found (skipped)")
        return

    resp = client.get(
        f"{API_BASE}/{ya['id']}/emissions",
        params={"bus_length_m": 12},
        headers=headers,
    )
    if resp.status_code != 200:
        record("emissions_cold_fetch_failed", False, f"status={resp.status_code}")
        return

    scenarios = resp.json().get("scenarios", [])
    cold = [s for s in scenarios if s["temperature_celsius"] < 10]

    if not cold:
        record("emissions_cold_no_cold_scenarios", True, "No cold scenarios (skipped)")
        return

    coldest = min(cold, key=lambda s: s["temperature_celsius"])
    record(
        "emissions_cold_dh_liters_positive",
        coldest["annual_diesel_heating_liters"] > 0,
        f"temp={coldest['temperature_celsius']}, annual_dh_L={coldest['annual_diesel_heating_liters']}",
    )
    record(
        "emissions_cold_gwp_dh_positive",
        coldest["gwp100a_diesel_heating_kg"] > 0,
        f"gwp_dh_kg={coldest['gwp100a_diesel_heating_kg']}",
    )
    record(
        "emissions_cold_gwp_total_gt_electric",
        coldest["gwp100a_total_kg"] > coldest["gwp100a_electric_kg"],
        f"total_kg={coldest['gwp100a_total_kg']}, el_kg={coldest['gwp100a_electric_kg']}",
    )


def test_emissions_default_vs_diesel_differ(client, record):
    """Mixed e-bus emissions must differ from default (full-electric) when diesel heating is active."""
    token = get_auth_token(client)
    if not token:
        record("emissions_diff_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)
    ya_default = _find_yearly_analysis_by_heating_type(client, headers, "default")
    ya_diesel = _find_yearly_analysis_by_heating_type(client, headers, "diesel")

    if ya_default is None or ya_diesel is None:
        record("emissions_diff_skip", True, "Need both default and diesel yearly analyses (skipped)")
        return

    resp_def = client.get(
        f"{API_BASE}/{ya_default['id']}/emissions",
        params={"bus_length_m": 12},
        headers=headers,
    )
    resp_dsl = client.get(
        f"{API_BASE}/{ya_diesel['id']}/emissions",
        params={"bus_length_m": 12},
        headers=headers,
    )
    if resp_def.status_code != 200 or resp_dsl.status_code != 200:
        record("emissions_diff_fetch_failed", False, "Could not fetch both emissions")
        return

    def_gwp = resp_def.json().get("ebus", {}).get("gwp100a", {}).get("total", 0)
    dsl_gwp = resp_dsl.json().get("ebus", {}).get("gwp100a", {}).get("total", 0)
    record(
        "emissions_default_vs_diesel_differ",
        abs(def_gwp - dsl_gwp) > 0.01,
        f"default_gwp={def_gwp}, diesel_gwp={dsl_gwp}",
    )

    def_el = resp_def.json().get("ebus", {}).get("gwp100a", {}).get("electric", 0)
    dsl_el = resp_dsl.json().get("ebus", {}).get("gwp100a", {}).get("electric", 0)
    record(
        "emissions_diesel_less_electric",
        dsl_el <= def_el,
        f"default_electric={def_el}, diesel_electric={dsl_el}",
    )


# ------------------------------------------------------------------
# Integration: full CRUD workflow
# ------------------------------------------------------------------

def test_yearly_analysis_crud_workflow(client, record):
    token = get_auth_token(client)
    if not token:
        record("workflow_auth_failed", False, "Could not get auth token")
        return

    headers = get_auth_headers(token)

    # 1. Create
    create_resp = client.post(
        f"{API_BASE}/",
        json={"name": "Workflow Analysis", "features": {"step": 1}},
        headers=headers,
    )
    record("workflow_create", create_resp.status_code == 200, f"status={create_resp.status_code}")
    if create_resp.status_code != 200:
        return
    analysis_id = create_resp.json()["id"]

    # 2. Read
    read_resp = client.get(f"{API_BASE}/{analysis_id}", headers=headers)
    record("workflow_read", read_resp.status_code == 200, f"status={read_resp.status_code}")

    # 3. List
    list_resp = client.get(f"{API_BASE}/", headers=headers)
    record("workflow_list", list_resp.status_code == 200, f"status={list_resp.status_code}")

    # 4. Update
    update_resp = client.patch(
        f"{API_BASE}/{analysis_id}",
        json={"name": "Workflow Analysis", "features": {"step": 2}},
        headers=headers,
    )
    record("workflow_update", update_resp.status_code == 200, f"status={update_resp.status_code}")
    if update_resp.status_code == 200:
        record("workflow_update_features", update_resp.json().get("features") == {"step": 2}, f"features={update_resp.json().get('features')}")

    # 5. Delete
    delete_resp = client.delete(f"{API_BASE}/{analysis_id}", headers=headers)
    record("workflow_delete", delete_resp.status_code == 200, f"status={delete_resp.status_code}")

    # 6. Verify deletion
    verify_resp = client.get(f"{API_BASE}/{analysis_id}", headers=headers)
    record("workflow_verify_deletion", verify_resp.status_code == 404, f"status={verify_resp.status_code}")
