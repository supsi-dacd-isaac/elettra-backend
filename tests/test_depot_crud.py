"""Pytest-based depot CRUD tests.
Run with: pytest -k depot
Generates a human-readable report in tests/reports/ via report_collector fixture.
"""

import pytest
import uuid
import os
from fastapi.testclient import TestClient

__report_module__ = "depot_crud"  # used by report_collector for report filename

API_BASE = "/api/v1/agency"
AUTH_BASE = "/auth"

# Test data - read from environment variables
TEST_AGENCY_ID = os.getenv("TEST_AGENCY_ID", "b0219529-bf96-4723-b9ee-461ce7d56344")
TEST_LOGIN_EMAIL = os.getenv("TEST_LOGIN_EMAIL", "test01.elettra@fart.ch")
TEST_LOGIN_PASSWORD = os.getenv("TEST_LOGIN_PASSWORD", "elettra")

# -----------------------------
# Helper functions
# -----------------------------

def get_auth_token(client: TestClient) -> str:
    """Get authentication token for test user"""
    login_data = {
        "email": TEST_LOGIN_EMAIL,
        "password": TEST_LOGIN_PASSWORD
    }
    response = client.post(f"{AUTH_BASE}/login", json=login_data)
    if response.status_code == 200:
        return response.json().get("access_token")
    return None

def get_auth_headers(token: str) -> dict:
    """Get authorization headers with token"""
    return {"Authorization": f"Bearer {token}"}

def create_test_depot(client: TestClient, token: str, name: str = "Test Depot") -> str:
    """Create a test depot and return its ID"""
    depot_data = {
        "agency_id": TEST_AGENCY_ID,
        "name": name,
        "address": "123 Test Street",
        "city": "Test City",
        "latitude": 40.7128,
        "longitude": -74.0060
    }
    
    headers = get_auth_headers(token)
    response = client.post(f"{API_BASE}/depots/", json=depot_data, headers=headers)
    
    if response.status_code == 200:
        return response.json()["id"]
    return None

# -----------------------------
# Create Depot Tests
# -----------------------------

def test_create_depot_success(client, record):
    """Test successful depot creation"""
    # First get auth token
    token = get_auth_token(client)
    if not token:
        record("create_depot_auth_failed", False, "Could not get auth token")
        return
    
    depot_data = {
        "agency_id": TEST_AGENCY_ID,
        "name": "Test Depot",
        "address": "123 Test Street",
        "city": "Test City",
        "latitude": 40.7128,
        "longitude": -74.0060,
        "features": {"capacity": 50, "facilities": ["charging", "maintenance"]}
    }
    
    headers = get_auth_headers(token)
    response = client.post(f"{API_BASE}/depots/", json=depot_data, headers=headers)
    
    record("create_depot_success", response.status_code == 200, f"status={response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        record("create_depot_response_id", "id" in data, "id field missing")
        record("create_depot_response_name", data.get("name") == "Test Depot", f"name={data.get('name')}")
        record("create_depot_response_agency_id", data.get("agency_id") == TEST_AGENCY_ID, f"agency_id={data.get('agency_id')}")

def test_create_depot_minimal_data(client, record):
    """Test depot creation with minimal required data"""
    token = get_auth_token(client)
    if not token:
        record("create_depot_minimal_auth_failed", False, "Could not get auth token")
        return
    
    depot_data = {
        "agency_id": TEST_AGENCY_ID,
        "name": "Minimal Depot"
    }
    
    headers = get_auth_headers(token)
    response = client.post(f"{API_BASE}/depots/", json=depot_data, headers=headers)
    
    record("create_depot_minimal", response.status_code == 200, f"status={response.status_code}")

def test_create_depot_invalid_agency_id(client, record):
    """Test depot creation with invalid agency ID"""
    token = get_auth_token(client)
    if not token:
        record("create_depot_invalid_agency_auth_failed", False, "Could not get auth token")
        return
    
    depot_data = {
        "agency_id": "00000000-0000-0000-0000-000000000999",
        "name": "Test Depot"
    }
    
    headers = get_auth_headers(token)
    response = client.post(f"{API_BASE}/depots/", json=depot_data, headers=headers)
    
    record("create_depot_invalid_agency", response.status_code == 400, f"status={response.status_code}")

def test_create_depot_missing_required_fields(client, record):
    """Test depot creation with missing required fields"""
    token = get_auth_token(client)
    if not token:
        record("create_depot_missing_fields_auth_failed", False, "Could not get auth token")
        return
    
    depot_data = {
        "agency_id": TEST_AGENCY_ID
        # Missing 'name' field
    }
    
    headers = get_auth_headers(token)
    response = client.post(f"{API_BASE}/depots/", json=depot_data, headers=headers)
    
    record("create_depot_missing_name", response.status_code == 422, f"status={response.status_code}")

def test_create_depot_invalid_coordinates(client, record):
    """Test depot creation with invalid coordinates"""
    token = get_auth_token(client)
    if not token:
        record("create_depot_invalid_coords_auth_failed", False, "Could not get auth token")
        return
    
    depot_data = {
        "agency_id": TEST_AGENCY_ID,
        "name": "Test Depot",
        "latitude": 200.0,  # Invalid latitude
        "longitude": -74.0060
    }
    
    headers = get_auth_headers(token)
    response = client.post(f"{API_BASE}/depots/", json=depot_data, headers=headers)
    
    record("create_depot_invalid_lat", response.status_code == 400, f"status={response.status_code}")

def test_create_depot_unauthorized(client, record):
    """Test depot creation without authentication"""
    depot_data = {
        "agency_id": TEST_AGENCY_ID,
        "name": "Test Depot"
    }
    
    response = client.post(f"{API_BASE}/depots/", json=depot_data)
    
    record("create_depot_unauthorized", response.status_code == 403, f"status={response.status_code}")

# -----------------------------
# Read Depot Tests
# -----------------------------

def test_read_depots_success(client, record):
    """Test successful depot listing"""
    token = get_auth_token(client)
    if not token:
        record("read_depots_auth_failed", False, "Could not get auth token")
        return
    
    headers = get_auth_headers(token)
    response = client.get(f"{API_BASE}/depots/", headers=headers)
    
    record("read_depots_success", response.status_code == 200, f"status={response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        record("read_depots_list", isinstance(data, list), f"response_type={type(data)}")

def test_read_depots_pagination(client, record):
    """Test depot listing with pagination"""
    token = get_auth_token(client)
    if not token:
        record("read_depots_pagination_auth_failed", False, "Could not get auth token")
        return
    
    headers = get_auth_headers(token)
    response = client.get(f"{API_BASE}/depots/?skip=0&limit=3", headers=headers)
    
    record("read_depots_pagination", response.status_code == 200, f"status={response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        record("read_depots_pagination_limit", len(data) <= 3, f"count={len(data)}")

def test_read_depot_by_id_success(client, record):
    """Test successful depot retrieval by ID"""
    token = get_auth_token(client)
    if not token:
        record("read_depot_by_id_auth_failed", False, "Could not get auth token")
        return
    
    # Create a test depot first
    depot_id = create_test_depot(client, token, "Read Test Depot")
    if not depot_id:
        record("read_depot_by_id_create_failed", False, "Could not create test depot")
        return
    
    headers = get_auth_headers(token)
    response = client.get(f"{API_BASE}/depots/{depot_id}", headers=headers)
    
    record("read_depot_by_id_success", response.status_code == 200, f"status={response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        record("read_depot_by_id_response_id", "id" in data, "id field missing")
        record("read_depot_by_id_response_name", "name" in data, "name field missing")
        record("read_depot_by_id_correct_id", data.get("id") == depot_id, f"expected_id={depot_id}, got={data.get('id')}")
    
    # Clean up - delete the test depot
    client.delete(f"{API_BASE}/depots/{depot_id}", headers=headers)

def test_read_depot_by_id_not_found(client, record):
    """Test depot retrieval with non-existent ID"""
    token = get_auth_token(client)
    if not token:
        record("read_depot_not_found_auth_failed", False, "Could not get auth token")
        return
    
    non_existent_id = "00000000-0000-0000-0000-000000000999"
    headers = get_auth_headers(token)
    response = client.get(f"{API_BASE}/depots/{non_existent_id}", headers=headers)
    
    record("read_depot_not_found", response.status_code == 404, f"status={response.status_code}")

def test_read_depot_unauthorized(client, record):
    """Test depot retrieval without authentication"""
    # Use a fake UUID for unauthorized test
    fake_depot_id = "00000000-0000-0000-0000-000000000999"
    response = client.get(f"{API_BASE}/depots/{fake_depot_id}")
    
    record("read_depot_unauthorized", response.status_code == 403, f"status={response.status_code}")

# -----------------------------
# Update Depot Tests
# -----------------------------

def test_update_depot_success(client, record):
    """Test successful depot update"""
    token = get_auth_token(client)
    if not token:
        record("update_depot_auth_failed", False, "Could not get auth token")
        return
    
    # Create a test depot first
    depot_id = create_test_depot(client, token, "Update Test Depot")
    if not depot_id:
        record("update_depot_create_failed", False, "Could not create test depot")
        return
    
    update_data = {
        "name": "Updated Depot",
        "city": "Updated City",
        "address": "456 New Street",
        "latitude": 41.8781,
        "longitude": -87.6298
    }
    
    headers = get_auth_headers(token)
    response = client.put(f"{API_BASE}/depots/{depot_id}", json=update_data, headers=headers)
    
    record("update_depot_success", response.status_code == 200, f"status={response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        record("update_depot_name", data.get("name") == "Updated Depot", f"name={data.get('name')}")
        record("update_depot_city", data.get("city") == "Updated City", f"city={data.get('city')}")
        record("update_depot_address", data.get("address") == "456 New Street", f"address={data.get('address')}")
    
    # Clean up - delete the test depot
    client.delete(f"{API_BASE}/depots/{depot_id}", headers=headers)

def test_update_depot_partial(client, record):
    """Test partial depot update"""
    token = get_auth_token(client)
    if not token:
        record("update_depot_partial_auth_failed", False, "Could not get auth token")
        return
    
    # Create a test depot first
    depot_id = create_test_depot(client, token, "Partial Update Test Depot")
    if not depot_id:
        record("update_depot_partial_create_failed", False, "Could not create test depot")
        return
    
    update_data = {
        "name": "Partially Updated Depot"
        # Only updating name
    }
    
    headers = get_auth_headers(token)
    response = client.put(f"{API_BASE}/depots/{depot_id}", json=update_data, headers=headers)
    
    record("update_depot_partial", response.status_code == 200, f"status={response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        record("update_depot_partial_name", data.get("name") == "Partially Updated Depot", f"name={data.get('name')}")
        # Verify other fields remain unchanged
        record("update_depot_partial_city_unchanged", data.get("city") == "Test City", f"city={data.get('city')}")
    
    # Clean up - delete the test depot
    client.delete(f"{API_BASE}/depots/{depot_id}", headers=headers)

def test_update_depot_not_found(client, record):
    """Test depot update with non-existent ID"""
    token = get_auth_token(client)
    if not token:
        record("update_depot_not_found_auth_failed", False, "Could not get auth token")
        return
    
    non_existent_id = "00000000-0000-0000-0000-000000000999"
    update_data = {"name": "Updated Depot"}
    
    headers = get_auth_headers(token)
    response = client.put(f"{API_BASE}/depots/{non_existent_id}", json=update_data, headers=headers)
    
    record("update_depot_not_found", response.status_code == 404, f"status={response.status_code}")

def test_update_depot_invalid_data(client, record):
    """Test depot update with invalid data"""
    token = get_auth_token(client)
    if not token:
        record("update_depot_invalid_data_auth_failed", False, "Could not get auth token")
        return
    
    # Create a test depot first
    depot_id = create_test_depot(client, token, "Invalid Data Test Depot")
    if not depot_id:
        record("update_depot_invalid_data_create_failed", False, "Could not create test depot")
        return
    
    update_data = {
        "latitude": 200.0,  # Invalid latitude
        "longitude": -74.0060
    }
    
    headers = get_auth_headers(token)
    response = client.put(f"{API_BASE}/depots/{depot_id}", json=update_data, headers=headers)
    
    record("update_depot_invalid_data", response.status_code == 400, f"status={response.status_code}")
    
    # Clean up - delete the test depot
    client.delete(f"{API_BASE}/depots/{depot_id}", headers=headers)

def test_update_depot_unauthorized(client, record):
    """Test depot update without authentication"""
    update_data = {"name": "Updated Depot"}
    fake_depot_id = "00000000-0000-0000-0000-000000000999"
    
    response = client.put(f"{API_BASE}/depots/{fake_depot_id}", json=update_data)
    
    record("update_depot_unauthorized", response.status_code == 403, f"status={response.status_code}")

# -----------------------------
# Delete Depot Tests
# -----------------------------

def test_delete_depot_success(client, record):
    """Test successful depot deletion"""
    token = get_auth_token(client)
    if not token:
        record("delete_depot_auth_failed", False, "Could not get auth token")
        return
    
    # Create a test depot first
    depot_id = create_test_depot(client, token, "Delete Test Depot")
    if not depot_id:
        record("delete_depot_create_failed", False, "Could not create test depot")
        return
    
    headers = get_auth_headers(token)
    response = client.delete(f"{API_BASE}/depots/{depot_id}", headers=headers)
    
    record("delete_depot_success", response.status_code == 200, f"status={response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        record("delete_depot_message", "message" in data, f"response={data}")
        
        # Verify the depot is actually deleted
        verify_response = client.get(f"{API_BASE}/depots/{depot_id}", headers=headers)
        record("delete_depot_verify_deletion", verify_response.status_code == 404, f"verify_status={verify_response.status_code}")

def test_delete_depot_not_found(client, record):
    """Test depot deletion with non-existent ID"""
    token = get_auth_token(client)
    if not token:
        record("delete_depot_not_found_auth_failed", False, "Could not get auth token")
        return
    
    non_existent_id = "00000000-0000-0000-0000-000000000999"
    headers = get_auth_headers(token)
    response = client.delete(f"{API_BASE}/depots/{non_existent_id}", headers=headers)
    
    record("delete_depot_not_found", response.status_code == 404, f"status={response.status_code}")

def test_delete_depot_unauthorized(client, record):
    """Test depot deletion without authentication"""
    fake_depot_id = "00000000-0000-0000-0000-000000000999"
    response = client.delete(f"{API_BASE}/depots/{fake_depot_id}")
    
    record("delete_depot_unauthorized", response.status_code == 403, f"status={response.status_code}")

# -----------------------------
# Integration Tests
# -----------------------------

def test_depot_crud_workflow(client, record):
    """Test complete CRUD workflow for depot"""
    token = get_auth_token(client)
    if not token:
        record("workflow_auth_failed", False, "Could not get auth token")
        return
    
    headers = get_auth_headers(token)
    
    # 1. Create depot
    create_data = {
        "agency_id": TEST_AGENCY_ID,
        "name": "Workflow Depot",
        "city": "Workflow City",
        "latitude": 40.7128,
        "longitude": -74.0060
    }
    
    create_response = client.post(f"{API_BASE}/depots/", json=create_data, headers=headers)
    record("workflow_create", create_response.status_code == 200, f"create_status={create_response.status_code}")
    
    if create_response.status_code != 200:
        return
    
    depot_id = create_response.json()["id"]
    
    # 2. Read depot
    read_response = client.get(f"{API_BASE}/depots/{depot_id}", headers=headers)
    record("workflow_read", read_response.status_code == 200, f"read_status={read_response.status_code}")
    
    # 3. Update depot
    update_data = {"name": "Updated Workflow Depot", "address": "456 Workflow Street"}
    update_response = client.put(f"{API_BASE}/depots/{depot_id}", json=update_data, headers=headers)
    record("workflow_update", update_response.status_code == 200, f"update_status={update_response.status_code}")
    
    # 4. Delete depot
    delete_response = client.delete(f"{API_BASE}/depots/{depot_id}", headers=headers)
    record("workflow_delete", delete_response.status_code == 200, f"delete_status={delete_response.status_code}")
    
    # 5. Verify deletion
    verify_response = client.get(f"{API_BASE}/depots/{depot_id}", headers=headers)
    record("workflow_verify_deletion", verify_response.status_code == 404, f"verify_status={verify_response.status_code}")

def test_depot_validation_coordinates(client, record):
    """Test coordinate validation for depot creation"""
    token = get_auth_token(client)
    if not token:
        record("validation_auth_failed", False, "Could not get auth token")
        return
    
    headers = get_auth_headers(token)
    
    test_cases = [
        ("valid_coordinates", {"latitude": 40.7128, "longitude": -74.0060}, 200),
        ("invalid_latitude_high", {"latitude": 91.0, "longitude": -74.0060}, 400),
        ("invalid_latitude_low", {"latitude": -91.0, "longitude": -74.0060}, 400),
        ("invalid_longitude_high", {"latitude": 40.7128, "longitude": 181.0}, 400),
        ("invalid_longitude_low", {"latitude": 40.7128, "longitude": -181.0}, 400),
    ]
    
    for test_name, coords, expected_status in test_cases:
        depot_data = {
            "agency_id": TEST_AGENCY_ID,
            "name": f"Test Depot {test_name}",
            **coords
        }
        
        response = client.post(f"{API_BASE}/depots/", json=depot_data, headers=headers)
        record(f"validation_{test_name}", response.status_code == expected_status, f"status={response.status_code}")