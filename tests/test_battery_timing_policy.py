from dataclasses import replace
from uuid import uuid4

import pytest

from app.schemas.requests import OptimizationRequest
from simulation.battery_policy import historical_battery_params, policy_from_params, resolve_battery_policy
from simulation.optimization_model import BusData, TripData, StationData, OptimizationConfig, solve_optimization
from simulation.energy_analysis import replay_profile


@pytest.mark.parametrize('soh,usable', [(1, 250), (.85, 212.5), (.7, 175)])
def test_residual_policy(soh, usable):
    p = resolve_battery_policy(state_of_health=soh)
    assert p.energy(500)['usable_capacity_kwh'] == pytest.approx(usable)
    assert p.energy(500)['min_energy_kwh'] == pytest.approx(500*soh*.4)


@pytest.mark.parametrize('soh,minimum,maximum', [(.7, .9-.5/.7, .9), (.6, .1, .1+.5/.6), (1, .4, .9)])
def test_constant_energy_expands_lower_first(soh, minimum, maximum):
    p = resolve_battery_policy(battery_soc_policy='constant_initial_usable', state_of_health=soh)
    assert p.min_soc == pytest.approx(minimum)
    assert p.max_soc == pytest.approx(maximum)
    assert p.energy(500)['usable_capacity_kwh'] == pytest.approx(250)
    assert p.as_dict()['preferred_max_soc_exceeded'] == (soh == .6)


def test_incompatible_energy_cannot_be_silently_clamped():
    with pytest.raises(ValueError, match='maximum available is 49.3%'):
        resolve_battery_policy(battery_soc_policy='constant_initial_usable', state_of_health=.58)


@pytest.mark.parametrize('minimum,warning', [(.2, False), (.19999999, True), (.4, False)])
def test_warning_uses_applied_unrounded_minimum(minimum, warning):
    assert resolve_battery_policy(min_soc=minimum).as_dict()['low_soc_warning'] == warning


@pytest.mark.parametrize('params', [{'state_of_health': 0}, {'state_of_health': float('nan')},
    {'state_of_health': True}, {'max_soc': float('inf')}, {'min_soc': .9},
    {'battery_soc_policy': 'unknown'}, {'allowed_min_soc': .96}])
def test_invalid_policy(params):
    with pytest.raises(ValueError):
        resolve_battery_policy(**params)


def request(**params):
    return OptimizationRequest(name='Policy', mode='battery_only', shift_ids=[uuid4()],
        prediction_params={'external_temp_celsius': -5}, **params)


def test_new_defaults_and_historical_replay_are_separate():
    new = request()
    assert new.state_of_health == .7 and new.session_connection_minutes == 1
    historical = historical_battery_params({})
    assert historical['state_of_health'] == 1 and historical['session_connection_minutes'] == 0
    assert historical_battery_params({'min_soc': 0, 'session_connection_minutes': 0})['min_soc'] == 0
    copied = request(**historical)
    assert copied.state_of_health == 1 and copied.session_connection_minutes == 0


@pytest.mark.parametrize('params', [{'layover_reduction_minutes': 1, 'lock_entire_dwell': False,
                                  'evaluate_both_bases': False},
    {'layover_reduction_minutes': -.1}, {'session_connection_minutes': 1.5},
    {'battery_soc_policy': 'constant_initial_usable', 'state_of_health': .58}])
def test_api_rejects_before_solver(params):
    with pytest.raises(ValueError):
        request(**params)


def fixture(dwell=5, depot=False):
    bus = BusData('a', 'A', 100, 0, 300, 50, 2, 2, 2,
        [TripData('out', 0, 10, 0, 40, 0), TripData('back', 10+dwell, 20+dwell, -1, 30, 0)])
    station = StationData('s', 'S', [100], 1, 300, 300, is_depot=depot)
    config = OptimizationConfig(mode='battery_only', min_soc=.1, max_soc=.9,
        fixed_battery_packs={'a': 2}, fixed_station_slots={'s': 1}, allow_excess_packs=False,
        charging_objective='soc_reserve', session_connection_minutes=1)
    return bus, station, config


@pytest.mark.parametrize('dwell,margin,depot,charged', [(5,0,False,20), (6,2,False,15),
    (6,2,True,25), (1,0,False,0), (3,10,False,0)])
def test_available_charging_time_and_exact_energy(dwell, margin, depot, charged):
    bus, station, config = fixture(dwell, depot)
    result = solve_optimization([bus], [station], replace(config, layover_reduction_minutes=margin))
    assert result.electrification_feasible
    summary = result.per_bus_summary[0]
    assert summary['total_charged_kwh'] == pytest.approx(charged)
    timeline = summary['energy_timeline']
    excluded = min(dwell, 1 + (0 if depot else margin))
    assert timeline['charge_kwh'][10:10+excluded] == pytest.approx([0]*excluded)
    for session in summary['charging_sessions']:
        assert session['technical_minutes'] + session['layover_reduction_minutes'] + session['available_charging_minutes'] == session['end_minute']-session['start_minute']
        assert session['available_charging_minutes'] == session['active_charging_minutes'] + session['idle_available_minutes']


@pytest.mark.parametrize('mode', ['battery_only', 'charging_only', 'joint'])
@pytest.mark.parametrize('kind', ['residual_soc', 'constant_initial_usable'])
def test_solver_and_replay_use_same_effective_limits(mode, kind):
    bus = BusData('a', 'A', 500, 0, 300, 50, 10, 10, 10,
                  [TripData('trip', 0, 10, -1, 150, 0)], battery_pack_cost_chf=100)
    config = OptimizationConfig(mode=mode, state_of_health=.7, battery_soc_policy=kind,
        fixed_battery_packs={'a':10}, fixed_station_slots={}, allow_excess_packs=False)
    result = solve_optimization([bus], [], config)
    assert result.electrification_feasible
    row = result.battery_results['a']
    expected = 175 if kind == 'residual_soc' else 250
    assert row['selected_usable_kwh'] == pytest.approx(expected)
    policy = config.battery_policy
    replay = replay_profile(bus, [150], result.per_bus_summary[0]['energy_timeline'], nominal_kwh=500,
        min_soc=policy.min_soc, max_soc=policy.max_soc, state_of_health=.7)
    assert replay['soc_kwh'] == pytest.approx(result.per_bus_summary[0]['energy_timeline']['soc_kwh'])
    assert replay['available_usable_kwh'] == pytest.approx(expected)


def test_depot_connection_buffer_is_one_minute_not_fifteen():
    # Overnight window is 00:02–00:07, with exactly four charging minutes.
    bus = BusData('a', 'A', 100, 0, 300, 50, 2, 2, 2,
                  [TripData('trip', 7, 1441, 0, 20, 0)], depot_station_idx=0)
    station = StationData('s', 'Depot', [100], 1, 300, 300, is_depot=True)
    config = OptimizationConfig(mode='battery_only', min_soc=.1, daily_cycle=True,
        session_connection_minutes=1, layover_reduction_minutes=100,
        fixed_battery_packs={'a':2}, fixed_station_slots={'s':1}, allow_excess_packs=False)
    result = solve_optimization([bus], [station], config)
    assert result.electrification_feasible
    row = result.daily_cycle['per_shift']['a']
    assert row['outside_shift_kwh'] == pytest.approx(20)
    assert row['available_charging_minutes'] == 4
    assert row['technical_minutes'] == 1 and row['layover_reduction_minutes'] == 0
    assert row['charging_intervals'][0]['power_kw_dc'] == 0


@pytest.mark.parametrize('slots,feasible', [(1, False), (2, True)])
def test_reduced_windows_do_not_free_occupied_chargers(slots, feasible):
    bus, station, config = fixture(dwell=6)
    bus = replace(bus, trips=[replace(bus.trips[0], base_energy_kwh=50),
                             replace(bus.trips[1], base_energy_kwh=40)])
    other = replace(bus, shift_id='b', shift_name='B')
    config = replace(config, layover_reduction_minutes=2, fixed_battery_packs={'a':2, 'b':2},
                     fixed_station_slots={'s':slots})
    station = replace(station, slot_costs_chf=[100]*slots, num_fixed_slots=slots, max_total_power_kw=600)
    result = solve_optimization([bus, other], [station], config)
    assert result.electrification_feasible == feasible
    if feasible:
        for row in result.per_bus_summary:
            assert row['charging_sessions'][0]['duration_minutes'] == 6
            assert row['charging_sessions'][0]['available_charging_minutes'] == 3


def test_excluded_short_stop_does_not_block_the_next_session():
    bus, station, config = fixture()
    bus = replace(bus, trips=[TripData('a',0,2,0,40,0), TripData('b',3,4,0,10,0),
                             TripData('c',20,25,-1,40,0)])
    result = solve_optimization([bus], [station], replace(config, layover_reduction_minutes=5))
    assert result.electrification_feasible
    timeline = result.per_bus_summary[0]['energy_timeline']['charge_kwh']
    assert timeline[2] == 0
    assert timeline[4:10] == pytest.approx([0]*6)
    assert sum(timeline[10:20]) > 0
