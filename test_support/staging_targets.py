"""Fail closed unless local tests use the dedicated ELETTRA staging stack."""

from __future__ import annotations

import asyncio
import json
import os
import socket
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


STAGING_FRONTEND_URL = "http://127.0.0.1:55558"
STAGING_BACKEND_URL = "http://127.0.0.1:9005"
STAGING_DATABASE_HOSTS = frozenset({"127.0.0.1", "localhost"})
STAGING_DATABASE_PORT = 9004
STAGING_DATABASE_NAME = "elettra"

CI_DATABASE_HOSTS = frozenset({"127.0.0.1", "localhost"})
CI_DATABASE_PORT = 5432
CI_DATABASE_NAME = "elettra"


class StagingTargetError(RuntimeError):
    """Raised when a test target could reach anything outside its test stack."""


@dataclass(frozen=True)
class TestTargets:
    frontend_url: str
    backend_url: str
    database_host: str
    database_port: int
    database_name: str
    ci: bool


def _required_http_target(env_name: str, expected: str) -> str:
    configured = os.getenv(env_name, expected).rstrip("/")
    parsed = urlparse(configured)
    if configured != expected or parsed.scheme != "http" or parsed.username or parsed.password:
        raise StagingTargetError(
            f"{env_name} must be exactly {expected!r} for ELETTRA tests; "
            f"got {configured!r}."
        )
    return configured


def validate_test_targets(database_url: str, *, ci: bool | None = None) -> TestTargets:
    """Validate the complete target set without opening any connections."""

    is_ci = os.getenv("GITHUB_ACTIONS") == "true" if ci is None else ci
    frontend_url = _required_http_target(
        "ELETTRA_TEST_FRONTEND_URL", STAGING_FRONTEND_URL
    )
    backend_url = _required_http_target(
        "ELETTRA_TEST_BACKEND_URL", STAGING_BACKEND_URL
    )

    parsed = urlparse(database_url)
    if not parsed.scheme.startswith("postgresql"):
        raise StagingTargetError("DATABASE_URL must use PostgreSQL for ELETTRA tests.")

    expected_hosts = CI_DATABASE_HOSTS if is_ci else STAGING_DATABASE_HOSTS
    expected_port = CI_DATABASE_PORT if is_ci else STAGING_DATABASE_PORT
    expected_name = CI_DATABASE_NAME if is_ci else STAGING_DATABASE_NAME
    database_name = parsed.path.lstrip("/")

    if (
        parsed.hostname not in expected_hosts
        or parsed.port != expected_port
        or database_name != expected_name
    ):
        environment = "the ephemeral GitHub Actions database" if is_ci else "staging"
        expected_host = "127.0.0.1/localhost"
        raise StagingTargetError(
            f"DATABASE_URL must target {environment} at "
            f"{expected_host}:{expected_port}/{expected_name}; got "
            f"{parsed.hostname}:{parsed.port}/{database_name}."
        )

    return TestTargets(
        frontend_url=frontend_url,
        backend_url=backend_url,
        database_host=parsed.hostname,
        database_port=parsed.port,
        database_name=database_name,
        ci=is_ci,
    )


def _read_url(url: str, *, timeout: float) -> tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": "elettra-test-preflight"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()
    except URLError as exc:
        raise StagingTargetError(f"Cannot reach required staging service {url}: {exc}") from exc


def validate_backend_health_payload(payload: object) -> None:
    """Accept the stable public health contract and validate details if present."""

    if not isinstance(payload, dict) or payload.get("status") != "healthy":
        raise StagingTargetError("Staging backend health is not healthy.")

    services = payload.get("services")
    if services is None:
        return
    try:
        database_status = services["database"]["status"]
    except (KeyError, TypeError) as exc:
        raise StagingTargetError(
            "Staging backend health has an invalid database status."
        ) from exc
    if database_status != "healthy":
        raise StagingTargetError(
            f"Staging backend reports database status {database_status!r}."
        )


async def _probe_database(database_url: str, expected_name: str) -> None:
    import asyncpg

    dsn = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn)
    try:
        actual_name = await connection.fetchval("SELECT current_database()")
    finally:
        await connection.close()
    if actual_name != expected_name:
        raise StagingTargetError(
            f"Connected to database {actual_name!r}, expected {expected_name!r}."
        )


def preflight_local_staging(
    targets: TestTargets, database_url: str, *, timeout: float = 5.0
) -> None:
    """Verify 55558 -> 9005 and the staging DB port before local pytest starts."""

    if targets.ci:
        return

    try:
        with socket.create_connection(
            (targets.database_host, targets.database_port), timeout=timeout
        ):
            pass
    except OSError as exc:
        raise StagingTargetError(
            "Cannot reach the required staging database at "
            f"{targets.database_host}:{targets.database_port}: {exc}"
        ) from exc

    try:
        asyncio.run(_probe_database(database_url, targets.database_name))
    except StagingTargetError:
        raise
    except Exception as exc:
        raise StagingTargetError(
            "Cannot authenticate to the required staging database on port 9004."
        ) from exc

    backend_health_status, backend_health_body = _read_url(
        f"{targets.backend_url}/health", timeout=timeout
    )
    if backend_health_status != 200:
        raise StagingTargetError(
            f"Staging backend health returned HTTP {backend_health_status}."
        )
    try:
        backend_health = json.loads(backend_health_body)
    except (TypeError, ValueError) as exc:
        raise StagingTargetError("Staging backend health is not valid JSON.") from exc
    validate_backend_health_payload(backend_health)

    frontend_health_status, _ = _read_url(
        f"{targets.frontend_url}/health", timeout=timeout
    )
    if frontend_health_status != 200:
        raise StagingTargetError(
            f"Staging frontend health returned HTTP {frontend_health_status}."
        )

    direct_status, direct_body = _read_url(
        f"{targets.backend_url}/api/v1/version", timeout=timeout
    )
    proxy_status, proxy_body = _read_url(
        f"{targets.frontend_url}/api/v1/version", timeout=timeout
    )
    if direct_status != 200 or proxy_status != 200:
        raise StagingTargetError(
            "The staging version endpoint must return HTTP 200 both directly "
            "through port 9005 and through frontend port 55558."
        )
    if json.loads(direct_body) != json.loads(proxy_body):
        raise StagingTargetError(
            "Frontend port 55558 is not proxying the backend exposed on port 9005."
        )


def main() -> None:
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise StagingTargetError("DATABASE_URL must be set before test preflight.")
    targets = validate_test_targets(database_url)
    preflight_local_staging(targets, database_url)
    if targets.ci:
        print("Verified ephemeral GitHub Actions test database target.")
    else:
        print("Verified ELETTRA staging targets: frontend 55558, backend 9005, DB 9004.")


if __name__ == "__main__":
    main()
