from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import numpy as np
import pytest

from app.models import BusesModels, Shifts
from app.services import aggregate_consumption, prediction
from app.services.hvac_policy import FEASIBILITY_POLICY, LEGACY_POLICY, fingerprint
from app.services.runtime_release import PredictionStack
from app.services.trip_occupancy import PROGRAMME_OCCUPANCY_POLICY, TRIP_OCCUPANCY_KEY
from simulation.aggregate_consumption import AggregateOptions
from test_yearly_prediction_batch import prepared_cases


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [LEGACY_POLICY, FEASIBILITY_POLICY])
async def test_programme_trip_occupancy_is_used_and_replayed(monkeypatch, policy):
    case = prepared_cases(True)[0]
    case.predictor.metadata["passenger_mass_kg"] = 68
    stack = NS(stack=PredictionStack.VECTO_G2, auxiliary_estimator="test-estimator")
    for service in (prediction, aggregate_consumption):
        monkeypatch.setattr(service, "get_predictor", lambda _: case.predictor)
        monkeypatch.setattr(service, "resolve_prediction_selection", lambda **kwargs: stack)
        monkeypatch.setattr(service, "validate_model_stack_contract", lambda *args: None)

    depot, gtfs, transfer = uuid4(), uuid4(), uuid4()
    classified = [(1, depot, "depot"), (2, gtfs, "gtfs"),
                  (3, transfer, "transfer"), (4, transfer, "transfer")]
    stats = [dict(trip_id=str(trip), sequence_number=seq, distance=4000.)
             for seq, trip, _ in classified]
    timetable = [dict(trip_id=str(trip), sequence_number=seq, timezone="Europe/Zurich",
                      start_minute=60 + 20 * seq, end_minute=80 + 20 * seq,
                      start_stop_id="a", end_stop_id="b")
                 for seq, trip, _ in classified]
    context = {"auxiliary_policy_id": policy}
    if policy == FEASIBILITY_POLICY:
        inputs = {"schedule": timetable, "policy_id": policy}
        context.update(hvac_inputs=inputs, hvac_input_fingerprint=fingerprint(inputs))
    new_shift, old_shift, bus_id = uuid4(), uuid4(), uuid4()

    def run(shift_id):
        return NS(id=uuid4(), shift_id=shift_id, bus_model_id=bus_id, model_name="test",
                  prediction_stack="vecto-g2", auxiliary_estimator_release=stack.auxiliary_estimator,
                  external_temp_celsius=-5, occupancy_percent=50, auxiliary_heating_type="default",
                  contextual_parameters=dict(context), status="running")

    def get(entity, key):
        if entity is BusesModels:
            return NS(specs=case.specs)
        assert entity is Shifts
        return NS(programme={"version": 1} if key == new_shift else None)

    db = NS(get=AsyncMock(side_effect=get), execute=AsyncMock(return_value=NS(all=lambda: classified)),
            commit=AsyncMock(), add=Mock())
    cache = {}
    planned, historical = run(new_shift), run(old_shift)
    new = await prediction._prepare_prediction(db, planned, 10, stats, hvac_cache=cache)
    old = await prediction._prepare_prediction(db, historical, 10, stats, hvac_cache=cache)
    await prediction._prepare_prediction(db, run(new_shift), 10, stats, hvac_cache=cache)
    db.execute.assert_awaited_once()
    assert new.override_mass.tolist() == [13500, 16220, 13500, 13500]
    assert old.override_mass.tolist() == [16220] * 4
    np.testing.assert_array_equal(new.qrf_reference_mass, old.qrf_reference_mass)

    forecast, past = prediction._predict_combined([new, old], [.5])
    assert [row["qrf_residual_kwh"] for row in forecast["predictions"]] == [
        row["qrf_residual_kwh"] for row in past["predictions"]]
    for actual, original, (_, _, trip_type) in zip(forecast["predictions"], past["predictions"], classified, strict=True):
        if trip_type == "gtfs":
            assert actual == original
        else:
            assert actual["mechanical_greybox_kwh"] < original["mechanical_greybox_kwh"]
            assert actual["hvac_electrical_kwh"] > original["hvac_electrical_kwh"]
    await prediction._store_prediction(db, new, forecast, [.5])
    assert planned.contextual_parameters[TRIP_OCCUPANCY_KEY] == {
        "policy_id": PROGRAMME_OCCUPANCY_POLICY,
        "occurrences": [{"sequence_number": seq, "trip_id": str(trip), "status": trip_type}
                        for seq, trip, trip_type in classified],
    }
    records = [call.args[0] for call in db.add.call_args_list]
    assert [record.trip_id for record in records] == [UUID(row["trip_id"]) for row in stats]

    async def no_live_classification(entity, key):
        assert entity is BusesModels
        return get(entity, key)

    db.get.side_effect = no_live_classification
    db.execute.return_value = NS(scalars=lambda: NS(all=lambda: records))
    profiles = await aggregate_consumption.load_aggregate_profiles(
        db, [(planned, AggregateOptions(100, 100))], statistics_cache={str(new_shift): stats},
    )
    np.testing.assert_allclose(profiles[str(planned.id)]["mean"], [row.prediction_kwh for row in records])
