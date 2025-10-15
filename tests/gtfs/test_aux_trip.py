import os
import pytest
import uuid

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


def _cleanup_trip(client, trip_id, headers):
    """Clean up a created trip by deleting it"""
    if trip_id:
        try:
            r = client.delete(f"{API_BASE}/gtfs-trips/{trip_id}", headers=headers)
            # Don't fail the test if cleanup fails, just log it
            if r.status_code not in [200, 204, 404]:
                print(f"Warning: Failed to cleanup trip {trip_id}: {r.status_code} {r.text}")
        except Exception as e:
            print(f"Warning: Exception during trip cleanup: {e}")


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
    
    created_trip_id = None
    if ok:
        data = r.json()
        created_trip_id = data.get("id")
        record("created_has_shape_id", bool(data.get("shape_id")), "Missing shape_id")
        record("created_status_depot", data.get("status") == "depot", f"status={data.get('status')}")

        # Try fetching elevation profile to ensure parquet accessible
        if created_trip_id:
            r2 = client.get(f"{API_BASE}/elevation-profile/by-trip/{created_trip_id}", headers=headers)
            elevation_fetch_ok = r2.status_code == 200
            record("elevation_profile_fetch", elevation_fetch_ok, f"status={r2.status_code}")
            
            # Check that elevation profile has non-null altitude values
            if elevation_fetch_ok:
                elevation_data = r2.json()
                records = elevation_data.get("records", [])
                non_null_altitudes = [r for r in records if r.get("altitude_m") is not None]
                has_valid_altitudes = len(non_null_altitudes) > 0
                record(
                    "elevation_has_valid_altitudes",
                    has_valid_altitudes,
                    f"Found {len(non_null_altitudes)}/{len(records)} non-null altitude values"
                )
    
    # Clean up the created trip
    _cleanup_trip(client, created_trip_id, headers)


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
    
    created_trip_id = None
    if ok:
        data = r.json()
        created_trip_id = data.get("id")
        record("created_status_transfer", data.get("status") == "transfer", f"status={data.get('status')}")
        record("gtfs_service_auxiliary", data.get("gtfs_service_id") == "auxiliary", f"gtfs_service_id={data.get('gtfs_service_id')}")
        
        # Try fetching elevation profile and check for valid altitudes
        if created_trip_id:
            r2 = client.get(f"{API_BASE}/elevation-profile/by-trip/{created_trip_id}", headers=headers)
            elevation_fetch_ok = r2.status_code == 200
            record("elevation_profile_fetch", elevation_fetch_ok, f"status={r2.status_code}")
            
            # Check that elevation profile has non-null altitude values
            if elevation_fetch_ok:
                elevation_data = r2.json()
                records = elevation_data.get("records", [])
                non_null_altitudes = [r for r in records if r.get("altitude_m") is not None]
                has_valid_altitudes = len(non_null_altitudes) > 0
                record(
                    "elevation_has_valid_altitudes",
                    has_valid_altitudes,
                    f"Found {len(non_null_altitudes)}/{len(records)} non-null altitude values"
                )
    
    # Clean up the created trip
    _cleanup_trip(client, created_trip_id, headers)


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
def test_create_depot_trip_with_day_of_week(client, record, monkeypatch):
    monkeypatch.setenv("MINIO_ENDPOINT", os.getenv("MINIO_ENDPOINT", "localhost:9002"))

    dep_stop_id = os.getenv("TEST_DEPOT_DEPARTURE_STOP_ID")
    arr_stop_id = os.getenv("TEST_DEPOT_ARRIVAL_STOP_ID")
    route_id = os.getenv("TEST_ROUTE_ID")

    headers = _auth_headers(client)

    payload = {
        "departure_stop_id": dep_stop_id,
        "arrival_stop_id": arr_stop_id,
        "departure_time": "10:00:00",
        "arrival_time": "10:20:00",
        "route_id": route_id,
        "status": "depot",
        "day_of_week": "monday",
    }
    r = client.post(f"{API_BASE}/aux-trip", json=payload, headers=headers)
    ok = r.status_code == 200
    record("create_depot_trip_mon_status", ok, f"status={r.status_code} body={r.text[:200]}")

    created_trip_id = None
    if ok:
        data = r.json()
        created_trip_id = data.get("id")
        record("gtfs_service_auxiliary_mon", data.get("gtfs_service_id") == "auxiliary_mon", f"gtfs_service_id={data.get('gtfs_service_id')}")

    _cleanup_trip(client, created_trip_id, headers)


