from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.routers.simulation import _serialize_optimization_run_list_item


def test_optimization_list_exposes_yearly_eligibility_without_heavy_results() -> None:
    run = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        bus_model_id=uuid4(),
        name="Eligible evaluation",
        mode="joint",
        status="completed",
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        prediction_run_ids=[],
        input_params={"consumption_basis": "mean"},
        results={
            "electrification_feasible": True,
            "integrity_audit": {"yearly_eligible": True},
            "battery_results": {"large-heavy-payload": {}},
        },
    )

    item = _serialize_optimization_run_list_item(run)

    assert item.electrification_feasible is True
    assert item.yearly_eligible is True
    assert not hasattr(item, "results")
    assert not hasattr(item, "input_params")


def test_optimization_list_preserves_unknown_eligibility_for_legacy_results() -> None:
    run = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        bus_model_id=None,
        name="Legacy evaluation",
        mode="battery_only",
        status="completed",
        created_at=datetime.now(timezone.utc),
        completed_at=None,
        prediction_run_ids=[],
        input_params={},
        results={"electrification_feasible": True},
    )

    assert _serialize_optimization_run_list_item(run).yearly_eligible is None
