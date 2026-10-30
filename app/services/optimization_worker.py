"""Single-run worker, started in a fresh interpreter by optimization_process."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from uuid import UUID


async def execute(run_id: UUID) -> None:
    from app.database import AsyncSessionLocal, engine
    from app.models import OptimizationRuns
    from app.services.elevation_profiles import validate_configured_release
    from app.services.model_release import validate_configured_model_release
    from app.services.optimization import run_optimization
    from app.services.optimization_process import threads_per_worker

    execution = {"pid": os.getpid(), "threads": threads_per_worker(),
                 "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        async with AsyncSessionLocal() as db:
            run = await db.get(OptimizationRuns, run_id)
            if run is None or run.status != "pending":
                raise ValueError("Worker requires a pending optimization")
            run.status = "running"
            run.results = {"execution": execution}
            await db.commit()
            # The API already checked the requested shifts. Validate immutable
            # model artifacts again in this process before making predictions.
            manifest = await asyncio.to_thread(validate_configured_release)
            await asyncio.to_thread(validate_configured_model_release, manifest)
            await run_optimization(db, run_id, execution_metadata=execution)
            await db.refresh(run)
            if run.status not in {"completed", "failed"}:
                raise RuntimeError("Optimization worker finished without a terminal result")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(execute(UUID(sys.argv[1])))
