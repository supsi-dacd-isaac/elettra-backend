"""Tests for /api/v1/gtfs/gtfs-trips/by-route/{route_id}

Environment prerequisites to run these tests end-to-end:
- TEST_API_TOKEN: A valid Bearer token for the API
- TEST_ROUTE_ID: A UUID of an existing route in the database

If variables are missing, tests are skipped.
"""

import os
import pytest

__report_module__ = "gtfs_trips"

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


def _route_id_env() -> str | None:
    return os.getenv("TEST_ROUTE_ID")


@pytest.mark.skipif(
    not (_route_id_env() and (os.getenv("TEST_API_TOKEN") or (os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD")))),
    reason="Provide TEST_ROUTE_ID and either TEST_API_TOKEN or TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD",
)
def test_trips_by_route_invalid_day_400(client, record):
    route_id = _route_id_env()
    url = f"{API_BASE}/gtfs-trips/by-route/{route_id}?day_of_week=notaday"
    r = client.get(url, headers=_auth_headers(client))
    record("trips_invalid_day_400", r.status_code == 400, f"status={r.status_code} body={r.text}")


@pytest.mark.skipif(
    not (_route_id_env() and (os.getenv("TEST_API_TOKEN") or (os.getenv("TEST_LOGIN_EMAIL") and os.getenv("TEST_LOGIN_PASSWORD")))),
    reason="Provide TEST_ROUTE_ID and either TEST_API_TOKEN or TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD",
)
@pytest.mark.parametrize("day", ["monday"])  # Add more days if desired
def test_trips_by_route_filter_subset(client, record, day):
    route_id = _route_id_env()
    base_url = f"{API_BASE}/gtfs-trips/by-route/{route_id}"

    # No filter
    r_all = client.get(base_url, headers=_auth_headers(client))
    if r_all.status_code != 200:
        record("trips_all_fetch", False, f"status={r_all.status_code} body={r_all.text}")
        return
    all_ids = {t["id"] for t in r_all.json()}

    # With day filter
    r_day = client.get(f"{base_url}?day_of_week={day}", headers=_auth_headers(client))
    if r_day.status_code != 200:
        record("trips_day_fetch", False, f"status={r_day.status_code} body={r_day.text}")
        return
    day_ids = {t["id"] for t in r_day.json()}

    # Expect: filtered set is subset of the unfiltered set (could be equal or smaller)
    is_subset = day_ids.issubset(all_ids)
    record(
        f"trips_subset_{day}",
        is_subset,
        f"filtered_not_subset: filtered={len(day_ids)} all={len(all_ids)}",
    )


