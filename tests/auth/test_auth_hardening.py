"""Focused regressions for public auth and agency account management."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.auth import require_admin
from app.core.rate_limit import SlidingWindowRateLimiter, _client_address
from app.schemas.auth import UserRegister, validate_password_strength
from app.schemas.database import UsersCreate, UsersUpdate
from main import app


def _route(method: str, path: str):
    for route in app.routes:
        if path == getattr(route, "path", None) and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"Missing route {method} {path}")


def test_repeated_password_characters_are_rejected() -> None:
    with pytest.raises(ValueError, match="repeated characters"):
        validate_password_strength("Safeee!Passw0rd")


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            UsersCreate,
            {
                "company_id": str(uuid4()),
                "email": "new@example.com",
                "full_name": "New user",
                "password": "Valid!Passw0rdXy",
                "password_hash": "caller-controlled",
            },
        ),
        (UsersUpdate, {"company_id": str(uuid4())}),
        (UsersUpdate, {"password_hash": "caller-controlled"}),
        (UsersUpdate, {"created_at": "2026-01-01T00:00:00Z"}),
        (
            UserRegister,
            {
                "company_id": str(uuid4()),
                "email": "new@example.com",
                "full_name": "New user",
                "password": "Valid!Passw0rdXy",
                "role": "admin",
            },
        ),
    ],
)
def test_account_contracts_reject_internal_or_authorization_fields(schema, payload) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_admin_create_contract_defaults_to_analyst() -> None:
    account = UsersCreate.model_validate(
        {
            "company_id": str(uuid4()),
            "email": "  New.User@Example.COM ",
            "full_name": "New user",
            "password": "Valid!Passw0rdXy",
        }
    )
    assert account.role.value == "analyst"


def test_agency_creation_requires_admin() -> None:
    dependencies = {
        dependency.call
        for dependency in _route("POST", "/api/v1/agency/agencies/").dependant.dependencies
    }
    assert require_admin in dependencies


def test_sliding_window_limiter_rejects_and_then_expires() -> None:
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=10)
    limiter.check_key("client", now=100.0)
    limiter.check_key("client", now=101.0)
    with pytest.raises(HTTPException) as exc:
        limiter.check_key("client", now=102.0)
    assert exc.value.status_code == 429
    assert exc.value.headers == {"Retry-After": "9"}

    limiter.check_key("client", now=111.0)


@pytest.mark.parametrize(
    ("peer", "forwarded", "expected"),
    [
        ("127.0.0.1", "203.0.113.8", "203.0.113.8"),
        ("10.0.0.2", "203.0.113.9", "203.0.113.9"),
        ("8.8.8.8", "203.0.113.10", "8.8.8.8"),
        ("8.8.8.8", "invalid", "8.8.8.8"),
    ],
)
def test_limiter_only_trusts_proxy_address_from_private_peer(peer, forwarded, expected) -> None:
    request = SimpleNamespace(
        client=SimpleNamespace(host=peer),
        headers={"x-real-ip": forwarded},
    )
    assert _client_address(request) == expected


def test_email_probe_returns_identical_generic_response(client) -> None:
    existing = os.environ["TEST_LOGIN_EMAIL"]
    missing = f"missing-{uuid4().hex}@example.com"
    existing_response = client.get(f"/auth/check-email/{existing}")
    missing_response = client.get(f"/auth/check-email/{missing}")

    assert existing_response.status_code == missing_response.status_code == 200
    assert existing_response.json() == missing_response.json() == {"status": "accepted"}


def test_login_email_is_case_insensitive(client) -> None:
    email = os.environ["TEST_LOGIN_EMAIL"]
    password = os.environ["TEST_LOGIN_PASSWORD"]
    response = client.post(
        "/auth/login",
        json={"email": f"  {email.upper()}  ", "password": password},
    )
    assert response.status_code == 200, response.text


def test_registration_duplicate_check_is_case_insensitive(client, monkeypatch) -> None:
    async def _not_breached(_password: str) -> None:
        return None

    monkeypatch.setattr("app.routers.auth.validate_password_breach", _not_breached)
    response = client.post(
        "/auth/register",
        json={
            "company_id": os.environ["TEST_AGENCY_ID"],
            "email": os.environ["TEST_LOGIN_EMAIL"].upper(),
            "full_name": "Duplicate user",
            "password": "Unique!Passw0rdXy",
        },
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Registration could not be completed"}
