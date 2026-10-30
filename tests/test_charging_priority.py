from dataclasses import replace

import numpy as np
import pytest

from simulation.optimization_model import BusData, TripData, StationData, OptimizationConfig, solve_optimization
from simulation.optimization_verification import optimize_and_verify


def bus(sid, first, second, *, packs=2, offset=0):
    return BusData(sid, sid, packs*50, 0, 450, 50, 2, 4, packs, [
        TripData(sid+'-1', offset, offset+10, 0, first, 0),
        TripData(sid+'-2', offset+20, offset+30, -1, second, 0)])


def station(slots=1, power=450):
    return StationData('panto', 'Pantograph', [500]*slots, slots, power, 450)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['battery_only', 'charging_only', 'joint'])
@pytest.mark.parametrize('urgent', [False, True])
async def test_reserve_schedule_preserves_design_and_full_dwell(mode, urgent):
    buses = [bus('low', 60, 10), bus('other', 40 if urgent else 20, 70 if urgent else 10)]
    if not urgent:
        buses.append(bus('later', 60, 40, offset=30))
    config = OptimizationConfig(mode=mode, min_soc=.2, max_soc=.9, battery_cost_per_kwh=300)
    solves = []
    def solve(b, s, c):
        result = solve_optimization(b, s, c)
        solves.append((c, result))
        return result
    async def exact(packs):
        return [replace(b, reference_packs=packs[b.shift_id]) for b in buses], {}
    result, verification = await optimize_and_verify(buses, [station()], config, exact, solve=solve)
    assert verification['status'] == 'verified'
    assert len(solves) == 2  # No additional production solve for SOC priority.
    before = solves[0][1]
    assert solves[0][0].charging_objective == 'feasibility'
    assert solves[1][0].charging_objective == 'soc_reserve'
    assert result.battery_results == before.battery_results
    assert result.installed_chargers == before.installed_chargers
    assert result.total_installation_cost_chf == before.total_installation_cost_chf
    assert result.total_battery_cost_chf == before.total_battery_cost_chf
    rows = {b['shift_id']: b for b in result.per_bus_summary}
    chosen, rejected = ('other', 'low') if urgent else ('low', 'other')
    assert rows[chosen]['total_charged_kwh'] == pytest.approx(40 if urgent else 60)
    assert rows[rejected]['total_charged_kwh'] == pytest.approx(0)
    session = rows[chosen]['charging_sessions'][0]
    assert (session['start_minute'], session['end_minute'], session['slot_number']) == (10, 20, 1)
    assert session['connected_without_charging_minutes'] >= 2
    assert result.charging_schedule_policy['occupancy'] == 'entire_dwell'


def test_shared_site_power_favours_low_soc_with_different_battery_capacities():
    buses = [bus('small',60,10), bus('large',40,20,packs=4)]
    config = OptimizationConfig(mode='charging_only',min_soc=.2,max_soc=.9,
        fixed_battery_packs={'small':2,'large':4},fixed_station_slots={'panto':2},
        allow_excess_packs=False,charging_objective='soc_reserve')
    result = solve_optimization(buses,[station(2,360)],config)
    assert result.solver_status == 'optimal'
    a,b = [r['energy_timeline'] for r in result.per_bus_summary]
    assert a['charge_kwh'][10] == pytest.approx(6)
    assert b['charge_kwh'][10] == pytest.approx(0)
    total = np.array(a['charge_kwh'])+np.array(b['charge_kwh'])
    assert total.max() <= 6+1e-6
    assert total.sum() == pytest.approx(60)
    assert abs(a['soc_kwh'][20]/100-b['soc_kwh'][20]/200) < .035
    sessions = [r['charging_sessions'][0] for r in result.per_bus_summary]
    assert {s['slot_number'] for s in sessions} == {1,2}
    assert all(s['start_minute']==10 and s['end_minute']==20 for s in sessions)


def test_reserve_objective_cannot_be_applied_during_sizing():
    with pytest.raises(ValueError,match='fixed design'):
        solve_optimization([bus('a',60,10)],[station()],
            OptimizationConfig(mode='battery_only',charging_objective='soc_reserve'))


def test_no_charging_skips_unnecessary_reserve_objective():
    b = bus('a',30,10)
    b.trips = [replace(t,end_station_idx=-1) for t in b.trips]
    config=OptimizationConfig(mode='battery_only',min_soc=.2,max_soc=.9,
        fixed_battery_packs={'a':2},fixed_station_slots={},allow_excess_packs=False,
        charging_objective='soc_reserve')
    result=solve_optimization([b],[],config)
    assert result.charging_schedule_policy['status']=='no_charging_opportunities'
    assert result.per_bus_summary[0]['energy_timeline']['soc_kwh'][-1]==pytest.approx(50)
