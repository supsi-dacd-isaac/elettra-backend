"""Pytest-based authentication tests (synchronous version).
Run with: pytest -k auth
Generates a human-readable report in tests/reports/ via report_collector fixture.
"""

import pytest
from jose import jwt
from datetime import datetime, timedelta, UTC

from app.core.config import get_cached_settings

__report_module__ = "auth"  # used by report_collector for report filename
settings = get_cached_settings()

BASE = "/"
AUTH_BASE = "/auth"
API_BASE = "/api/v1"

# -----------------------------
# Helper
# -----------------------------

def _expired_token():
    payload = {"sub": "fake-user-id", "exp": datetime.now(UTC) - timedelta(minutes=1)}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)

# -----------------------------
# Tests (sync via TestClient)
# -----------------------------

def test_health(client, record):
    r = client.get(BASE)
    record("health", r.status_code == 200, f"status={r.status_code}")

def test_health_check_endpoint(client, record):
    """Test the dedicated health check endpoint"""
    r = client.get("/health")
    record("health_check_endpoint", r.status_code == 200, f"status={r.status_code}")
    
    if r.status_code == 200:
        data = r.json()
        record("health_check_status", data.get("status") == "healthy", f"status={data.get('status')}")
        record("health_check_services", "services" in data, "services field missing")
        record("health_check_database", "database" in data.get("services", {}), "database service missing")
        record("health_check_uptime", "uptime_seconds" in data, "uptime_seconds missing")

def test_login_missing_body(client, record):
    r = client.post(f"{AUTH_BASE}/login", json={})
    record("login_missing_body", r.status_code == 422, f"status={r.status_code}")

def test_login_invalid_credentials(client, record):
    r = client.post(f"{AUTH_BASE}/login", json={"email": "no@user", "password": "bad"})
    record("login_bad_creds", r.status_code == 401, f"status={r.status_code}")

def test_login_malformed_email(client, record):
    r = client.post(f"{AUTH_BASE}/login", json={"email": "not-an-email", "password": "x"})
    record("login_malformed_email", r.status_code in (401, 422), f"status={r.status_code}")

def test_access_without_token(client, record):
    r = client.get(f"{API_BASE}/agency/agencies/")
    record("access_no_token", r.status_code in (401, 403), f"status={r.status_code}")

def test_access_with_invalid_token(client, record):
    r = client.get(f"{API_BASE}/agency/agencies/", headers={"Authorization": "Bearer invalid"})
    record("access_invalid_token", r.status_code == 401, f"status={r.status_code}")

@pytest.mark.parametrize("auth_header", [
    "just_a_token",
    "Basic abcdef",
    "Bearer ",
    "Bearertoken",
])
def test_malformed_auth_headers(client, record, auth_header):
    r = client.get(f"{API_BASE}/agency/agencies/", headers={"Authorization": auth_header})
    record(f"malformed_header:{auth_header}", r.status_code in (401, 403), f"status={r.status_code}")

def test_register_invalid_payloads(client, record):
    cid = "00000000-0000-0000-0000-000000000001"
    cases = [
        ("missing_email", {"company_id": cid, "full_name": "X", "password": "p", "role": "viewer"}),
        ("invalid_email", {"company_id": cid, "email": "bad", "full_name": "X", "password": "p", "role": "viewer"}),
        ("missing_password", {"company_id": cid, "email": "x@example.com", "full_name": "X", "role": "viewer"}),
        ("invalid_company", {"company_id": "not-a-uuid", "email": "x@example.com", "full_name": "X", "password": "p", "role": "viewer"}),
    ]
    for name, payload in cases:
        r = client.post(f"{AUTH_BASE}/register", json=payload)
        record(f"register_{name}", r.status_code == 422, f"status={r.status_code}")

def test_expired_token(client, record):
    token = _expired_token()
    r = client.get(f"{API_BASE}/agency/agencies/", headers={"Authorization": f"Bearer {token}"})
    record("expired_token", r.status_code == 401, f"status={r.status_code}")

def test_swagger_available(client, record):
    r = client.get("/docs")
    record("swagger_ui", r.status_code == 200 and b"swagger" in r.content.lower(), f"status={r.status_code}")

def test_openapi_has_auth_paths(client, record):
    r = client.get("/openapi.json")
    if r.status_code != 200:
        record("openapi_fetch", False, f"status={r.status_code}")
        return
    spec = r.json()
    paths = spec.get("paths", {})
    needed = {"/auth/login", "/auth/register", "/auth/me", "/auth/logout"}
    missing = needed - set(paths.keys())
    record("openapi_auth_paths", not missing, f"missing={missing}")

def test_logout_without_token(client, record):
    r = client.post(f"{AUTH_BASE}/logout")
    record("logout_no_token", r.status_code in (401, 403), f"status={r.status_code}")

def test_logout_with_invalid_token(client, record):
    r = client.post(f"{AUTH_BASE}/logout", headers={"Authorization": "Bearer invalid"})
    record("logout_invalid_token", r.status_code == 401, f"status={r.status_code}")

def test_logout_with_valid_token(client, record):
    # First login to get a valid token
    login_data = {
        "email": "test01.elettra@fart.ch",
        "password": "elettra"
    }
    login_r = client.post(f"{AUTH_BASE}/login", json=login_data)
    if login_r.status_code != 200:
        record("logout_login_failed", False, f"login_status={login_r.status_code}")
        return
    
    token = login_r.json().get("access_token")
    if not token:
        record("logout_no_token_from_login", False, "no token in login response")
        return
    
    # Now test logout with valid token
    logout_r = client.post(f"{AUTH_BASE}/logout", headers={"Authorization": f"Bearer {token}"})
    record("logout_valid_token", logout_r.status_code == 200, f"status={logout_r.status_code}")
    
    if logout_r.status_code == 200:
        data = logout_r.json()
        record("logout_response_format", "message" in data, f"response={data}")

@pytest.mark.parametrize("injection", [
    "admin@example.com'; DROP TABLE users; --",
    "admin@example.com' OR '1'='1",
    "admin@example.com' UNION SELECT * FROM users --",
])
def test_sql_injection_attempts(client, record, injection):
    r = client.post(f"{AUTH_BASE}/login", json={"email": injection, "password": "x"})
    record(f"sql_injection:{injection[:15]}", r.status_code == 401, f"status={r.status_code}")
