import pytest
from simulation.infrastructure_costs import infrastructure_cost_breakdown
from simulation.optimization_model import BusData, StationData, TripData, OptimizationConfig, solve_optimization
from app.schemas.requests import InfrastructureCostConfig

MODEL=dict(station_type='depot',point_cost_chf=110000,site_fixed_cost_chf=250000,
           connection_cost_per_kw_chf=250,ac_dc_efficiency=.95)


def test_calibration_and_unused_site_cost():
    assert infrastructure_cost_breakdown(MODEL,1500,10)['total_cost_chf']==pytest.approx(1744736.8421)
    parts=infrastructure_cost_breakdown(MODEL,1500,0)
    assert parts['total_cost_chf']==0
    assert parts['grid_connection_power_kw_ac']==0
    with pytest.raises(ValueError): InfrastructureCostConfig(**{**MODEL,'point_cost_chf':float('nan')})


@pytest.mark.parametrize('mode',['battery_only','charging_only','joint'])
def test_solver_reports_and_prices_common_cost_once(mode):
    # Both buses must recharge at the single stop to complete the second trip.
    buses=[BusData(str(i),str(i),100,0,300,50,2,2,2,
        trips=[TripData('a',0,1,0,60,0),TripData('b',61,62,-1,60,0)],battery_pack_cost_chf=7000) for i in range(2)]
    stations=[StationData('depot','Depot',[],2,300,150,cost_model=MODEL),
              StationData('unused','Unused',[],2,300,150,cost_model=MODEL)]
    result=solve_optimization(buses,stations,OptimizationConfig(mode=mode,min_soc=0,max_soc=1,battery_cost_per_kwh=999,session_penalty_weight=0))
    assert result.electrification_feasible
    depot=result.installed_chargers['depot']
    assert depot['num_slots']==2
    assert depot['cost_chf']==pytest.approx(220000+250000+250*300/.95)
    assert depot['cost_breakdown']['site_fixed_cost_chf']==250000
    if mode!='battery_only':
        assert result.installed_chargers['unused']['num_slots']==0
        assert result.installed_chargers['unused']['cost_chf']==0
    assert result.total_installation_cost_chf==pytest.approx(sum(s['cost_chf'] for s in result.installed_chargers.values()))


def test_joint_uses_bus_pack_cost_instead_of_legacy_kwh_rate():
    bus=BusData('bus','Bus',150,0,150,50,1,3,3,trips=[TripData('t',0,1,-1,90,0)],battery_pack_cost_chf=7000)
    result=solve_optimization([bus],[],OptimizationConfig(mode='joint',min_soc=0,max_soc=1,battery_cost_per_kwh=999))
    assert result.battery_results['bus']['optimized_packs']==2
    assert result.total_battery_cost_chf==7000
    assert result.battery_results['bus']['selected_battery_cost_chf']==14000


@pytest.mark.parametrize('mode', ['charging_only', 'joint'])
@pytest.mark.parametrize('common_component', ['fixed', 'power'])
def test_optimizer_compares_complete_site_cost_not_only_point_price(mode, common_component):
    bus = BusData('bus', 'Bus', 100, 0, 150, 50, 2, 2, 2, trips=[
        TripData('a', 0, 1, 0, 40, 0),
        TripData('b', 61, 62, 1, 10, 0),
        TripData('c', 122, 123, -1, 70, 0),
    ], battery_pack_cost_chf=7000)
    costly_site = {**MODEL, 'point_cost_chf': 1000, 'site_fixed_cost_chf': 50000,
                   'connection_cost_per_kw_chf': 0}
    cheaper_site = {**costly_site, 'point_cost_chf': 10000, 'site_fixed_cost_chf': 0}
    if common_component == 'power':
        costly_site.update(site_fixed_cost_chf=0, connection_cost_per_kw_chf=400)
    stations = [
        StationData('a', 'A', [], 1, 150, 150, cost_model=costly_site),
        StationData('b', 'B', [], 1, 150, 150, cost_model=cheaper_site),
    ]
    result = solve_optimization([bus], stations, OptimizationConfig(
        mode=mode, min_soc=0, max_soc=1, session_penalty_weight=0))
    assert result.electrification_feasible
    assert result.installed_chargers['a']['num_slots'] == 0
    assert result.installed_chargers['b']['num_slots'] == 1
    assert result.total_installation_cost_chf == 10000


@pytest.mark.parametrize('mode', ['charging_only', 'joint'])
@pytest.mark.parametrize('required_points', [1, 2])
def test_only_selected_points_are_priced_and_common_costs_apply_once(mode, required_points):
    buses = [BusData(str(i), str(i), 100, 0, 150, 50, 2, 2, 2,
        trips=[TripData('a', 0, 1, 0, 60, 0), TripData('b', 61, 62, -1, 60, 0)],
        battery_pack_cost_chf=7000) for i in range(required_points)]
    stations = [StationData('depot', 'Depot', [], 4, 600, 150, cost_model=MODEL),
                StationData('unused', 'Unused', [], 4, 600, 150, cost_model=MODEL)]
    result = solve_optimization(buses, stations, OptimizationConfig(
        mode=mode, min_soc=0, max_soc=1, session_penalty_weight=0))
    assert result.electrification_feasible
    depot = result.installed_chargers['depot']
    assert depot['num_slots'] == required_points
    # Independent arithmetic: count only chosen points; configured site power
    # is 600 kW DC even when fewer than four points are selected.
    expected = required_points * 110000 + 250000 + 600 / .95 * 250
    assert depot['cost_chf'] == pytest.approx(expected)
    assert depot['cost_breakdown']['chargers_cost_chf'] == required_points * 110000
    assert depot['cost_breakdown']['total_cost_chf'] == pytest.approx(expected)
    assert result.installed_chargers['unused']['cost_breakdown']['total_cost_chf'] == 0
    assert result.total_installation_cost_chf == pytest.approx(expected)
    assert result.objective_value == pytest.approx(expected)


@pytest.mark.parametrize('mode', ['charging_only', 'joint'])
def test_two_active_sites_each_pay_their_own_common_costs(mode):
    bus = BusData('bus', 'Bus', 100, 0, 150, 50, 2, 2, 2, trips=[
        TripData('a', 0, 1, 0, 60, 0),
        TripData('b', 61, 62, 1, 60, 0),
        TripData('c', 122, 123, -1, 60, 0),
    ], battery_pack_cost_chf=7000)
    stations = [StationData('a', 'A', [], 3, 300, 150, cost_model=MODEL),
                StationData('b', 'B', [], 2, 150, 150, cost_model=MODEL)]
    result = solve_optimization([bus], stations, OptimizationConfig(
        mode=mode, min_soc=0, max_soc=1, session_penalty_weight=0))
    assert result.electrification_feasible
    assert [s['num_slots'] for s in result.installed_chargers.values()] == [1, 1]
    assert result.total_installation_cost_chf == pytest.approx(2 * (110000 + 250000) + (300 + 150) / .95 * 250)


@pytest.mark.parametrize('pack_cost, expected_packs, expected_points', [
    (4000, 3, 0),
    (20000, 2, 1),
])
def test_joint_trades_a_battery_pack_against_the_complete_site_investment(pack_cost, expected_packs, expected_points):
    bus = BusData('bus', 'Bus', 100, 0, 150, 50, 2, 3, 2, trips=[
        TripData('a', 0, 1, 0, 60, 0), TripData('b', 61, 62, -1, 60, 0),
    ], battery_pack_cost_chf=pack_cost)
    # Site = 2,000 point + 3,000 fixed + 95/.95*50 = 10,000 CHF.
    model = {**MODEL, 'point_cost_chf': 2000, 'site_fixed_cost_chf': 3000,
             'connection_cost_per_kw_chf': 50}
    station = StationData('depot', 'Depot', [], 4, 95, 150, cost_model=model)
    result = solve_optimization([bus], [station], OptimizationConfig(
        mode='joint', min_soc=0, max_soc=1, session_penalty_weight=0))
    assert result.electrification_feasible
    assert result.battery_results['bus']['optimized_packs'] == expected_packs
    assert result.installed_chargers['depot']['num_slots'] == expected_points
    assert result.total_installation_cost_chf == expected_points * 10000
    assert result.objective_value == min(pack_cost, 10000)
