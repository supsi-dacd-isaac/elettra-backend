from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from simulation.aggregate_consumption import AggregateOptions, aggregate_profile, empirical_qrf_support, PredictiveDistributionUnavailable, shift_seed
from simulation.energy_analysis import replay_profile, build_energy_analysis, simulate_plan_coverage
from simulation.optimization_model import BusData, TripData, OptimizationConfig, StationData, solve_optimization
from simulation.optimization_verification import optimize_and_verify


def test_independent_repeated_occurrences_reduce_tail_and_reproduce_seed():
    support = np.tile(np.linspace(0, 20, 450), (24, 1))
    options = AggregateOptions(samples=10000, max_samples=10000)
    a = aggregate_profile(support, options=options)
    b = aggregate_profile(support, options=options)
    assert a['aggregate_q95'] == b['aggregate_q95']
    assert sum(a['mean']) == pytest.approx(240)
    assert 275 < sum(a['aggregate_q95']) < 300
    assert sum(a['aggregate_q95']) < np.quantile(support, .95, axis=1).sum()-100
    assert sum(a['aggregate_q95']) == pytest.approx(a['statistics']['q95_total_kwh'])
    histogram = a['statistics']['total_energy_histogram']
    assert histogram == b['statistics']['total_energy_histogram']
    assert sum(histogram['counts']) == a['statistics']['samples']
    assert len(histogram['bin_edges_kwh']) == len(histogram['counts']) + 1
    assert histogram['bin_edges_kwh'][0] < 240 < a['statistics']['q95_total_kwh'] < histogram['bin_edges_kwh'][-1]
    assert shift_seed(42, 'a') != shift_seed(42, 'b')


def test_degenerate_distribution_and_bounded_precision_refinement():
    fixed = aggregate_profile(np.array([[2, 2], [3, 3]]), options=AggregateOptions(100, 400))
    assert fixed['aggregate_q95'] == [2, 3]
    assert fixed['statistics']['samples'] == 100
    assert fixed['statistics']['total_energy_histogram'] == {'bin_edges_kwh': [4.5, 5.5], 'counts': [100]}
    uncertain = aggregate_profile(np.tile(np.linspace(0, 100, 450), (5, 1)),
        options=AggregateOptions(100, 400, precision_kwh=1e-9))
    assert uncertain['statistics']['samples'] == 400
    assert not uncertain['statistics']['precision_met']
    assert len(uncertain['statistics']['refinement']) == 3
    assert uncertain['statistics']['trajectory_coverage_probability'] is None


def test_no_distribution_reconstruction_from_three_quantiles():
    predictor = SimpleNamespace(model=SimpleNamespace(max_samples_leaf=2))
    with pytest.raises(PredictiveDistributionUnavailable):
        empirical_qrf_support(predictor, None)


def test_exact_support_adapter_matches_real_quantile_forest_mean():
    import pandas as pd
    from quantile_forest import RandomForestQuantileRegressor
    frame = pd.DataFrame({'distance':np.arange(30)})
    forest = RandomForestQuantileRegressor(n_estimators=17,max_samples_leaf=1,random_state=42,n_jobs=1)
    forest.fit(frame, np.arange(30)**1.2)
    predictor = SimpleNamespace(model=forest,is_hybrid_greybox=False,is_greybox=False)
    support, mean, metadata = empirical_qrf_support(predictor,frame)
    assert support.shape == (30,17)
    assert np.allclose(support.mean(axis=1),mean)
    assert metadata['support_points']==17


def test_refinement_checks_pack_boundary_even_with_loose_energy_precision():
    support=np.tile(np.linspace(0,100,450),(5,1))
    initial=aggregate_profile(support,options=AggregateOptions(100,100,precision_kwh=100))
    threshold=initial['statistics']['q95_total_kwh']
    refined=aggregate_profile(support,options=AggregateOptions(100,400,precision_kwh=100,capacity_step_kwh=threshold))
    assert refined['statistics']['samples']>100


def duty(sid='s', energy=80):
    return BusData(sid, sid, 200, 0, 300, 50, 2, 4, 4, [
        TripData('same', 0, 10, 0, energy, 0),
        TripData('same', 20, 30, -1, energy, 0)])


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['battery_only', 'charging_only', 'joint'])
async def test_all_modes_verify_fixed_mass_with_shared_chargers_and_replay(mode):
    buses = [duty('a'), duty('b')]
    station = StationData('panto', 'Panto', [100, 100], 2, 600, 300)
    config = OptimizationConfig(mode=mode, min_soc=.2, max_soc=.9, state_of_health=.9,
                                battery_cost_per_kwh=100)
    calls = []
    async def exact(packs):
        calls.append(packs)
        return [replace(b, reference_packs=packs[b.shift_id]) for b in buses], {'a': 'pa', 'b': 'pb'}
    result, verification = await optimize_and_verify(buses, [station], config, exact)
    assert verification['status'] == 'verified'
    assert len(calls) == 1
    assert result.installed_chargers['panto']['num_slots'] == 2
    profiles = {b.shift_id: {'mean': [70, 70], 'aggregate_q95': [80, 80],
        'reference': {}, 'statistics': {}, 'occurrences': []} for b in buses}
    analysis = build_energy_analysis(buses, result, profiles, config, basis='aggregate_q95', verified=True)
    for b in buses:
        row = analysis['per_shift'][b.shift_id]
        assert row['trip_intervals'] == [
            {'departure_minute': 0, 'arrival_minute': 10},
            {'departure_minute': 20, 'arrival_minute': 30}]
        assert row['usable_kwh'] == pytest.approx(row['nominal_kwh']*.9*.7)
        assert row['cases']['aggregate_q95']['within_soc_limits']
        assert row['cases']['mean']['margin_kwh'] > row['cases']['aggregate_q95']['margin_kwh']
        assert min(row['cases']['aggregate_q95']['soc_kwh']) == pytest.approx(min(row['verified_soc_kwh']))


def test_pantograph_timing_matters_and_charge_is_curtailed_not_reallocated():
    bus = duty(energy=80)
    early = [0.0]*31
    early[5] = 40  # surplus before the first trip ends cannot cover later demand
    late = [0.0]*31
    late[15] = 40
    def replay(charges):
        return replay_profile(bus, [80, 80], {'first_minute': 0, 'charge_kwh': charges},
            nominal_kwh=200, min_soc=.2, max_soc=.9, state_of_health=1)
    assert replay(early)['required_usable_kwh'] == 160
    assert replay(early)['curtailed_charge_kwh'] == 40
    assert replay(late)['required_usable_kwh'] == 120
    assert not replay(early)['within_soc_limits']
    assert replay(late)['within_soc_limits']


def test_soh_changes_required_packs_without_changing_physical_mass_sensitivity():
    bus = duty(energy=60)
    bus.trips = [replace(t, end_station_idx=-1) for t in bus.trips]
    config = OptimizationConfig(mode='battery_only', min_soc=.2, max_soc=.9)
    healthy = solve_optimization([bus], [], config)
    aged = solve_optimization([bus], [], replace(config, state_of_health=.8))
    assert healthy.battery_results['s']['optimized_packs'] == 4
    assert aged.battery_results['s']['excess_packs'] == 1
    assert aged.battery_results['s']['physical_limit_exceeded']


def test_monte_carlo_coverage_checks_whole_trajectory_and_timing():
    bus=duty()
    support=np.array([[0,100],[0,100]])
    charge=[0.0]*31
    timeline={'first_minute':0,'charge_kwh':charge}
    result=simulate_plan_coverage(bus,support,timeline,usable_kwh=100,samples=100000,seed=42)
    assert result['completion_probability']==pytest.approx(.75,abs=.005)
    # A charge before either arrival is curtailed and cannot improve coverage.
    charge[5]=100
    early=simulate_plan_coverage(bus,support,timeline,usable_kwh=100,samples=100000,seed=42)
    assert early['completion_probability']==result['completion_probability']
    charge[5]=0; charge[15]=100
    between=simulate_plan_coverage(bus,support,timeline,usable_kwh=100,samples=100000,seed=42)
    assert between['completion_probability']==1
