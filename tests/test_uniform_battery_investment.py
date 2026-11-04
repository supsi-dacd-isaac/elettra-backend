from dataclasses import replace

import pytest

from simulation.optimization_model import BusData, TripData, OptimizationConfig, solve_optimization


def buses():
    return [BusData(str(i), name, 100, 0, 150, 50, 1, 4, 2,
        trips=[TripData('trip', 0, 1, -1, consumption, 0)],
        battery_pack_cost_chf=price, bus_cost_chf=body, bus_model_id='model', bus_model_name='Test')
        for i, (name, consumption, price, body) in enumerate([
            ('40101', 60, 6000, 300000), ('40502', 120, 7000, 400000)])]


@pytest.mark.parametrize('mode', ['battery_only', 'joint'])
@pytest.mark.parametrize('sizing, expected', [('per_bus', [2, 3]), ('uniform', [3, 3])])
def test_uniform_applies_to_entire_fleet_and_prices_each_bus(mode, sizing, expected):
    result = solve_optimization(buses(), [], OptimizationConfig(
        mode=mode, battery_sizing_mode=sizing, min_soc=0, max_soc=1))
    assert result.electrification_feasible
    assert [row['optimized_packs'] for row in result.battery_results.values()] == expected
    for bus, packs in zip(buses(), expected):
        row = result.battery_results[bus.shift_id]
        summary = next(s for s in result.per_bus_summary if s['shift_id'] == bus.shift_id)
        assert row['selected_nominal_kwh'] == packs * 50
        assert row['selected_battery_cost_chf'] == packs * bus.battery_pack_cost_chf
        assert row['selected_vehicle_cost_chf'] == bus.bus_cost_chf + packs * bus.battery_pack_cost_chf
        assert summary['selected_battery_cost_chf'] == row['selected_battery_cost_chf']
        assert row['investment_cost_source'] == 'bus_model_snapshot'


def test_uniform_rejects_different_pack_capacities():
    fleet = buses()
    fleet[1] = replace(fleet[1], pack_size_kwh=40)
    with pytest.raises(ValueError, match='same battery pack size'):
        solve_optimization(fleet, [], OptimizationConfig(mode='joint', battery_sizing_mode='uniform'))


def test_charging_only_keeps_fixed_battery_prices_for_each_bus():
    fleet = [replace(bus, reference_packs=3, battery_capacity_kwh=150) for bus in buses()]
    result = solve_optimization(fleet, [], OptimizationConfig(mode='charging_only', min_soc=0, max_soc=1))
    assert result.electrification_feasible
    assert [r['selected_battery_cost_chf'] for r in result.battery_results.values()] == [18000, 21000]
