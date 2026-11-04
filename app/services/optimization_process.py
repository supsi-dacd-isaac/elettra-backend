"""Bounded, independent optimization processes for paired evaluations."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)


def positive_env(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def threads_per_worker() -> int:
    return positive_env("ELETTRA_OPTIMIZATION_THREADS_PER_WORKER", 2)


@lru_cache(maxsize=1)
def worker_slots() -> asyncio.Semaphore:
    # Shared across evaluations in this API process, not across API replicas.
    return asyncio.Semaphore(positive_env("ELETTRA_OPTIMIZATION_WORKERS", 2))


def worker_environment() -> dict[str, str]:
    env = os.environ.copy()
    threads = str(threads_per_worker())
    # Set these before the fresh interpreter imports any numerical libraries.
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "LOKY_MAX_CPU_COUNT"):
        env[key] = threads
    env["PYTHONUNBUFFERED"] = "1"
    return env


async def mark_worker_failed(run_id: UUID, message: str) -> None:
    from app.database import AsyncSessionLocal
    from app.models import OptimizationRuns

    async with AsyncSessionLocal() as db:
        run = await db.get(OptimizationRuns, run_id)
        if run is not None and run.status not in {"completed", "failed"}:
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            run.results = {**(run.results or {}), "error": message}
            await db.commit()


async def run_worker(run_id: UUID) -> None:
    process = None
    try:
        async with worker_slots():
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "app.services.optimization_worker", str(run_id),
                cwd=str(Path(__file__).resolve().parents[2]), env=worker_environment(),
            )
            logger.info("Optimization %s started in process %s", run_id, process.pid)
            code = await process.wait()
            if code != 0:
                await mark_worker_failed(run_id, f"Optimization worker exited with code {code}")
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        await mark_worker_failed(run_id, "Optimization worker interrupted")
        raise
    except Exception:
        logger.exception("Optimization worker failed for %s", run_id)
        await mark_worker_failed(run_id, "Optimization worker could not complete")


async def run_workers(*run_ids: UUID) -> None:
    # A failure in one profile must not cancel its sibling's calculation.
    outcomes = await asyncio.gather(*(run_worker(key) for key in run_ids), return_exceptions=True)
    for key, outcome in zip(run_ids, outcomes):
        if isinstance(outcome, BaseException):
            logger.error("Could not supervise optimization %s: %r", key, outcome)
