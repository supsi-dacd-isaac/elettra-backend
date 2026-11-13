"""Tests for the optimization-run `name` field (request validation,
persistence, list/detail responses, and legacy `input_params.name`
backward-compat fallback).

Run with: ./run_tests.sh tests/test_optimization_run_name.py
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import get_cached_settings
from app.schemas.requests import OptimizationRequest, OptimizationRunUpdate

__report_module__ = "optimization_run_name"

SIM_BASE = "/api/v1/simulation"
AUTH_BASE = "/auth"

settings = get_cached_settings()


def _dsn_for_asyncpg() -> str:
    dsn = settings.database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return dsn


def _get_auth_token(client: TestClient) -> str | None:
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


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _insert_legacy_run_async(
    user_id: str, db_name: str | None, input_params_name: str | None
) -> str:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        ip: dict = {"shift_ids": [], "min_soc": 0.4}
        if input_params_name is not None:
            ip["name"] = input_params_name
        run_id = await conn.fetchval(
            """
            INSERT INTO optimization_runs (user_id, mode, status, input_params, name)
            VALUES ($1::uuid, 'battery_only', 'completed', $2::jsonb, $3)
            RETURNING id
            """,
            uuid.UUID(user_id),
            json.dumps(ip),
            db_name,
        )
        return str(run_id)
    finally:
        await conn.close()


async def _delete_run_async(run_id: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        await conn.execute(
            "DELETE FROM optimization_runs WHERE id = $1::uuid", uuid.UUID(run_id)
        )
    finally:
        await conn.close()


def _insert_legacy_run(
    user_id: str, db_name: str | None, input_params_name: str | None
) -> str:
    return asyncio.run(_insert_legacy_run_async(user_id, db_name, input_params_name))


def _delete_run(run_id: str) -> None:
    asyncio.run(_delete_run_async(run_id))


async def _insert_paired_runs_async(user_id: str) -> tuple[str, str]:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        root_id = await conn.fetchval(
            """
            INSERT INTO optimization_runs (user_id, mode, status, input_params, name)
            VALUES ($1::uuid, 'battery_only', 'completed', $2::jsonb, 'Original family')
            RETURNING id
            """,
            uuid.UUID(user_id),
            json.dumps({
                "shift_ids": [],
                "consumption_basis": "mean",
                "paired_basis_role": "primary",
            }),
        )
        comparison_id = await conn.fetchval(
            """
            INSERT INTO optimization_runs (user_id, mode, status, input_params, name)
            VALUES ($1::uuid, 'battery_only', 'completed', $2::jsonb, 'Original family · Q95')
            RETURNING id
            """,
            uuid.UUID(user_id),
            json.dumps({
                "shift_ids": [],
                "consumption_basis": "aggregate_q95",
                "paired_basis_role": "comparison",
                "basis_source_run_id": str(root_id),
            }),
        )
        basis_ids = {"mean": str(root_id), "aggregate_q95": str(comparison_id)}
        await conn.execute(
            """
            UPDATE optimization_runs
            SET input_params = input_params || $2::jsonb
            WHERE id = ANY($1::uuid[])
            """,
            [root_id, comparison_id],
            json.dumps({"basis_run_ids": basis_ids}),
        )
        return str(root_id), str(comparison_id)
    finally:
        await conn.close()


def _insert_paired_runs(user_id: str) -> tuple[str, str]:
    return asyncio.run(_insert_paired_runs_async(user_id))


@pytest.fixture
def auth_token(client: TestClient):
    token = _get_auth_token(client)
    if not token:
        pytest.skip(
            "Auth credentials missing (TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD or "
            "TEST_API_TOKEN)"
        )
    return token


@pytest.fixture
def current_user_id(client: TestClient, auth_token: str) -> str:
    r = client.get(f"{AUTH_BASE}/me", headers=_auth_headers(auth_token))
    assert r.status_code == 200, f"fetch /me failed: {r.text}"
    return r.json()["id"]


# Minimal payload that passes basic shape but will fail later (fake UUIDs).
# All these tests aim at the request-validation step (Pydantic 422), so the
# downstream 404 paths are not exercised.
def _base_payload(**overrides) -> dict:
    body = {
        "mode": "battery_only",
        "shift_ids": [str(uuid.uuid4())],
        "bus_model_id": str(uuid.uuid4()),
        "prediction_params": {
            "model_name": "test",
            "external_temp_celsius": 15.0,
        },
        "charging_stations": [
            {
                "stop_id": str(uuid.uuid4()),
                "num_slots": 2,
                "max_total_power_kw": 450,
            }
        ],
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Pure Pydantic-level tests (write path: validation + trimming + dump)
# ---------------------------------------------------------------------------

def test_pydantic_name_required(record):
    """OptimizationRequest must reject payloads without `name`."""
    body = _base_payload()
    body.pop("name", None)
    raised = False
    msg = ""
    try:
        OptimizationRequest.model_validate(body)
    except ValidationError as e:
        raised = True
        msg = "; ".join(
            f"{err['loc']}:{err['msg']}" for err in e.errors() if err["loc"] == ("name",)
        )
    record("pydantic_name_required", raised and "Field required" in msg, msg)


def test_pydantic_name_whitespace_only_rejected(record):
    """Whitespace-only names are rejected with a clear error."""
    body = _base_payload(name="   ")
    raised = False
    msg = ""
    try:
        OptimizationRequest.model_validate(body)
    except ValidationError as e:
        raised = True
        msg = "; ".join(err["msg"] for err in e.errors() if err["loc"] == ("name",))
    record(
        "pydantic_name_whitespace_rejected",
        raised and "must not be empty" in msg,
        msg,
    )


def test_pydantic_name_trimmed(record):
    """Leading/trailing whitespace is stripped on validation."""
    body = _base_payload(name="   My feasibility evaluation   ")
    req = OptimizationRequest.model_validate(body)
    record(
        "pydantic_name_trimmed",
        req.name == "My feasibility evaluation",
        f"name={req.name!r}",
    )


def test_pydantic_name_excluded_from_input_params_dump(record):
    """The router dumps `model_dump(exclude={'name'})` for input_params."""
    body = _base_payload(name="My feasibility evaluation")
    req = OptimizationRequest.model_validate(body)
    dumped = req.model_dump(mode="json", exclude={"name"})
    record(
        "pydantic_name_excluded_from_dump",
        "name" not in dumped and "mode" in dumped,
        f"name_in_dump={'name' in dumped}, keys_sample={sorted(dumped)[:3]}",
    )


def test_pydantic_update_name_is_trimmed_and_validated(record):
    update = OptimizationRunUpdate.model_validate({"name": "  Renamed evaluation  "})
    whitespace_rejected = False
    try:
        OptimizationRunUpdate.model_validate({"name": "   "})
    except ValidationError:
        whitespace_rejected = True
    record(
        "pydantic_update_name",
        update.name == "Renamed evaluation" and whitespace_rejected,
        f"name={update.name!r}, whitespace_rejected={whitespace_rejected}",
    )


# ---------------------------------------------------------------------------
# HTTP request-validation tests (use the real /optimization-runs/ endpoint)
# ---------------------------------------------------------------------------

def test_post_without_name_returns_422(client: TestClient, auth_token, record):
    body = _base_payload()
    body.pop("name", None)
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json=body,
        headers=_auth_headers(auth_token),
    )
    record(
        "post_without_name_422",
        r.status_code == 422,
        f"status={r.status_code} body={r.text[:200]}",
    )


def test_post_with_whitespace_name_returns_422(client: TestClient, auth_token, record):
    body = _base_payload(name="   ")
    r = client.post(
        f"{SIM_BASE}/optimization-runs/",
        json=body,
        headers=_auth_headers(auth_token),
    )
    record(
        "post_whitespace_name_422",
        r.status_code == 422,
        f"status={r.status_code} body={r.text[:200]}",
    )


def test_patch_renames_single_owned_run(
    client, auth_token, current_user_id, record
):
    run_id = _insert_legacy_run(current_user_id, "Original", None)
    try:
        response = client.patch(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            json={"name": "  Renamed evaluation  "},
            headers=_auth_headers(auth_token),
        )
        detail = client.get(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            headers=_auth_headers(auth_token),
        )
        ok = (
            response.status_code == 200
            and response.json().get("name") == "Renamed evaluation"
            and detail.status_code == 200
            and detail.json().get("name") == "Renamed evaluation"
        )
        record(
            "patch_single_run_name",
            ok,
            f"patch={response.status_code}, detail={detail.status_code}, "
            f"name={detail.json().get('name') if detail.status_code == 200 else None!r}",
        )
    finally:
        _delete_run(run_id)


def test_patch_rejects_whitespace_without_changing_name(
    client, auth_token, current_user_id, record
):
    run_id = _insert_legacy_run(current_user_id, "Original", None)
    try:
        response = client.patch(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            json={"name": "   "},
            headers=_auth_headers(auth_token),
        )
        detail = client.get(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            headers=_auth_headers(auth_token),
        )
        ok = (
            response.status_code == 422
            and detail.status_code == 200
            and detail.json().get("name") == "Original"
        )
        record(
            "patch_whitespace_name",
            ok,
            f"patch={response.status_code}, name="
            f"{detail.json().get('name') if detail.status_code == 200 else None!r}",
        )
    finally:
        _delete_run(run_id)


def test_patch_renames_mean_q95_family_atomically(
    client, auth_token, current_user_id, record
):
    root_id, comparison_id = _insert_paired_runs(current_user_id)
    try:
        response = client.patch(
            f"{SIM_BASE}/optimization-runs/{root_id}",
            json={"name": "Winter service"},
            headers=_auth_headers(auth_token),
        )
        root = client.get(
            f"{SIM_BASE}/optimization-runs/{root_id}",
            headers=_auth_headers(auth_token),
        )
        comparison = client.get(
            f"{SIM_BASE}/optimization-runs/{comparison_id}",
            headers=_auth_headers(auth_token),
        )
        ok = (
            response.status_code == 200
            and root.status_code == 200
            and comparison.status_code == 200
            and root.json().get("name") == "Winter service"
            and comparison.json().get("name") == "Winter service · Q95"
        )
        record(
            "patch_paired_run_names",
            ok,
            f"patch={response.status_code}, root="
            f"{root.json().get('name') if root.status_code == 200 else None!r}, comparison="
            f"{comparison.json().get('name') if comparison.status_code == 200 else None!r}",
        )
    finally:
        _delete_run(comparison_id)
        _delete_run(root_id)


# ---------------------------------------------------------------------------
# Read-time fallback / DB round-trip (no real optimization needed)
# ---------------------------------------------------------------------------

def test_get_returns_db_name_when_set(client, auth_token, current_user_id, record):
    """When `optimization_runs.name` is set, the response exposes it trimmed."""
    run_id = _insert_legacy_run(current_user_id, "  My feasibility evaluation  ", None)
    try:
        r = client.get(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            headers=_auth_headers(auth_token),
        )
        ok = r.status_code == 200 and r.json().get("name") == "My feasibility evaluation"
        record(
            "get_returns_db_name",
            ok,
            f"status={r.status_code} name={r.json().get('name') if r.status_code == 200 else None!r}",
        )
    finally:
        _delete_run(run_id)


def test_get_returns_null_when_db_and_legacy_both_missing(
    client, auth_token, current_user_id, record
):
    run_id = _insert_legacy_run(current_user_id, None, None)
    try:
        r = client.get(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            headers=_auth_headers(auth_token),
        )
        ok = r.status_code == 200 and r.json().get("name") is None
        record(
            "get_returns_null_when_missing",
            ok,
            f"status={r.status_code} name={r.json().get('name') if r.status_code == 200 else None!r}",
        )
    finally:
        _delete_run(run_id)


def test_get_falls_back_to_legacy_input_params_name(
    client, auth_token, current_user_id, record
):
    run_id = _insert_legacy_run(current_user_id, None, "  Legacy IP Name  ")
    try:
        r = client.get(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            headers=_auth_headers(auth_token),
        )
        ok = r.status_code == 200 and r.json().get("name") == "Legacy IP Name"
        record(
            "get_legacy_input_params_fallback",
            ok,
            f"status={r.status_code} name={r.json().get('name') if r.status_code == 200 else None!r}",
        )
    finally:
        _delete_run(run_id)


def test_db_column_takes_precedence_over_legacy_input_params(
    client, auth_token, current_user_id, record
):
    run_id = _insert_legacy_run(current_user_id, "DB Name", "Legacy IP Name")
    try:
        r = client.get(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            headers=_auth_headers(auth_token),
        )
        ok = r.status_code == 200 and r.json().get("name") == "DB Name"
        record(
            "db_name_precedence",
            ok,
            f"status={r.status_code} name={r.json().get('name') if r.status_code == 200 else None!r}",
        )
    finally:
        _delete_run(run_id)


def test_list_endpoint_returns_top_level_name(
    client, auth_token, current_user_id, record
):
    run_id = _insert_legacy_run(current_user_id, "  In List  ", None)
    try:
        # The list endpoint is paginated and ordered by created_at DESC,
        # so the brand-new run lands on the first page.
        r = client.get(
            f"{SIM_BASE}/optimization-runs/?skip=0&limit=100",
            headers=_auth_headers(auth_token),
        )
        ok = r.status_code == 200
        body = r.json() if r.status_code == 200 else {}
        items = body.get("items", []) if isinstance(body, dict) else []
        names_for_run = [item.get("name") for item in items if item.get("id") == run_id]
        ok = ok and names_for_run == ["In List"]
        record(
            "list_top_level_name",
            ok,
            f"status={r.status_code} match={names_for_run}",
        )
    finally:
        _delete_run(run_id)


# ---------------------------------------------------------------------------
# OpenAPI contract
# ---------------------------------------------------------------------------

def test_openapi_includes_name_in_request_and_response(client: TestClient, record):
    r = client.get("/openapi.json")
    if r.status_code != 200:
        record("openapi_fetched", False, f"status={r.status_code}")
        return
    schemas = r.json().get("components", {}).get("schemas", {})
    request_schema = schemas.get("OptimizationRequest", {})
    read_schema = schemas.get("OptimizationRunsRead", {})

    name_required = "name" in request_schema.get("required", [])
    name_in_read_props = "name" in read_schema.get("properties", {})
    request_examples = list(
        request_schema.get("properties", {}).get("name", {}).get("examples") or []
    )

    ok = name_required and name_in_read_props and len(request_examples) > 0
    record(
        "openapi_name_field",
        ok,
        f"request_required={name_required}, read_has_name={name_in_read_props}, "
        f"req_name_examples={request_examples}",
    )
