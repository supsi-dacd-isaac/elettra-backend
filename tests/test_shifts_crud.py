"""CRUD tests for Shifts with structure.

Run with: pytest -k shifts_crud
Uses report_collector via the shared record fixture.
"""

import os
import uuid
import pytest
from fastapi.testclient import TestClient

__report_module__ = "shifts_crud"

API_BASE = "/api/v1/agency"
AUTH_BASE = "/auth"


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


def ensure_bus(client: TestClient, token: str) -> str:
    hdrs = auth_headers(token)
    user_id = current_user_id(client, token)
    # Create a simple bus; model is optional
    r = client.post(
        f"{API_BASE}/buses/",
        json={
            "user_id": user_id,
            "name": f"Test Bus for Shifts {uuid.uuid4()}",
            "specs": {},
            "bus_model_id": None,
        },
        headers=hdrs,
    )
    assert r.status_code == 200, f"create bus failed: {r.text}"
    return r.json()["id"]


def any_trip_ids(client: TestClient, token: str, limit: int = 3) -> list[str]:
    # Reuse route from env and fetch trips by route
    route_id = os.getenv("TEST_ROUTE_ID")
    assert route_id, "TEST_ROUTE_ID missing in test.env"
    r = client.get(f"/api/v1/gtfs/gtfs-trips/by-route/{route_id}", headers=auth_headers(token))
    assert r.status_code == 200, f"fetch trips failed: {r.text}"
    js = r.json()
    assert isinstance(js, list) and len(js) >= 2, "expected at least 2 trips to build a structure"
    return [t["id"] for t in js[:limit]]


@pytest.mark.skipif(
    not (os.getenv("TEST_ROUTE_ID") and (os.getenv("TEST_API_TOKEN") or (os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD")))),
    reason="Requires TEST_ROUTE_ID and auth env vars",
)
def test_shifts_crud_flow(client: TestClient, record):
    token = get_auth_token(client)
    if not token:
        record("shift_auth_failed", False, "login failed")
        return
    hdrs = auth_headers(token)

    # Seed: bus and trips
    bus_id = ensure_bus(client, token)
    trips = any_trip_ids(client, token, limit=3)

    # Create shift with 3 trips
    r = client.post(
        f"{API_BASE}/shifts/",
        json={"name": "Test Shift", "bus_id": bus_id, "trip_ids": trips},
        headers=hdrs,
    )
    record("shift_create_200", r.status_code == 200, f"status={r.status_code} body={r.text}")
    if r.status_code != 200:
        return
    created = r.json()
    shift_id = created["id"]
    record("shift_create_has_structure", len(created.get("structure", [])) == len(trips))

    # Read shift
    r = client.get(f"{API_BASE}/shifts/{shift_id}", headers=hdrs)
    record("shift_get_200", r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        data = r.json()
        record("shift_get_structure_len", len(data.get("structure", [])) == len(trips))

    # List shifts, filter by bus_id
    r = client.get(f"{API_BASE}/shifts/?bus_id={bus_id}", headers=hdrs)
    ok = r.status_code == 200 and any(s["id"] == shift_id for s in r.json())
    record("shift_list_filter_bus", ok, f"status={r.status_code}")

    # Update: replace structure with first 2 trips
    new_trips = trips[:2]
    r = client.put(
        f"{API_BASE}/shifts/{shift_id}",
        json={"name": "Updated Shift", "trip_ids": new_trips},
        headers=hdrs,
    )
    ok = r.status_code == 200 and r.json()["name"] == "Updated Shift" and len(r.json().get("structure", [])) == 2
    record("shift_update_replace_structure", ok, f"status={r.status_code} body={r.text}")

    # Delete
    r = client.delete(f"{API_BASE}/shifts/{shift_id}", headers=hdrs)
    record("shift_delete_200", r.status_code == 200, f"status={r.status_code}")
    # Verify 404 after delete
    r = client.get(f"{API_BASE}/shifts/{shift_id}", headers=hdrs)
    record("shift_verify_deleted_404", r.status_code == 404, f"status={r.status_code}")

    # Cleanup bus
    client.delete(f"{API_BASE}/buses/{bus_id}", headers=hdrs)


