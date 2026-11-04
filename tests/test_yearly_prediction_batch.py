"""Annual inference must preserve duty, mass, auxiliary and quantile semantics."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError
from quantile_forest import RandomForestQuantileRegressor

from app.routers import simulation as router
from app.schemas.requests import YearlyPredictionRequest
from app.services import prediction as service
from elettra_core import AuxiliaryEnergyComponents, CappedRegenAffineGreyBox, HybridGreyboxQRF
from simulation.consumption_prediction import ConsumptionPredictor
from simulation.greybox_models import CombinedGreyboxQRF


def prepared_cases(hybrid):
    greybox = CappedRegenAffineGreyBox()
    greybox.theta_ = greybox.initial_bounds()[0]
    forest = RandomForestQuantileRegressor(n_estimators=7, max_samples_leaf=1, random_state=42)
    forest.fit(pd.DataFrame({'greybox_pred_kwh': np.arange(40, dtype=float)}),
               np.sin(np.arange(40, dtype=float)))
    if hybrid:
        model = HybridGreyboxQRF(greybox=greybox, qrf=forest,
                                selected_features=['greybox_pred_kwh'], prediction_stack='vecto-g2',
                                qrf_reference_occupancy_percent=21.5)
    else:
        model = CombinedGreyboxQRF(greybox, forest, ['greybox_pred_kwh'])
    predictor = ConsumptionPredictor()
    predictor.model = model
    predictor.metadata = {'greybox_params': greybox.get_params_dict()}
    predictor.required_features = ['greybox_pred_kwh']
    predictor.is_greybox = True
    predictor.is_hybrid_greybox = hybrid

    # Synthetic trip features isolate batching from the already-tested feature builder.
    def features(trip_statistics, bus_length_m, battery_capacity_kwh, external_temp_celsius):
        return pd.DataFrame([{
            'trip_id': item['trip_id'], 'bus_length_m': bus_length_m,
            'bus_battery_kwh': battery_capacity_kwh, 'external_temp_celsius': external_temp_celsius,
            'total_distance_m': item['distance'], 'driving_average_speed_kmh': 20.,
            'total_ascent_m': 20., 'total_descent_m': 10., 'driving_time_minutes': 15.,
            'total_duration_minutes': 20., 'pct_downhill_segments': 20.,
        } for item in trip_statistics])
    predictor.prepare_features = features
    repeated_trip = str(uuid4())
    specs = dict(bus_length_m=12, battery_pack_size_kwh=50, battery_pack_weight_kg=350,
                 empty_weight_kg=10000, max_passengers=80, max_battery_packs=16)
    cases = []
    for temperature, packs, occupancy, distances in [(-5, 10, 100, [4000, 4000]),
                                                     (20, 16, 50, [8000]), (-5, 16, 0, [8000])]:
        mass = service.physical_bus_mass(specs, occupancy_percent=occupancy, num_battery_packs=packs)
        trips = [dict(trip_id=repeated_trip, sequence_number=i * 3, distance=distance)
                 for i, distance in enumerate(distances)]
        def auxiliary(frame, temp=temperature):
            energy = frame['total_duration_minutes'].to_numpy() * (30 - temp) / 60
            if not hybrid:
                return energy
            zero = np.zeros(len(frame))
            return AuxiliaryEnergyComponents(energy, zero, energy * 0.2, energy * 0.02, zero)
        cases.append(service.PreparedPrediction(
            SimpleNamespace(shift_id=uuid4(), external_temp_celsius=temperature), specs, None,
            predictor, mass, 68, trips, auxiliary, None,
            np.full(len(trips), mass.total_weight_kg),
            np.full(len(trips), mass.empty_weight_kg + mass.battery_weight_kg + 21.5 * .01 * 80 * 68)
            if hybrid else None, 21.5 if hybrid else None,
        ))
    return cases


@pytest.mark.parametrize('hybrid,quantiles', [(False, [0.05, 0.5, 0.95]), (True, [0.05, 0.5, 0.95]), (True, [0.5])])
def test_combined_predictions_equal_individual_predictions(hybrid, quantiles):
    cases = prepared_cases(hybrid)
    expected = [case.predictor.predict_from_json(
        **case.prediction_arguments(), quantiles=quantiles,
        aux_energy_fn=case.aux_fn, qrf_reference_mass=case.qrf_reference_mass,
    ) for case in cases]
    assert service._predict_combined(cases, quantiles) == expected
    assert service._predict_combined(list(reversed(cases)), quantiles) == list(reversed(expected))
    assert [result for case in cases for result in service._predict_combined([case], quantiles)] == expected


def test_combined_predictions_fail_on_lost_rows():
    cases = prepared_cases(True)
    cases[0].predictor.predict = Mock(return_value=pd.DataFrame())
    with pytest.raises(ValueError, match='cardinality'):
        service._predict_combined(cases, [0.5])


def request_body():
    return dict(yearly_analysis_id=uuid4(), bus_model_id=uuid4(),
                scenarios=[dict(scenario_id='cold', external_temp_celsius=-5),
                           dict(scenario_id='warm', external_temp_celsius=20)],
                assignments=[dict(shift_id=uuid4(), num_battery_packs=10),
                             dict(shift_id=uuid4(), num_battery_packs=16)])


@pytest.mark.parametrize('invalid', ['duplicate_shift', 'duplicate_scenario', 'nan', 'fractional_packs', 'empty'])
def test_yearly_request_rejects_ambiguous_or_invalid_matrix(invalid):
    body = request_body()
    if invalid == 'duplicate_shift': body['assignments'].append(body['assignments'][0])
    elif invalid == 'duplicate_scenario': body['scenarios'].append(body['scenarios'][0])
    elif invalid == 'nan': body['scenarios'][0]['external_temp_celsius'] = float('nan')
    elif invalid == 'fractional_packs': body['assignments'][0]['num_battery_packs'] = 10.5
    else: body['assignments'] = []
    with pytest.raises(ValidationError):
        YearlyPredictionRequest(**body)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'ownership', 'profile', 'already_exists'])
async def test_endpoint_schedules_one_task_after_validating_whole_matrix(monkeypatch, failure):
    request = YearlyPredictionRequest(**request_body())
    user = SimpleNamespace(id=uuid4(), company_id=uuid4())
    bus = SimpleNamespace(specs=dict(bus_length_m=12, battery_pack_size_kwh=50,
        battery_pack_weight_kg=350, empty_weight_kg=10000, max_passengers=80, max_battery_packs=16))
    for name, value in [('get_owned_yearly_analysis', SimpleNamespace()), ('get_owned_bus_model', bus),
                        ('get_owned_shift', SimpleNamespace())]:
        monkeypatch.setattr(router, name, AsyncMock(return_value=value))
    if failure == 'ownership':
        router.get_owned_shift.side_effect = [None, HTTPException(404, 'inaccessible')]
    monkeypatch.setattr(router, 'ensure_shift_profiles_ready', AsyncMock())
    if failure == 'profile':
        router.ensure_shift_profiles_ready.side_effect = HTTPException(409, 'profile not ready')
    db = SimpleNamespace(add=Mock(), flush=AsyncMock(), commit=AsyncMock(), execute=AsyncMock(),
                         scalar=AsyncMock(return_value=uuid4() if failure == 'already_exists' else None))
    async def assign_id():
        db.add.call_args.args[0].id = uuid4()
    db.flush.side_effect = assign_id
    tasks = BackgroundTasks()
    if failure:
        with pytest.raises(HTTPException):
            await router.create_yearly_prediction_runs(request, tasks, db, user)
        db.add.assert_not_called()
        db.commit.assert_not_called()
        assert not tasks.tasks
    else:
        result = await router.create_yearly_prediction_runs(request, tasks, db, user)
        assert len(tasks.tasks) == 1
        assert [packs for _, packs in tasks.tasks[0].args[0]] == [10, 16, 10, 16]
        assert [row['scenario_id'] for row in result['scenarios']] == ['cold', 'warm']
        assert len(set(result['prediction_run_ids'])) == 4
        assert router.ensure_shift_profiles_ready.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_yearly_statistics_are_reused_and_failures_are_terminal(monkeypatch, fail):
    cases = prepared_cases(True)
    owner, analysis, shift = uuid4(), uuid4(), uuid4()
    runs = [SimpleNamespace(id=uuid4(), status='pending', shift_id=shift, user_id=owner,
             yearly_analysis_id=analysis, model_name='same', prediction_stack='vecto-g2',
             auxiliary_estimator_release='same') for _ in range(3)]
    by_id = {run.id: run for run in runs}
    db = SimpleNamespace(get=AsyncMock(side_effect=lambda _model, id: by_id[id]),
                         commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(service, 'load_shift_trip_statistics', AsyncMock(return_value=cases[0].trip_stats))
    monkeypatch.setattr(service, '_prepare_prediction', AsyncMock(side_effect=cases))
    monkeypatch.setattr(service, '_store_prediction', AsyncMock())
    monkeypatch.setattr(service, '_predict_combined', Mock(
        side_effect=RuntimeError('inference failed') if fail else None, return_value=[{}, {}, {}]))
    jobs = [(run.id, 10) for run in runs]
    if fail:
        with pytest.raises(RuntimeError, match='inference failed'):
            await service.predict_yearly_consumption(db, jobs, [.5])
        assert all(run.status == 'failed' for run in runs)
        service._store_prediction.assert_not_called()
    else:
        await service.predict_yearly_consumption(db, jobs, [.5])
        assert service._store_prediction.await_count == 3
    assert service.load_shift_trip_statistics.await_count == 1
    assert service._predict_combined.call_count == 1
