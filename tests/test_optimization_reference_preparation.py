"""Database-adapter contract tests with no external prediction services."""
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from app.models import BusesModels, GtfsStopsTimes, PredictionRuns, Shifts, ShiftsStructures, TripPredictions
from app.services import optimization as service
from app.routers.yearly_analysis import _require_verified_optimization
from fastapi import HTTPException


class Rows:
    def __init__(self, rows): self.rows = rows
    def scalars(self): return self
    def all(self): return self.rows


class DB:
    def __init__(self):
        self.shift, self.owner, self.model, self.trip = uuid4(), uuid4(), uuid4(), uuid4()
        self.predictions = [NS(id=uuid4(), shift_id=self.shift, user_id=self.owner,
            bus_model_id=self.model, status="completed", model_name="test", prediction_stack="legacy",
            auxiliary_estimator_release=None, external_temp_celsius=10, occupancy_percent=50,
            auxiliary_heating_type="default", contextual_parameters={"num_battery_packs": n,
                "battery_capacity_kwh": n*50}) for n in range(10, 17)]
        self.trip_predictions = [NS(sequence_number=0, trip_id=self.trip, prediction_kwh=30,
            mass_sensitivity_kwh_per_kwh_batt=.05)]
        self.specs = {"battery_pack_size_kwh": 50, "min_battery_packs": 10, "max_battery_packs": 16}
        self.complete = True
    async def get(self, entity, key):
        if entity is PredictionRuns: return next(p for p in self.predictions if p.id == key)
        if entity is BusesModels: return NS(specs=self.specs)
        if entity is Shifts: return NS(id=self.shift, name="Duty", bus_id=None)
        raise AssertionError(entity)
    async def execute(self, query):
        entity = query.column_descriptions[0]["entity"]
        if entity is TripPredictions: return Rows(self.trip_predictions)
        if entity is ShiftsStructures:
            return Rows([NS(sequence_number=0, trip_id=self.trip)] if self.complete else [])
        if entity is GtfsStopsTimes:
            return Rows([NS(departure_time="08:00:00", arrival_time="08:00:00", stop_id="a"),
                         NS(departure_time="08:30:00", arrival_time="08:30:00", stop_id="b")])
        raise AssertionError(entity)


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(service, "_prediction_provenance", lambda runs: {})
    db = DB()
    run = NS(bus_model_id=db.model, user_id=db.owner, mode="battery_only",
             input_params={"shift_ids": [str(db.shift)]})
    return db, run


@pytest.mark.asyncio
async def test_service_groups_seven_references_and_keeps_one_full_trip_sequence(context):
    db, run = context
    buses, stations, config = await service.prepare_optimization_input(db, run, [p.id for p in db.predictions])
    assert len(buses) == 1
    assert buses[0].reference_packs == 16
    assert buses[0].trips[0].base_energy_kwh == 30
    assert buses[0].trips[0].sensitivity == .05


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
async def test_missing_sensitivity_does_not_turn_into_zero(context, bad):
    db, run = context
    db.trip_predictions[0].mass_sensitivity_kwh_per_kwh_batt = bad
    with pytest.raises(ValueError, match="sensitivity"):
        await service.prepare_optimization_input(db, run, [p.id for p in db.predictions])


@pytest.mark.asyncio
async def test_partial_shift_predictions_cannot_be_silently_accepted(context):
    db, run = context
    db.complete = False
    with pytest.raises(ValueError, match="complete shift"):
        await service.prepare_optimization_input(db, run, [p.id for p in db.predictions])


def test_new_yearly_analysis_requires_verified_design_not_old_solver_success():
    run = NS(user_id="owner", status="completed", input_params={"shift_ids": ["s"]},
        results={"solver_status": "optimal", "electrification_feasible": True,
                 "battery_results": {"s": {}}, "per_bus_summary": [{"shift_id": "s"}]})
    with pytest.raises(HTTPException) as exc:
        _require_verified_optimization(run, "owner")
    assert exc.value.status_code == 409
