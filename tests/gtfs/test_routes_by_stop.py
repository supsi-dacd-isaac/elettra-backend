"""Tests for /api/v1/gtfs/gtfs-routes/by-stop/{stop_id}

Environment prerequisites to run these tests end-to-end:
- TEST_API_TOKEN: A valid Bearer token for the API
- TEST_STOP_ID: A UUID of an existing stop in the database
- TEST_AGENCY_ID: A UUID of an existing agency in the database (optional)

If variables are missing, tests are skipped.
"""

import os
import pytest

__report_module__ = "gtfs_routes_by_stop"

API_BASE = "/api/v1/gtfs"


def _get_token(client) -> str | None:
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


def _auth_headers(client) -> dict:
    token = _get_token(client)
    return {"Authorization": f"Bearer {token}"} if token else {}


def _stop_id_env() -> str | None:
    return os.getenv("TEST_STOP_ID")


def _agency_id_env() -> str | None:
    return os.getenv("TEST_AGENCY_ID")


def _gtfs_params() -> tuple[str | None, str | None, str | None, str | None]:
    return (
        os.getenv("TEST_GTFS_YEAR"),
        os.getenv("TEST_GTFS_FILE_DATE"),
        os.getenv("TEST_GTFS_YEAR_ALT"),
        os.getenv("TEST_GTFS_FILE_DATE_ALT"),
    )


@pytest.mark.skipif(
    not (_stop_id_env() and (os.getenv("TEST_API_TOKEN") or (os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD")))),
    reason="Provide TEST_STOP_ID and either TEST_API_TOKEN or TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD",
)
def test_routes_by_stop_basic(client, record):
    """Test basic functionality of routes by stop endpoint"""
    stop_id = _stop_id_env()
    url = f"{API_BASE}/gtfs-routes/by-stop/{stop_id}"
    r = client.get(url, headers=_auth_headers(client))
    
    record("routes_by_stop_basic", r.status_code == 200, f"status={r.status_code} body={r.text}")
    
    if r.status_code == 200:
        data = r.json()
        assert isinstance(data, list), "Response should be a list"
        record("routes_by_stop_response_format", True, f"Got {len(data)} routes")


@pytest.mark.skipif(
    not (_stop_id_env() and _agency_id_env() and (os.getenv("TEST_API_TOKEN") or (os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD")))),
    reason="Provide TEST_STOP_ID, TEST_AGENCY_ID and either TEST_API_TOKEN or TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD",
)
def test_routes_by_stop_with_agency_filter(client, record):
    """Test routes by stop endpoint with agency filter"""
    stop_id = _stop_id_env()
    agency_id = _agency_id_env()
    
    # Get all routes for the stop
    url_all = f"{API_BASE}/gtfs-routes/by-stop/{stop_id}"
    r_all = client.get(url_all, headers=_auth_headers(client))
    
    if r_all.status_code != 200:
        record("routes_by_stop_all_fetch", False, f"status={r_all.status_code} body={r_all.text}")
        return
    
    all_routes = r_all.json()
    all_agency_ids = {route["agency_id"] for route in all_routes}
    
    # Get routes filtered by agency
    url_filtered = f"{API_BASE}/gtfs-routes/by-stop/{stop_id}?agency_id={agency_id}"
    r_filtered = client.get(url_filtered, headers=_auth_headers(client))
    
    if r_filtered.status_code != 200:
        record("routes_by_stop_filtered_fetch", False, f"status={r_filtered.status_code} body={r_filtered.text}")
        return
    
    filtered_routes = r_filtered.json()
    
    # Check that all filtered routes belong to the specified agency
    all_filtered_agency_ids = {route["agency_id"] for route in filtered_routes}
    correct_agency = all_filtered_agency_ids.issubset({agency_id})
    
    # Check that filtered routes are a subset of all routes
    filtered_route_ids = {route["id"] for route in filtered_routes}
    all_route_ids = {route["id"] for route in all_routes}
    is_subset = filtered_route_ids.issubset(all_route_ids)
    
    record("routes_by_stop_agency_filter", correct_agency and is_subset, 
           f"correct_agency={correct_agency} is_subset={is_subset} filtered={len(filtered_routes)} all={len(all_routes)}")


@pytest.mark.skipif(
    not (_stop_id_env() and (os.getenv("TEST_API_TOKEN") or (os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD")))),
    reason="Provide TEST_STOP_ID and either TEST_API_TOKEN or TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD",
)
def test_routes_by_stop_with_gtfs_params(client, record):
    """Test routes by stop endpoint with explicit gtfs_year and gtfs_file_date"""
    stop_id = _stop_id_env()
    year, file_date, year_alt, file_date_alt = _gtfs_params()

    if not (year and file_date):
        pytest.skip("TEST_GTFS_YEAR and TEST_GTFS_FILE_DATE not set")

    url = f"{API_BASE}/gtfs-routes/by-stop/{stop_id}?gtfs_year={year}&gtfs_file_date={file_date}"
    r = client.get(url, headers=_auth_headers(client))
    record("routes_by_stop_with_params_status", r.status_code == 200, f"status={r.status_code} body={r.text}")
    if r.status_code == 200:
        data = r.json()
        assert isinstance(data, list), "Response should be a list"
        record("routes_by_stop_with_params_format", True, f"Got {len(data)} routes for year={year} date={file_date}")

    if year_alt and file_date_alt:
        url_alt = f"{API_BASE}/gtfs-routes/by-stop/{stop_id}?gtfs_year={year_alt}&gtfs_file_date={file_date_alt}"
        r_alt = client.get(url_alt, headers=_auth_headers(client))
        record("routes_by_stop_with_params_alt_status", r_alt.status_code == 200, f"status={r_alt.status_code} body={r_alt.text}")
        if r_alt.status_code == 200:
            data_alt = r_alt.json()
            assert isinstance(data_alt, list), "Response should be a list"
            record("routes_by_stop_with_params_alt_format", True, f"Got {len(data_alt)} routes for year={year_alt} date={file_date_alt}")


@pytest.mark.skipif(
    not (_stop_id_env() and _agency_id_env() and (os.getenv("TEST_API_TOKEN") or (os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD")))),
    reason="Provide TEST_STOP_ID, TEST_AGENCY_ID and either TEST_API_TOKEN or TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD",
)
def test_routes_by_stop_with_agency_and_gtfs_params(client, record):
    """Test routes by stop endpoint with agency filter and gtfs params"""
    stop_id = _stop_id_env()
    agency_id = _agency_id_env()
    year, file_date, _, _ = _gtfs_params()

    if not (year and file_date):
        pytest.skip("TEST_GTFS_YEAR and TEST_GTFS_FILE_DATE not set")

    url = (
        f"{API_BASE}/gtfs-routes/by-stop/{stop_id}?agency_id={agency_id}"
        f"&gtfs_year={year}&gtfs_file_date={file_date}"
    )
    r = client.get(url, headers=_auth_headers(client))
    record("routes_by_stop_with_agency_params_status", r.status_code == 200, f"status={r.status_code} body={r.text}")
    if r.status_code == 200:
        data = r.json()
        assert isinstance(data, list), "Response should be a list"
        ok_agency = all(rt.get("agency_id") == agency_id for rt in data)
        record("routes_by_stop_with_agency_params_format", ok_agency, f"Got {len(data)} routes; agency_ok={ok_agency}")


@pytest.mark.skipif(
    not (_stop_id_env() and (os.getenv("TEST_API_TOKEN") or (os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD")))),
    reason="Provide TEST_STOP_ID and either TEST_API_TOKEN or TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD",
)
def test_routes_by_stop_invalid_stop_404(client, record):
    """Test routes by stop endpoint with invalid stop ID"""
    # Use a valid UUID format but non-existent stop ID
    invalid_stop_id = "00000000-0000-0000-0000-000000000000"
    url = f"{API_BASE}/gtfs-routes/by-stop/{invalid_stop_id}"
    r = client.get(url, headers=_auth_headers(client))
    
    # Should return 200 with empty list, not 404, since the query will just return no results
    record("routes_by_stop_invalid_stop", r.status_code == 200, f"status={r.status_code} body={r.text}")
    
    if r.status_code == 200:
        data = r.json()
        assert isinstance(data, list), "Response should be a list"
        record("routes_by_stop_invalid_stop_empty", len(data) == 0, f"Got {len(data)} routes for invalid stop")
