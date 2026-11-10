"""Safety checks for the opt-in, test-only long fleet worker."""
import asyncio
from types import SimpleNamespace

import pytest

from test_support import fleet_unbounded_worker as diagnostic


@pytest.mark.parametrize("missing", [
    "ELETTRA_TESTING", "ELETTRA_RUN_LONG_FLEET", "ELETTRA_FLEET_UNBOUNDED",
])
def test_diagnostic_requires_every_opt_in(monkeypatch, missing):
    for key in ("ELETTRA_TESTING", "ELETTRA_RUN_LONG_FLEET", "ELETTRA_FLEET_UNBOUNDED"):
        monkeypatch.setenv(key, "1")
    monkeypatch.delenv(missing)
    with pytest.raises(RuntimeError, match="explicit test opt-in"):
        diagnostic.validate_diagnostic_target()


def test_diagnostic_rejects_production_database(monkeypatch):
    from app.core import config
    from test_support.staging_targets import StagingTargetError
    for key in ("ELETTRA_TESTING", "ELETTRA_RUN_LONG_FLEET", "ELETTRA_FLEET_UNBOUNDED"):
        monkeypatch.setenv(key, "1")
    monkeypatch.setattr(config, "get_cached_settings", lambda: SimpleNamespace(
        database_url="postgresql+asyncpg://localhost:5440/elettra"))
    with pytest.raises(StagingTargetError, match="9004"):
        diagnostic.validate_diagnostic_target()


def test_supervisor_removes_deadline_only_in_test_process(monkeypatch):
    from app.services import optimization_process
    calls = []

    async def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return "child"

    monkeypatch.setattr(diagnostic, "validate_diagnostic_target", lambda: None)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    diagnostic.install_test_supervisor(monkeypatch)
    assert asyncio.run(optimization_process.worker_timeout_seconds("run")) is None
    assert asyncio.run(asyncio.create_subprocess_exec(
        "python", "-m", "app.services.optimization_worker", "run", cwd="test")) == "child"
    assert calls == [(("python", "-m", "test_support.fleet_unbounded_worker", "run"), {"cwd": "test"})]
