"""Pytest-based buses_models CRUD tests.
Run with: pytest -k bus_models
Generates a human-readable report in tests/reports/ via report_collector fixture.
"""

import os
import pytest
from fastapi.testclient import TestClient

__report_module__ = "bus_models_crud"

API_BASE = "/api/v1/agency"
AUTH_BASE = "/auth"


def get_auth_token(client: TestClient) -> str | None:
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


def create_bus_model(client: TestClient, token: str, name: str = "Test Model") -> str | None:
    payload = {
        "name": name,
        "manufacturer": "Test Maker",
        "specs": {"battery_kwh": 300}
    }
    r = client.post(f"{API_BASE}/bus-models/", json=payload, headers=auth_headers(token))
    if r.status_code == 200:
        return r.json()["id"]
    return None


@pytest.fixture(autouse=True)
def cleanup_models(client):
    yield
    try:
        token = get_auth_token(client)
        if not token:
            return
        hdrs = auth_headers(token)
        r = client.get(f"{API_BASE}/bus-models/", headers=hdrs)
        if r.status_code == 200:
            for m in r.json():
                if m.get("name", "").startswith("Test Model") or m.get("name", "").startswith("Updated Model"):
                    client.delete(f"{API_BASE}/bus-models/{m['id']}", headers=hdrs)
    except Exception:
        pass


def test_create_read_update_delete_bus_model(client: TestClient, record):
    token = get_auth_token(client)
    if not token:
        record("bm_auth_failed", False, "login failed")
        return
    hdrs = auth_headers(token)

    # Create
    r = client.post(f"{API_BASE}/bus-models/", json={
        "name": "Test Model 1",
        "manufacturer": "Maker",
        "specs": {"battery_kwh": 250}
    }, headers=hdrs)
    record("bm_create", r.status_code == 200, f"status={r.status_code}")
    model = r.json()
    model_id = model["id"]

    # Read list
    r = client.get(f"{API_BASE}/bus-models/", headers=hdrs)
    record("bm_list", r.status_code == 200 and any(m["id"] == model_id for m in r.json()), f"status={r.status_code}")

    # Read by id
    r = client.get(f"{API_BASE}/bus-models/{model_id}", headers=hdrs)
    record("bm_get", r.status_code == 200 and r.json()["id"] == model_id, f"status={r.status_code}")

    # Update
    r = client.put(f"{API_BASE}/bus-models/{model_id}", json={"name": "Updated Model 1"}, headers=hdrs)
    ok = r.status_code == 200 and r.json()["name"] == "Updated Model 1"
    record("bm_update", ok, f"status={r.status_code}")

    # Delete
    r = client.delete(f"{API_BASE}/bus-models/{model_id}", headers=hdrs)
    record("bm_delete", r.status_code == 200, f"status={r.status_code}")
    # Verify deleted
    r = client.get(f"{API_BASE}/bus-models/{model_id}", headers=hdrs)
    record("bm_verify_deleted", r.status_code == 404, f"status={r.status_code}")


def test_bus_model_not_found(client: TestClient, record):
    token = get_auth_token(client)
    if not token:
        record("bm_nf_auth_failed", False, "login failed")
        return
    hdrs = auth_headers(token)

    invalid_id = "00000000-0000-0000-0000-000000000999"
    r = client.get(f"{API_BASE}/bus-models/{invalid_id}", headers=hdrs)
    record("bm_get_404", r.status_code == 404, f"status={r.status_code}")
    r = client.put(f"{API_BASE}/bus-models/{invalid_id}", json={"name": "X"}, headers=hdrs)
    record("bm_put_404", r.status_code == 404, f"status={r.status_code}")
    r = client.delete(f"{API_BASE}/bus-models/{invalid_id}", headers=hdrs)
    record("bm_del_404", r.status_code == 404, f"status={r.status_code}")


