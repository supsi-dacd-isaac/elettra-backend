"""Permanent regressions for the operational adapter, leaving VECTO frozen."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from app.services.hvac_policy import (
    ANNUAL_POLICY, FEASIBILITY_POLICY, LEGACY_POLICY, SolarResponse, annual_ghi,
    calculate_operational_hvac, feasibility_ghi, fingerprint, resolve_policy, terminal_intervals,
)
from app.services.hvac_context import cluster_calendar
from elettra_core import vecto_ssm
from elettra_core.vecto_templates import environmental_condition_for_scenario
from simulation.energy_analysis import replay_profile, simulate_plan_coverage
from simulation.optimization_model import BusData, TripData, OptimizationConfig, _prepare_arrays


@pytest.mark.parametrize('temperature,expected', [(-30,50),(10,50),(13.5,425),(17,800),(21,900),(25,1000),(45,1000)])
def test_one_ghi_for_temperature(temperature, expected):
    assert feasibility_ghi(temperature) == expected


@pytest.mark.parametrize('temperature', [float('nan'), float('inf'), -float('inf')])
def test_invalid_temperature(temperature):
    with pytest.raises(ValueError):
        feasibility_ghi(temperature)


def test_explicit_policy_and_historical_semantics():
    assert resolve_policy(None) == FEASIBILITY_POLICY
    assert resolve_policy(None, annual=True) == ANNUAL_POLICY
    assert resolve_policy(None, legacy_stack=True) == LEGACY_POLICY
    assert resolve_policy(LEGACY_POLICY, annual=True) == LEGACY_POLICY
    with pytest.raises(ValueError):
        resolve_policy(ANNUAL_POLICY)


def schedule(gap):
    return [dict(trip_id=str(i), sequence_number=i, start_minute=start, end_minute=end,
                 start_stop_id='terminal', end_stop_id='terminal', timezone='Europe/Zurich',
                 start_is_depot=False, end_is_depot=False)
            for i, (start,end) in enumerate([(60,90),(90+gap,120+gap)])]


@pytest.mark.parametrize('gap', [0,5,10,60])
def test_terminal_cap_empty_bus_and_fixed_auxiliary_scope(gap):
    trips, intervals, meta = calculate_operational_hvac(policy_id=FEASIBILITY_POLICY,
        schedule=schedule(gap), length=18, passengers=102.2, temperature=-5, heating_type='diesel')
    assert meta['terminal_hvac_minutes'] == min(gap,10)
    assert len(intervals) == (gap > 0)
    if gap:
        assert intervals[0]['start_minute'] == 90+gap-min(gap,10)
        empty = SolarResponse(18,0,-5,'diesel').direct(50)[0]
        assert intervals[0]['hvac_electrical_kwh'] == pytest.approx(empty[0]*min(gap,10)/60)
        assert intervals[0]['diesel_fuel_kwh'] == pytest.approx(empty[1]*min(gap,10)/60)
        assert sum(x['electrical_kwh'] for x in intervals[0]['minute_energy']) == pytest.approx(intervals[0]['hvac_electrical_kwh'])
    occupied = SolarResponse(18,102.2,-5,'diesel').direct(50)[0]
    np.testing.assert_allclose(trips.sum(axis=0), occupied)
    assert meta['terminal_hvac']['fixed_auxiliaries_included'] is False


def test_depots_and_transfers_excluded():
    rows=schedule(60)
    rows[0]['end_is_depot']=True
    assert terminal_intervals(rows)==[]
    rows[0]['end_is_depot']=False
    rows[1]['start_stop_id']='different'
    assert terminal_intervals(rows)==[]


@pytest.mark.parametrize('temperature', [-30,-5,9.6,16.999,17,24.8,45])
@pytest.mark.parametrize('heater', ['default','diesel'])
def test_adapter_equals_frozen_ssm_with_clouding_one(temperature, heater):
    response=SolarResponse(18,102.2,temperature,heater)
    for ghi in [0,50,87.5,100,350,800,1000]:
        actual,_=response.direct(ghi)
        environment=environmental_condition_for_scenario(temperature_celsius=temperature,solar_irradiance_wm2=ghi)
        # Independent oracle only in a test; production never patches globals.
        with patch.multiple(vecto_ssm, _SOLAR_CLOUDING_LOW=1., _SOLAR_CLOUDING_HIGH=1.):
            result=vecto_ssm.vecto_auxiliary_power(environment=environment,inputs=response.inputs)
        np.testing.assert_allclose(actual,[result.p_hvac_electrical_kw,result.p_fuel_kw,result.unmet_thermal_demand_kw],atol=1e-12)


@pytest.mark.parametrize('temperature', [-30,9.6,16.999,17,25,45])
def test_lookup_at_ventilation_climatic_and_saturation_transitions(temperature):
    response=SolarResponse(18,102.2,temperature,'default')
    ghi=np.arange(0.125,1600,0.5)
    actual=response.values(ghi)
    expected=np.array([response.direct(g)[0] for g in ghi])
    np.testing.assert_allclose(actual,expected,atol=.002,rtol=0)


def test_cached_climatic_selection_preserves_official_tie_breaks():
    rows = [vecto_ssm.default_environmental_condition(i) for i in range(1,12)]
    def boundaries(values):
        ordered=sorted(set(values))
        return ordered+[(a+b)/2+epsilon for a,b in zip(ordered,ordered[1:]) for epsilon in [-1e-8,0,1e-8]]
    for temperature in boundaries(row.temperature_celsius for row in rows):
        response=SolarResponse(18,102.2,temperature,'default')
        for ghi in boundaries(row.solar_irradiance_wm2 for row in rows):
            expected=environmental_condition_for_scenario(temperature_celsius=temperature,solar_irradiance_wm2=ghi)
            assert response.direct(ghi)[1][0] == expected.environmental_id


def weather():
    origin=datetime(2030,1,1,tzinfo=timezone.utc)
    return dict(time_utc=[(origin+timedelta(hours=i)).isoformat() for i in range(8760)],
                ghi=(np.arange(8760)%24*10.).tolist(),irradiance_time_offset_hours=0.)


@pytest.mark.parametrize('date,utc_hour', [('2030-01-15',11),('2030-07-15',10),('2030-03-31',10),('2030-10-27',11)])
def test_gtfs_local_noon_dst_and_extended_hours(date,utc_hour):
    data=weather()
    actual=annual_ghi(data,[date],'Europe/Zurich',27*60)
    assert actual[0,12*60] == pytest.approx((utc_hour+.5/60)*10)
    assert actual[0,26*60] == pytest.approx(((utc_hour+14)%24+.5/60)*10)
    data['irradiance_time_offset_hours']=.1773
    assert annual_ghi(data,[date],'Europe/Zurich',721)[0,720] == pytest.approx((utc_hour+.5/60-.1773)*10)


def test_missing_weather_fails_and_fingerprints_change():
    data=weather()
    original=fingerprint(data)
    data['ghi'][1]+=1
    assert fingerprint(data)!=original
    data['ghi'][2]=float('nan')
    with pytest.raises(ValueError): annual_ghi(data,['2030-01-01'],'Europe/Zurich',60)
    data=weather();data['time_utc'].pop()
    with pytest.raises(ValueError): annual_ghi(data,['2030-01-01'],'Europe/Zurich',60)


def test_fingerprint_matches_jsonb_negative_zero_normalization():
    assert fingerprint({'temperature_c': [-0.0, 1.], 'metadata': {'offset': -0.0}}) == fingerprint(
        {'temperature_c': [0.0, 1.], 'metadata': {'offset': 0.0}})


def test_terminal_discharge_during_charging_solver_replay_and_statistical_coverage():
    bus=BusData('shift','test',100,0,60,100,1,1,1,
        trips=[TripData('a',0,1,0,10,0),TripData('b',6,7,0,10,0)],
        terminal_hvac_kwh={minute:2. for minute in range(1,6)})
    arrays=_prepare_arrays([bus],[],OptimizationConfig(mode='charging_only'))
    assert arrays['discharge_base'].sum()==30
    assert arrays['discharge_sens'].sum()==0
    timeline=dict(first_minute=0,charge_kwh=[0,1,1,1,1,1,0,0])
    result=replay_profile(bus,[10,10],timeline,nominal_kwh=100,min_soc=0,max_soc=1,state_of_health=1)
    assert result['total_consumption_kwh']==30
    assert result['required_usable_kwh']==25
    assert result['accepted_charge_kwh']==5
    support=np.array([[10.,10.],[10.,10.]])
    for capacity,probability in [(24,0),(25,1)]:
        stats=simulate_plan_coverage(bus,support,timeline,usable_kwh=capacity,samples=100,seed=42)
        assert stats['completion_probability']==probability
        assert stats['required_usable_kwh_q95']==25


def test_405_annual_audit_and_terminal_minutes():
    data=json.loads((Path(__file__).parent/'fixtures/hvac_405.json').read_text())
    snapshot=weather()
    snapshot.update({k:data[k] for k in ['ghi','temperature_c','irradiance_time_offset_hours']})
    calendar=cluster_calendar(snapshot,data['scenarios'])
    assert sum(sum(r['end_minute']-r['start_minute'] for r in terminal_intervals(rows))
               for rows in data['schedules'].values())==955
    cache={'hvac_last_minute':max(int(r['end_minute']) for rows in data['schedules'].values() for r in rows)}
    annual=legacy=terminal=0.
    for scenario in data['scenarios']:
        temperature,days=scenario['temperature'],scenario['occurrences']
        original=SolarResponse(18,102.2,temperature,'default',clouding_one=False).direct(100)[0][0]
        for rows in data['schedules'].values():
            values,stops,_=calculate_operational_hvac(policy_id=ANNUAL_POLICY,schedule=rows,length=18,
                passengers=102.2,temperature=temperature,heating_type='default',weather=snapshot,
                dates=calendar[temperature],cache=cache)
            annual+=values[:,0].sum()*days
            terminal+=sum(r['hvac_electrical_kwh'] for r in stops)*days
            legacy+=original*sum(r['end_minute']-r['start_minute'] for r in rows)/60*days
    assert legacy==pytest.approx(data['expected']['legacy_trip_kwh'],abs=.001)
    assert annual==pytest.approx(data['expected']['pvgis_trip_kwh'],abs=1.)
    assert terminal>0


def test_operational_solar_does_not_add_qrf_calls_or_change_occurrence_count():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from test_yearly_prediction_batch import prepared_cases
    from app.services.prediction import _predict_combined
    from app.services.vecto_auxiliary import build_vecto_auxiliary_binding
    from app.services.runtime_release import PredictionStack
    cases = prepared_cases(True)
    predictor = cases[0].predictor
    infer = Mock(wraps=predictor.model.qrf.predict)
    predictor.model.qrf.predict = infer
    stack = SimpleNamespace(stack=PredictionStack.VECTO_G2)
    def bind(operational):
        cache = {}
        for case in cases:
            rows = [dict(trip_id=r['trip_id'], sequence_number=r['sequence_number'],
                         start_minute=i*35, end_minute=i*35+20,
                         start_stop_id='stop', end_stop_id='stop', timezone='Europe/Zurich')
                    for i,r in enumerate(case.trip_stats)]
            binding = build_vecto_auxiliary_binding(stack_release=stack, bus_model_specs=case.specs,
                occupancy_percent=case.mass.occupancy_percent, external_temp_celsius=case.run.external_temp_celsius,
                auxiliary_heating_type='default', hvac_cache=cache,
                hvac_inputs=dict(policy_id=FEASIBILITY_POLICY, schedule=rows) if operational else None)
            case.aux_fn = binding.energy_fn
    bind(False)
    baseline = _predict_combined(cases,[.05,.5,.95]); baseline_calls = infer.call_count
    infer.reset_mock(); bind(True)
    operational = _predict_combined(cases,[.05,.5,.95])
    assert infer.call_count == baseline_calls
    assert [len(r['predictions']) for r in operational] == [len(r['predictions']) for r in baseline]


@pytest.mark.asyncio
async def test_terminal_hvac_preserves_existing_mean_q95_solver_workflow():
    from simulation.optimization_model import solve_optimization
    from simulation.optimization_verification import optimize_and_verify
    counts = []
    for terminal in [{}, {minute: .1 for minute in range(10, 20)}]:
        solves = []
        for energy in [25., 35.]:  # Existing Mean and Q95 evaluations only.
            bus = BusData('s', 's', 500, 0, 150, 50, 2, 10, 10,
                [TripData('a', 0, 10, -1, energy, 0), TripData('b', 20, 30, -1, energy, 0)],
                terminal_hvac_kwh=terminal)
            def solve(buses, stations, config):
                solves.append(config)
                return solve_optimization(buses, stations, config)
            async def exact(packs):
                return [replace(bus, reference_packs=packs['s'])], {'s': 'exact'}
            result, verification = await optimize_and_verify([bus], [],
                OptimizationConfig(mode='battery_only', min_soc=0, max_soc=.9), exact, solve=solve)
            assert verification['status'] == 'verified' and result.electrification_feasible
            assert verification['iterations'][0]['exact_energy_kwh']['s'] == pytest.approx(2*energy+sum(terminal.values()))
        counts.append(len(solves))
    assert counts == [4, 4]  # One sizing and one fixed-design solve for each basis.


@pytest.mark.parametrize('field', ['auxiliary_policy_id', 'hvac_input_fingerprint'])
def test_battery_variants_cannot_mix_solar_policies_or_inputs(field):
    from test_optimization_physical_shift import prediction
    from simulation.optimization_contract import select_references
    a, b = prediction(10), prediction(11)
    for run in [a, b]:
        run.contextual_parameters.update(auxiliary_policy_id=FEASIBILITY_POLICY, hvac_input_fingerprint='same')
    b.contextual_parameters[field] = LEGACY_POLICY if field == 'auxiliary_policy_id' else 'different'
    with pytest.raises(ValueError, match='variants'):
        select_references([a, b], ['s'], mode='joint', user_id='owner')


@pytest.mark.asyncio
async def test_saved_input_fingerprint_rejects_mutation():
    from types import SimpleNamespace
    from app.services.hvac_context import prepare_hvac_contexts
    inputs = dict(policy_id=FEASIBILITY_POLICY, schedule=schedule(5))
    context = dict(auxiliary_policy_id=FEASIBILITY_POLICY,hvac_inputs=inputs,hvac_input_fingerprint=fingerprint(inputs))
    run = SimpleNamespace(contextual_parameters=context,status='completed')
    await prepare_hvac_contexts(None,[run],{})
    inputs['schedule'][0]['end_minute']+=1
    with pytest.raises(ValueError, match='fingerprint'):
        await prepare_hvac_contexts(None,[run],{})


@pytest.mark.asyncio
async def test_weather_export_is_lazy_and_checks_the_archive():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.services.hvac_export import export_runs, compact
    payload={'ghi':[1,2]}; sha=fingerprint(payload)
    run=SimpleNamespace(id='run',shift_id='shift',external_temp_celsius=17,occupancy_percent=70,
        auxiliary_heating_type='default',summary={},contextual_parameters={
            'auxiliary_policy_id':ANNUAL_POLICY,'hvac_inputs':{'weather_snapshot_sha256':sha,'schedule':schedule(10)}})
    db=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(payload=payload)))
    summary=await export_runs(db,[run])
    assert not summary['weather_inputs'] and 'inputs' not in summary['runs'][0]
    db.get.assert_not_awaited()
    full=await export_runs(db,[run,run],include_inputs=True)
    assert full['weather_inputs']=={sha:payload} and db.get.await_count==1
    assert 'hvac_inputs' not in compact(run.contextual_parameters)
    payload['ghi'][0]=3
    with pytest.raises(ValueError,match='corrupt'):
        await export_runs(db,[run],include_inputs=True)


def test_audit_exports_require_authentication(client):
    from uuid import uuid4
    for prefix in ['simulation/prediction-runs','simulation/optimization-runs','yearly-analysis']:
        assert client.get(f'/api/v1/{prefix}/{uuid4()}/auxiliary-assumptions?include_inputs=true').status_code==401


@pytest.mark.asyncio
async def test_reading_historical_weather_does_not_trigger_an_import(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4
    from app.routers import simulation
    from app.services.hybrid_temperature import HYBRID_PROVIDER, OPENMETEO_MODEL
    existing=SimpleNamespace(id=uuid4(),provider=HYBRID_PROVIDER,openmeteo_model=OPENMETEO_MODEL,
        processing_version='elettra-hybrid-temperature-v1',openmeteo_metadata=[],
        requested_latitude=46.,requested_longitude=9.,pvgis_months_selected=[])
    monkeypatch.setattr(simulation,'count_weather_records',AsyncMock(return_value=8760))
    monkeypatch.setattr(simulation,'get_active_temperature_series',AsyncMock(return_value=existing))
    fetch=AsyncMock();monkeypatch.setattr(simulation,'fetch_hybrid_temperature_series',fetch)
    result=await simulation.generate_pvgis_tmy(46.,9.,False,None,None)
    assert result.processing_version=='elettra-hybrid-temperature-v1'
    assert result.source=='db'
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_weather_archive_is_deduplicated_and_immutable():
    from uuid import uuid4
    from sqlalchemy import delete, update, select
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy.exc import DBAPIError
    from app.database import AsyncSessionLocal
    from app.models import HvacWeatherSnapshots
    payload=dict(test=str(uuid4()),ghi=[-0.,100.],temperature_c=[-0.0,1.]); sha=fingerprint(payload)
    async with AsyncSessionLocal() as db:
        try:
            for _ in range(2):
                await db.execute(insert(HvacWeatherSnapshots).values(sha256=sha,payload=payload).on_conflict_do_nothing())
            await db.commit()
            rows=(await db.execute(select(HvacWeatherSnapshots).where(HvacWeatherSnapshots.sha256==sha))).scalars().all()
            assert len(rows)==1 and fingerprint(rows[0].payload)==sha
            with pytest.raises(DBAPIError,match='immutable'):
                async with db.begin_nested():
                    await db.execute(update(HvacWeatherSnapshots).where(HvacWeatherSnapshots.sha256==sha).values(payload={'ghi':[999]}))
        finally:
            await db.execute(delete(HvacWeatherSnapshots).where(HvacWeatherSnapshots.sha256==sha))
            await db.commit()
