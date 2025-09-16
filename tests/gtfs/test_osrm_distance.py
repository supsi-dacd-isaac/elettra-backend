"""Tests for OSRM driving distance endpoint.
Run with: pytest -k osrm_distance
"""

import os
import uuid
import pytest

API_BASE = "/api/v1/gtfs"

__report_module__ = "gtfs_osrm"


def _auth_headers(client):
    email = os.getenv("TEST_LOGIN_EMAIL")
    password = os.getenv("TEST_LOGIN_PASSWORD")
    assert email and password, "TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD missing in test.env"
    r = client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed status={r.status_code} body={r.text}"
    token = r.json().get("access_token")
    assert token, "no access_token in login response"
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.skipif(
    not os.getenv("OSRM_BASE_URL"), reason="OSRM_BASE_URL not configured in environment"
)
def test_osrm_distance_wgs84_to_stop(client, record):
    stop_uuid_str = os.getenv("TEST_STOP_ID")
    depot_str = os.getenv("TEST_DEPOT_LOCATION")  # format: lat:lon
    assert stop_uuid_str, "TEST_STOP_ID missing in test.env"
    assert depot_str, "TEST_DEPOT_LOCATION missing in test.env (lat:lon)"

    stop_uuid = str(uuid.UUID(stop_uuid_str))
    lat_str, lon_str = depot_str.split(":", 1)
    lat = float(lat_str)
    lon = float(lon_str)

    headers = _auth_headers(client)
    r = client.get(
        f"{API_BASE}/osrm/driving-distance",
        params={
            "stop_uuid": stop_uuid,
            "lat": lat,
            "lon": lon,
            "direction": "to_stop",
            "coord_sys": "wgs84",
        },
        headers=headers,
    )
    ok = r.status_code == 200 and "distance_meters" in r.json()
    details = f"status={r.status_code} body={r.text[:200]}"
    record("osrm_distance_wgs84_to_stop", ok, details)


@pytest.mark.skipif(
    not os.getenv("OSRM_BASE_URL"), reason="OSRM_BASE_URL not configured in environment"
)
def test_osrm_distance_from_stop(client, record):
    stop_uuid_str = os.getenv("TEST_STOP_ID")
    depot_str = os.getenv("TEST_DEPOT_LOCATION")  # format: lat:lon
    assert stop_uuid_str, "TEST_STOP_ID missing in test.env"
    assert depot_str, "TEST_DEPOT_LOCATION missing in test.env (lat:lon)"

    stop_uuid = str(uuid.UUID(stop_uuid_str))
    lat_str, lon_str = depot_str.split(":", 1)
    lat = float(lat_str)
    lon = float(lon_str)

    headers = _auth_headers(client)
    r = client.get(
        f"{API_BASE}/osrm/driving-distance",
        params={
            "stop_uuid": stop_uuid,
            "lat": lat,
            "lon": lon,
            "direction": "from_stop",
            "coord_sys": "wgs84",
        },
        headers=headers,
    )
    ok = r.status_code == 200 and "distance_meters" in r.json()
    details = f"status={r.status_code} body={r.text[:200]}"
    record("osrm_distance_from_stop", ok, details)


@pytest.mark.skipif(
    not os.getenv("OSRM_BASE_URL"), reason="OSRM_BASE_URL not configured in environment"
)
def test_osrm_distance_with_geometry(client, record):
    stop_uuid_str = os.getenv("TEST_STOP_ID")
    depot_str = os.getenv("TEST_DEPOT_LOCATION")  # format: lat:lon
    assert stop_uuid_str, "TEST_STOP_ID missing in test.env"
    assert depot_str, "TEST_DEPOT_LOCATION missing in test.env (lat:lon)"

    stop_uuid = str(uuid.UUID(stop_uuid_str))
    lat_str, lon_str = depot_str.split(":", 1)
    lat = float(lat_str)
    lon = float(lon_str)

    headers = _auth_headers(client)
    r = client.get(
        f"{API_BASE}/osrm/driving-distance",
        params={
            "stop_uuid": stop_uuid,
            "lat": lat,
            "lon": lon,
            "direction": "to_stop",
            "coord_sys": "wgs84",
            "include_geometry": True,
        },
        headers=headers,
    )
    
    if r.status_code == 200:
        data = r.json()
        has_geometry = "geometry" in data
        has_geometry_type = "geometry_type" in data
        ok = has_geometry and has_geometry_type
        details = f"status={r.status_code} has_geometry={has_geometry} geometry_type={data.get('geometry_type', 'none')}"
    else:
        ok = False
        details = f"status={r.status_code} body={r.text[:200]}"
    
    record("osrm_distance_with_geometry", ok, details)


