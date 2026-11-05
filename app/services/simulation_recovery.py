"""Close simulation jobs whose in-process supervisors did not survive restart."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models import OptimizationRuns, PredictionRuns


INTERRUPTED_MESSAGE = "Simulation interrupted by an API restart; submit a new run"


async def reconcile_interrupted_simulation_jobs(db) -> tuple[int, int]:
    """Mark nonterminal in-process jobs failed using existing result columns.

    ELETTRA's prediction and optimization tasks are supervised by one API
    process. A row left pending/running before this process starts has no live
    supervisor and must not remain indefinitely nonterminal.
    """

    now = datetime.now(timezone.utc)
    predictions = list(
        (
            await db.execute(
                select(PredictionRuns).where(
                    PredictionRuns.status.in_(("pending", "running"))
                )
            )
        )
        .scalars()
        .all()
    )
    optimizations = list(
        (
            await db.execute(
                select(OptimizationRuns).where(
                    OptimizationRuns.status.in_(("pending", "running"))
                )
            )
        )
        .scalars()
        .all()
    )

    for run in predictions:
        run.status = "failed"
        run.completed_at = now
        run.summary = {**(run.summary or {}), "error": INTERRUPTED_MESSAGE}
    for run in optimizations:
        run.status = "failed"
        run.completed_at = now
        run.results = {**(run.results or {}), "error": INTERRUPTED_MESSAGE}

    if predictions or optimizations:
        await db.commit()
    return len(predictions), len(optimizations)
