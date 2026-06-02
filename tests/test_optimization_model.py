import pytest

pyomo = pytest.importorskip("pyomo.environ")
from pyomo.environ import SolverFactory

from simulation.optimization_model import BusData, OptimizationConfig, TripData, solve_optimization


if not SolverFactory("highs").available(exception_flag=False):
    pytest.skip("HiGHS solver not available", allow_module_level=True)


def _make_bus(reference_packs: int = 10) -> BusData:
    return BusData(
        shift_id="shift-1",
        shift_name="L12_monday",
        battery_capacity_kwh=400.0,
        battery_offset_kwh=0.0,
        max_charging_power_kw=450.0,
        pack_size_kwh=40.0,
        min_packs=6,
        max_packs=10,
        reference_packs=reference_packs,
        trips=[
            TripData(
                trip_id="trip-1",
                departure_minute=0,
                arrival_minute=1,
                end_station_idx=-1,
                base_energy_kwh=637.0,
                sensitivity=0.0,
            )
        ],
    )


def test_battery_only_maxes_physical_packs_before_excess():
    bus = _make_bus()

    result = solve_optimization(
        buses=[bus],
        stations=[],
        config=OptimizationConfig(
            mode="battery_only",
            min_soc=0.4,
            max_soc=0.9,
            solver_name="highs",
            max_battery_penalty_per_kwh=1e6,
            soc_increase_weight=1e4,
        ),
    )

    battery = result.battery_results["shift-1"]

    assert result.solver_status == "optimal"
    assert result.electrification_feasible is False
    assert battery["optimized_packs"] == 10
    assert battery["excess_packs"] > 0
    assert battery["required_total_packs"] == battery["optimized_packs"] + battery["excess_packs"]
    assert battery["physical_feasible"] is False
    assert result.electrification_summary["status"] == "infeasible"


def test_battery_results_report_simulation_bus_model_physical_bounds():
    bus = BusData(
        shift_id="shift-18m-override",
        shift_name="L31_MON_after11am_12m",
        battery_capacity_kwh=1000.0,
        battery_offset_kwh=400.0,
        max_charging_power_kw=450.0,
        pack_size_kwh=50.0,
        min_packs=12,
        max_packs=20,
        reference_packs=12,
        trips=[
            TripData(
                trip_id="trip-1",
                departure_minute=0,
                arrival_minute=1,
                end_station_idx=-1,
                base_energy_kwh=650.0,
                sensitivity=0.0,
            )
        ],
    )

    result = solve_optimization(
        buses=[bus],
        stations=[],
        config=OptimizationConfig(
            mode="battery_only",
            min_soc=0.4,
            max_soc=0.9,
            solver_name="highs",
            max_battery_penalty_per_kwh=1e6,
            soc_increase_weight=1e4,
        ),
    )

    battery = result.battery_results["shift-18m-override"]

    assert result.solver_status == "optimal"
    assert battery["base_packs"] == 12
    assert battery["max_physical_packs"] == 20
    assert battery["max_physical_kwh"] == 1000.0
    assert battery["required_total_packs"] > battery["max_physical_packs"]
    assert battery["physical_feasible"] is False


@pytest.mark.parametrize(
    ("mode", "reference_packs", "battery_cost_per_kwh", "expected_physical_packs"),
    [
        ("charging_only", 8, 0.0, 8),
        ("joint", 8, 300.0, 10),
    ],
)
def test_any_mode_using_excess_packs_is_marked_infeasible(
    mode: str,
    reference_packs: int,
    battery_cost_per_kwh: float,
    expected_physical_packs: int,
):
    bus = _make_bus(reference_packs=reference_packs)

    config = OptimizationConfig(
        mode=mode,
        min_soc=0.4,
        max_soc=0.9,
        solver_name="highs",
        max_battery_penalty_per_kwh=1e6,
        battery_cost_per_kwh=battery_cost_per_kwh,
        soc_increase_weight=1e4,
    )

    result = solve_optimization(
        buses=[bus],
        stations=[],
        config=config,
    )

    battery = result.battery_results["shift-1"]

    assert result.solver_status == "optimal"
    assert result.electrification_feasible is False
    assert result.electrification_summary["status"] == "infeasible"
    assert result.electrification_summary["num_infeasible_buses"] == 1
    assert battery["optimized_packs"] == expected_physical_packs
    assert battery["excess_packs"] > 0
    assert battery["physical_feasible"] is False
