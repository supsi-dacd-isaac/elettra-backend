"""Request limits must fail before rows or background jobs are created."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from app.routers import simulation as simulation_router
from app.schemas.requests import (
    ChargingStationConfig,
    OptimizationRequest,
    PredictionRequest,
    ShiftCreateRequest,
    ShiftUpdateRequest,
    TripStatisticsRequest,
    YearlyPredictionRequest,
)


def _prediction(**overrides):
    values = {
        "shift_ids": [uuid4()],
        "bus_model_id": uuid4(),
        "external_temp_celsius": 10,
    }
    values.update(overrides)
    return PredictionRequest(**values)


def _optimization(**overrides):
    values = {
        "name": "Bounded feasibility",
        "mode": "battery_only",
        "shift_ids": [uuid4()],
        "prediction_run_ids": [uuid4()],
    }
    values.update(overrides)
    return OptimizationRequest(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"shift_ids": []},
        {"shift_ids": [uuid4(), uuid4()] * 51},
        {"external_temp_celsius": float("nan")},
        {"external_temp_celsius": float("inf")},
        {"num_battery_packs": 0},
        {"num_battery_packs": 2.5},
        {"quantiles": [index / 100 for index in range(1, 22)]},
    ],
)
def test_prediction_request_rejects_unbounded_or_invalid_values(overrides) -> None:
    with pytest.raises(ValidationError):
        _prediction(**overrides)


@pytest.mark.parametrize(
    "payload",
    [
        {"stop_id": uuid4(), "slot_costs_chf": [], "max_total_power_kw": 100},
        {"stop_id": uuid4(), "slot_costs_chf": [-1], "max_total_power_kw": 100},
        {"stop_id": uuid4(), "slot_costs_chf": [float("inf")], "max_total_power_kw": 100},
        {"stop_id": uuid4(), "num_slots": 0, "max_total_power_kw": 100},
        {"stop_id": uuid4(), "num_slots": 1, "max_total_power_kw": 0},
        {"stop_id": uuid4(), "num_slots": 1, "max_total_power_kw": float("nan")},
        {"stop_id": uuid4(), "num_slots": 1, "max_total_power_kw": 100, "max_power_per_slot_kw": -1},
    ],
)
def test_charging_station_rejects_empty_negative_or_nonfinite_capacity(payload) -> None:
    with pytest.raises(ValidationError):
        ChargingStationConfig(**payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"shift_ids": [uuid4() for _ in range(101)]},
        {"prediction_run_ids": [uuid4() for _ in range(501)]},
        {"max_solver_time_seconds": 0},
        {"max_solver_time_seconds": 3601},
        {"mip_rel_gap": -0.1},
        {"mip_abs_gap": float("inf")},
        {"feasibility_tol": 0},
        {"max_battery_penalty_per_kwh": float("nan")},
        {"session_penalty_weight": float("inf")},
    ],
)
def test_optimization_request_rejects_unbounded_solver_inputs(overrides) -> None:
    with pytest.raises(ValidationError):
        _optimization(**overrides)


def test_prediction_and_optimization_ids_must_be_unique() -> None:
    shift_id = uuid4()
    with pytest.raises(ValidationError, match="shift_ids must be unique"):
        _prediction(shift_ids=[shift_id, shift_id])

    prediction_id = uuid4()
    with pytest.raises(ValidationError, match="prediction_run_ids must be unique"):
        _optimization(prediction_run_ids=[prediction_id, prediction_id])


def test_yearly_matrix_has_explicit_cardinality_limits() -> None:
    common = {
        "yearly_analysis_id": uuid4(),
        "bus_model_id": uuid4(),
        "assignments": [{"shift_id": uuid4(), "num_battery_packs": 10}],
    }
    with pytest.raises(ValidationError):
        YearlyPredictionRequest(
            **common,
            scenarios=[
                {"scenario_id": f"scenario-{index}", "external_temp_celsius": 10}
                for index in range(101)
            ],
        )


@pytest.mark.parametrize(
    "schema,payload",
    [
        (ShiftCreateRequest, {"name": "  ", "bus_id": uuid4(), "trip_ids": [uuid4()]}),
        (ShiftCreateRequest, {"name": "Shift", "bus_id": uuid4(), "trip_ids": []}),
        (ShiftUpdateRequest, {"trip_ids": []}),
        (ShiftUpdateRequest, {"name": "\t"}),
        (TripStatisticsRequest, {"trip_ids": []}),
    ],
)
def test_shift_and_statistics_contracts_reject_empty_work(schema, payload) -> None:
    with pytest.raises(ValidationError):
        schema(**payload)


def test_trip_lists_reject_duplicates() -> None:
    trip_id = uuid4()
    for schema, payload in (
        (ShiftCreateRequest, {"name": "Shift", "trip_ids": [trip_id, trip_id]}),
        (ShiftUpdateRequest, {"trip_ids": [trip_id, trip_id]}),
        (TripStatisticsRequest, {"trip_ids": [trip_id, trip_id]}),
    ):
        with pytest.raises(ValidationError, match="trip_ids must be unique"):
            schema(**payload)


@pytest.mark.asyncio
async def test_joint_missing_pack_price_fails_before_persistence(monkeypatch) -> None:
    owner_id = uuid4()
    company_id = uuid4()
    model_id = uuid4()
    shift_id = uuid4()
    prediction_id = uuid4()
    model = SimpleNamespace(id=model_id, specs={})
    prediction = SimpleNamespace(
        id=prediction_id,
        user_id=owner_id,
        shift_id=shift_id,
        bus_model_id=model_id,
        status="completed",
        model_name="test-model",
        prediction_stack="legacy",
        auxiliary_estimator_release=None,
    )
    monkeypatch.setattr(
        simulation_router, "get_owned_bus_model", AsyncMock(return_value=model)
    )
    monkeypatch.setattr(
        simulation_router,
        "get_owned_shift",
        AsyncMock(return_value=SimpleNamespace(id=shift_id, bus_id=uuid4())),
    )
    monkeypatch.setattr(
        simulation_router, "get_owned_prediction_run", AsyncMock(return_value=prediction)
    )
    monkeypatch.setattr(
        simulation_router,
        "resolve_prediction_selection",
        Mock(
            return_value=SimpleNamespace(
                stack=SimpleNamespace(value="legacy"),
                model_release="test-model",
                auxiliary_estimator="unused",
            )
        ),
    )
    profiles = AsyncMock()
    monkeypatch.setattr(simulation_router, "ensure_shift_profiles_ready", profiles)

    request = OptimizationRequest(
        name="Joint missing pack price",
        mode="joint",
        shift_ids=[shift_id],
        bus_model_id=model_id,
        prediction_run_ids=[prediction_id],
        charging_stations=[
            {
                "stop_id": uuid4(),
                "slot_costs_chf": [100_000],
                "max_total_power_kw": 300,
            }
        ],
    )
    db = SimpleNamespace(add=Mock(), commit=AsyncMock())
    tasks = BackgroundTasks()

    with pytest.raises(HTTPException) as exc:
        await simulation_router.create_optimization_run(
            tasks,
            request,
            db,
            SimpleNamespace(id=owner_id, company_id=company_id),
        )

    assert exc.value.status_code == 422
    assert "battery_pack_cost_chf" in exc.value.detail
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    profiles.assert_not_awaited()
    assert not tasks.tasks
