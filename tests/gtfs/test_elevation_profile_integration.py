import os
import pytest


API_BASE = "/api/v1/gtfs"


def _should_run():
    return os.getenv("ELEVATION_IT") == "1"


def _trip_id_env():
    return os.getenv("ELEVATION_IT_TRIP_ID")


def _auth_headers(client):
    token = os.getenv("TEST_API_TOKEN")
    if not token:
        email = os.getenv("TEST_LOGIN_EMAIL")
        password = os.getenv("TEST_LOGIN_PASSWORD")
        if not (email and password):
            pytest.skip("Missing TEST_API_TOKEN or TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD for integration test")
        r = client.post("/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.skipif(not _should_run(), reason="Set ELEVATION_IT=1 to run MinIO integration test")
def test_elevation_profile_by_trip_integration(client, record, monkeypatch):
    trip_id = _trip_id_env()
    if not trip_id:
        pytest.skip("Set ELEVATION_IT_TRIP_ID to a valid trip UUID with a corresponding parquet in MinIO")

    # Ensure the app points to the host-exposed MinIO port
    # docker-compose exposes minio on localhost:9002 by default
    monkeypatch.setenv("MINIO_ENDPOINT", os.getenv("MINIO_ENDPOINT", "localhost:9002"))
    # If your MinIO uses different creds, set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in env before running

    url = f"{API_BASE}/elevation-profile/by-trip/{trip_id}"
    r = client.get(url, headers=_auth_headers(client))
    assert r.status_code == 200, r.text
    data = r.json()
    assert "shape_id" in data
    assert isinstance(data.get("records"), list)

    record("elevation_profile_by_trip_integration", True, "Fetched parquet from real MinIO successfully")


