"""Opt-in staging diagnostic; never imported by the application or release entrypoint."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from uuid import UUID

# JSONB cannot store infinity. This finite pipeline sentinel is about 10**92
# years; the supervisor and individual solver calls have no time limit.
DIAGNOSTIC_BUDGET_SECONDS = 1e100


def validate_diagnostic_target():
    from app.core.config import get_cached_settings
    from test_support.staging_targets import validate_test_targets
    if any(os.getenv(key) != "1" for key in (
        "ELETTRA_TESTING", "ELETTRA_RUN_LONG_FLEET", "ELETTRA_FLEET_UNBOUNDED"
    )):
        raise RuntimeError("Unbounded fleet diagnostics require explicit test opt-in")
    return validate_test_targets(get_cached_settings().database_url, ci=False)


def install_test_supervisor(monkeypatch):
    validate_diagnostic_target()
    from app.services import optimization_process
    original_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        args = tuple("test_support.fleet_unbounded_worker"
                     if value == "app.services.optimization_worker" else value
                     for value in args)
        return await original_spawn(*args, **kwargs)

    async def no_deadline(_run_id):
        return None  # asyncio.wait_for(..., timeout=None)

    monkeypatch.setattr(optimization_process, "worker_timeout_seconds", no_deadline)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)


async def execute(run_id):
    validate_diagnostic_target()
    from app.database import AsyncSessionLocal, engine
    from app.models import OptimizationRuns
    from app.services import optimization
    from app.services.optimization_worker import execute as execute_application_worker
    from simulation.optimization_model import solve_optimization

    output = Path(os.environ["ELETTRA_FLEET_DIAGNOSTIC_RESULTS"])
    output.mkdir(parents=True, exist_ok=True)
    async with AsyncSessionLocal() as db:
        run = await db.get(OptimizationRuns, run_id)
        if (run is None or run.status != "pending"
                or run.mode not in {"battery_only", "joint"}
                or not run.name.startswith(("Fleet battery-only integration test", "Fleet joint integration test"))):
            raise RuntimeError("Diagnostic worker only accepts pending fleet test runs")
        run.input_params = {**run.input_params,
                            "max_solver_time_seconds": None,
                            "optimization_time_budget_seconds": DIAGNOSTIC_BUDGET_SECONDS,
                            "test_only_unbounded_diagnostic": True}
        await db.commit()

    original_verify = optimization.optimize_and_verify

    def solve_without_deadline(buses, stations, config):
        return solve_optimization(buses, stations, replace(config, max_solver_time_seconds=None))

    async def verify(*args, **kwargs):
        return await original_verify(*args, **kwargs, solve=solve_without_deadline)

    optimization.optimize_and_verify = verify
    try:
        await execute_application_worker(run_id)
    finally:
        # Preserve complete mean/Q95 outcomes before the fixture removes its rows.
        async with AsyncSessionLocal() as db:
            run = await db.get(OptimizationRuns, run_id)
            if run is not None:
                (output / f"{run_id}.json").write_text(json.dumps({
                    "id": str(run.id), "name": run.name, "status": run.status,
                    "input_params": run.input_params, "results": run.results,
                }, indent=2, default=str))
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(execute(UUID(sys.argv[1])))
