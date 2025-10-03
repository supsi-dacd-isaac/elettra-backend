"""Pytest-based authentication tests (synchronous version).
Run with: pytest -k auth
Generates a human-readable report in tests/reports/ via report_collector fixture.
"""

import os
import uuid
import time
import asyncio
import pytest
from jose import jwt
from datetime import datetime, timedelta, UTC
from fastapi.testclient import TestClient

from app.core.config import get_cached_settings
# Direct DB cleanup via asyncpg to avoid event-loop conflicts

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

# -----------------------------
# Temp user helpers (ensure no DB garbage)
# -----------------------------
STRONG_PASSWORD = "Tmp!Passw0rdXy"


def _register_temp_user(client: TestClient, *, password: str = "Tmp!Passw0rdXy") -> tuple[str, str]:
    """Create a temporary user for mutation tests and return (email, password)."""
    email = f"tmp_{uuid.uuid4().hex[:10]}@example.com"
    valid_company_id = os.getenv("TEST_AGENCY_ID")
    if not valid_company_id:
        raise ValueError("TEST_AGENCY_ID environment variable is required")
    r = client.post(f"{AUTH_BASE}/register", json={
        "company_id": valid_company_id,
        "email": email,
        "full_name": "Temp User",
        "password": password,
        "role": "viewer",
    })
    # 200 = created, 400 = already exists (shouldn't happen with random email)
    assert r.status_code == 200, f"temp user register failed: status={r.status_code} body={r.text}"
    return email, password


def _dsn_for_asyncpg() -> str:
    dsn = settings.database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return dsn


async def _delete_user_async(email: str) -> None:
    import asyncpg
    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        await conn.execute("DELETE FROM users WHERE email = $1", email)
    finally:
        await conn.close()


def _delete_user(email: str) -> None:
    asyncio.run(_delete_user_async(email))

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
    # Use a protected endpoint to verify auth enforcement
    r = client.get(f"{API_BASE}/user/buses/")
    record("access_no_token", r.status_code in (401, 403), f"status={r.status_code}")

def test_access_with_invalid_token(client, record):
    r = client.get(f"{API_BASE}/user/buses/", headers={"Authorization": "Bearer invalid"})
    record("access_invalid_token", r.status_code == 401, f"status={r.status_code}")

@pytest.mark.parametrize("auth_header", [
    "just_a_token",
    "Basic abcdef",
    "Bearer ",
    "Bearertoken",
])
def test_malformed_auth_headers(client, record, auth_header):
    r = client.get(f"{API_BASE}/user/buses/", headers={"Authorization": auth_header})
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
    r = client.get(f"{API_BASE}/user/buses/", headers={"Authorization": f"Bearer {token}"})
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
    email = os.getenv("TEST_LOGIN_EMAIL")
    password = os.getenv("TEST_LOGIN_PASSWORD")
    if not email or not password:
        pytest.skip("TEST_LOGIN_EMAIL and TEST_LOGIN_PASSWORD environment variables are required")
    
    login_data = {
        "email": email,
        "password": password
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

# -----------------------------
# Password Validation Tests
# -----------------------------

def test_register_weak_password(client, record):
    """Test registration with weak password"""
    # Use a valid company_id from the database
    valid_company_id = os.getenv("TEST_AGENCY_ID")
    if not valid_company_id:
        pytest.skip("TEST_AGENCY_ID environment variable is required")
    
    weak_passwords = [
        "Short1!a",  # Too short
        "nouppercase123!a",  # No uppercase letters
        "NOLOWERCASE123!",  # No lowercase letters
        "NoDigits!!!!aa",  # No digits
        "NoSpecial1234a",  # No special characters
        "ValidPassw0rd!123",  # Contains sequential characters
        "ReeelllyStrong!!!",  # Contains repeated characters
        "Password123!",  # Common password pattern
    ]
    
    for weak_password in weak_passwords:
        r = client.post(f"{AUTH_BASE}/register", json={
            "company_id": valid_company_id,
            "email": f"test{weak_password}@example.com",
            "full_name": "Test User",
            "password": weak_password,
            "role": "viewer"
        })
        record(f"register_weak_password_{weak_password[:10]}", r.status_code == 422, f"status={r.status_code}")

def test_register_strong_password(client, record):
    """Test registration with strong password"""
    # Use a valid company_id from the database
    valid_company_id = os.getenv("TEST_AGENCY_ID")
    if not valid_company_id:
        pytest.skip("TEST_AGENCY_ID environment variable is required")
    temp_email = f"strong_{uuid.uuid4().hex[:10]}@example.com"
    try:
        r = client.post(f"{AUTH_BASE}/register", json={
            "company_id": valid_company_id,
            "email": temp_email,
            "full_name": "Test User",
            "password": "Str0ng!Passw@rdX",
            "role": "viewer"
        })
        record("register_strong_password", r.status_code == 200, f"status={r.status_code}")
    finally:
        _delete_user(temp_email)

def test_update_password_weak(client, record):
    """Test password update with weak password using a temporary user"""
    # Create temp user to avoid mutating shared credentials
    email, current_password = _register_temp_user(client, password="Tmp!Passw0rdXy")
    try:
        # Login as temp user
        login = client.post(f"{AUTH_BASE}/login", json={"email": email, "password": current_password})
        assert login.status_code == 200, f"temp login failed: {login.text}"
        token = login.json().get("access_token")

        weak_passwords = [
            "Short1!a",  # Too short
            "nouppercase123!a",  # No uppercase letters
            "NOLOWERCASE123!",  # No lowercase letters
            "NoDigits!!!!aa",  # No digits
            "NoSpecial1234a",  # No special characters
            "ValidPassw0rd!123",  # Contains sequential characters
            "ReeelllyStrong!!!",  # Contains repeated characters
            "Password123!",  # Common password pattern
        ]

        for weak_password in weak_passwords:
            r = client.put(f"{AUTH_BASE}/me/password",
                           json={"current_password": current_password, "new_password": weak_password},
                           headers=auth_headers(token))
            record(f"update_password_weak_{weak_password[:10]}", r.status_code == 422, f"status={r.status_code}")
    finally:
        _delete_user(email)

def test_update_password_strong(client, record):
    """Test password update with strong password using a temporary user"""
    # Create temp user to avoid mutating shared credentials
    email, current_password = _register_temp_user(client, password="Tmp!Passw0rdXy")
    try:
        # Login as temp user
        login = client.post(f"{AUTH_BASE}/login", json={"email": email, "password": current_password})
        assert login.status_code == 200, f"temp login failed: {login.text}"
        token = login.json().get("access_token")

        r = client.put(f"{AUTH_BASE}/me/password",
                       json={"current_password": current_password, "new_password": "N3w!Str0ngPasswrd"},
                       headers=auth_headers(token))
        record("update_password_strong", r.status_code == 200, f"status={r.status_code}")
    finally:
        _delete_user(email)

def test_update_profile(client, record):
    """Test user profile update using a temporary user (no mutation of seeded users)."""
    # Create temp user
    email, password = _register_temp_user(client, password=STRONG_PASSWORD)
    try:
        # Login as temp user
        login = client.post(f"{AUTH_BASE}/login", json={"email": email, "password": password})
        assert login.status_code == 200, f"temp login failed: {login.text}"
        token = login.json().get("access_token")

        r = client.put(
            f"{AUTH_BASE}/me",
            json={"full_name": "Updated Name", "email": f"updated_{uuid.uuid4().hex[:6]}@example.com"},
            headers=auth_headers(token),
        )
        record("update_profile", r.status_code in [200, 400], f"status={r.status_code}")
    finally:
        _delete_user(email)


def test_delete_me_ephemeral_user(client, record):
    """Create a temp user, delete via DELETE /auth/me, ensure it's gone."""
    # Register temp user
    email, password = _register_temp_user(client, password=STRONG_PASSWORD)
    # Login as temp user
    login = client.post(f"{AUTH_BASE}/login", json={"email": email, "password": password})
    assert login.status_code == 200, f"temp login failed: {login.text}"
    token = login.json().get("access_token")

    # Delete self
    r = client.delete(f"{AUTH_BASE}/me", headers=auth_headers(token))
    record("delete_me_status", r.status_code == 204, f"status={r.status_code} body={r.text}")

    # Verify user cannot login anymore
    relog = client.post(f"{AUTH_BASE}/login", json={"email": email, "password": password})
    record("delete_me_login_blocked", relog.status_code == 401, f"status={relog.status_code}")

def test_check_email_availability(client, record):
    """Test email availability check endpoint"""
    # Test with non-existent email
    r = client.get(f"{AUTH_BASE}/check-email/nonexistent@example.com")
    record("check_email_nonexistent", r.status_code == 200, f"status={r.status_code}")
    if r.status_code == 200:
        data = r.json()
        record("check_email_nonexistent_available", data.get("available") == True, f"data={data}")
    
    # Test with existing email (use test user email)
    existing_email = os.getenv("TEST_LOGIN_EMAIL")
    if existing_email:
        r = client.get(f"{AUTH_BASE}/check-email/{existing_email}")
        record("check_email_existing", r.status_code == 200, f"status={r.status_code}")
        if r.status_code == 200:
            data = r.json()
            record("check_email_existing_not_available", data.get("available") == False, f"data={data}")

def test_check_email_availability_with_temp_user(client, record):
    """Test email availability check with temporary user"""
    # Create temp user
    email, password = _register_temp_user(client, password=STRONG_PASSWORD)
    try:
        # Check that email is now unavailable
        r = client.get(f"{AUTH_BASE}/check-email/{email}")
        record("check_email_temp_user", r.status_code == 200, f"status={r.status_code}")
        if r.status_code == 200:
            data = r.json()
            record("check_email_temp_user_not_available", data.get("available") == False, f"data={data}")
    finally:
        _delete_user(email)
