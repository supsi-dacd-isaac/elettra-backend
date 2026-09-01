"""Regression tests for deleting optimization runs.

Run with: ./run_tests.sh tests/test_optimization_run_delete.py
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_cached_settings

__report_module__ = "optimization_run_delete"

SIM_BASE = "/api/v1/simulation"
AUTH_BASE = "/auth"
TEST_AGENCY_ID = os.getenv("TEST_AGENCY_ID")

settings = get_cached_settings()


def _dsn_for_asyncpg() -> str:
    dsn = settings.database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return dsn


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _get_auth_token(client: TestClient) -> str | None:
    token = os.getenv("TEST_API_TOKEN")
    if token:
        return token

    email = os.getenv("TEST_LOGIN_EMAIL")
    password = os.getenv("TEST_LOGIN_PASSWORD")
    if not email or not password:
        return None

    response = client.post(f"{AUTH_BASE}/login", json={"email": email, "password": password})
    if response.status_code != 200:
        return None
    return response.json().get("access_token")


def _current_user_id(client: TestClient, token: str) -> str:
    response = client.get(f"{AUTH_BASE}/me", headers=_auth_headers(token))
    assert response.status_code == 200, f"fetch /me failed: {response.text}"
    return response.json()["id"]


def _register_temp_user(client: TestClient) -> dict:
    if not TEST_AGENCY_ID:
        pytest.skip("TEST_AGENCY_ID environment variable is required")

    email = f"tmp_opt_delete_{uuid.uuid4().hex[:10]}@example.com"
    password = f"Tmp-{uuid.uuid4().hex}aA1!"
    response = client.post(
        f"{AUTH_BASE}/register",
        json={
            "company_id": TEST_AGENCY_ID,
            "email": email,
            "full_name": "Optimization Delete Scope Test User",
            "password": password,
            "role": "viewer",
        },
    )
    assert response.status_code == 200, (
        f"temp user register failed: status={response.status_code} body={response.text}"
    )
    data = response.json()
    return {"id": data["id"], "email": email, "password": password}


def _login_as(client: TestClient, email: str, password: str) -> str:
    response = client.post(f"{AUTH_BASE}/login", json={"email": email, "password": password})
    assert response.status_code == 200, (
        f"temp user login failed: status={response.status_code} body={response.text}"
    )
    return response.json()["access_token"]


async def _insert_optimization_run_async(user_id: str, name: str) -> str:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        run_id = await conn.fetchval(
            """
            INSERT INTO optimization_runs (user_id, mode, status, input_params, results, name)
            VALUES ($1::uuid, 'battery_only', 'completed', $2::jsonb, $3::jsonb, $4)
            RETURNING id
            """,
            uuid.UUID(user_id),
            json.dumps({"shift_ids": [], "test_marker": "optimization_run_delete"}),
            json.dumps({
                "electrification_feasible": True,
                "solver_status": "test",
                "objective_value": 1.0,
            }),
            name,
        )
        return str(run_id)
    finally:
        await conn.close()


async def _insert_yearly_analysis_async(optimization_run_id: str, name: str) -> str:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        yearly_analysis_id = await conn.fetchval(
            """
            INSERT INTO yearly_analysis (optimization_run_id, name, features)
            VALUES ($1::uuid, $2, $3::jsonb)
            RETURNING id
            """,
            uuid.UUID(optimization_run_id),
            name,
            json.dumps({"test_marker": "optimization_run_delete"}),
        )
        return str(yearly_analysis_id)
    finally:
        await conn.close()


async def _optimization_run_exists_async(run_id: str) -> bool:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        value = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM optimization_runs WHERE id = $1::uuid)",
            uuid.UUID(run_id),
        )
        return bool(value)
    finally:
        await conn.close()


async def _yearly_analysis_optimization_run_id_async(yearly_analysis_id: str) -> str | None:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        value = await conn.fetchval(
            "SELECT optimization_run_id FROM yearly_analysis WHERE id = $1::uuid",
            uuid.UUID(yearly_analysis_id),
        )
        return str(value) if value is not None else None
    finally:
        await conn.close()


async def _cleanup_async(
    *,
    run_ids: list[str] | None = None,
    yearly_analysis_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
) -> None:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        if yearly_analysis_ids:
            await conn.execute(
                "DELETE FROM yearly_analysis WHERE id = ANY($1::uuid[])",
                [uuid.UUID(v) for v in yearly_analysis_ids],
            )
        if run_ids:
            await conn.execute(
                "DELETE FROM optimization_runs WHERE id = ANY($1::uuid[])",
                [uuid.UUID(v) for v in run_ids],
            )
        if user_ids:
            await conn.execute(
                "DELETE FROM users WHERE id = ANY($1::uuid[])",
                [uuid.UUID(v) for v in user_ids],
            )
    finally:
        await conn.close()


def _insert_optimization_run(user_id: str, name: str) -> str:
    return asyncio.run(_insert_optimization_run_async(user_id, name))


def _insert_yearly_analysis(optimization_run_id: str, name: str) -> str:
    return asyncio.run(_insert_yearly_analysis_async(optimization_run_id, name))


def _optimization_run_exists(run_id: str) -> bool:
    return asyncio.run(_optimization_run_exists_async(run_id))


def _yearly_analysis_optimization_run_id(yearly_analysis_id: str) -> str | None:
    return asyncio.run(_yearly_analysis_optimization_run_id_async(yearly_analysis_id))


def _cleanup(
    *,
    run_ids: list[str] | None = None,
    yearly_analysis_ids: list[str] | None = None,
    user_ids: list[str] | None = None,
) -> None:
    asyncio.run(
        _cleanup_async(
            run_ids=run_ids,
            yearly_analysis_ids=yearly_analysis_ids,
            user_ids=user_ids,
        )
    )


@pytest.fixture
def auth_token(client: TestClient):
    token = _get_auth_token(client)
    if not token:
        pytest.skip(
            "Auth credentials missing (TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD or TEST_API_TOKEN)"
        )
    return token


@pytest.fixture
def current_user_id(client: TestClient, auth_token: str) -> str:
    return _current_user_id(client, auth_token)


def test_delete_optimization_run_hard_deletes_from_api_and_db(
    client: TestClient, auth_token: str, current_user_id: str, record
):
    run_id = _insert_optimization_run(current_user_id, "Disposable delete test run")
    try:
        before_detail = client.get(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            headers=_auth_headers(auth_token),
        )
        before_list = client.get(
            f"{SIM_BASE}/optimization-runs/?skip=0&limit=100",
            headers=_auth_headers(auth_token),
        )
        before_items = before_list.json().get("items", []) if before_list.status_code == 200 else []
        before_db_exists = _optimization_run_exists(run_id)

        delete_response = client.delete(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            headers=_auth_headers(auth_token),
        )

        after_detail = client.get(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            headers=_auth_headers(auth_token),
        )
        after_list = client.get(
            f"{SIM_BASE}/optimization-runs/?skip=0&limit=100",
            headers=_auth_headers(auth_token),
        )
        after_items = after_list.json().get("items", []) if after_list.status_code == 200 else []
        after_db_exists = _optimization_run_exists(run_id)

        ok = (
            before_detail.status_code == 200
            and before_list.status_code == 200
            and any(item.get("id") == run_id for item in before_items)
            and before_db_exists
            and delete_response.status_code == 200
            and delete_response.json() == {"deleted": True, "id": run_id}
            and after_detail.status_code == 404
            and after_list.status_code == 200
            and not any(item.get("id") == run_id for item in after_items)
            and not after_db_exists
        )
        record(
            "delete_hard_deletes_from_api_and_db",
            ok,
            (
                f"before_detail={before_detail.status_code}, "
                f"before_list={before_list.status_code}, before_db={before_db_exists}, "
                f"delete={delete_response.status_code}:{delete_response.text}, "
                f"after_detail={after_detail.status_code}, "
                f"after_list={after_list.status_code}, after_db={after_db_exists}"
            ),
        )
    finally:
        _cleanup(run_ids=[run_id])


def test_delete_optimization_run_non_existing_returns_404(
    client: TestClient, auth_token: str, record
):
    missing_id = str(uuid.uuid4())
    response = client.delete(
        f"{SIM_BASE}/optimization-runs/{missing_id}",
        headers=_auth_headers(auth_token),
    )
    record(
        "delete_non_existing_404",
        response.status_code == 404,
        f"status={response.status_code} body={response.text[:200]}",
    )


def test_delete_optimization_run_owned_by_other_user_returns_404(
    client: TestClient, auth_token: str, record
):
    temp_user = _register_temp_user(client)
    temp_token = _login_as(client, temp_user["email"], temp_user["password"])
    temp_user_id = _current_user_id(client, temp_token)
    run_id = _insert_optimization_run(temp_user_id, "Disposable other-user run")
    try:
        response = client.delete(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            headers=_auth_headers(auth_token),
        )
        still_exists = _optimization_run_exists(run_id)
        record(
            "delete_other_user_404_and_preserves_row",
            response.status_code == 404 and still_exists,
            f"status={response.status_code} body={response.text[:200]} db_exists={still_exists}",
        )
    finally:
        _cleanup(run_ids=[run_id], user_ids=[temp_user["id"]])


def test_delete_optimization_run_nulls_linked_yearly_analysis(
    client: TestClient, auth_token: str, current_user_id: str, record
):
    run_id = _insert_optimization_run(current_user_id, "Disposable yearly-analysis run")
    yearly_analysis_id = _insert_yearly_analysis(run_id, "Disposable linked analysis")
    try:
        before_link = _yearly_analysis_optimization_run_id(yearly_analysis_id)
        response = client.delete(
            f"{SIM_BASE}/optimization-runs/{run_id}",
            headers=_auth_headers(auth_token),
        )
        after_link = _yearly_analysis_optimization_run_id(yearly_analysis_id)
        run_exists = _optimization_run_exists(run_id)
        record(
            "delete_nulls_linked_yearly_analysis",
            before_link == run_id
            and response.status_code == 200
            and after_link is None
            and not run_exists,
            (
                f"before_link={before_link}, status={response.status_code}, "
                f"body={response.text[:200]}, after_link={after_link}, run_exists={run_exists}"
            ),
        )
    finally:
        _cleanup(run_ids=[run_id], yearly_analysis_ids=[yearly_analysis_id])


def test_openapi_includes_delete_optimization_run(client: TestClient, record):
    response = client.get("/openapi.json")
    paths = response.json().get("paths", {}) if response.status_code == 200 else {}
    operation = paths.get(f"{SIM_BASE}/optimization-runs/{{run_id}}", {}).get("delete")
    response_schema_ref = (
        operation.get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("$ref")
        if operation
        else None
    )
    record(
        "openapi_includes_delete_optimization_run",
        response.status_code == 200
        and operation is not None
        and response_schema_ref == "#/components/schemas/OptimizationDeleteResponse",
        f"status={response.status_code} has_delete={operation is not None} schema={response_schema_ref}",
    )
