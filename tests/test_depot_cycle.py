from dataclasses import replace
from types import SimpleNamespace

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


def refinement_fixture():
    """Two depots, fixed assignments, a short daytime spike and a blocked interval."""
    from pyomo.environ import ConcreteModel, Var, Objective, SolverFactory
    model = ConcreteModel()
    model.depot_power = Var([(j, b) for j in range(3) for b in range(2)], initialize=0)
    model.depot_power[1, 0].value = 40
    model.depot_power[2, 1].value = 20
    model.DepotAssignIndex = [(0, 0), (1, 0)]
    model.depot_assign = Var(model.DepotAssignIndex, initialize=1)
    model.soc = Var([(t, b) for t in [0, 45] for b in range(2)], initialize=90)
    model.soc[45, 0].value = 80
    model.soc[45, 1].value = 85
    model.day = Var(initialize=90)
    model.day.fix()
    model.obj = Objective(expr=model.day)
    intervals = [(0, 15), (15, 30), (30, 45)]
    day = {(minute, s): model.day if minute == 7 and s == 0 else 0
           for minute in range(45) for s in range(2)}
    context = {'windows': {0: (45, 1440, 0), 1: (45, 1440, 1)},
               'intervals': intervals, 'active': set(model.depot_power), 'first_minute': 0,
               'daytime_loads': day, 'site_limits': {0: 100, 1: 30},
               'power_limits': {(j, b): 0 if j == 0 and b == 1 else 60
                                for j, b in model.depot_power},
               'loads': {(minute, s): day[minute, s] + model.depot_power[minute//15, s]
                         for minute in range(45) for s in range(2)}}
    return model, SolverFactory('highs'), context, OptimizationConfig(mode='battery_only')


def test_independent_peak_lp_preserves_original_model_and_checks_each_minute():
    import simulation.depot_cycle as cycle
    from pyomo.environ import Var, value
    model, solver, context, config = refinement_fixture()
    original = [(v, v.value, v.fixed) for v in model.component_data_objects(Var)
                if v.parent_component() is not model.depot_power]
    components = list(model.component_map())
    objective = model.obj.expr
    cycle.shave_depot_peaks(model, solver, context, config)
    assert context['peak_status'] == 'optimized'
    assert context['peak_model_size'] == {'variables': 8, 'constraints': 8}
    assert list(model.component_map()) == components and model.obj.expr is objective
    assert all(v.value == val and v.fixed == fixed for v, val, fixed in original)
    assert sum(value(model.depot_power[j, 0])*.25 for j in range(3)) == pytest.approx(10)
    assert sum(value(model.depot_power[j, 1])*.25 for j in range(3)) == pytest.approx(5)
    assert value(model.depot_power[0, 1]) == 0  # connection buffer remains enforced
    assert max(value(load) for (_, s), load in context['loads'].items() if s == 0) <= 90+1e-5
    assert max(value(load) for (_, s), load in context['loads'].items() if s == 1) == pytest.approx(10)


@pytest.mark.parametrize('failure', ['exception', 'no_solution', 'invalid_energy', 'nan', 'negative', 'site_limit'])
def test_peak_failure_never_changes_the_verified_schedule(monkeypatch, failure):
    import simulation.depot_cycle as cycle
    from pyomo.environ import Var
    model, solver, context, config = refinement_fixture()
    snapshot = [(v, v.value, v.fixed) for v in model.component_data_objects(Var)]
    class FailedSolver:
        options = {}
        def solve(self, lp, **kwargs):
            if failure == 'exception':
                raise RuntimeError('timeout')
            lp.solutions.load_from = lambda _: None
            if failure == 'invalid_energy': lp.power[1, 0].value = 0
            if failure == 'nan': lp.power[1, 0].value = float('nan')
            if failure == 'negative': lp.power[1, 0].value = -1
            if failure == 'site_limit':
                lp.power[1, 0].value = 0
                lp.power[0, 0].value = 40
            return SimpleNamespace(solution=[] if failure == 'no_solution' else [1],
                                   solver=SimpleNamespace(termination_condition='maxTimeLimit'))
    monkeypatch.setattr(cycle, 'SolverFactory', lambda _: FailedSolver())
    cycle.shave_depot_peaks(model, solver, context, config)
    assert context['peak_status'] == 'unavailable'
    assert context['peak_solution_quality'] == 'retained_feasible_plan'
    assert all(v.value == val and v.fixed == fixed for v, val, fixed in snapshot)


def test_valid_nonoptimal_peak_solution_is_accepted_without_waiting(monkeypatch):
    import simulation.depot_cycle as cycle
    from pyomo.opt import TerminationCondition
    factory = cycle.SolverFactory
    class TimedSolver:
        def __init__(self):
            self.solver = factory('highs')
            self.options = self.solver.options
        def solve(self, lp, **kwargs):
            result = self.solver.solve(lp, **kwargs)
            result.solver.termination_condition = TerminationCondition.maxTimeLimit
            return result
    monkeypatch.setattr(cycle, 'SolverFactory', lambda _: TimedSolver())
    model, solver, context, config = refinement_fixture()
    cycle.shave_depot_peaks(model, solver, context, config)
    assert context['peak_status'] == 'optimized'
    assert context['peak_solution_quality'] == 'feasible'
