"""Regression tests for structured, per-shift optimization input failures."""

from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.models import (
    Depots,
    GtfsStops,
    GtfsStopsTimes,
    OptimizationRuns,
    PredictionRuns,
    Shifts,
    ShiftsStructures,
    TripPredictions,
)
from app.services import optimization as service


def boundary(shift_id, shift_name, start, end):
    return {
        "shift_id": str(shift_id),
        "shift_name": shift_name,
        "first_stop_id": start,
        "last_stop_id": end,
    }


def test_daily_cycle_validation_reports_every_failure_with_stop_names():
    boundaries = [
        boundary("shift-ok", "40101", "depot-1", "depot-1"),
        boundary("shift-mismatch", "40104", "depot-2", "depot-1"),
        boundary("shift-not-depot", "40105", "terminus", "terminus"),
        boundary("shift-not-selected", "40106", "depot-3", "depot-3"),
    ]

    issues = service._daily_cycle_validation_issues(
        boundaries,
        depot_stop_ids={"depot-1", "depot-3"},
        selected_station_stop_ids={"depot-1"},
        stop_name_map={
            "depot-1": "TPL Rimessa 1",
            "depot-2": "TPL Rimessa 2",
            "depot-3": "TPL Rimessa 3",
            "terminus": "Lugano Centro",
        },
    )

    assert [issue["shift_name"] for issue in issues] == ["40104", "40105", "40106"]
    assert [issue["code"] for issue in issues] == [
        "daily_cycle_depot_mismatch",
        "daily_cycle_stop_not_depot",
        "daily_cycle_depot_not_selected",
    ]
    assert issues[0]["details"] == {
        "start_stop_id": "depot-2",
        "start_stop_name": "TPL Rimessa 2",
        "end_stop_id": "depot-1",
        "end_stop_name": "TPL Rimessa 1",
    }


def test_validation_failure_distinguishes_invalid_and_not_evaluated_shifts():
    shifts = [
        {"shift_id": "one", "shift_name": "40101"},
        {"shift_id": "two", "shift_name": "40104"},
        {"shift_id": "three", "shift_name": "40105"},
    ]
    issues = service._daily_cycle_validation_issues(
        [boundary("two", "40104", "depot-2", "depot-1")],
        depot_stop_ids={"depot-1", "depot-2"},
        selected_station_stop_ids={"depot-1", "depot-2"},
        stop_name_map={},
    )

    failure = service.OptimizationInputValidationError(issues, shifts).failure_payload()

    assert failure["code"] == "input_validation_failed"
    assert failure["phase"] == "input_validation"
    assert failure["shift_outcomes"] == [
        {
            "shift_id": "one",
            "shift_name": "40101",
            "status": "not_evaluated",
            "blocked_by_shift_ids": ["two"],
        },
        {
            "shift_id": "two",
            "shift_name": "40104",
            "status": "validation_failed",
            "failure_codes": ["daily_cycle_depot_mismatch"],
        },
        {
            "shift_id": "three",
            "shift_name": "40105",
            "status": "not_evaluated",
            "blocked_by_shift_ids": ["two"],
        },
    ]


def test_multiple_invalid_shifts_all_block_the_remaining_shift():
    shifts = [
        {"shift_id": "one", "shift_name": "A"},
        {"shift_id": "two", "shift_name": "B"},
        {"shift_id": "three", "shift_name": "C"},
    ]
    issues = service._daily_cycle_validation_issues(
        [
            boundary("one", "A", "x", "y"),
            boundary("two", "B", "z", "z"),
        ],
        depot_stop_ids={"z"},
        selected_station_stop_ids=set(),
        stop_name_map={},
    )

    outcomes = service.OptimizationInputValidationError(issues, shifts).failure_payload()[
        "shift_outcomes"
    ]

    assert [row["status"] for row in outcomes] == [
        "validation_failed",
        "validation_failed",
        "not_evaluated",
    ]
    assert outcomes[2]["blocked_by_shift_ids"] == ["one", "two"]


def test_failed_results_keep_legacy_error_and_add_structured_payload():
    issue = service._daily_cycle_validation_issues(
        [boundary("two", "40104", "depot-2", "depot-1")],
        depot_stop_ids={"depot-1", "depot-2"},
        selected_station_stop_ids={"depot-1", "depot-2"},
        stop_name_map={},
    )
    error = service.OptimizationInputValidationError(
        issue,
        [{"shift_id": "two", "shift_name": "40104"}],
    )
    finished_at = datetime(2026, 11, 4, 12, 30, tzinfo=timezone.utc)

    result = service._failed_run_results(
        error,
        execution_metadata={"worker": "test"},
        finished_at=finished_at,
    )

    assert result["error"].startswith("40104: daily repetition requires")
    assert result["failure"]["issues"][0]["shift_id"] == "two"
    assert result["execution"] == {
        "worker": "test",
        "finished_at": "2026-11-04T12:30:00+00:00",
    }
    assert service._failed_run_results(
        RuntimeError("solver unavailable"),
        execution_metadata=None,
        finished_at=finished_at,
    ) == {"error": "solver unavailable"}


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class MultiShiftPreparationDB:
    def __init__(self):
        self.owner = uuid4()
        self.model = uuid4()
        self.depot_1 = uuid4()
        self.depot_2 = uuid4()
        self.terminus = uuid4()
        self.shifts = [NS(id=uuid4(), name=name, bus_id=None) for name in ("40101", "40104", "40105")]
        self.predictions = []
        self.structures = {}
        self.trip_predictions = {}
        self.stop_times = {}
        boundaries = [
            (self.depot_1, self.depot_1),
            (self.depot_2, self.depot_1),
            (self.terminus, self.terminus),
        ]
        for shift, (start, end) in zip(self.shifts, boundaries, strict=True):
            trip_id = uuid4()
            prediction = NS(
                id=uuid4(),
                shift_id=shift.id,
                user_id=self.owner,
                bus_model_id=self.model,
                status="completed",
                model_name="test",
                prediction_stack="legacy",
                auxiliary_estimator_release=None,
                external_temp_celsius=-5,
                occupancy_percent=100,
                auxiliary_heating_type="default",
                contextual_parameters={"num_battery_packs": 10, "battery_capacity_kwh": 500},
            )
            self.predictions.append(prediction)
            self.structures[shift.id] = [NS(sequence_number=0, trip_id=trip_id)]
            self.trip_predictions[prediction.id] = [NS(
                sequence_number=0,
                trip_id=trip_id,
                prediction_kwh=100,
                prediction_median_kwh=None,
                quantiles=None,
                mass_sensitivity_kwh_per_kwh_batt=0.05,
            )]
            self.stop_times[trip_id] = [
                NS(stop_id=start, departure_time="08:00:00", arrival_time="08:00:00"),
                NS(stop_id=end, departure_time="09:00:00", arrival_time="09:00:00"),
            ]
        self.stop_time_queries = 0

    async def get(self, entity, key):
        if entity is PredictionRuns:
            return next(row for row in self.predictions if row.id == key)
        if entity is Shifts:
            return next(row for row in self.shifts if row.id == key)
        raise AssertionError(entity)

    @staticmethod
    def _query_value(query):
        return next(iter(query.compile().params.values()))

    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is GtfsStops:
            return Rows([
                (self.depot_1, "TPL Rimessa 1"),
                (self.depot_2, "TPL Rimessa 2"),
                (self.terminus, "Lugano Centro"),
            ])
        if entity is Depots:
            return Rows([self.depot_1])
        if entity is ShiftsStructures:
            return Rows(self.structures[self._query_value(query)])
        if entity is TripPredictions:
            return Rows(self.trip_predictions[self._query_value(query)])
        if entity is GtfsStopsTimes:
            self.stop_time_queries += 1
            return Rows(self.stop_times[self._query_value(query)])
        raise AssertionError(entity)


@pytest.mark.asyncio
async def test_preparation_collects_all_daily_cycle_errors_before_solver(monkeypatch):
    db = MultiShiftPreparationDB()
    monkeypatch.setattr(service, "_prediction_provenance", lambda runs: {})
    run = NS(
        bus_model_id=db.model,
        user_id=db.owner,
        mode="battery_only",
        input_params={
            "shift_ids": [str(shift.id) for shift in db.shifts],
            "daily_cycle": True,
            "charging_stations": [{"stop_id": str(db.depot_1), "num_slots": 1}],
        },
    )

    with pytest.raises(service.OptimizationInputValidationError) as caught:
        await service.prepare_optimization_input(
            db,
            run,
            [prediction.id for prediction in db.predictions],
        )

    assert db.stop_time_queries == 3
    failure = caught.value.failure_payload()
    assert {issue["shift_name"] for issue in failure["issues"]} == {"40104", "40105"}
    outcomes = {row["shift_name"]: row for row in failure["shift_outcomes"]}
    assert outcomes["40101"]["status"] == "not_evaluated"
    assert outcomes["40104"]["status"] == "validation_failed"
    assert outcomes["40105"]["status"] == "validation_failed"


@pytest.mark.asyncio
async def test_run_persists_structured_validation_failure(monkeypatch):
    run_id, owner, model, shift_id, prediction_id = (uuid4() for _ in range(5))
    prediction = NS(id=prediction_id, shift_id=shift_id)
    run = NS(
        id=run_id,
        user_id=owner,
        bus_model_id=model,
        mode="battery_only",
        status="pending",
        prediction_run_ids=[str(prediction_id)],
        input_params={"shift_ids": [str(shift_id)], "consumption_basis": "mean"},
        results=None,
        completed_at=None,
    )

    class DB:
        commit = AsyncMock()
        rollback = AsyncMock()

        async def get(self, entity, key):
            if entity is OptimizationRuns:
                return run
            if entity is PredictionRuns:
                return prediction
            raise AssertionError(entity)

    issue = {
        "code": "daily_cycle_depot_mismatch",
        "shift_id": str(shift_id),
        "shift_name": "40104",
        "message": "40104: daily repetition requires matching depots",
        "details": {},
    }
    failure = service.OptimizationInputValidationError(
        [issue],
        [{"shift_id": str(shift_id), "shift_name": "40104"}],
    )
    monkeypatch.setattr(service, "_prediction_provenance", lambda runs: {})
    monkeypatch.setattr(
        service,
        "select_references",
        lambda *args, **kwargs: {str(shift_id): prediction},
    )
    monkeypatch.setattr(service, "_prediction_component_summary", AsyncMock(return_value=None))
    monkeypatch.setattr(
        service,
        "prepare_optimization_input",
        AsyncMock(side_effect=failure),
    )

    db = DB()
    await service.run_optimization(
        db,
        run_id,
        execution_metadata={"worker": "unit-test"},
    )

    assert run.status == "failed"
    assert run.results["error"] == str(failure)
    assert run.results["failure"]["phase"] == "input_validation"
    assert run.results["failure"]["shift_outcomes"][0]["status"] == "validation_failed"
    assert run.results["execution"]["worker"] == "unit-test"
    assert db.rollback.await_count == 1
