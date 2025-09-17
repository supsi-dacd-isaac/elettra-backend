"""CRUD tests for GTFS trips and status filtering.

Run with: pytest -k trips_crud
Uses report_collector via the shared record fixture.
"""

import os
import uuid
import pytest
from fastapi.testclient import TestClient

__report_module__ = "gtfs_trips_crud"

API_BASE = "/api/v1/gtfs"
AUTH_BASE = "/auth"

TEST_ROUTE_ID = os.getenv("TEST_ROUTE_ID")
TEST_AGENCY_ID = os.getenv("TEST_AGENCY_ID")
TEST_LOGIN_EMAIL = os.getenv("TEST_LOGIN_EMAIL")
TEST_LOGIN_PASSWORD = os.getenv("TEST_LOGIN_PASSWORD")


def _get_token(client: TestClient) -> str | None:
    token = os.getenv("TEST_API_TOKEN")
    if token:
        return token
    if TEST_LOGIN_EMAIL and TEST_LOGIN_PASSWORD:
        r = client.post(f"{AUTH_BASE}/login", json={"email": TEST_LOGIN_EMAIL, "password": TEST_LOGIN_PASSWORD})
        if r.status_code == 200:
            return r.json().get("access_token")
    return None


def _auth_headers(client: TestClient) -> dict:
    token = _get_token(client)
    return {"Authorization": f"Bearer {token}"} if token else {}


def _existing_trip_on_route(client: TestClient, headers: dict) -> dict | None:
    r = client.get(f"{API_BASE}/gtfs-trips/by-route/{TEST_ROUTE_ID}", headers=headers)
    if r.status_code == 200 and isinstance(r.json(), list) and r.json():
        return r.json()[0]
    return None


def _make_trip_payload(client: TestClient, headers: dict, status: str = "other") -> dict | None:
    base = _existing_trip_on_route(client, headers)
    if not base:
        return None
    return {
        "route_id": TEST_ROUTE_ID,
        "service_id": base.get("service_id"),
        "gtfs_service_id": base.get("gtfs_service_id", "svc-test"),
        "trip_id": f"test-trip-{uuid.uuid4()}",
        "status": status,
    }


@pytest.mark.skipif(
    not (TEST_ROUTE_ID and (os.getenv("TEST_API_TOKEN") or (TEST_LOGIN_EMAIL and TEST_LOGIN_PASSWORD))),
    reason="Requires TEST_ROUTE_ID and auth env vars",
)
def test_trip_crud_and_status_filter(client: TestClient, record):
    headers = _auth_headers(client)
    if not headers:
        record("auth_missing", False, "No auth token available")
        return

    # Create a trip with status 'other'
    payload = _make_trip_payload(client, headers, status="other")
    if not payload:
        record("no_existing_trip_for_seed", False, "No existing GTFS trip found on route to clone service_id")
        return

    created_id = None
    try:
        r_create = client.post(f"{API_BASE}/gtfs-trips/", json=payload, headers=headers)
        record("create_trip_status", r_create.status_code == 200, f"status={r_create.status_code} body={r_create.text}")
        if r_create.status_code != 200:
            return
        created = r_create.json()
        created_id = created.get("id")
        record("create_trip_has_id", bool(created_id), "Missing trip id")
        record("create_trip_status_other", created.get("status") == "other", f"status={created.get('status')}")

        # Read trips by route, default status (gtfs) should not include our 'other' trip
        r_read_default = client.get(f"{API_BASE}/gtfs-trips/by-route/{TEST_ROUTE_ID}", headers=headers)
        record("read_default_200", r_read_default.status_code == 200, f"status={r_read_default.status_code}")
        if r_read_default.status_code == 200:
            ids_default = {t["id"] for t in r_read_default.json()}
            record("default_excludes_other", created_id not in ids_default, "'other' trip appears without status filter")

        # Read with status=other should include it
        r_read_other = client.get(f"{API_BASE}/gtfs-trips/by-route/{TEST_ROUTE_ID}?status=other", headers=headers)
        record("read_other_200", r_read_other.status_code == 200, f"status={r_read_other.status_code}")
        if r_read_other.status_code == 200:
            ids_other = {t["id"] for t in r_read_other.json()}
            record("other_included", created_id in ids_other, "'other' trip not in filtered list")

        # Update trip: set trip_headsign and status remain other
        update = {"trip_headsign": "Updated Head", "status": "other"}
        r_update = client.put(f"{API_BASE}/gtfs-trips/{created_id}", json=update, headers=headers)
        record("update_trip_200", r_update.status_code == 200, f"status={r_update.status_code} body={r_update.text}")
        if r_update.status_code == 200:
            updated = r_update.json()
            record("update_trip_headsign", updated.get("trip_headsign") == "Updated Head", f"trip_headsign={updated.get('trip_headsign')}")
            record("update_trip_status_still_other", updated.get("status") == "other", f"status={updated.get('status')}")
    finally:
        # Cleanup: delete the created trip
        if created_id:
            client.delete(f"{API_BASE}/gtfs-trips/{created_id}", headers=headers)


@pytest.mark.skipif(
    not (TEST_ROUTE_ID and (os.getenv("TEST_API_TOKEN") or (TEST_LOGIN_EMAIL and TEST_LOGIN_PASSWORD))),
    reason="Requires TEST_ROUTE_ID and auth env vars",
)
def test_trip_delete_cleanup(client: TestClient, record):
    headers = _auth_headers(client)
    if not headers:
        record("auth_missing2", False, "No auth token available")
        return

    payload = _make_trip_payload(client, headers, status="other")
    if not payload:
        record("no_existing_trip_for_seed2", False, "No existing GTFS trip found on route to clone service_id")
        return
    created_id = None
    r_create = client.post(f"{API_BASE}/gtfs-trips/", json=payload, headers=headers)
    record("create_trip2_200", r_create.status_code == 200, f"status={r_create.status_code}")
    if r_create.status_code == 200:
        created_id = r_create.json().get("id")

    if created_id:
        r_del = client.delete(f"{API_BASE}/gtfs-trips/{created_id}", headers=headers)
        record("delete_trip2_200", r_del.status_code == 200, f"status={r_del.status_code}")
        # Verify deletion by attempting to delete again
        r_del_again = client.delete(f"{API_BASE}/gtfs-trips/{created_id}", headers=headers)
        record("delete_trip2_404", r_del_again.status_code == 404, f"status={r_del_again.status_code}")


