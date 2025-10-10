"""
Test trip statistics endpoint
"""
import os
import pytest
from uuid import UUID
from fastapi.testclient import TestClient

__report_module__ = "trip_statistics"

# Get test credentials from environment
TEST_LOGIN_EMAIL = os.getenv("TEST_LOGIN_EMAIL", "test@supsi.ch")
TEST_LOGIN_PASSWORD = os.getenv("TEST_LOGIN_PASSWORD", ">tha0-!UdLb.hZ@aP)*x")
ELEVATION_IT_TRIP_ID = os.getenv("ELEVATION_IT_TRIP_ID", "5adc5823-61b8-4f7f-a953-13e93fb1f7fa")
TEST_TRIP_STATISTICS_ID1 = os.getenv("TEST_TRIP_STATISTICS_ID1", "aead2c47-ae3c-4740-8895-8eec54c3aecb")
TEST_TRIP_STATISTICS_ID2 = os.getenv("TEST_TRIP_STATISTICS_ID2", "c99e99a4-2167-4967-bf7a-af94a6278c4f")
AUTH_BASE = "/auth"


def get_auth_token(client: TestClient) -> str | None:
    """Get authentication token for testing"""
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
    """Create authorization headers"""
    return {"Authorization": f"Bearer {token}"}


def test_trip_statistics_single_trip(client, record):
    """Test computing combined statistics for a single trip (still single response)"""
    
    # Get authentication token
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")
    
    headers = auth_headers(token)
    
    # Use TEST_TRIP_STATISTICS_ID1 for single trip test
    trip_id = UUID(TEST_TRIP_STATISTICS_ID1)
    
    # Request combined statistics (single response)
    response = client.post(
        "/api/v1/simulation/trip-statistics/",
        json={"trip_ids": [str(trip_id)]},
        headers=headers
    )
    
    try:
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        
        result = response.json()
        assert "trip_ids" in result
        assert "statistics" in result
        assert "error" in result
        assert len(result["trip_ids"]) == 1
        
        # If no error, check that statistics were computed
        if result["error"] is None:
            stats = result["statistics"]
            
            # Check for basic trip metrics
            assert "total_duration_minutes" in stats
            assert "total_number_of_stops" in stats
            assert "total_distance_m" in stats
            assert "average_speed_kmh" in stats
            assert "driving_time_minutes" in stats
            assert "total_dwell_time_minutes" in stats
            
            # Check for elevation metrics (may be 0.0 if no elevation data)
            assert "elevation_range_m" in stats
            assert "mean_elevation_m" in stats
            assert "total_ascent_m" in stats
            assert "total_descent_m" in stats
            assert "mean_gradient" in stats
            
            # Verify reasonable values
            assert stats["total_duration_minutes"] > 0
            assert stats["total_number_of_stops"] >= 2
            assert stats["total_distance_m"] > 0
            assert stats["average_speed_kmh"] > 0
            
            details = (
                f"Duration: {stats['total_duration_minutes']:.2f} min, "
                f"Distance: {stats['total_distance_m']:.2f} m, "
                f"Stops: {stats['total_number_of_stops']}, "
                f"Speed: {stats['average_speed_kmh']:.2f} km/h"
            )
            
            if stats.get('total_ascent_m', 0) > 0:
                details += (
                    f", Ascent: {stats['total_ascent_m']:.2f} m, "
                    f"Descent: {stats['total_descent_m']:.2f} m"
                )
            
            record("trip_statistics_single_trip", True, details)
        else:
            record("trip_statistics_single_trip", False, f"Error: {result['error']}")
    except AssertionError as e:
        record("trip_statistics_single_trip", False, str(e))


def test_trip_statistics_multiple_trips(client, record):
    """Test computing combined statistics for two trips (single response)"""
    
    # Get authentication token
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")
    
    headers = auth_headers(token)
    
    # Use two different trip IDs
    trip_id1 = UUID(TEST_TRIP_STATISTICS_ID1)
    trip_id2 = UUID(TEST_TRIP_STATISTICS_ID2)
    
    # Request combined statistics (single response)
    response = client.post(
        "/api/v1/simulation/trip-statistics/",
        json={"trip_ids": [str(trip_id1), str(trip_id2)]},
        headers=headers
    )
    
    try:
        assert response.status_code == 200
        
        result = response.json()
        assert "trip_ids" in result
        assert "statistics" in result
        assert "error" in result
        assert set(result["trip_ids"]) == {str(trip_id1), str(trip_id2)}
        
        record("trip_statistics_multiple_trips", True, f"Processed combined stats for 2 trips successfully")
    except AssertionError as e:
        record("trip_statistics_multiple_trips", False, str(e))


def test_trip_statistics_invalid_trip(client, record):
    """Test computing combined statistics for a non-existent trip"""
    
    # Get authentication token
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")
    
    headers = auth_headers(token)
    
    # Use a random UUID that likely doesn't exist
    fake_trip_id = "00000000-0000-0000-0000-000000000000"
    
    response = client.post(
        "/api/v1/simulation/trip-statistics/",
        json={"trip_ids": [fake_trip_id]},
        headers=headers
    )
    
    try:
        assert response.status_code == 200
        result = response.json()
        assert result["error"] is not None or len(result["statistics"]) == 0
        
        error_msg = result.get('error', 'No stops found')
        record("trip_statistics_invalid_trip", True, f"Correctly handled non-existent trip: {error_msg}")
    except AssertionError as e:
        record("trip_statistics_invalid_trip", False, str(e))


def test_trip_statistics_unauthorized(client, record):
    """Test that the endpoint requires authentication (combined response)"""
    
    trip_id = UUID(ELEVATION_IT_TRIP_ID)
    
    # Request without authentication
    response = client.post(
        "/api/v1/simulation/trip-statistics/",
        json={"trip_ids": [str(trip_id)]}
    )
    
    try:
        # Accept either 401 (Unauthorized) or 403 (Forbidden) as both indicate auth is required
        assert response.status_code in [401, 403]
        record("trip_statistics_unauthorized", True, f"Endpoint correctly requires authentication (status: {response.status_code})")
    except AssertionError as e:
        record("trip_statistics_unauthorized", False, f"Expected 401 or 403, got {response.status_code}")


def test_trip_statistics_empty_list(client, record):
    """Test computing combined statistics for an empty list of trips"""
    
    # Get authentication token
    token = get_auth_token(client)
    if not token:
        pytest.skip("Unable to get authentication token")
    
    headers = auth_headers(token)
    
    response = client.post(
        "/api/v1/simulation/trip-statistics/",
        json={"trip_ids": []},
        headers=headers
    )
    
    try:
        assert response.status_code == 200
        result = response.json()
        assert result["trip_ids"] == []
        assert result["statistics"] == {}
        record("trip_statistics_empty_list", True, "Empty trip list handled correctly")
    except AssertionError as e:
        record("trip_statistics_empty_list", False, str(e))

