from __future__ import annotations

from fastapi import Response
import pytest

import main


# The shared API TestClient stubs external startup dependencies for the broad
# integration suite. These focused tests always restore the real probe
# functions so readiness behavior itself is exercised.
_REAL_PROFILE_STORE_PROBE = main.probe_elevation_profiles_store
_REAL_SCHEMA_PROBE = main.validate_elevation_jobs_schema


@pytest.fixture(autouse=True)
def _use_real_runtime_probes(monkeypatch):
    monkeypatch.setattr(main, "probe_elevation_profiles_store", _REAL_PROFILE_STORE_PROBE)
    monkeypatch.setattr(main, "validate_elevation_jobs_schema", _REAL_SCHEMA_PROBE)


class _ScalarResult:
    def __init__(self, values=(), row=(1,)):
        self._values = list(values)
        self._row = row

    def scalars(self):
        return self

    def all(self):
        return list(self._values)

    def fetchone(self):
        return self._row

    def scalar_one(self):
        return self._row[0]


class _ReadySession:
    async def execute(self, statement):
        sql = str(statement)
        if "contype = 'f'" in sql:
            return _ScalarResult(row=(False,))
        if "SELECT 1" in sql:
            return _ScalarResult()
        if "information_schema.columns" in sql:
            if "elevation_profile_cleanup_jobs" in sql:
                return _ScalarResult(main._CLEANUP_JOB_COLUMNS)
            return _ScalarResult(main._ELEVATION_JOB_COLUMNS)
        if "pg_constraint" in sql:
            if "elevation_profile_cleanup_jobs" in sql:
                return _ScalarResult(main._CLEANUP_JOB_CONSTRAINTS)
            return _ScalarResult(main._ELEVATION_JOB_CONSTRAINTS)
        if "pg_indexes" in sql:
            if "elevation_profile_cleanup_jobs" in sql:
                return _ScalarResult([main._CLEANUP_JOB_INDEX])
            return _ScalarResult([main._ELEVATION_JOB_INDEX])
        raise AssertionError(f"Unexpected readiness SQL: {sql}")


async def _ready_sessions():
    yield _ReadySession()


@pytest.mark.asyncio
async def test_health_returns_503_when_database_or_migration_is_unavailable(monkeypatch):
    async def broken_sessions():
        raise RuntimeError("migration unavailable")
        yield  # pragma: no cover

    async def minio_ready():
        return "MinIO ready"

    monkeypatch.setattr(main, "get_async_session", broken_sessions)
    monkeypatch.setattr(main, "probe_elevation_profiles_store", minio_ready)
    response = Response()

    payload = await main.health_check(response)

    assert response.status_code == 503
    assert payload.status == "unhealthy"
    assert payload.services["database"].status == "unhealthy"


@pytest.mark.asyncio
async def test_health_returns_503_when_minio_is_unavailable(monkeypatch):
    async def minio_broken():
        raise RuntimeError("bucket unavailable")

    monkeypatch.setattr(main, "get_async_session", _ready_sessions)
    monkeypatch.setattr(main, "probe_elevation_profiles_store", minio_broken)
    response = Response()

    payload = await main.health_check(response)

    assert response.status_code == 503
    assert payload.status == "unhealthy"
    assert payload.services["database"].status == "healthy"
    assert payload.services["elevation_profiles"].status == "unhealthy"


@pytest.mark.asyncio
async def test_health_is_200_only_after_schema_and_minio_preflights(monkeypatch):
    async def minio_ready():
        return "Legacy MinIO namespace is reachable"

    monkeypatch.setattr(main, "get_async_session", _ready_sessions)
    monkeypatch.setattr(main, "probe_elevation_profiles_store", minio_ready)
    response = Response()

    payload = await main.health_check(response)

    assert response.status_code == 200
    assert payload.status == "healthy"
    assert "005/006" in payload.services["database"].message


@pytest.mark.asyncio
async def test_schema_preflight_rejects_partial_migration():
    class PartialSession(_ReadySession):
        async def execute(self, statement):
            if "information_schema.columns" in str(statement):
                return _ScalarResult({"id", "trip_id"})
            return await super().execute(statement)

    with pytest.raises(RuntimeError, match="missing or incomplete"):
        await main.validate_elevation_jobs_schema(PartialSession())


@pytest.mark.asyncio
async def test_legacy_minio_probe_contacts_configured_bucket(monkeypatch):
    calls = []

    class Client:
        def bucket_exists(self, bucket):
            calls.append(bucket)
            return True

    monkeypatch.setattr(main, "configured_release", lambda: None)
    monkeypatch.setattr(main, "elevation_profiles_bucket", lambda: "profiles")
    monkeypatch.setattr(main, "create_minio_client", Client)

    message = await main.probe_elevation_profiles_store()

    assert calls == ["profiles"]
    assert "Legacy" in message


@pytest.mark.asyncio
async def test_startup_refuses_to_yield_before_database_and_minio_preflights(monkeypatch):
    calls = []

    class SessionContext:
        async def __aenter__(self):
            calls.append("database")
            return _ReadySession()

        async def __aexit__(self, *_args):
            pass

    async def minio_ready():
        calls.append("minio")
        return "MinIO ready"

    monkeypatch.setattr(main, "AsyncSessionLocal", SessionContext)
    monkeypatch.setattr(main, "probe_elevation_profiles_store", minio_ready)
    monkeypatch.setattr(main, "configured_release", lambda: None)

    async with main.lifespan(main.app):
        calls.append("yielded")

    assert "database" in calls
    assert "minio" in calls
    assert calls[-1] == "yielded"
