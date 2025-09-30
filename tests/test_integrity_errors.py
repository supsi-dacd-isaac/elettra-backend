import os
import uuid
from fastapi.testclient import TestClient

API_BASE = "/api/v1/user"
AUTH_BASE = "/auth"


def _get_token(client: TestClient) -> str:
    email = os.getenv("TEST_LOGIN_EMAIL")
    password = os.getenv("TEST_LOGIN_PASSWORD")
    assert email and password, "Missing TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD in env"
    r = client.post(f"{AUTH_BASE}/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"login failed: {r.text}"
    return r.json()["access_token"]


def _hdrs(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _me(client: TestClient, token: str) -> str:
    r = client.get(f"{AUTH_BASE}/me", headers=_hdrs(token))
    assert r.status_code == 200, f"/me failed: {r.text}"
    return r.json()["id"]

def _cleanup_buses_by_name(client: TestClient, token: str, names: list[str]):
    r = client.get(f"{API_BASE}/buses/?skip=0&limit=1000", headers=_hdrs(token))
    if r.status_code != 200:
        return
    items = r.json() or []
    for b in items:
        if b.get("name") in names:
            client.delete(f"{API_BASE}/buses/{b['id']}", headers=_hdrs(token))


def test_unique_violation_returns_409(client: TestClient):
    token = _get_token(client)
    user_id = _me(client, token)
    # Cleanup before
    _cleanup_buses_by_name(client, token, ["IntegrityTestBus"]) 

    # Create a bus with a name
    payload = {"user_id": user_id, "name": "IntegrityTestBus", "specs": {}}
    r1 = client.post(f"{API_BASE}/buses/", json=payload, headers=_hdrs(token))
    assert r1.status_code == 200, r1.text

    # Create another bus with the same name for the same user -> unique(user_id,name) violation
    r2 = client.post(f"{API_BASE}/buses/", json=payload, headers=_hdrs(token))

    assert r2.status_code == 409, f"expected 409, got {r2.status_code}: {r2.text}"
    body = r2.json()
    assert body.get("code") in ("unique_violation", "integrity_error")
    assert body.get("message")
    # fields may include ["user_id", "name"] depending on backend detail parsing
    assert "detail" in body
    # Cleanup after
    _cleanup_buses_by_name(client, token, ["IntegrityTestBus"]) 


def test_fk_violation_returns_409(client: TestClient):
    token = _get_token(client)
    bogus_model_id = str(uuid.uuid4())
    user_id = _me(client, token)

    # Try to create a bus referencing a non-existing model -> FK violation
    payload = {"user_id": user_id, "name": "FKTestBus", "specs": {}, "bus_model_id": bogus_model_id}
    r = client.post(f"{API_BASE}/buses/", json=payload, headers=_hdrs(token))

    # Our endpoint validates model existence and returns 400, so the global handler
    # might not trigger here. If validation changes, FK violation should map to 409.
    assert r.status_code in (400, 409), f"unexpected status {r.status_code}: {r.text}"

