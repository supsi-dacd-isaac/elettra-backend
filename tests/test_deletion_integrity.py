"""Dependency responses are complete, stable and machine readable."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.deletion_integrity import (
    blocker,
    optimization_blockers_for_bus_model,
    optimization_blockers_for_shift,
    optimization_blockers_for_stop,
    reject_blocked_deletion,
)


def _run(*, params=None, model_id=None, name="Evaluation"):
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        bus_model_id=model_id,
        input_params=params or {},
    )


def test_shift_references_are_read_from_persisted_optimization_inputs() -> None:
    target = uuid4()
    matching = _run(params={"shift_ids": [str(uuid4()), str(target)]})
    unrelated = _run(params={"shift_ids": [str(uuid4())]})
    assert optimization_blockers_for_shift([matching, unrelated], target) == [
        blocker("optimization_run", matching.id, matching.name)
    ]


def test_bus_model_reference_supports_column_and_legacy_json() -> None:
    target = uuid4()
    direct = _run(model_id=target, name="Direct")
    legacy = _run(params={"bus_model_id": str(target)}, name="Legacy")
    unrelated = _run(model_id=uuid4())
    assert optimization_blockers_for_bus_model(
        [direct, legacy, unrelated], target
    ) == [
        blocker("optimization_run", direct.id, "Direct"),
        blocker("optimization_run", legacy.id, "Legacy"),
    ]


def test_station_reference_checks_every_candidate() -> None:
    target = uuid4()
    matching = _run(
        params={
            "charging_stations": [
                {"stop_id": str(uuid4())},
                {"stop_id": str(target), "num_slots": 2},
            ]
        }
    )
    malformed = _run(params={"charging_stations": "invalid"})
    assert optimization_blockers_for_stop([matching, malformed], target) == [
        blocker("optimization_run", matching.id, matching.name)
    ]


def test_blocked_response_is_deduplicated_and_structured() -> None:
    item = blocker("shift", uuid4(), "Morning")
    with pytest.raises(HTTPException) as exc:
        reject_blocked_deletion([item, dict(item)])
    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "message": "Resource is still referenced and cannot be deleted",
        "blockers": [item],
    }


def test_empty_dependency_set_allows_deletion() -> None:
    assert reject_blocked_deletion([]) is None
