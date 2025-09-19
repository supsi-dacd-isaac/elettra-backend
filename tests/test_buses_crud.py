"""Pytest-based buses CRUD tests.
Run with: pytest -k buses
Generates a human-readable report in tests/reports/ via report_collector fixture.
"""

import os
import pytest
from fastapi.testclient import TestClient

__report_module__ = "buses_crud"

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


def ensure_bus_model(client: TestClient, token: str) -> str:
    hdrs = auth_headers(token)
    # Always create a fresh model so we can safely delete it later
    r = client.post(
        f"{API_BASE}/bus-models/",
        json={"name": "Test Model for Buses", "specs": {}},
        headers=hdrs,
    )
    assert r.status_code == 200, f"create model failed: {r.text}"
    return r.json()["id"]


@pytest.fixture(autouse=True)
def cleanup_buses(client):
    yield
    try:
        token = get_auth_token(client)
        if not token:
            return
        hdrs = auth_headers(token)
        r = client.get(f"{API_BASE}/buses/", headers=hdrs)
        if r.status_code == 200:
            for b in r.json():
                if b.get("name", "").startswith("Test Bus") or b.get("name", "").startswith("Updated Bus"):
                    client.delete(f"{API_BASE}/buses/{b['id']}", headers=hdrs)
    except Exception:
        pass


def test_create_read_update_delete_bus(client: TestClient, record):
    token = get_auth_token(client)
    if not token:
        record("bus_auth_failed", False, "login failed")
        return
    hdrs = auth_headers(token)

    agency_id = os.getenv("TEST_AGENCY_ID")
    assert agency_id, "TEST_AGENCY_ID missing in test.env"
    model_id = ensure_bus_model(client, token)

    # Create
    r = client.post(f"{API_BASE}/buses/", json={
        "agency_id": agency_id,
        "name": "Test Bus 1",
        "specs": {"range_km": 250},
        "bus_model_id": model_id
    }, headers=hdrs)
    record("bus_create", r.status_code == 200, f"status={r.status_code}")
    bus_id = r.json()["id"]

    # List
    r = client.get(f"{API_BASE}/buses/", headers=hdrs)
    record("bus_list", r.status_code == 200 and any(b["id"] == bus_id for b in r.json()), f"status={r.status_code}")

    # Get
    r = client.get(f"{API_BASE}/buses/{bus_id}", headers=hdrs)
    record("bus_get", r.status_code == 200 and r.json()["id"] == bus_id, f"status={r.status_code}")

    # Update
    r = client.put(f"{API_BASE}/buses/{bus_id}", json={"name": "Updated Bus 1"}, headers=hdrs)
    ok = r.status_code == 200 and r.json()["name"] == "Updated Bus 1"
    record("bus_update", ok, f"status={r.status_code}")

    # Delete bus
    r = client.delete(f"{API_BASE}/buses/{bus_id}", headers=hdrs)
    record("bus_delete", r.status_code == 200, f"status={r.status_code}")
    # Verify bus deleted
    r = client.get(f"{API_BASE}/buses/{bus_id}", headers=hdrs)
    record("bus_verify_deleted", r.status_code == 404, f"status={r.status_code}")
    # Also delete the model that was created for this test
    r = client.delete(f"{API_BASE}/bus-models/{model_id}", headers=hdrs)
    record("bus_cleanup_model", r.status_code == 200, f"status={r.status_code}")


def test_bus_not_found(client: TestClient, record):
    token = get_auth_token(client)
    if not token:
        record("bus_nf_auth_failed", False, "login failed")
        return
    hdrs = auth_headers(token)
    invalid_id = "00000000-0000-0000-0000-000000000999"
    r = client.get(f"{API_BASE}/buses/{invalid_id}", headers=hdrs)
    record("bus_get_404", r.status_code == 404, f"status={r.status_code}")
    r = client.put(f"{API_BASE}/buses/{invalid_id}", json={"name": "X"}, headers=hdrs)
    record("bus_put_404", r.status_code == 404, f"status={r.status_code}")
    r = client.delete(f"{API_BASE}/buses/{invalid_id}", headers=hdrs)
    record("bus_del_404", r.status_code == 404, f"status={r.status_code}")


