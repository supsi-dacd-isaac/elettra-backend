from dataclasses import replace

import pytest

from simulation.optimization_model import BusData, TripData, StationData, OptimizationConfig, solve_optimization
from simulation.optimization_verification import optimize_and_verify


def bus(sid, departure=360, arrival=1080, energy=60):
    return BusData(sid, sid, 100, 0, 100, 50, 2, 2, 2,
                   [TripData(sid, departure, arrival, 0, energy, 0)], depot_station_idx=0)


def depot(slots=2, power=100):
    return StationData('depot', 'Depot', [500]*slots, slots, power, 100)


@pytest.mark.parametrize('mode', ['battery_only', 'charging_only', 'joint'])
@pytest.mark.asyncio
async def test_daily_cycle_and_peak_shaving_preserve_design_and_close_energy(mode):
    buses = [bus('a'), bus('b')]
    config = OptimizationConfig(mode=mode, min_soc=.2, max_soc=.9, daily_cycle=True,
                                depot_peak_shaving=True, battery_cost_per_kwh=300)
    async def exact(packs):
        return buses, {}
    result, verification = await optimize_and_verify(buses, [depot()], config, exact)
    assert verification['status'] == 'verified'
    report = result.daily_cycle
    assert report['status'] == 'verified'
    assert report['peak_shaving']['status'] == 'optimized'
    assert result.installed_chargers['depot']['num_slots'] == 2
    for row in report['per_shift'].values():
        assert row['outside_shift_kwh'] == pytest.approx(60)
        assert row['departure_soc_kwh'] == pytest.approx(90)
        assert row['during_shift_kwh'] == 0
    site = report['stations']['depot']
    assert site['simulated_peak_kw_dc'] == pytest.approx(120 / ((1440-721)/60))
    assert site['total_charged_kwh'] == pytest.approx(120)
    assert site['simulated_peak_kw_dc'] <= site['previous_plan_peak_kw_dc'] + 1e-5


@pytest.mark.parametrize('slots,power', [(1,100), (2,5)])
def test_daily_cycle_rejects_insufficient_plugs_or_energy_even_with_full_initial_batteries(slots,power):
    result = solve_optimization([bus('a'),bus('b')],[depot(slots,power)],
        OptimizationConfig(mode='battery_only',daily_cycle=True,min_soc=.2))
    assert not result.electrification_feasible


def test_periodic_depot_stays_conflict_across_different_raw_days():
    # A parks 02:01–20:00; B parks 22:01–06:00. Their 02:01–06:00
    # overlap only appears after folding the raw intervals modulo one day.
    buses=[bus('a',1200,1560),bus('b',360,1320)]
    result=solve_optimization(buses,[depot(1)],OptimizationConfig(mode='battery_only',daily_cycle=True,min_soc=.2))
    assert not result.electrification_feasible


def test_legacy_service_only_does_not_claim_daily_readiness():
    result=solve_optimization([bus('a')],[],OptimizationConfig(mode='battery_only',min_soc=.2))
    assert result.electrification_feasible
    assert not result.daily_cycle


def test_daily_cycle_requires_a_real_depot_and_time_before_next_departure():
    config=OptimizationConfig(mode='battery_only',daily_cycle=True)
    with pytest.raises(ValueError,match='configure'):
        solve_optimization([replace(bus('a'),depot_station_idx=-1)],[depot()],config)
    with pytest.raises(ValueError,match='leave time'):
        solve_optimization([bus('a',360,1800)],[depot()],config)


def test_peak_shaving_preserves_daytime_pantograph_charge_and_joint_site_limit(monkeypatch):
    import simulation.depot_cycle as cycle
    original=cycle.shave_depot_peaks
    preserved=[]
    def checked_peak(model,solver,context,config):
        before=[v.value for v in model.power.values()]
        original(model,solver,context,config)
        assert [v.value for v in model.power.values()]==pytest.approx(before)
        preserved.append(True)
    monkeypatch.setattr(cycle,'shave_depot_peaks',checked_peak)
    a=bus('early',300,360,60)
    b=replace(bus('late'),trips=[TripData('out',360,370,0,60,0),TripData('back',390,400,0,30,0)])
    config=OptimizationConfig(mode='battery_only',min_soc=.2,daily_cycle=True,depot_peak_shaving=True,
        fixed_battery_packs={'early':2,'late':2},fixed_station_slots={'depot':2},
        allow_excess_packs=False,charging_objective='soc_reserve',max_solver_time_seconds=60)
    result=solve_optimization([a,b],[depot(2,80)],config)
    assert result.electrification_feasible and preserved
    assert result.daily_cycle['peak_shaving']['status']=='optimized'
    assert result.per_bus_summary[1]['total_charged_kwh']==pytest.approx(80*20/60)
    assert result.daily_cycle['stations']['depot']['simulated_peak_kw_dc']==pytest.approx(80)
    assert result.daily_cycle['stations']['depot']['peak_connected_buses']==2
    loads=[0.0]*1440
    for row in result.per_bus_summary:
        timeline=row['energy_timeline']
        for t,energy in enumerate(timeline['charge_kwh']):loads[(t+timeline['first_minute'])%1440]+=energy*60
    for row in result.daily_cycle['per_shift'].values():
        for interval in row['charging_intervals']:
            for minute in range(interval['start_minute'],interval['end_minute']):loads[minute%1440]+=interval['power_kw_dc']
    assert max(loads)<=80+1e-5
