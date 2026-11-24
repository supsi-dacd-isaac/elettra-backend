from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from app.models import Shifts
from app.services.shift_weekdays import ShiftWeekdays, resolve_study_weekdays, weekdays_for_shifts


@pytest.mark.asyncio
async def test_weekdays_for_shifts_intersects_calendar_rows_in_one_query():
    planned, historical, no_calendar, no_overlap, partial = [uuid4() for _ in range(5)]
    db = AsyncMock()
    db.execute.return_value = [
        (historical, "gtfs", "service-a", 1, 1, 1, 1, 1, 0, 0),
        (historical, "gtfs", "service-b", 0, 1, 1, 1, 1, 1, 0),
        (historical, "school", "auxiliary_tue", 0, 1, 0, 0, 0, 0, 0),
        (historical, "service", "auxiliary", 1, 1, 1, 1, 1, 1, 1),
        (no_calendar, "school", "auxiliary", 1, 1, 1, 1, 1, 1, 1),
        (no_overlap, "gtfs", "monday-only", 1, 0, 0, 0, 0, 0, 0),
        (no_overlap, "service", "auxiliary_tue", 0, 1, 0, 0, 0, 0, 0),
        (partial, "gtfs", "monday-only", 1, 0, 0, 0, 0, 0, 0),
        (partial, "gtfs", None, None, None, None, None, None, None, None),
    ]
    shifts = [Shifts(id=id) for id in (historical, no_calendar, no_overlap, partial)]
    shifts.append(Shifts(id=planned, programme={"version": 1}, service_weekday="sunday"))

    result = await weekdays_for_shifts(db, shifts)

    db.execute.assert_awaited_once()
    params = db.execute.call_args.args[0].compile().params
    assert set(params["shift_id_1"]) == {historical, no_calendar, no_overlap, partial}
    assert set(params["status_1"]) == {"gtfs", "school", "service"}
    assert result[planned].compatible_weekdays == ["sunday"]
    assert result[planned].planner_version == 1
    assert result[planned].service_weekday == "sunday"
    assert result[historical].compatible_weekdays == ["tuesday"]
    assert result[historical].service_weekday is None
    assert result[no_calendar].compatible_weekdays is None
    assert result[no_overlap].compatible_weekdays == []
    assert result[partial].compatible_weekdays is None
    assert result[partial].known_weekdays == {"monday"}


def test_study_weekday_records_selection_or_unique_inference():
    first = ShiftWeekdays(known_weekdays=frozenset({"monday", "tuesday"}), complete=True)
    second = ShiftWeekdays(known_weekdays=frozenset({"tuesday", "wednesday"}), complete=True)
    weekdays = {uuid4(): first, uuid4(): second}

    explicit = resolve_study_weekdays("tuesday", weekdays)
    assert explicit["service_weekday"] == "tuesday"
    assert explicit["compatible_weekdays"] == ["tuesday"]
    assert explicit["service_weekday_provenance"] == {
        "source": "selected", "complete": True, "unverified_shift_ids": [],
    }

    inferred = resolve_study_weekdays(None, weekdays)
    assert inferred["service_weekday"] == "tuesday"
    assert inferred["compatible_weekdays"] == ["tuesday"]
    ambiguous = resolve_study_weekdays(None, {uuid4(): first})
    assert ambiguous["service_weekday"] is None
    assert ambiguous["compatible_weekdays"] == ["monday", "tuesday"]


def test_new_studies_require_verifiable_calendars_even_when_legacy_clients_omit_the_day():
    calendar_id, missing_id = uuid4(), uuid4()
    known = ShiftWeekdays(known_weekdays=frozenset({"tuesday"}), complete=True)
    weekdays = {calendar_id: known, missing_id: ShiftWeekdays()}
    with pytest.raises(HTTPException, match="could not be verified"):
        resolve_study_weekdays(None, weekdays)

    with pytest.raises(HTTPException, match="could not be verified"):
        resolve_study_weekdays("tuesday", weekdays)
    weekdays[calendar_id] = ShiftWeekdays(
        planner_version=1, service_weekday="tuesday", known_weekdays=frozenset({"tuesday"}), complete=True,
    )
    with pytest.raises(HTTPException, match="could not be verified"):
        resolve_study_weekdays(None, weekdays)
    with pytest.raises(HTTPException, match="requested service weekday"):
        resolve_study_weekdays("monday", weekdays)

    weekdays[missing_id] = ShiftWeekdays(known_weekdays=frozenset({"monday"}))
    with pytest.raises(HTTPException, match="no common service weekday"):
        resolve_study_weekdays(None, weekdays)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["shift", "prediction", "weekday"])
async def test_study_source_and_calendar_validation_precedes_persistence(monkeypatch, failure):
    from app.routers import simulation
    from app.schemas.requests import OptimizationRequest
    from app.services import gtfs_datasets

    owner, shift_id, prediction_id, other_shift = [uuid4() for _ in range(4)]
    shift = Shifts(id=shift_id, programme={"version": 1}, service_weekday="monday")
    prediction = SimpleNamespace(
        id=prediction_id, user_id=owner, shift_id=other_shift, status="completed",
        bus_model_id=uuid4(), model_name="legacy", prediction_stack="legacy",
    )
    source = AsyncMock(return_value=shift)
    if failure == "shift":
        source.side_effect = HTTPException(404, "Shift not found")
    monkeypatch.setattr(simulation, "get_owned_shift", source)
    monkeypatch.setattr(simulation, "get_owned_prediction_run", AsyncMock(return_value=prediction))
    monkeypatch.setattr(simulation, "resolve_prediction_selection", Mock(return_value=SimpleNamespace(
        stack=SimpleNamespace(value="legacy"), model_release="legacy", auxiliary_estimator="legacy",
    )))
    monkeypatch.setattr(gtfs_datasets, "capture_provenance", AsyncMock(return_value={"shifts": {}}))
    prepare = AsyncMock()
    monkeypatch.setattr(simulation, "ensure_shift_profiles_ready", prepare)
    db = SimpleNamespace(add=Mock(), execute=AsyncMock(), commit=AsyncMock())
    request = OptimizationRequest(
        name="Study", mode="battery_only", shift_ids=[shift_id], service_weekday="tuesday",
        **({"prediction_run_ids": [prediction_id]} if failure == "prediction"
           else {"prediction_params": {"external_temp_celsius": 15}}),
    )

    tasks = BackgroundTasks()
    expected = {
        "shift": "Shift not found", "prediction": "requesting user and selected shift",
        "weekday": "requested service weekday",
    }
    with pytest.raises(HTTPException, match=expected[failure]) as exc:
        await simulation.create_optimization_run(tasks, request, db, SimpleNamespace(id=owner, company_id=uuid4()))

    assert exc.value.status_code == (404 if failure == "shift" else 422)
    db.execute.assert_not_awaited()
    prepare.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    assert not tasks.tasks


def test_optimization_request_validates_weekday_name():
    from app.schemas.requests import OptimizationRequest

    with pytest.raises(ValidationError, match="service_weekday"):
        OptimizationRequest(
            name="Study", mode="battery_only", shift_ids=[uuid4()], service_weekday="weekday",
            prediction_params={"external_temp_celsius": 15},
        )
