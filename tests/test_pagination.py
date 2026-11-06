"""End-to-end tests for the new paginated list endpoints.

Covers:
- shape of the response envelope (items/total/skip/limit/count/has_next/has_previous)
- default ``limit = 20``
- accepted limits 20, 50, 100
- invalid limits (e.g. 33, 0, 25, "abc") are rejected with 422
- skip < 0 rejected
- ``total`` reflects the full filtered query (constant across pages)
- ``has_next`` / ``has_previous`` correctness across pages
- existing filters (``optimization_run_id``, ``bus_id``) keep working
- shift search is applied before counting and pagination, including matches
  beyond the first page and assigned bus-model names
- shift search remains user-scoped and composes with the existing ``bus_id``
  filter without changing requests that omit ``search``
- list payload no longer carries the heavy ``features`` / ``results`` /
  ``input_params`` blobs

Run with: ``./run_tests.sh tests/test_pagination.py``
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, Iterable

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_cached_settings

__report_module__ = "pagination"

AUTH_BASE = "/auth"
USER_BASE = "/api/v1/user"
GTFS_BASE = "/api/v1/gtfs"
SIM_BASE = "/api/v1/simulation"
YA_BASE = "/api/v1/yearly-analysis"

TEST_LOGIN_EMAIL = os.getenv("TEST_LOGIN_EMAIL", "test01.elettra@fart.ch")
TEST_LOGIN_PASSWORD = os.getenv("TEST_LOGIN_PASSWORD", "elettra")
TEST_AGENCY_ID = os.getenv("TEST_AGENCY_ID")
TMP_PASSWORD = "Tmp!Passw0rdXy"

settings = get_cached_settings()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENVELOPE_KEYS = (
    "items",
    "total",
    "skip",
    "limit",
    "count",
    "has_next",
    "has_previous",
)


def _dsn_for_asyncpg() -> str:
    dsn = settings.database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return dsn


async def _async_first_agency_id() -> str | None:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        agency_id = await conn.fetchval(
            "SELECT id FROM gtfs_agencies ORDER BY id LIMIT 1"
        )
        return str(agency_id) if agency_id else None
    finally:
        await conn.close()


def _login(client: TestClient, email: str, password: str) -> str | None:
    r = client.post(f"{AUTH_BASE}/login", json={"email": email, "password": password})
    if r.status_code != 200:
        return None
    return r.json().get("access_token")


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login_default(client: TestClient) -> str | None:
    return _login(client, TEST_LOGIN_EMAIL, TEST_LOGIN_PASSWORD)


def _register_temp_user(client: TestClient, *, prefix: str) -> dict:
    agency_id = TEST_AGENCY_ID or asyncio.run(_async_first_agency_id())
    if not agency_id:
        pytest.skip("A test agency is required to register an isolated user")
    email = f"{prefix}_{uuid.uuid4().hex[:10]}@example.com"
    response = client.post(
        f"{AUTH_BASE}/register",
        json={
            "company_id": agency_id,
            "email": email,
            "full_name": "Pagination Test User",
            "password": TMP_PASSWORD,
        },
    )
    assert response.status_code == 200, (
        f"temp user register failed: status={response.status_code} body={response.text}"
    )
    data = response.json()
    return {"id": data["id"], "email": email, "password": TMP_PASSWORD}


def _assert_envelope(record, name_prefix: str, body: Any) -> None:
    record(
        f"{name_prefix}_is_object",
        isinstance(body, dict),
        f"type={type(body)}",
    )
    if not isinstance(body, dict):
        return
    for key in ENVELOPE_KEYS:
        record(
            f"{name_prefix}_has_{key}",
            key in body,
            f"keys={list(body.keys())}",
        )
    record(
        f"{name_prefix}_items_is_list",
        isinstance(body.get("items"), list),
        f"items_type={type(body.get('items'))}",
    )
    if isinstance(body.get("items"), list):
        record(
            f"{name_prefix}_count_matches_items_len",
            body.get("count") == len(body["items"]),
            f"count={body.get('count')}, items_len={len(body['items'])}",
        )


# ---------------------------------------------------------------------------
# Cross-endpoint parameter validation (tested on each endpoint)
# ---------------------------------------------------------------------------


PAGINATED_LIST_URLS = [
    f"{USER_BASE}/bus-models/",
    f"{USER_BASE}/shifts/",
    f"{GTFS_BASE}/gtfs-stops/",
    f"{SIM_BASE}/optimization-runs/",
    f"{YA_BASE}/",
]


@pytest.fixture(scope="module")
def auth_token(client):
    token = _login_default(client)
    temp_user_id = None
    if not token:
        user = _register_temp_user(client, prefix="tmp_pagination_auth")
        temp_user_id = user["id"]
        token = _login(client, user["email"], user["password"])
    if not token:
        pytest.fail("Could not authenticate an isolated pagination test user")
    yield token
    if temp_user_id:
        asyncio.run(_async_cleanup([], [], [temp_user_id]))


# --- Default limit ---------------------------------------------------------


def test_default_limit_is_20_for_each_endpoint(client: TestClient, auth_token, record):
    for url in PAGINATED_LIST_URLS:
        r = client.get(url, headers=_headers(auth_token))
        record(
            f"default_limit_{url}_status",
            r.status_code == 200,
            f"status={r.status_code} body={r.text[:200]}",
        )
        if r.status_code == 200:
            body = r.json()
            record(
                f"default_limit_{url}_envelope",
                isinstance(body, dict) and set(ENVELOPE_KEYS).issubset(body.keys()),
                f"keys={list(body.keys()) if isinstance(body, dict) else None}",
            )
            record(
                f"default_limit_{url}_is_20",
                isinstance(body, dict) and body.get("limit") == 20,
                f"limit={body.get('limit') if isinstance(body, dict) else None}",
            )


# --- Allowed limits --------------------------------------------------------


@pytest.mark.parametrize("limit", [20, 50, 100])
def test_allowed_limits_accepted(client: TestClient, auth_token, record, limit):
    for url in PAGINATED_LIST_URLS:
        r = client.get(f"{url}?limit={limit}", headers=_headers(auth_token))
        record(
            f"limit_{limit}_{url}_status",
            r.status_code == 200,
            f"status={r.status_code} body={r.text[:200]}",
        )
        if r.status_code == 200:
            body = r.json()
            record(
                f"limit_{limit}_{url}_value",
                body.get("limit") == limit,
                f"limit={body.get('limit')}",
            )
            items = body.get("items", []) if isinstance(body, dict) else []
            record(
                f"limit_{limit}_{url}_items_within_bound",
                len(items) <= limit,
                f"len={len(items)}",
            )


# --- Invalid limits --------------------------------------------------------


@pytest.mark.parametrize("invalid_limit", [0, 1, 19, 25, 33, 200, -1, "abc"])
def test_invalid_limit_rejected(client: TestClient, auth_token, record, invalid_limit):
    for url in PAGINATED_LIST_URLS:
        r = client.get(f"{url}?limit={invalid_limit}", headers=_headers(auth_token))
        record(
            f"invalid_limit_{invalid_limit}_{url}",
            r.status_code == 422,
            f"status={r.status_code} body={r.text[:200]}",
        )


# --- Negative skip ---------------------------------------------------------


def test_negative_skip_rejected(client: TestClient, auth_token, record):
    for url in PAGINATED_LIST_URLS:
        r = client.get(f"{url}?skip=-1", headers=_headers(auth_token))
        record(
            f"negative_skip_{url}",
            r.status_code == 422,
            f"status={r.status_code} body={r.text[:200]}",
        )


# --- Skip works (page advances) -------------------------------------------


def test_skip_advances_within_total(client: TestClient, auth_token, record):
    """If the endpoint has at least one row, skip=1 should drop that row."""
    for url in PAGINATED_LIST_URLS:
        r0 = client.get(f"{url}?skip=0&limit=20", headers=_headers(auth_token))
        if r0.status_code != 200:
            continue
        b0 = r0.json()
        items0 = b0.get("items", [])
        if not items0:
            continue
        r1 = client.get(f"{url}?skip=1&limit=20", headers=_headers(auth_token))
        record(
            f"skip_advances_{url}_status",
            r1.status_code == 200,
            f"status={r1.status_code}",
        )
        if r1.status_code != 200:
            continue
        b1 = r1.json()
        record(
            f"skip_advances_{url}_total_constant",
            b1.get("total") == b0.get("total"),
            f"total0={b0.get('total')}, total1={b1.get('total')}",
        )
        record(
            f"skip_advances_{url}_skip_value",
            b1.get("skip") == 1,
            f"skip={b1.get('skip')}",
        )
        items1 = b1.get("items", [])
        if items1:
            first0 = items0[0].get("id") if isinstance(items0[0], dict) else None
            first1 = items1[0].get("id") if isinstance(items1[0], dict) else None
            if first0 is not None and first1 is not None:
                record(
                    f"skip_advances_{url}_drops_first",
                    first0 != first1,
                    f"first0={first0}, first1={first1}",
                )


# --- has_next / has_previous correctness ----------------------------------


def test_has_next_and_previous_consistency(client: TestClient, auth_token, record):
    for url in PAGINATED_LIST_URLS:
        r = client.get(f"{url}?skip=0&limit=20", headers=_headers(auth_token))
        if r.status_code != 200:
            continue
        body = r.json()
        total = int(body.get("total") or 0)
        count = int(body.get("count") or 0)
        record(
            f"has_previous_false_first_page_{url}",
            body.get("has_previous") is False,
            f"has_previous={body.get('has_previous')}",
        )
        expected_has_next = (count) < total
        record(
            f"has_next_first_page_{url}",
            body.get("has_next") == expected_has_next,
            f"has_next={body.get('has_next')}, count={count}, total={total}",
        )

        if total > 20:
            r2 = client.get(f"{url}?skip=20&limit=20", headers=_headers(auth_token))
            if r2.status_code == 200:
                b2 = r2.json()
                record(
                    f"has_previous_true_second_page_{url}",
                    b2.get("has_previous") is True,
                    f"has_previous={b2.get('has_previous')}",
                )

        # Page beyond the end: items=[], has_next=False, has_previous=True
        if total > 0:
            beyond = max(total, 20)
            rb = client.get(
                f"{url}?skip={beyond}&limit=20",
                headers=_headers(auth_token),
            )
            if rb.status_code == 200:
                bb = rb.json()
                record(
                    f"beyond_end_count_zero_{url}",
                    bb.get("count") == 0,
                    f"count={bb.get('count')}",
                )
                record(
                    f"beyond_end_has_next_false_{url}",
                    bb.get("has_next") is False,
                    f"has_next={bb.get('has_next')}",
                )


# --- Total constant across pages (DB-level COUNT) -------------------------


def test_total_constant_across_pages(client: TestClient, auth_token, record):
    for url in PAGINATED_LIST_URLS:
        totals = []
        for skip in (0, 20, 40):
            r = client.get(f"{url}?skip={skip}&limit=20", headers=_headers(auth_token))
            if r.status_code != 200:
                break
            totals.append(r.json().get("total"))
        if not totals:
            continue
        record(
            f"total_constant_{url}",
            len(set(totals)) == 1,
            f"totals_per_page={totals}",
        )


# --- Heavy fields are NOT in list payloads --------------------------------


def test_optimization_runs_list_excludes_heavy_blobs(
    client: TestClient, auth_token, record
):
    r = client.get(
        f"{SIM_BASE}/optimization-runs/?skip=0&limit=20",
        headers=_headers(auth_token),
    )
    record("opt_runs_status", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return
    items = r.json().get("items", [])
    record(
        "opt_runs_no_input_params",
        all("input_params" not in item for item in items),
        f"sample_keys={sorted(items[0].keys()) if items else []}",
    )
    record(
        "opt_runs_no_results",
        all("results" not in item for item in items),
        f"sample_keys={sorted(items[0].keys()) if items else []}",
    )
    if items:
        first = items[0]
        for key in ("id", "user_id", "name", "mode", "status", "created_at"):
            record(
                f"opt_runs_has_{key}",
                key in first,
                f"keys={sorted(first.keys())}",
            )


def test_yearly_analyses_list_excludes_features(client: TestClient, auth_token, record):
    r = client.get(
        f"{YA_BASE}/?skip=0&limit=20",
        headers=_headers(auth_token),
    )
    record("ya_list_status", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return
    items = r.json().get("items", [])
    record(
        "ya_list_no_features",
        all("features" not in item for item in items),
        f"sample_keys={sorted(items[0].keys()) if items else []}",
    )
    if items:
        first = items[0]
        for key in ("id", "name", "created_at", "optimization_run_id"):
            record(
                f"ya_list_has_{key}",
                key in first,
                f"keys={sorted(first.keys())}",
            )


def test_shifts_list_uses_lightweight_schema(client: TestClient, auth_token, record):
    r = client.get(
        f"{USER_BASE}/shifts/?skip=0&limit=20",
        headers=_headers(auth_token),
    )
    record("shifts_list_status", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return
    items = r.json().get("items", [])
    record(
        "shifts_list_no_structure",
        all("structure" not in item for item in items),
        f"sample_keys={sorted(items[0].keys()) if items else []}",
    )
    if items:
        first = items[0]
        for key in ("id", "name", "trip_count"):
            record(
                f"shifts_list_has_{key}",
                key in first,
                f"keys={sorted(first.keys())}",
            )


# --- Existing filters still work ------------------------------------------


def test_yearly_analysis_filter_by_optimization_run_still_works(
    client: TestClient, auth_token, record
):
    fake_opt_id = "00000000-0000-0000-0000-000000000001"
    r = client.get(
        f"{YA_BASE}/?optimization_run_id={fake_opt_id}",
        headers=_headers(auth_token),
    )
    record("ya_filter_status", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return
    body = r.json()
    items = body.get("items", [])
    record(
        "ya_filter_all_match_or_empty",
        all(item.get("optimization_run_id") == fake_opt_id for item in items),
        f"items={len(items)}",
    )


def test_shifts_filter_by_bus_id_still_works(client: TestClient, auth_token, record):
    """Pass a non-existing bus_id; expect 200 with zero items."""
    fake_bus_id = "00000000-0000-0000-0000-000000000123"
    r = client.get(
        f"{USER_BASE}/shifts/?bus_id={fake_bus_id}",
        headers=_headers(auth_token),
    )
    record("shifts_filter_status", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return
    body = r.json()
    record(
        "shifts_filter_returns_envelope",
        isinstance(body, dict) and "items" in body and "total" in body,
        f"keys={list(body.keys()) if isinstance(body, dict) else None}",
    )
    record(
        "shifts_filter_zero_for_fake_id",
        body.get("total", -1) == 0 and body.get("items") == [],
        f"total={body.get('total')}, items={body.get('items')}",
    )


# --- Per-user scoping for paginated lists ---------------------------------


async def _async_insert_yearly_analysis(user_id: str, name: str) -> tuple[str, str]:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        opt_id = await conn.fetchval(
            """
            INSERT INTO optimization_runs (user_id, mode, status, input_params)
            VALUES ($1::uuid, 'joint', 'completed', $2::jsonb)
            RETURNING id
            """,
            user_id,
            json.dumps({}),
        )
        ya_id = await conn.fetchval(
            """
            INSERT INTO yearly_analysis (optimization_run_id, name, features)
            VALUES ($1::uuid, $2, $3::jsonb)
            RETURNING id
            """,
            opt_id,
            name,
            json.dumps({}),
        )
        return str(opt_id), str(ya_id)
    finally:
        await conn.close()


async def _async_insert_shift_search_fixture(
    user_id: str,
    *,
    group_term: str,
    needle_term: str,
    model_term: str,
) -> dict[str, Any]:
    """Create an isolated, deterministic set of shifts for search tests."""
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        base_model_id = await conn.fetchval(
            """
            INSERT INTO buses_models (name, specs, user_id)
            VALUES ($1, $2::jsonb, $3::uuid)
            RETURNING id
            """,
            f"Base model {uuid.uuid4().hex}",
            json.dumps({}),
            user_id,
        )
        rare_model_id = await conn.fetchval(
            """
            INSERT INTO buses_models (name, specs, user_id)
            VALUES ($1, $2::jsonb, $3::uuid)
            RETURNING id
            """,
            model_term,
            json.dumps({}),
            user_id,
        )
        base_bus_id = await conn.fetchval(
            """
            INSERT INTO buses (name, specs, bus_model_id, user_id)
            VALUES ($1, $2::jsonb, $3::uuid, $4::uuid)
            RETURNING id
            """,
            f"Base search bus {uuid.uuid4().hex}",
            json.dumps({}),
            base_model_id,
            user_id,
        )
        rare_bus_id = await conn.fetchval(
            """
            INSERT INTO buses (name, specs, bus_model_id, user_id)
            VALUES ($1, $2::jsonb, $3::uuid, $4::uuid)
            RETURNING id
            """,
            f"Rare search bus {uuid.uuid4().hex}",
            json.dumps({}),
            rare_model_id,
            user_id,
        )

        group_ids = []
        for index in range(25):
            shift_id = await conn.fetchval(
                """
                INSERT INTO shifts (name, bus_id)
                VALUES ($1, $2::uuid)
                RETURNING id
                """,
                f"000 {group_term} {index:02d}",
                base_bus_id,
            )
            group_ids.append(str(shift_id))

        model_shift_id = await conn.fetchval(
            """
            INSERT INTO shifts (name, bus_id)
            VALUES ($1, $2::uuid)
            RETURNING id
            """,
            f"yyy ordinary model-backed shift {uuid.uuid4().hex}",
            rare_bus_id,
        )
        needle_shift_id = await conn.fetchval(
            """
            INSERT INTO shifts (name, bus_id)
            VALUES ($1, $2::uuid)
            RETURNING id
            """,
            f"zzz {needle_term}",
            base_bus_id,
        )
        return {
            "base_bus_id": str(base_bus_id),
            "rare_bus_id": str(rare_bus_id),
            "group_ids": group_ids,
            "model_shift_id": str(model_shift_id),
            "needle_shift_id": str(needle_shift_id),
        }
    finally:
        await conn.close()


async def _async_cleanup(
    yearly_analysis_ids: Iterable[str],
    optimization_run_ids: Iterable[str],
    user_ids: Iterable[str],
) -> None:
    import asyncpg

    conn = await asyncpg.connect(_dsn_for_asyncpg())
    try:
        ya = list(yearly_analysis_ids)
        ors = list(optimization_run_ids)
        us = list(user_ids)
        if ya:
            await conn.execute(
                "DELETE FROM yearly_analysis WHERE id = ANY($1::uuid[])",
                ya,
            )
        if ors:
            await conn.execute(
                "DELETE FROM optimization_runs WHERE id = ANY($1::uuid[])",
                ors,
            )
        if us:
            await conn.execute(
                "DELETE FROM users WHERE id = ANY($1::uuid[])",
                us,
            )
    finally:
        await conn.close()


def test_shift_search_filters_before_count_and_pagination(
    client: TestClient, record
):
    """Search sees later pages, preserves pagination, filters models and stays scoped."""
    user_ids: list[str] = []
    suffix = uuid.uuid4().hex
    group_term = f"groupable-{suffix}"
    needle_term = f"deep-needle-{suffix}"
    model_term = f"rare-model-{suffix}"

    try:
        owner = _register_temp_user(client, prefix="tmp_shift_search_owner")
        user_ids.append(owner["id"])
        foreign = _register_temp_user(client, prefix="tmp_shift_search_foreign")
        user_ids.append(foreign["id"])

        owner_token = _login(client, owner["email"], owner["password"])
        assert owner_token, "Could not login as shift-search owner"

        overlong = client.get(
            f"{USER_BASE}/shifts/",
            params={"search": "x" * 101},
            headers=_headers(owner_token),
        )
        record(
            "shift_search_rejects_over_100_characters",
            overlong.status_code == 422,
            f"status={overlong.status_code} body={overlong.text[:300]}",
        )

        owner_rows = asyncio.run(
            _async_insert_shift_search_fixture(
                owner["id"],
                group_term=group_term,
                needle_term=needle_term,
                model_term=model_term,
            )
        )
        asyncio.run(
            _async_insert_shift_search_fixture(
                foreign["id"],
                group_term=group_term,
                needle_term=needle_term,
                model_term=model_term,
            )
        )

        # Backwards compatibility: omitting search still returns the same
        # lightweight envelope and the user's complete, ordered collection.
        unfiltered = client.get(
            f"{USER_BASE}/shifts/",
            params={"skip": 0, "limit": 20},
            headers=_headers(owner_token),
        )
        assert unfiltered.status_code == 200, unfiltered.text
        unfiltered_body = unfiltered.json()
        record(
            "shift_search_omitted_preserves_total",
            unfiltered_body.get("total") == 27,
            f"body={unfiltered_body}",
        )
        record(
            "shift_search_omitted_preserves_envelope",
            set(ENVELOPE_KEYS).issubset(unfiltered_body)
            and unfiltered_body.get("count") == 20,
            f"body={unfiltered_body}",
        )
        record(
            "shift_search_target_is_beyond_first_page",
            owner_rows["needle_shift_id"]
            not in {item["id"] for item in unfiltered_body.get("items", [])},
            f"first_page_ids={[item['id'] for item in unfiltered_body.get('items', [])]}",
        )

        # Whitespace is treated like an omitted search, avoiding a breaking
        # change for clients that always include an empty query parameter.
        whitespace = client.get(
            f"{USER_BASE}/shifts/",
            params={"search": "   ", "skip": 0, "limit": 20},
            headers=_headers(owner_token),
        )
        assert whitespace.status_code == 200, whitespace.text
        record(
            "shift_search_whitespace_is_backwards_compatible",
            whitespace.json().get("total") == 27,
            f"body={whitespace.json()}",
        )

        # Case-insensitive name search finds a shift which is absent from the
        # unfiltered first page. The foreign user's identically named shift
        # must not affect either the result or the filtered total.
        needle = client.get(
            f"{USER_BASE}/shifts/",
            params={"search": needle_term.upper(), "skip": 0, "limit": 20},
            headers=_headers(owner_token),
        )
        assert needle.status_code == 200, needle.text
        needle_body = needle.json()
        record(
            "shift_search_finds_match_beyond_first_page",
            needle_body.get("total") == 1
            and [item["id"] for item in needle_body.get("items", [])]
            == [owner_rows["needle_shift_id"]],
            f"body={needle_body}",
        )
        record(
            "shift_search_filtered_envelope_is_consistent",
            needle_body.get("count") == 1
            and needle_body.get("has_next") is False
            and needle_body.get("has_previous") is False,
            f"body={needle_body}",
        )

        # A filtered result set larger than the default page size retains
        # correct totals and navigation metadata across both pages.
        group_page_1 = client.get(
            f"{USER_BASE}/shifts/",
            params={"search": group_term, "skip": 0, "limit": 20},
            headers=_headers(owner_token),
        )
        group_page_2 = client.get(
            f"{USER_BASE}/shifts/",
            params={"search": group_term, "skip": 20, "limit": 20},
            headers=_headers(owner_token),
        )
        assert group_page_1.status_code == 200, group_page_1.text
        assert group_page_2.status_code == 200, group_page_2.text
        group_body_1 = group_page_1.json()
        group_body_2 = group_page_2.json()
        record(
            "shift_search_filtered_first_page_metadata",
            group_body_1.get("total") == 25
            and group_body_1.get("count") == 20
            and group_body_1.get("has_next") is True
            and group_body_1.get("has_previous") is False,
            f"body={group_body_1}",
        )
        record(
            "shift_search_filtered_second_page_metadata",
            group_body_2.get("total") == 25
            and group_body_2.get("count") == 5
            and group_body_2.get("has_next") is False
            and group_body_2.get("has_previous") is True,
            f"body={group_body_2}",
        )
        record(
            "shift_search_filtered_pages_do_not_overlap",
            {item["id"] for item in group_body_1.get("items", [])}.isdisjoint(
                {item["id"] for item in group_body_2.get("items", [])}
            ),
            f"page1={group_body_1.get('items')} page2={group_body_2.get('items')}",
        )

        # Searching the assigned model composes with the pre-existing bus_id
        # filter instead of replacing it.
        model_match = client.get(
            f"{USER_BASE}/shifts/",
            params={
                "search": model_term.upper(),
                "bus_id": owner_rows["rare_bus_id"],
            },
            headers=_headers(owner_token),
        )
        wrong_bus = client.get(
            f"{USER_BASE}/shifts/",
            params={
                "search": model_term,
                "bus_id": owner_rows["base_bus_id"],
            },
            headers=_headers(owner_token),
        )
        assert model_match.status_code == 200, model_match.text
        assert wrong_bus.status_code == 200, wrong_bus.text
        record(
            "shift_search_matches_assigned_bus_model",
            model_match.json().get("total") == 1
            and [item["id"] for item in model_match.json().get("items", [])]
            == [owner_rows["model_shift_id"]],
            f"body={model_match.json()}",
        )
        record(
            "shift_search_composes_with_bus_id",
            wrong_bus.json().get("total") == 0
            and wrong_bus.json().get("items") == [],
            f"body={wrong_bus.json()}",
        )

        # LIKE metacharacters are literal user input, not implicit wildcards.
        literal_wildcard = client.get(
            f"{USER_BASE}/shifts/",
            params={"search": "%"},
            headers=_headers(owner_token),
        )
        assert literal_wildcard.status_code == 200, literal_wildcard.text
        record(
            "shift_search_treats_wildcards_literally",
            literal_wildcard.json().get("total") == 0,
            f"body={literal_wildcard.json()}",
        )
    finally:
        asyncio.run(_async_cleanup([], [], user_ids))


def test_yearly_analyses_total_reflects_filter_for_temp_user(
    client: TestClient, record
):
    """Inserting N yearly analyses for a fresh user yields total == N (DB count, not page length)."""
    user_ids: list[str] = []
    opt_ids: list[str] = []
    ya_ids: list[str] = []
    try:
        user = _register_temp_user(client, prefix="tmp_paginate_ya")
        user_ids.append(user["id"])
        token = _login(client, user["email"], user["password"])
        if not token:
            record("paginate_temp_login", False, "Could not login as temp user")
            return

        # Insert 25 rows → 1 full page of 20 plus a partial page of 5.
        for i in range(25):
            opt_id, ya_id = asyncio.run(
                _async_insert_yearly_analysis(user["id"], f"Pagination YA {i:02d}")
            )
            opt_ids.append(opt_id)
            ya_ids.append(ya_id)

        r1 = client.get(f"{YA_BASE}/?skip=0&limit=20", headers=_headers(token))
        record("paginate_p1_status", r1.status_code == 200, f"status={r1.status_code}")
        b1 = r1.json()
        record("paginate_p1_total_25", b1.get("total") == 25, f"total={b1.get('total')}")
        record("paginate_p1_count_20", b1.get("count") == 20, f"count={b1.get('count')}")
        record("paginate_p1_has_next", b1.get("has_next") is True, f"has_next={b1.get('has_next')}")
        record(
            "paginate_p1_has_previous",
            b1.get("has_previous") is False,
            f"has_previous={b1.get('has_previous')}",
        )

        r2 = client.get(f"{YA_BASE}/?skip=20&limit=20", headers=_headers(token))
        record("paginate_p2_status", r2.status_code == 200, f"status={r2.status_code}")
        b2 = r2.json()
        record("paginate_p2_total_25", b2.get("total") == 25, f"total={b2.get('total')}")
        record("paginate_p2_count_5", b2.get("count") == 5, f"count={b2.get('count')}")
        record("paginate_p2_has_next", b2.get("has_next") is False, f"has_next={b2.get('has_next')}")
        record(
            "paginate_p2_has_previous",
            b2.get("has_previous") is True,
            f"has_previous={b2.get('has_previous')}",
        )

        # No id appears in both pages
        ids_p1 = {item["id"] for item in b1.get("items", [])}
        ids_p2 = {item["id"] for item in b2.get("items", [])}
        record(
            "paginate_no_overlap_between_pages",
            ids_p1.isdisjoint(ids_p2),
            f"overlap={sorted(ids_p1 & ids_p2)}",
        )

        # limit=50 returns all 25 in a single page
        r3 = client.get(f"{YA_BASE}/?skip=0&limit=50", headers=_headers(token))
        b3 = r3.json()
        record(
            "paginate_limit50_count_25",
            b3.get("count") == 25 and b3.get("limit") == 50,
            f"count={b3.get('count')}, limit={b3.get('limit')}",
        )

        # limit=100 also returns all 25
        r4 = client.get(f"{YA_BASE}/?skip=0&limit=100", headers=_headers(token))
        b4 = r4.json()
        record(
            "paginate_limit100_count_25",
            b4.get("count") == 25 and b4.get("limit") == 100,
            f"count={b4.get('count')}, limit={b4.get('limit')}",
        )
    finally:
        asyncio.run(_async_cleanup(ya_ids, opt_ids, user_ids))


# ---------------------------------------------------------------------------
# OpenAPI documents the paginated envelope
# ---------------------------------------------------------------------------


def test_openapi_documents_paginated_responses(client: TestClient, record):
    r = client.get("/openapi.json")
    record("openapi_status", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return
    schemas = r.json().get("components", {}).get("schemas", {})
    expected = [
        "PaginatedResponse_BusesModelsListItemRead_",
        "PaginatedResponse_GtfsStopsListItemRead_",
        "PaginatedResponse_ShiftListItemRead_",
        "PaginatedResponse_OptimizationRunListItemRead_",
        "PaginatedResponse_YearlyAnalysisListItemRead_",
    ]
    for name in expected:
        record(
            f"openapi_has_{name}",
            name in schemas,
            f"present={name in schemas}",
        )

    # Spot-check the envelope's required keys are present in the schema
    sample = schemas.get("PaginatedResponse_BusesModelsListItemRead_", {})
    props = sample.get("properties", {})
    for key in ENVELOPE_KEYS:
        record(
            f"openapi_envelope_property_{key}",
            key in props,
            f"props={list(props.keys())}",
        )

    shift_parameters = (
        r.json()
        .get("paths", {})
        .get(f"{USER_BASE}/shifts/", {})
        .get("get", {})
        .get("parameters", [])
    )
    search_parameter = next(
        (
            parameter
            for parameter in shift_parameters
            if parameter.get("name") == "search"
        ),
        None,
    )
    record(
        "openapi_documents_shift_search",
        search_parameter is not None
        and search_parameter.get("in") == "query"
        and search_parameter.get("schema", {}).get("maxLength") == 100,
        f"search_parameter={search_parameter}",
    )
