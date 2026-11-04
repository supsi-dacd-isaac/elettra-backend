"""All known optimization forecasts are inferred together, with stable ordering."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import numpy as np
import pytest
from fastapi import BackgroundTasks
from app.models import PredictionRuns
from app.schemas.requests import PredictionRequest
from app.routers import simulation as router
from app.services import prediction, optimization, aggregate_consumption
from simulation.aggregate_consumption import AggregateOptions, empirical_qrf_support
from test_yearly_prediction_batch import prepared_cases


@pytest.mark.asyncio
async def test_standard_prediction_endpoint_schedules_all_duties_together(monkeypatch):
    request = PredictionRequest(shift_ids=[uuid4(), uuid4(), uuid4()], bus_model_id=uuid4(),
                               external_temp_celsius=-5, num_battery_packs=14)
    user = NS(id=uuid4(), company_id=uuid4())
    monkeypatch.setattr(router, 'get_owned_bus_model', AsyncMock(return_value=NS(specs={})))
    monkeypatch.setattr(router, 'get_owned_shift', AsyncMock())
    monkeypatch.setattr(router, 'ensure_shift_profiles_ready', AsyncMock())
    monkeypatch.setattr(router, '_validate_vecto_prediction_request', Mock())
    db = NS(add=Mock(), flush=AsyncMock(), commit=AsyncMock())
    async def flush(): db.add.call_args.args[0].id = uuid4()
    db.flush.side_effect = flush
    tasks = BackgroundTasks()
    response = await router.create_prediction_runs(request, tasks, db, user)
    assert len(tasks.tasks) == 1
    task = tasks.tasks[0]
    assert task.func is prediction.run_prediction_jobs_background
    assert task.args[0] == [(id, 14) for id in response.prediction_run_ids]
    assert len(response.prediction_run_ids) == 3
    assert router.get_owned_shift.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('deferred', [False, True])
async def test_ensure_predictions_batches_missing_duties_and_reuses_completed(monkeypatch, deferred):
    shifts = [uuid4(), uuid4(), uuid4()]
    model = NS(specs=dict(max_battery_packs=16, battery_pack_size_kwh=50,
        bus_length_m=12, empty_weight_kg=10000, battery_pack_weight_kg=300, max_passengers=80))
    existing = NS(id=uuid4(), contextual_parameters=dict(quantiles=[.05,.5,.95],
        num_battery_packs=14, battery_capacity_kwh=700, total_weight_kg=17000, bus_length_m=12))
    results = [NS(scalars=lambda: NS(all=lambda: [])),
               NS(scalars=lambda: NS(all=lambda: [existing])), NS(scalars=lambda: NS(all=lambda: []))]
    db = NS(get=AsyncMock(return_value=model), execute=AsyncMock(side_effect=results),
            add=Mock(), flush=AsyncMock(), commit=AsyncMock())
    async def flush(): db.add.call_args.args[0].id = uuid4()
    db.flush.side_effect = flush
    infer = AsyncMock()
    monkeypatch.setattr(prediction, 'predict_consumption_jobs', infer)
    jobs = [] if deferred else None
    ids = await optimization.ensure_predictions(db, uuid4(), shifts, uuid4(),
        dict(model_name='greybox_qrf_production_crps_optimized_3', prediction_stack='legacy',
             external_temp_celsius=-5, occupancy_percent=50, auxiliary_heating_type='default',
             quantiles=[.05,.5,.95], num_battery_packs=14), pending_jobs=jobs)
    assert ids[1] == existing.id
    expected = [(ids[0], 14), (ids[2], 14)]
    if deferred:
        assert jobs == expected
        infer.assert_not_awaited()
    else:
        assert infer.await_count == 1
        assert infer.call_args.args[1] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('hybrid', [False, True])
async def test_aggregate_support_is_identical_for_all_masses_and_duties(monkeypatch, hybrid):
    cases = prepared_cases(hybrid)
    expected = []
    for case in cases:
        case.run.id = uuid4()
        case.run.model_name = 'same'
        case.run.prediction_stack = 'vecto-g2' if hybrid else 'legacy'
        frames, frame, args = prediction.combined_prediction_inputs([case])
        expected.append(empirical_qrf_support(case.predictor, frame, **args))
    by_id = {case.run.id: case for case in cases}
    async def prepare(db, run, **kwargs):
        return by_id[run.id], lambda frame, support, mean, distribution: (support, mean, distribution)
    monkeypatch.setattr(aggregate_consumption, '_prepare_aggregate_profile', prepare)
    infer = Mock(wraps=empirical_qrf_support)
    monkeypatch.setattr(aggregate_consumption, 'empirical_qrf_support', infer)
    results = await aggregate_consumption.load_aggregate_profiles(None,
        [(case.run, AggregateOptions()) for case in cases])
    assert infer.call_count == 1
    for case, (support, mean, distribution) in zip(cases, expected, strict=True):
        actual = results[str(case.run.id)]
        np.testing.assert_array_equal(actual[0], support)
        np.testing.assert_array_equal(actual[1], mean)
        assert actual[2] == distribution


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['battery_only', 'charging_only', 'joint'])
async def test_exact_verification_collects_all_chosen_masses_before_inference(monkeypatch, mode):
    from app.models import OptimizationRuns
    owner, model = uuid4(), uuid4()
    initial = [NS(id=uuid4(), shift_id=uuid4(), user_id=owner, bus_model_id=model,
        status='completed', model_name='same', prediction_stack='legacy', auxiliary_estimator_release=None,
        external_temp_celsius=-5, occupancy_percent=50, auxiliary_heating_type='default',
        contextual_parameters={'num_battery_packs':16, 'battery_capacity_kwh':800}) for _ in range(2)]
    rows = {row.id: row for row in initial}
    run = NS(id=uuid4(), user_id=owner, bus_model_id=model, mode=mode, status='pending',
        prediction_run_ids=[str(row.id) for row in initial],
        input_params={'shift_ids':[str(row.shift_id) for row in initial], 'consumption_basis':'mean',
            'reference_prediction_run_ids':{str(row.shift_id):str(row.id) for row in initial}}, results=None)
    db = NS(get=AsyncMock(side_effect=lambda entity, key: run if entity is OptimizationRuns else rows[key]),
            commit=AsyncMock(), rollback=AsyncMock())
    async def prepare(db, run, ids, **kwargs):
        return [NS(shift_id=str(rows[id].shift_id)) for id in ids], [], NS()
    async def ensure(db, user, shifts, bus_model_id, params, *, pending_jobs):
        row = NS(id=uuid4(), shift_id=shifts[0], contextual_parameters={})
        rows[row.id] = row
        pending_jobs.append((row.id, params['num_battery_packs']))
        return [row.id]
    inference = AsyncMock()
    async def solve(buses, stations, config, load_exact, **kwargs):
        exact, references = await load_exact({str(initial[0].shift_id):10, str(initial[1].shift_id):14})
        assert len(exact) == 2 and len(references) == 2
        raise RuntimeError('verification reached')
    monkeypatch.setattr(optimization, 'prepare_optimization_input', prepare)
    monkeypatch.setattr(optimization, 'ensure_predictions', ensure)
    monkeypatch.setattr(optimization, '_prediction_provenance', lambda runs: {})
    monkeypatch.setattr(optimization, '_prediction_component_summary', AsyncMock(return_value=None))
    monkeypatch.setattr(optimization, 'optimize_and_verify', solve)
    monkeypatch.setattr(prediction, 'predict_consumption_jobs', inference)
    await optimization.run_optimization(db, run.id)
    assert inference.await_count == 1
    jobs = inference.call_args.args[1]
    assert [packs for _, packs in jobs] == [10, 14]
    assert [rows[id].shift_id for id, _ in jobs] == [row.shift_id for row in initial]
