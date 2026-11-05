"""Authoritative dependency checks for user-owned destructive operations."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from app.models import OptimizationRuns


def blocker(resource_type: str, resource_id: Any = None, name: Any = None) -> dict:
    value = {"type": resource_type}
    if resource_id is not None:
        value["id"] = str(resource_id)
    if name is not None and str(name).strip():
        value["name"] = str(name).strip()
    return value


def reject_blocked_deletion(blockers: Iterable[dict]) -> None:
    unique = []
    seen = set()
    for item in blockers:
        key = (item.get("type"), item.get("id"), item.get("name"))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    if unique:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Resource is still referenced and cannot be deleted",
                "blockers": unique,
            },
        )


async def load_owned_optimization_references(db, user_id) -> list[Any]:
    result = await db.execute(
        select(
            OptimizationRuns.id,
            OptimizationRuns.name,
            OptimizationRuns.bus_model_id,
            OptimizationRuns.input_params,
        ).where(OptimizationRuns.user_id == user_id)
    )
    return list(result.all())


def _params(run: Any) -> dict:
    value = getattr(run, "input_params", None)
    return value if isinstance(value, dict) else {}


def optimization_blockers_for_shift(runs: Iterable[Any], shift_id) -> list[dict]:
    target = str(shift_id)
    return [
        blocker("optimization_run", run.id, run.name)
        for run in runs
        if target in {str(value) for value in _params(run).get("shift_ids", [])}
    ]


def optimization_blockers_for_bus_model(runs: Iterable[Any], model_id) -> list[dict]:
    target = str(model_id)
    blockers = []
    for run in runs:
        direct = getattr(run, "bus_model_id", None)
        stored = _params(run).get("bus_model_id")
        if target in {str(value) for value in (direct, stored) if value is not None}:
            blockers.append(blocker("optimization_run", run.id, run.name))
    return blockers


def optimization_blockers_for_stop(runs: Iterable[Any], stop_id) -> list[dict]:
    target = str(stop_id)
    blockers = []
    for run in runs:
        stations = _params(run).get("charging_stations", [])
        if not isinstance(stations, list):
            continue
        if any(
            isinstance(station, dict) and str(station.get("stop_id")) == target
            for station in stations
        ):
            blockers.append(blocker("optimization_run", run.id, run.name))
    return blockers
