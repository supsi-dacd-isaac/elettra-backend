from dataclasses import replace

import pytest

from simulation.optimization_model import BusData, TripData, StationData, OptimizationConfig, solve_optimization
from simulation.infeasibility_diagnostics import diagnose_infeasible_cycle


def bus(sid='a', energy=60):
    return BusData(sid, sid, 100, 0, 100, 50, 2, 2, 2,
                   [TripData(sid, 360, 1080, -1, energy, 0)], depot_station_idx=0)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['battery_only', 'charging_only', 'joint'])
async def test_infeasible_depot_preserves_daytime_battery_and_quantifies_power_shortfall(mode):
    buses = [bus()]
    stations = [StationData('depot', 'Depot', [500], 1, 4, 100)]
    config = OptimizationConfig(mode=mode, min_soc=.2, max_soc=.9, daily_cycle=True, battery_cost_per_kwh=300)
    original = solve_optimization(buses, stations, config)
    assert original.solver_status == 'infeasible'
    async def exact(packs):
        return buses, {'a': 'exact-prediction'}
    diagnostic = await diagnose_infeasible_cycle(buses, stations, config, exact, time_budget_seconds=30)
    assert diagnostic['status'] == 'available'
    assert diagnostic['forecast_verification']['status'] == 'verified'
    assert diagnostic['battery_results']['a']['required_total_packs'] == 2
    reason = next(r for r in diagnostic['reasons'] if r['code'] == 'depot_power')
    assert reason['required_recharge_kwh'] == pytest.approx(60)
    assert reason['maximum_recharge_kwh'] == pytest.approx(4*719/60)
    assert reason['minimum_shortfall_kwh'] == pytest.approx(60-4*719/60)
    assert original.battery_results == {} and not original.electrification_feasible


@pytest.mark.asyncio
async def test_excess_battery_is_retained_as_diagnostic_not_verified_physical_design():
    buses = [bus(energy=90)]
    config = OptimizationConfig(mode='battery_only', min_soc=.2, daily_cycle=True)
    stations = [StationData('depot', 'Depot', [500], 1, 1, 100)]
    async def exact(packs):
        raise AssertionError('Virtual packs must not be verified as physically installable')
    diagnostic = await diagnose_infeasible_cycle(buses, stations, config, exact, time_budget_seconds=30)
    battery = diagnostic['battery_results']['a']
    assert battery['required_total_packs'] == 3 and battery['max_physical_packs'] == 2
    assert diagnostic['forecast_verification']['status'] == 'physical_limits_exceeded'
    assert diagnostic['reasons'][0]['code'] == 'battery_limit'
    assert not diagnostic['per_shift']


@pytest.mark.asyncio
async def test_whole_depot_stay_plug_conflict_is_reported():
    buses = [bus('a'), bus('b')]
    async def exact(packs):
        return buses, {}
    diagnostic = await diagnose_infeasible_cycle(buses, [StationData('depot','Depot',[500],1,100,100)],
        OptimizationConfig(mode='battery_only', min_soc=.2, daily_cycle=True), exact, time_budget_seconds=30)
    assert not any(r['code'] == 'depot_power' for r in diagnostic['reasons'])
    reason = next(r for r in diagnostic['reasons'] if r['code'] == 'depot_plugs')
    assert (reason['required_plugs'], reason['available_plugs']) == (2, 1)


@pytest.mark.asyncio
async def test_exhausted_budget_returns_no_invented_battery():
    diagnostic = await diagnose_infeasible_cycle([], [], OptimizationConfig(mode='joint',daily_cycle=True),
        None, time_budget_seconds=0)
    assert diagnostic['status'] == 'unavailable' and diagnostic['battery_results'] == {}
