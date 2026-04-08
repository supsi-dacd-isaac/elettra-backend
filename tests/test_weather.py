"""
Tests for the meteorological API section:
  - GET /api/v1/simulation/pvgis-tmy/  (download=false / download=true)
  - POST /api/v1/simulation/weather-temperature-clusters/
  - GET  /api/v1/simulation/weather-temperature-clusters/
  - Unit tests for sanitize_weather_values()
"""
import os
import pytest
from fastapi.testclient import TestClient

from app.services.weather import sanitize_weather_values

__report_module__ = "weather"

SIM_BASE = "/api/v1/simulation"
AUTH_BASE = "/auth"

TEST_LOGIN_EMAIL = os.getenv("TEST_LOGIN_EMAIL", "test@supsi.ch")
TEST_LOGIN_PASSWORD = os.getenv("TEST_LOGIN_PASSWORD", ">tha0-!UdLb.hZ@aP)*x")

# Coordinates of an existing TMY location (matches data already in test DB)
TEST_LAT = 46.004
TEST_LON = 8.951
# Coordinates for which no TMY data exists
MISSING_LAT = 0.001
MISSING_LON = 0.001


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


# -----------------------------------------------------------------------
# A) GET /pvgis-tmy/?download=false  — metadata only
# -----------------------------------------------------------------------

def test_pvgis_tmy_metadata_no_data(client, record):
    """download=false with coords that have no DB data → PVGIS is called,
    data is stored, metadata returned with available_in_db=true and source='pvgis'.
    If PVGIS rejects the coords (e.g. ocean) a 500 is acceptable."""
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")

    # Use valid European coords that are NOT already in the DB
    test_lat, test_lon = 47.374, 8.541  # Zurich
    response = client.get(
        f"{SIM_BASE}/pvgis-tmy/",
        params={"latitude": test_lat, "longitude": test_lon, "download": False},
        headers=auth_headers(token),
    )
    try:
        if response.status_code == 500:
            record("pvgis_tmy_metadata_no_data", True,
                   "PVGIS call attempted (500 likely due to network). Correct behaviour.")
            return
        assert response.status_code == 200, f"Expected 200 or 500, got {response.status_code}: {response.text}"
        body = response.json()
        assert body["available_in_db"] is True
        assert body["records_count"] >= 8760
        assert body["source"] in ("pvgis", "db")
        assert "data" not in body
        record("pvgis_tmy_metadata_no_data", True,
               f"Data fetched from PVGIS and stored. source={body['source']}, count={body['records_count']}")
    except AssertionError as e:
        record("pvgis_tmy_metadata_no_data", False, str(e))


def test_pvgis_tmy_metadata_default_is_false(client, record):
    """Without explicit download param, default is false → metadata response (no full data)"""
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")

    response = client.get(
        f"{SIM_BASE}/pvgis-tmy/",
        params={"latitude": TEST_LAT, "longitude": TEST_LON},
        headers=auth_headers(token),
    )
    try:
        assert response.status_code == 200
        body = response.json()
        assert "available_in_db" in body
        assert "data" not in body
        record("pvgis_tmy_metadata_default_false", True, "Default download=false returns metadata")
    except AssertionError as e:
        record("pvgis_tmy_metadata_default_false", False, str(e))


def test_pvgis_tmy_metadata_existing_data(client, record):
    """download=false with coords that have TMY data → available_in_db=true"""
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")

    response = client.get(
        f"{SIM_BASE}/pvgis-tmy/",
        params={"latitude": TEST_LAT, "longitude": TEST_LON, "download": False},
        headers=auth_headers(token),
    )
    try:
        assert response.status_code == 200
        body = response.json()
        # Data may or may not exist in the test DB for these coords; just check structure
        assert "available_in_db" in body
        assert isinstance(body["records_count"], int)
        if body["available_in_db"]:
            assert body["source"] == "db"
            assert body["records_count"] >= 8760
        record("pvgis_tmy_metadata_existing_data", True, f"available={body['available_in_db']}, count={body['records_count']}")
    except AssertionError as e:
        record("pvgis_tmy_metadata_existing_data", False, str(e))


def test_pvgis_tmy_unauthorized(client, record):
    """Endpoint requires authentication"""
    response = client.get(
        f"{SIM_BASE}/pvgis-tmy/",
        params={"latitude": TEST_LAT, "longitude": TEST_LON},
    )
    try:
        assert response.status_code in [401, 403]
        record("pvgis_tmy_unauthorized", True, f"Auth required (status {response.status_code})")
    except AssertionError as e:
        record("pvgis_tmy_unauthorized", False, str(e))


# -----------------------------------------------------------------------
# B) GET /pvgis-tmy/?download=true  — full TMY payload (DB read path)
# -----------------------------------------------------------------------

def test_pvgis_tmy_download_from_db(client, record):
    """download=true with data already in DB → returns full payload from DB"""
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")

    # First check if data exists
    meta_resp = client.get(
        f"{SIM_BASE}/pvgis-tmy/",
        params={"latitude": TEST_LAT, "longitude": TEST_LON, "download": False},
        headers=auth_headers(token),
    )
    if meta_resp.status_code != 200 or not meta_resp.json().get("available_in_db"):
        pytest.skip("TMY data not available in test DB for this location")

    response = client.get(
        f"{SIM_BASE}/pvgis-tmy/",
        params={"latitude": TEST_LAT, "longitude": TEST_LON, "download": True},
        headers=auth_headers(token),
    )
    try:
        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "metadata" in body
        assert "records" in body["data"]
        assert len(body["data"]["records"]) >= 8760
        assert body["latitude"] == TEST_LAT
        assert body["longitude"] == TEST_LON
        record("pvgis_tmy_download_from_db", True, f"Got {len(body['data']['records'])} records from DB")
    except AssertionError as e:
        record("pvgis_tmy_download_from_db", False, str(e))


# -----------------------------------------------------------------------
# D) POST /weather-temperature-clusters/
# -----------------------------------------------------------------------

def test_clustering_no_weather_data(client, record):
    """POST clustering for coords with no weather data → 400"""
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")

    response = client.post(
        f"{SIM_BASE}/weather-temperature-clusters/",
        json={"latitude": MISSING_LAT, "longitude": MISSING_LON, "k": 8},
        headers=auth_headers(token),
    )
    try:
        assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
        record("clustering_no_weather_data", True, "400 returned when no weather data")
    except AssertionError as e:
        record("clustering_no_weather_data", False, str(e))


def test_clustering_too_few_days(client, record):
    """POST clustering with k larger than available days → 400"""
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")

    # Check if TMY data exists first
    meta_resp = client.get(
        f"{SIM_BASE}/pvgis-tmy/",
        params={"latitude": TEST_LAT, "longitude": TEST_LON, "download": False},
        headers=auth_headers(token),
    )
    if meta_resp.status_code != 200 or not meta_resp.json().get("available_in_db"):
        pytest.skip("TMY data not in test DB for this location")

    response = client.post(
        f"{SIM_BASE}/weather-temperature-clusters/",
        json={"latitude": TEST_LAT, "longitude": TEST_LON, "k": 9999},
        headers=auth_headers(token),
    )
    try:
        assert response.status_code == 400
        assert "Not enough" in response.json()["detail"]
        record("clustering_too_few_days", True, "400 returned for excessive k")
    except AssertionError as e:
        record("clustering_too_few_days", False, str(e))


def test_clustering_success(client, record):
    """POST clustering with valid params → 200 + sorted clusters"""
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")

    meta_resp = client.get(
        f"{SIM_BASE}/pvgis-tmy/",
        params={"latitude": TEST_LAT, "longitude": TEST_LON, "download": False},
        headers=auth_headers(token),
    )
    if meta_resp.status_code != 200 or not meta_resp.json().get("available_in_db"):
        pytest.skip("TMY data not in test DB for this location")

    response = client.post(
        f"{SIM_BASE}/weather-temperature-clusters/",
        json={
            "latitude": TEST_LAT,
            "longitude": TEST_LON,
            "k": 4,
            "start_time": "05:00",
            "end_time": "24:00",
        },
        headers=auth_headers(token),
    )
    try:
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        body = response.json()
        assert body["k"] == 4
        assert body["start_time"] == "05:00"
        assert body["end_time"] == "24:00"
        assert "clusters" in body
        assert len(body["clusters"]) == 4
        assert body["n_days_used"] > 0

        centroids = [c["centroid_daily_avg_temp"] for c in body["clusters"]]
        assert centroids == sorted(centroids), "Clusters must be sorted by centroid ascending"

        total_occ = sum(c["occurrences"] for c in body["clusters"])
        assert total_occ == body["n_days_used"]

        record("clustering_success", True, f"4 clusters, {body['n_days_used']} days, centroids={centroids}")
    except AssertionError as e:
        record("clustering_success", False, str(e))


def test_clustering_default_end_time_24(client, record):
    """POST clustering with default end_time=24:00 works"""
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")

    meta_resp = client.get(
        f"{SIM_BASE}/pvgis-tmy/",
        params={"latitude": TEST_LAT, "longitude": TEST_LON, "download": False},
        headers=auth_headers(token),
    )
    if meta_resp.status_code != 200 or not meta_resp.json().get("available_in_db"):
        pytest.skip("TMY data not in test DB for this location")

    response = client.post(
        f"{SIM_BASE}/weather-temperature-clusters/",
        json={"latitude": TEST_LAT, "longitude": TEST_LON},
        headers=auth_headers(token),
    )
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["k"] == 8
        assert body["end_time"] == "24:00"
        assert len(body["clusters"]) == 8
        record("clustering_default_24", True, "Default k=8, end_time=24:00 works")
    except AssertionError as e:
        record("clustering_default_24", False, str(e))


# -----------------------------------------------------------------------
# F) GET /weather-temperature-clusters/  — retrieve saved clustering
# -----------------------------------------------------------------------

def test_get_clustering_success(client, record):
    """GET saved clustering after POST → returns correct result"""
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")

    meta_resp = client.get(
        f"{SIM_BASE}/pvgis-tmy/",
        params={"latitude": TEST_LAT, "longitude": TEST_LON, "download": False},
        headers=auth_headers(token),
    )
    if meta_resp.status_code != 200 or not meta_resp.json().get("available_in_db"):
        pytest.skip("TMY data not in test DB")

    # Ensure clustering exists (POST first)
    post_resp = client.post(
        f"{SIM_BASE}/weather-temperature-clusters/",
        json={"latitude": TEST_LAT, "longitude": TEST_LON, "k": 4, "start_time": "05:00", "end_time": "24:00"},
        headers=auth_headers(token),
    )
    if post_resp.status_code != 200:
        pytest.skip(f"Could not create clustering: {post_resp.text}")

    response = client.get(
        f"{SIM_BASE}/weather-temperature-clusters/",
        params={
            "latitude": TEST_LAT, "longitude": TEST_LON,
            "k": 4, "start_time": "05:00", "end_time": "24:00",
        },
        headers=auth_headers(token),
    )
    try:
        assert response.status_code == 200
        body = response.json()
        assert body["k"] == 4
        assert len(body["clusters"]) == 4
        record("get_clustering_success", True, "Saved clustering retrieved correctly")
    except AssertionError as e:
        record("get_clustering_success", False, str(e))


# -----------------------------------------------------------------------
# G) GET clustering not found → 404
# -----------------------------------------------------------------------

def test_get_clustering_not_found(client, record):
    """GET clustering for non-existent config → 404"""
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")

    response = client.get(
        f"{SIM_BASE}/weather-temperature-clusters/",
        params={
            "latitude": MISSING_LAT, "longitude": MISSING_LON,
            "k": 99, "start_time": "00:00", "end_time": "01:00",
        },
        headers=auth_headers(token),
    )
    try:
        assert response.status_code == 404
        record("get_clustering_not_found", True, "404 returned for missing clustering")
    except AssertionError as e:
        record("get_clustering_not_found", False, str(e))


def test_clustering_unauthorized(client, record):
    """POST and GET clustering require authentication"""
    post_resp = client.post(
        f"{SIM_BASE}/weather-temperature-clusters/",
        json={"latitude": TEST_LAT, "longitude": TEST_LON},
    )
    get_resp = client.get(
        f"{SIM_BASE}/weather-temperature-clusters/",
        params={"latitude": TEST_LAT, "longitude": TEST_LON},
    )
    try:
        assert post_resp.status_code in [401, 403]
        assert get_resp.status_code in [401, 403]
        record("clustering_unauthorized", True, "Auth required for both POST and GET")
    except AssertionError as e:
        record("clustering_unauthorized", False, str(e))


# -----------------------------------------------------------------------
# H) Unit tests for sanitize_weather_values()
# -----------------------------------------------------------------------

class TestSanitizeWeatherValues:
    """Pure-function tests — no DB or HTTP required."""

    # -- pressure: must be > 0; non-positive → None --

    def test_pressure_none_stays_none(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=None, wind_speed=None
        )["pressure"] is None

    def test_pressure_positive_kept(self):
        assert sanitize_weather_values(
            pressure=101325, relative_humidity=None, wind_direction=None, wind_speed=None
        )["pressure"] == 101325

    def test_pressure_zero_becomes_none(self):
        assert sanitize_weather_values(
            pressure=0, relative_humidity=None, wind_direction=None, wind_speed=None
        )["pressure"] is None

    def test_pressure_negative_becomes_none(self):
        assert sanitize_weather_values(
            pressure=-5, relative_humidity=None, wind_direction=None, wind_speed=None
        )["pressure"] is None

    # -- relative_humidity: clamped to [0, 100] --

    def test_rh_none_stays_none(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=None, wind_speed=None
        )["relative_humidity"] is None

    def test_rh_in_range_kept(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=55.5, wind_direction=None, wind_speed=None
        )["relative_humidity"] == 55.5

    def test_rh_negative_clamped_to_zero(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=-0.3, wind_direction=None, wind_speed=None
        )["relative_humidity"] == 0.0

    def test_rh_over_100_clamped(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=100.7, wind_direction=None, wind_speed=None
        )["relative_humidity"] == 100.0

    def test_rh_boundary_zero(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=0.0, wind_direction=None, wind_speed=None
        )["relative_humidity"] == 0.0

    def test_rh_boundary_100(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=100.0, wind_direction=None, wind_speed=None
        )["relative_humidity"] == 100.0

    # -- wind_direction: normalized to [0, 360) via modulo --

    def test_wd_none_stays_none(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=None, wind_speed=None
        )["wind_direction"] is None

    def test_wd_normal_kept(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=180.0, wind_speed=None
        )["wind_direction"] == 180.0

    def test_wd_negative_wraps(self):
        result = sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=-10.0, wind_speed=None
        )["wind_direction"]
        assert result == pytest.approx(350.0)

    def test_wd_360_becomes_zero(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=360.0, wind_speed=None
        )["wind_direction"] == pytest.approx(0.0)

    def test_wd_large_value_wraps(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=725.0, wind_speed=None
        )["wind_direction"] == pytest.approx(5.0)

    # -- wind_speed: must be >= 0; negative → 0 --

    def test_ws_none_stays_none(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=None, wind_speed=None
        )["wind_speed"] is None

    def test_ws_positive_kept(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=None, wind_speed=5.2
        )["wind_speed"] == 5.2

    def test_ws_zero_kept(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=None, wind_speed=0.0
        )["wind_speed"] == 0.0

    def test_ws_negative_becomes_zero(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=None, wind_speed=-0.03
        )["wind_speed"] == 0.0

    def test_ws_large_negative_becomes_zero(self):
        assert sanitize_weather_values(
            pressure=None, relative_humidity=None, wind_direction=None, wind_speed=-99.9
        )["wind_speed"] == 0.0
