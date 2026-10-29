from dataclasses import replace
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models import OptimizationRuns, PredictionRuns
from app.schemas.requests import OptimizationRequest
from app.services import optimization as service
from simulation.aggregate_consumption import PredictiveDistributionUnavailable
from simulation.optimization_model import BusData, TripData, StationData, OptimizationConfig


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['battery_only', 'charging_only', 'joint'])
@pytest.mark.parametrize('basis', ['mean', 'aggregate_q95'])
async def test_service_uses_full_profile_at_selected_mass_once_per_reference(monkeypatch, mode, basis):
    owner, sid, model, run_id = uuid4(), uuid4(), uuid4(), uuid4()
    predictions = [NS(id=uuid4(), shift_id=sid, user_id=owner, bus_model_id=model,
        status='completed', model_name='test', prediction_stack='legacy', auxiliary_estimator_release=None,
        external_temp_celsius=10, occupancy_percent=50, auxiliary_heating_type='default',
        contextual_parameters={'num_battery_packs':n, 'battery_capacity_kwh':n*50}) for n in (2,3,4)]
    initial = predictions[-1]
    run = NS(id=run_id, status='pending', user_id=owner, bus_model_id=model, mode=mode,
        prediction_run_ids=[str(p.id) for p in predictions], results=None,
        input_params={'shift_ids':[str(sid)], 'consumption_basis':basis, 'quantile_consumption':'mean',
            'charging_stations':[{'stop_id':'p'}],
            'reference_prediction_run_ids':{str(sid):str(initial.id)}})
    class DB:
        async def get(self, entity, key):
            if entity is OptimizationRuns: return run
            if entity is PredictionRuns: return next(p for p in predictions if p.id==key)
            raise AssertionError(entity)
        async def commit(self): pass
        async def rollback(self): pass
    calls = []
    async def profile(db, prediction, **kwargs):
        n=prediction.contextual_parameters['num_battery_packs']
        calls.append(n)
        return {'mean':[70+n-4]*2, 'aggregate_q95':[80+n-4]*2,
                'reference':{'battery_packs':n}, 'statistics':{}, 'occurrences':[]}
    async def prepare(db, run, ids, reference_ids=None):
        n=next(p.contextual_parameters['num_battery_packs'] for p in predictions
               if str(p.id)==(reference_ids or run.input_params['reference_prediction_run_ids'])[str(sid)])
        bus=BusData(str(sid), 'Duty', 200, 200-n*50, 300, 50, 2, 4, n,
            [TripData('repeat',0,10,0,70+n-4,0), TripData('repeat',20,30,-1,70+n-4,0)])
        config=OptimizationConfig(mode=mode, min_soc=.2, max_soc=.9, battery_cost_per_kwh=100)
        return [bus], [StationData('p','P',[100],1,300,300)], config
    async def components(*args, **kwargs): return None
    monkeypatch.setattr(service, 'prepare_optimization_input', prepare)
    monkeypatch.setattr(service, 'load_aggregate_profile', profile)
    monkeypatch.setattr(service, '_prediction_provenance', lambda runs: {})
    monkeypatch.setattr(service, '_prediction_component_summary', components)
    await service.run_optimization(DB(), run_id)
    assert run.status == 'completed', run.results
    assert run.results['forecast_verification']['status'] == 'verified'
    analysis=run.results['consumption_analysis']['per_shift'][str(sid)]
    chosen=run.results['battery_results'][str(sid)]['optimized_packs']
    assert analysis['reference']['battery_packs'] == chosen
    assert analysis['cases'][basis]['within_soc_limits']
    assert len(calls) == len(set(calls))
    assert run.results['selected_prediction_run_ids'][str(sid)] == str(next(p.id for p in predictions if p.contextual_parameters['num_battery_packs']==chosen))


def test_explicit_aggregate_basis_cannot_mix_legacy_quantiles():
    params=dict(name='Test',mode='battery_only',shift_ids=[uuid4()],prediction_run_ids=[uuid4()])
    assert OptimizationRequest(**params,consumption_basis='aggregate_q95').quantile_consumption=='mean'
    legacy=OptimizationRequest(**params,quantile_consumption='0.95')
    assert legacy.consumption_basis is None
    with pytest.raises(ValidationError):
        OptimizationRequest(**params,consumption_basis='aggregate_q95',quantile_consumption='0.95')
    with pytest.raises(ValidationError):
        OptimizationRequest(**params,aggregate_sample_count=100000,aggregate_max_samples=10000)

@pytest.mark.asyncio
async def test_basis_lookup_reuses_owned_result_without_creating_work(monkeypatch):
    from app.routers import simulation as router
    from simulation.aggregate_consumption import METHOD_VERSION
    owner, source_id, candidate_id = uuid4(), uuid4(), uuid4()
    source=NS(id=source_id,input_params={'consumption_basis':'mean'},results={},status='completed')
    candidate=NS(id=candidate_id,input_params={'consumption_basis':'aggregate_q95','basis_method_version':METHOD_VERSION},results={},status='pending')
    async def owned(db,key,user_id):
        assert key==source_id and user_id==owner
        return source
    class DB:
        async def execute(self,query):
            # Owner filtering is required even for rows linked by a source ID.
            assert 'user_id' in str(query)
            return NS(scalars=lambda:NS(all=lambda:[candidate]))
    monkeypatch.setattr(router,'get_owned_optimization_run',owned)
    response=await router.find_basis_comparison(source_id,'aggregate_q95',DB(),NS(id=owner))
    assert response=={'optimization_run_id':str(candidate_id),'status':'pending'}
    candidate.input_params['basis_method_version']='obsolete'
    response=await router.find_basis_comparison(source_id,'aggregate_q95',DB(),NS(id=owner))
    assert response['optimization_run_id'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['battery_only','charging_only','joint'])
async def test_basis_creation_preserves_constraints_and_uses_normal_validation(monkeypatch,mode):
    from app.routers import simulation as router
    from fastapi import BackgroundTasks
    source_id,created_id,sid,pid=uuid4(),uuid4(),uuid4(),uuid4()
    source=NS(id=source_id,name='Source',input_params={'mode':mode,'shift_ids':[str(sid)],
        'prediction_run_ids':[str(pid)],'reference_prediction_run_ids':{str(sid):str(pid)},
        'min_soc':.2,'max_soc':.9,'state_of_health':.8,'battery_cost_per_kwh':300,
        'charging_stations':[]})
    created=NS(id=created_id,input_params={},status='pending')
    async def comparison(*args): return source,None
    async def create(tasks,request,db,user):
        assert request.mode==mode and request.consumption_basis=='aggregate_q95'
        assert request.min_soc==.2 and request.state_of_health==.8
        assert request.reference_prediction_run_ids=={sid:pid}
        created.input_params=request.model_dump(mode='json',exclude={'name'})
        return NS(optimization_run_id=created_id)
    class DB:
        async def get(self,entity,key): assert key==created_id; return created
        async def commit(self): pass
    monkeypatch.setattr(router,'_basis_comparison',comparison)
    monkeypatch.setattr(router,'create_optimization_run',create)
    result=await router.create_basis_comparison(source_id,'aggregate_q95',BackgroundTasks(),DB(),NS(id=uuid4()))
    assert result['reused'] is False
    assert created.input_params['basis_source_run_id']==str(source_id)

@pytest.mark.asyncio
async def test_distribution_loader_accepts_database_numeric_decimals_and_rejects_stale_forecasts(monkeypatch):
    from decimal import Decimal
    import numpy as np
    import pandas as pd
    from app.services import aggregate_consumption as loader
    from app.services.runtime_release import PredictionStack
    from simulation.aggregate_consumption import AggregateOptions
    sid, pid, trip = uuid4(), uuid4(), uuid4()
    mass=NS(total_weight_kg=15000,battery_capacity_kwh=100,battery_packs=2,bus_length_m=12)
    prediction=NS(id=pid,shift_id=sid,bus_model_id=uuid4(),model_name='test',prediction_stack='legacy',
        occupancy_percent=50,external_temp_celsius=10,auxiliary_heating_type='default',auxiliary_estimator_release=None,
        contextual_parameters={'num_battery_packs':2,'battery_capacity_kwh':100,'total_weight_kg':15000})
    records=[NS(sequence_number=i,trip_id=trip,prediction_kwh=Decimal(str(10*(i+1)))) for i in range(2)]
    stats=[{'sequence_number':i,'trip_id':str(trip)} for i in range(2)]
    class DB:
        async def get(self,*args): return NS(specs={})
        async def execute(self,*args): return NS(scalars=lambda:NS(all=lambda:records))
    async def statistics(*args): return stats
    monkeypatch.setattr(loader,'physical_bus_mass',lambda *args,**kwargs:mass)
    monkeypatch.setattr(loader,'resolve_prediction_selection',lambda **kwargs:NS(stack=PredictionStack.LEGACY))
    monkeypatch.setattr(loader,'validate_model_stack_contract',lambda *args:None)
    monkeypatch.setattr(loader,'_model_passenger_mass_kg',lambda *args:68)
    monkeypatch.setattr(loader,'get_predictor',lambda *args:NS(metadata={},model=NS(),prepare_features=lambda *args:pd.DataFrame({'distance':[1,2]})))
    monkeypatch.setattr(loader,'load_shift_trip_statistics',statistics)
    monkeypatch.setattr(loader,'build_aux_energy_fn',lambda *args:None)
    monkeypatch.setattr(loader,'empirical_qrf_support',lambda *args,**kwargs:(np.array([[9,11],[19,21]]),np.array([10,20]),{}))
    result=await loader.load_aggregate_profile(DB(),prediction,options=AggregateOptions(100,100))
    assert result['mean']==[10,20]
    assert result['reference']['battery_packs']==2
    assert [x['sequence_number'] for x in result['occurrences']]==[0,1]
    records[0].prediction_kwh=Decimal('30')
    with pytest.raises(PredictiveDistributionUnavailable,match='do not reproduce'):
        await loader.load_aggregate_profile(DB(),prediction,options=AggregateOptions(100,100))
