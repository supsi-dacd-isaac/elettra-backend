import os
import pytest

API_BASE = "/api/v1/gtfs"


def _get_token(client):
    token = os.getenv("TEST_API_TOKEN")
    if token:
        return token
    email = os.getenv("TEST_LOGIN_EMAIL")
    password = os.getenv("TEST_LOGIN_PASSWORD")
    if email and password:
        r = client.post("/auth/login", json={"email": email, "password": password})
        if r.status_code == 200:
            return r.json().get("access_token")
    return None


def _auth_headers(client):
    token = _get_token(client)
    return {"Authorization": f"Bearer {token}"} if token else {}


@pytest.mark.skipif(
    not (
        os.getenv("OSRM_BASE_URL")
        and os.getenv("TEST_ROUTE_ID")
        and os.getenv("TEST_DEPOT_DEPARTURE_STOP_ID")
        and os.getenv("TEST_DEPOT_ARRIVAL_STOP_ID")
        and (
            os.getenv("TEST_API_TOKEN")
            or (
                os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD")
            )
        )
    ),
    reason="Requires OSRM_BASE_URL, auth, TEST_ROUTE_ID and depot stop ids",
)
def test_create_depot_trip(client, record, monkeypatch):
    # Ensure MINIO endpoint points to host-exposed port when running tests locally
    monkeypatch.setenv("MINIO_ENDPOINT", os.getenv("MINIO_ENDPOINT", "localhost:9002"))

    dep_stop_id = os.getenv("TEST_DEPOT_DEPARTURE_STOP_ID")
    arr_stop_id = os.getenv("TEST_DEPOT_ARRIVAL_STOP_ID")
    route_id = os.getenv("TEST_ROUTE_ID")

    payload = {
        "departure_stop_id": dep_stop_id,
        "arrival_stop_id": arr_stop_id,
        "departure_time": "08:00:00",
        "arrival_time": "08:15:00",
        "route_id": route_id,
    }
    headers = _auth_headers(client)
    # Use aux-trip endpoint, explicitly setting depot status for same behavior
    payload["status"] = "depot"
    r = client.post(f"{API_BASE}/aux-trip", json=payload, headers=headers)
    ok = r.status_code == 200
    record("create_depot_trip_status", ok, f"status={r.status_code} body={r.text[:200]}")
    if ok:
        data = r.json()
        record("created_has_shape_id", bool(data.get("shape_id")), "Missing shape_id")
        record("created_status_depot", data.get("status") == "depot", f"status={data.get('status')}")

        # Try fetching elevation profile to ensure parquet accessible
        trip_id = data.get("id")
        if trip_id:
            r2 = client.get(f"{API_BASE}/elevation-profile/by-trip/{trip_id}", headers=headers)
            record("elevation_profile_fetch", r2.status_code == 200, f"status={r2.status_code}")


@pytest.mark.skipif(
    not (
        os.getenv("OSRM_BASE_URL")
        and os.getenv("TEST_ROUTE_ID")
        and os.getenv("TEST_TRANSFER_DEPARTURE_STOP_ID")
        and os.getenv("TEST_TRANSFER_ARRIVAL_STOP_ID")
        and (
            os.getenv("TEST_API_TOKEN")
            or (
                os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD")
            )
        )
    ),
    reason="Requires OSRM_BASE_URL, auth, TEST_ROUTE_ID and transfer stop ids",
)
def test_create_transfer_trip(client, record, monkeypatch):
    monkeypatch.setenv("MINIO_ENDPOINT", os.getenv("MINIO_ENDPOINT", "localhost:9002"))

    dep_stop_id = os.getenv("TEST_TRANSFER_DEPARTURE_STOP_ID")
    arr_stop_id = os.getenv("TEST_TRANSFER_ARRIVAL_STOP_ID")
    route_id = os.getenv("TEST_ROUTE_ID")

    payload = {
        "departure_stop_id": dep_stop_id,
        "arrival_stop_id": arr_stop_id,
        "departure_time": "09:00:00",
        "arrival_time": "09:10:00",
        "route_id": route_id,
        "status": "transfer",
    }
    headers = _auth_headers(client)
    r = client.post(f"{API_BASE}/aux-trip", json=payload, headers=headers)
    ok = r.status_code == 200
    record("create_transfer_trip_status", ok, f"status={r.status_code} body={r.text[:200]}")
    if ok:
        data = r.json()
        record("created_status_transfer", data.get("status") == "transfer", f"status={data.get('status')}")
        record("gtfs_service_auxiliary", data.get("gtfs_service_id") == "auxiliary", f"gtfs_service_id={data.get('gtfs_service_id')}")


