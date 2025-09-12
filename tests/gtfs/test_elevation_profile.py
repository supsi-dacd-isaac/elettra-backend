from types import SimpleNamespace
from uuid import UUID
import pytest

from app.core.auth import get_current_user
from app.database import get_async_session
from main import app


class FakeSession:
    async def get(self, model, pk):
        # Return an object with a shape_id for any requested trip
        return SimpleNamespace(shape_id="shape_test_123")


class FakeMinioObject:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def close(self):
        pass

    def release_conn(self):
        pass


class FakeMinioClient:
    def __init__(self, *args, **kwargs):
        pass

    def get_object(self, bucket, object_name):
        # Return any non-empty bytes; pandas.read_parquet will be mocked
        return FakeMinioObject(b"parquet-bytes-placeholder")


def _override_auth():
    return SimpleNamespace(id=UUID("00000000-0000-0000-0000-000000000001"), company_id=UUID("00000000-0000-0000-0000-000000000002"))


@pytest.mark.parametrize("trip_id", ["b26b6a4a-7c96-492d-9748-e933bf7a1a30"])  # any UUID works with fakes
def test_get_elevation_profile_by_trip(client, monkeypatch, record, trip_id):
    # Override dependencies
    async def _override_session():
        yield FakeSession()

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[get_current_user] = _override_auth

    # Mock Minio client
    from app import routers
    monkeypatch.setattr(routers.gtfs, "Minio", FakeMinioClient)

    # Mock pandas.read_parquet to return deterministic data
    import pandas as pd

    def fake_read_parquet(_buf):
        return pd.DataFrame([
            {"segment_id": "A", "point_number": 1, "latitude": 46.0, "longitude": 8.0, "altitude_m": 500.0},
            {"segment_id": "A", "point_number": 2, "latitude": 46.001, "longitude": 8.001, "altitude_m": 505.5},
        ])

    monkeypatch.setattr(routers.gtfs.pd, "read_parquet", fake_read_parquet)

    # Call endpoint
    try:
        resp = client.get(f"/api/v1/gtfs/elevation-profile/by-trip/{trip_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Validate response structure
        assert data["shape_id"] == "shape_test_123"
        assert isinstance(data["records"], list)
        assert len(data["records"]) == 2
        assert set(data["records"][0].keys()) == {"segment_id", "point_number", "latitude", "longitude", "altitude_m"}

        record("elevation_profile_by_trip_returns_records", True, "Endpoint returned mocked parquet records successfully")
    finally:
        # Clean up overrides to avoid affecting other tests
        app.dependency_overrides.pop(get_async_session, None)
        app.dependency_overrides.pop(get_current_user, None)


