"""Supervisors bound concurrency, time and restart recovery."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import optimization_process, prediction
from app.services.simulation_recovery import (
    INTERRUPTED_MESSAGE,
    reconcile_interrupted_simulation_jobs,
)


class _SessionContext:
    def __init__(self, session=None):
        self.session = session or SimpleNamespace()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_prediction_timeout_marks_every_job_terminal(monkeypatch) -> None:
    jobs = [(uuid4(), 10), (uuid4(), 12)]
    failed = AsyncMock()
    semaphore = asyncio.Semaphore(1)

    async def slow_runner(_db, _jobs, _quantiles):
        await asyncio.sleep(1)

    monkeypatch.setattr(prediction, "prediction_slots", lambda: semaphore)
    monkeypatch.setattr(prediction, "prediction_timeout_seconds", lambda: 0.01)
    monkeypatch.setattr(prediction, "AsyncSessionLocal", _SessionContext)
    monkeypatch.setattr(prediction, "mark_prediction_jobs_failed", failed)

    await prediction._run_prediction_batch_background(
        jobs, [0.5], slow_runner, label="test forecast"
    )

    failed.assert_awaited_once()
    assert failed.await_args.args[0] == jobs
    assert "timeout" in failed.await_args.args[1]
    assert semaphore._value == 1


@pytest.mark.asyncio
async def test_prediction_batches_respect_shared_capacity(monkeypatch) -> None:
    active = 0
    maximum = 0
    semaphore = asyncio.Semaphore(1)

    async def runner(_db, _jobs, _quantiles):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1

    monkeypatch.setattr(prediction, "prediction_slots", lambda: semaphore)
    monkeypatch.setattr(prediction, "prediction_timeout_seconds", lambda: 1.0)
    monkeypatch.setattr(prediction, "AsyncSessionLocal", _SessionContext)

    await asyncio.gather(
        prediction._run_prediction_batch_background(
            [(uuid4(), 10)], [0.5], runner, label="first"
        ),
        prediction._run_prediction_batch_background(
            [(uuid4(), 10)], [0.5], runner, label="second"
        ),
    )
    assert maximum == 1


@pytest.mark.asyncio
async def test_optimization_timeout_reaps_child_and_marks_failed(monkeypatch) -> None:
    class Process:
        pid = 123
        returncode = None

        def __init__(self):
            self.terminated = False
            self.killed = False

        async def wait(self):
            if not self.terminated and not self.killed:
                await asyncio.sleep(60)
            self.returncode = -15 if self.terminated else -9
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

    process = Process()
    failed = AsyncMock()

    async def timeout(_run_id):
        return 0.01

    monkeypatch.setattr(optimization_process, "worker_slots", lambda: asyncio.Semaphore(1))
    monkeypatch.setattr(optimization_process, "worker_timeout_seconds", timeout)
    monkeypatch.setattr(
        optimization_process.asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    monkeypatch.setattr(optimization_process, "mark_worker_failed", failed)

    await optimization_process.run_worker(uuid4())

    assert process.terminated is True
    failed.assert_awaited_once()
    assert "timeout" in failed.await_args.args[1]


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


@pytest.mark.asyncio
async def test_restart_recovery_closes_both_job_types() -> None:
    predictions = [SimpleNamespace(status="pending", completed_at=None, summary=None)]
    optimizations = [
        SimpleNamespace(status="running", completed_at=None, results={"phase": "solver"})
    ]
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Rows(predictions), _Rows(optimizations)]),
        commit=AsyncMock(),
    )

    counts = await reconcile_interrupted_simulation_jobs(db)

    assert counts == (1, 1)
    assert predictions[0].status == optimizations[0].status == "failed"
    assert predictions[0].completed_at is not None
    assert optimizations[0].completed_at is not None
    assert predictions[0].summary["error"] == INTERRUPTED_MESSAGE
    assert optimizations[0].results == {
        "phase": "solver",
        "error": INTERRUPTED_MESSAGE,
    }
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_restart_recovery_does_not_commit_without_live_jobs() -> None:
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Rows([]), _Rows([])]),
        commit=AsyncMock(),
    )
    assert await reconcile_interrupted_simulation_jobs(db) == (0, 0)
    db.commit.assert_not_awaited()
