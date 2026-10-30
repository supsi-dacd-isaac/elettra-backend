import asyncio
import json
import os
import sys
from uuid import uuid4

import pytest

from app.services import optimization_process as service


@pytest.mark.asyncio
async def test_independent_processes_overlap_and_respect_capacity(monkeypatch, tmp_path):
    real_spawn = asyncio.create_subprocess_exec
    monkeypatch.setattr(service, "worker_slots", lambda: slots)
    slots = asyncio.Semaphore(2)
    monkeypatch.setenv("ELETTRA_OPTIMIZATION_THREADS_PER_WORKER", "3")
    script = """
import json, os, sys, time
start = time.time()
time.sleep(.4)
with open(sys.argv[1], 'w') as f:
    json.dump({'pid':os.getpid(), 'start':start, 'end':time.time(),
               'threads':os.environ['OMP_NUM_THREADS']}, f)
"""
    async def spawn(*args, **kwargs):
        assert args[1:3] == ("-m", "app.services.optimization_worker")
        return await real_spawn(sys.executable, "-c", script, str(tmp_path / args[-1]), **kwargs)
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", spawn)
    keys = [uuid4() for _ in range(3)]
    await service.run_workers(*keys)
    rows = [json.loads((tmp_path / str(key)).read_text()) for key in keys]
    assert len({row['pid'] for row in rows}) == 3
    assert all(row['pid'] != os.getpid() and row['threads'] == '3' for row in rows)
    assert max(row['start'] for row in rows[:2]) < min(row['end'] for row in rows[:2])
    assert rows[2]['start'] >= min(row['end'] for row in rows[:2])


@pytest.mark.asyncio
async def test_worker_crash_does_not_cancel_other_profile(monkeypatch, tmp_path):
    real_spawn = asyncio.create_subprocess_exec
    slots = asyncio.Semaphore(2)
    monkeypatch.setattr(service, "worker_slots", lambda: slots)
    failed, completed = uuid4(), uuid4()
    errors = []
    async def mark(key, message):
        errors.append((key, message))
    async def spawn(*args, **kwargs):
        script = "raise SystemExit(7)" if args[-1] == str(failed) else (
            "import time, pathlib; time.sleep(.2); pathlib.Path('" + str(tmp_path / 'done') + "').touch()")
        return await real_spawn(sys.executable, "-c", script, **kwargs)
    monkeypatch.setattr(service, "mark_worker_failed", mark)
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", spawn)
    await service.run_workers(failed, completed)
    assert errors == [(failed, "Optimization worker exited with code 7")]
    assert (tmp_path / 'done').exists()


def test_invalid_resource_limits_are_rejected(monkeypatch):
    monkeypatch.setenv("ELETTRA_OPTIMIZATION_THREADS_PER_WORKER", "0")
    with pytest.raises(ValueError, match='must be positive'):
        service.worker_environment()


@pytest.mark.asyncio
async def test_cancellation_reaps_child_and_marks_run_failed(monkeypatch):
    real_spawn = asyncio.create_subprocess_exec
    slots = asyncio.Semaphore(1)
    monkeypatch.setattr(service, "worker_slots", lambda: slots)
    spawned = asyncio.Event()
    children, failures = [], []
    async def spawn(*args, **kwargs):
        child = await real_spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        children.append(child)
        spawned.set()
        return child
    async def mark(key, message):
        failures.append((key, message))
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(service, "mark_worker_failed", mark)
    key = uuid4()
    task = asyncio.create_task(service.run_worker(key))
    await spawned.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[0].returncode is not None
    assert failures == [(key, "Optimization worker interrupted")]
    assert not slots.locked()
