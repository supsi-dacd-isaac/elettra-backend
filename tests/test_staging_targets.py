import pytest

from test_support.staging_targets import (
    STAGING_BACKEND_URL,
    STAGING_FRONTEND_URL,
    StagingTargetError,
    validate_backend_health_payload,
    validate_test_targets,
)


STAGING_DATABASE_URL = "postgresql+asyncpg://user:secret@localhost:9004/elettra"


def test_local_targets_are_fixed_to_the_complete_staging_stack(monkeypatch):
    monkeypatch.delenv("ELETTRA_TEST_FRONTEND_URL", raising=False)
    monkeypatch.delenv("ELETTRA_TEST_BACKEND_URL", raising=False)

    targets = validate_test_targets(STAGING_DATABASE_URL, ci=False)

    assert targets.frontend_url == STAGING_FRONTEND_URL
    assert targets.backend_url == STAGING_BACKEND_URL
    assert targets.database_host == "localhost"
    assert targets.database_port == 9004
    assert targets.database_name == "elettra"


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+asyncpg://user:secret@localhost:5440/elettra",
        "postgresql+asyncpg://user:secret@localhost:9005/elettra",
        "postgresql+asyncpg://user:secret@db:5432/elettra",
        "postgresql+asyncpg://user:secret@localhost:9004/another_database",
        "sqlite:///elettra.db",
    ],
)
def test_local_tests_reject_every_non_staging_database(monkeypatch, database_url):
    monkeypatch.delenv("ELETTRA_TEST_FRONTEND_URL", raising=False)
    monkeypatch.delenv("ELETTRA_TEST_BACKEND_URL", raising=False)

    with pytest.raises(StagingTargetError):
        validate_test_targets(database_url, ci=False)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("ELETTRA_TEST_FRONTEND_URL", "http://127.0.0.1:9010"),
        ("ELETTRA_TEST_FRONTEND_URL", "http://example.test:55558"),
        ("ELETTRA_TEST_BACKEND_URL", "http://127.0.0.1:8002"),
        ("ELETTRA_TEST_BACKEND_URL", "http://example.test:9005"),
    ],
)
def test_http_test_targets_cannot_be_redirected(monkeypatch, variable, value):
    monkeypatch.delenv("ELETTRA_TEST_FRONTEND_URL", raising=False)
    monkeypatch.delenv("ELETTRA_TEST_BACKEND_URL", raising=False)
    monkeypatch.setenv(variable, value)

    with pytest.raises(StagingTargetError):
        validate_test_targets(STAGING_DATABASE_URL, ci=False)


def test_ci_accepts_only_the_workflow_ephemeral_database(monkeypatch):
    monkeypatch.delenv("ELETTRA_TEST_FRONTEND_URL", raising=False)
    monkeypatch.delenv("ELETTRA_TEST_BACKEND_URL", raising=False)
    ci_url = "postgresql+asyncpg://admin:admin@127.0.0.1:5432/elettra"

    targets = validate_test_targets(ci_url, ci=True)

    assert targets.ci is True
    with pytest.raises(StagingTargetError):
        validate_test_targets(STAGING_DATABASE_URL, ci=True)


def test_stable_public_health_contract_is_accepted():
    assert validate_backend_health_payload(
        {"status": "healthy", "version": "1.4.0"}
    ) is None


def test_detailed_health_contract_still_validates_database_status():
    assert validate_backend_health_payload(
        {"status": "healthy", "services": {"database": {"status": "healthy"}}}
    ) is None

    with pytest.raises(StagingTargetError, match="database status"):
        validate_backend_health_payload(
            {"status": "healthy", "services": {"database": {"status": "down"}}}
        )
