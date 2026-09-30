from dataclasses import replace
from types import SimpleNamespace

import pytest

from simulation.optimization_contract import CONTRACT_VERSION, result_integrity, select_references
from simulation.optimization_model import BusData, TripData, StationData, OptimizationConfig, solve_optimization
from simulation.optimization_verification import optimize_and_verify


def prediction(packs, shift="s", **changes):
    return SimpleNamespace(**({
        "id": f"{shift}-{packs}", "shift_id": shift, "user_id": "owner", "status": "completed",
        "bus_model_id": "bus", "model_name": "frozen", "external_temp_celsius": -5,
        "occupancy_percent": 100, "auxiliary_heating_type": "diesel",
        "prediction_stack": "vecto-g2", "auxiliary_estimator_release": "vecto",
        "contextual_parameters": {"num_battery_packs": packs},
    } | changes))


def test_seven_comparison_forecasts_are_one_reference_independent_of_order():
    runs = [prediction(p) for p in range(10, 17)]
    for variants in (runs, list(reversed(runs))):
        refs = select_references(variants, ["s"], mode="battery_only", user_id="owner")
        assert {sid: run.id for sid, run in refs.items()} == {"s": "s-16"}
    assert len(runs) == 7


def test_multi_shift_and_explicit_selection():
    runs = [prediction(p, s) for p in (10, 11) for s in ("a", "b")]
    refs = select_references(runs, ["a", "b"], mode="charging_only", user_id="owner",
                             explicit={"a": "a-10", "b": "b-11"})
    assert len(refs) == 2
    assert refs["a"].id == "a-10"


@pytest.mark.parametrize("changes", [{"occupancy_percent": 50}, {"user_id": "other"},
                                    {"shift_id": "other"}, {"status": "pending"},
                                    {"contextual_parameters": {}}])
def test_incompatible_catalogues_rejected(changes):
    with pytest.raises(ValueError):
        select_references([prediction(10), prediction(11, **changes)], ["s"],
                          mode="joint", user_id="owner")


def test_charging_only_never_silently_picks_a_battery():
    with pytest.raises(ValueError, match="explicit"):
        select_references([prediction(10), prediction(11)], ["s"], mode="charging_only", user_id="owner")
    assert len(select_references([prediction(10)], ["s"], mode="charging_only", user_id="owner")) == 1


def bus(shift="s", *, energy=80, reference=3):
    return BusData(shift, shift, 150, 150-reference*50, 300, 50, 2, 3, reference, [
        TripData("a", 0, 10, 0, energy, 0), TripData("b", 20, 30, -1, energy, 0)])


def station(slots=1):
    return StationData("depot", "Depot", [1000]*slots, slots, 300*slots, 300)


def test_duplicate_bus_guard_prevents_false_infeasibility_and_overwrite():
    with pytest.raises(ValueError, match="exactly once"):
        solve_optimization([bus(), bus(reference=2)], [station()], OptimizationConfig(mode="battery_only"))


@pytest.mark.parametrize("mode", ["battery_only", "joint", "charging_only"])
def test_one_duty_uses_one_charger(mode):
    result = solve_optimization([bus()], [station(2)], OptimizationConfig(
        mode=mode, min_soc=.1, max_soc=.9, battery_cost_per_kwh=100))
    assert result.electrification_feasible
    assert len(result.per_bus_summary) == len(result.battery_results) == 1
    assert result.battery_results["s"]["optimized_packs"] == 3
    assert result.station_utilization["depot"]["peak_concurrent_buses"] == 1
    if mode != "battery_only":
        assert result.installed_chargers["depot"]["num_slots"] == 1
        assert result.total_installation_cost_chf == 1000


def test_two_real_duties_still_compete_for_charging():
    result = solve_optimization([bus("a"), bus("b")], [station(2)], OptimizationConfig(
        mode="joint", min_soc=.1, max_soc=.9, battery_cost_per_kwh=100))
    assert result.electrification_feasible
    assert result.installed_chargers["depot"]["num_slots"] == 2
    assert len(result.per_bus_summary) == 2


def simple_bus(energy, reference=10):
    return BusData("s", "s", 500, 500-reference*50, 150, 50, 2, 10, reference,
                   [TripData("a", 0, 1, -1, energy, .01)])


@pytest.mark.asyncio
@pytest.mark.parametrize("exact_energy,expected_packs,iterations", [(400, 9, 1), (407, 10, 2)])
async def test_verify_selected_nine_packs_then_reanchor_only_if_needed(exact_energy, expected_packs, iterations):
    calls = []
    async def load(packs):
        calls.append(packs["s"])
        return [simple_bus(exact_energy, packs["s"])], {"s": f"forecast-{packs['s']}"}
    result, verification = await optimize_and_verify([simple_bus(396)], [],
        OptimizationConfig(mode="battery_only", min_soc=0, max_soc=.9), load)
    assert result.electrification_feasible
    assert verification["status"] == "verified"
    assert result.battery_results["s"]["optimized_packs"] == expected_packs
    assert len(verification["iterations"]) == iterations
    assert calls[0] == 9
    assert result.per_bus_summary[0]["min_soc_kwh"] == pytest.approx(expected_packs*45-exact_energy)


@pytest.mark.asyncio
async def test_failed_verification_detects_cycle_and_cannot_certify():
    base = simple_bus(396)
    async def load(packs):
        return [replace(base, reference_packs=packs["s"])], {"s": "exact"}
    def fail_check(buses, stations, config):
        result = solve_optimization(buses, stations, config)
        if config.fixed_battery_packs is not None:
            result.electrification_feasible = False
        return result
    result, verification = await optimize_and_verify([base], [],
        OptimizationConfig(mode="battery_only", min_soc=0, max_soc=.9), load, solve=fail_check)
    assert verification["status"] == "cycle_detected"
    assert not result.electrification_feasible
    assert not result.battery_results["s"]["physical_feasible"]


@pytest.mark.asyncio
async def test_timeout_is_not_feasible():
    async def load(_):
        raise TimeoutError()
    result, verification = await optimize_and_verify([simple_bus(396)], [],
        OptimizationConfig(mode="battery_only", min_soc=0, max_soc=.9), load)
    assert verification["status"] == "time_budget_exhausted"
    assert not result.electrification_feasible


def test_historical_integrity_is_read_only_and_blocks_duplicate_or_unverified():
    old = {"per_bus_summary": [{"shift_id": "s"}, {"shift_id": "s"}],
           "battery_results": {"s": {}}, "electrification_feasible": True}
    assert result_integrity(old)["status"] == "duplicate_physical_shifts"
    assert len(old["per_bus_summary"]) == 2
    old["per_bus_summary"].pop()
    assert not result_integrity(old)["yearly_eligible"]
    old.update(contract_version=CONTRACT_VERSION, forecast_verification={"status": "verified"})
    assert result_integrity(old)["yearly_eligible"]


@pytest.mark.parametrize("sensitivity", [-.1, .1])
def test_mechanical_mass_delta_includes_signed_sensitivity(sensitivity):
    b = simple_bus(100, reference=10)
    b.trips[0].sensitivity = sensitivity
    result = solve_optimization([b], [], OptimizationConfig(mode="battery_only", min_soc=0,
        max_soc=.9, fixed_battery_packs={"s": 9}, fixed_station_slots={}, allow_excess_packs=False))
    assert result.per_bus_summary[0]["min_soc_kwh"] == pytest.approx(405-(100-50*sensitivity))
