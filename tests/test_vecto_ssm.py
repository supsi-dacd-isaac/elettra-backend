"""Regression tests for the VECTO 5.1.3 HVAC SSM transcription."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elettra_core.vecto_ssm import (
    VectoAuxResult,
    VectoComfortPolicy,
    VectoEnvironmentalCondition,
    VectoSsmInputs,
    _heating_distribution_case,
    _ssm_calculate,
    default_environmental_condition,
    vecto_auxiliary_power,
)

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "vecto_ssm_5_1_3_cases.json").read_text())
GOLDEN = json.loads((FIXTURES / "vecto_ssm_5_1_3_golden.json").read_text())
GOLDEN_BY_NAME = {item["name"]: item for item in GOLDEN}
BREAKDOWN_FIELDS = (
    "electrical_cooling_and_ventilation_w",
    "mechanical_cooling_w",
    "required_heating_power_w",
    "electrical_heat_pump_w",
    "mechanical_heat_pump_w",
    "electric_heater_w",
    "fuel_heater_w",
)


def _optional_number(value):
    return None if value == "NaN" else value


def _inputs_from_oracle_case(case: dict) -> tuple[VectoEnvironmentalCondition, VectoSsmInputs]:
    efficiency: dict[str, float] = {}
    fuel_efficiency = _optional_number(case["fuel_heater_efficiency"])
    if fuel_efficiency is not None:
        efficiency["fuel"] = fuel_efficiency
    electric_efficiency = _optional_number(case["electric_heater_efficiency"])
    if case["electric_heater"] != "none" and electric_efficiency is not None:
        efficiency[case["electric_heater"]] = electric_efficiency

    environment = VectoEnvironmentalCondition(
        environmental_id=case["environmental_id"],
        temperature_celsius=case["temperature_celsius"],
        solar_irradiance_wm2=case["solar_irradiance_wm2"],
        heat_pump_cop=case["heat_pump_cop"],
        heater_efficiency=efficiency,
    )
    inputs = VectoSsmInputs(
        number_of_passengers=case["number_of_passengers"],
        floor_type=case["floor_type"],
        surface_area_m2=case["surface_area_m2"],
        window_surface_m2=case["window_surface_m2"],
        volume_m3=case["volume_m3"],
        u_value_w_per_k_m2=case["u_value_w_per_k_square_m"],
        hvac_configuration=int(case["hvac_configuration"].removeprefix("Configuration")),
        driver_heat_pump=case["driver_heat_pump"],
        passenger_heat_pump=case["passenger_heat_pump"],
        electric_heaters=(
            () if case["electric_heater"] == "none" else (case["electric_heater"],)
        ),
        driver_compartment_length_m=case["driver_compartment_length_m"],
        passenger_compartment_length_m=case["passenger_compartment_length_m"],
        max_cooling_power_driver_w=case["max_cooling_power_driver_w"],
        max_cooling_power_passenger_w=case["max_cooling_power_passenger_w"],
        max_heating_power_driver_w=case["max_heating_power_driver_w"],
        max_heating_power_passenger_w=case["max_heating_power_passenger_w"],
        fuel_heater_capacity_w=case["fuel_heater_capacity_w"],
        ventilation_rate_per_hour=case["ventilation_rate_per_hour"],
        ventilation_rate_heating_per_hour=case["ventilation_rate_heating_per_hour"],
        specific_ventilation_power_wh_per_m3=case[
            "specific_ventilation_power_wh_per_m3"
        ],
        ventilation_on_during_heating=case["ventilation_on_during_heating"],
        ventilation_during_cooling=case["ventilation_during_cooling"],
        ventilation_when_inactive=case["ventilation_when_inactive"],
        engine_waste_heat_w=case.get("engine_waste_heat_w", 0.0),
        heating_variation=case.get("heating_variation", 0.0),
        heating_ventilation_variation=case.get(
            "heating_ventilation_variation", 0.0
        ),
        inactive_ventilation_variation=case.get(
            "inactive_ventilation_variation", 0.0
        ),
        cooling_ventilation_variation=case.get(
            "cooling_ventilation_variation", 0.0
        ),
        cooling_variation=case.get("cooling_variation", 0.0),
    )
    return environment, inputs


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_matches_official_vecto_5_1_3_oracle(case):
    """Compare each component with output produced by official VectoCore.dll."""
    environment, inputs = _inputs_from_oracle_case(case)
    actual = _ssm_calculate(environment, inputs)
    expected = GOLDEN_BY_NAME[case["name"]]

    for field in BREAKDOWN_FIELDS:
        assert getattr(actual, field) == pytest.approx(expected[field], abs=1e-9)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_explicit_default_comfort_policy_is_bit_for_bit_oracle(case):
    environment, inputs = _inputs_from_oracle_case(case)

    implicit = _ssm_calculate(environment, inputs)
    explicit = _ssm_calculate(
        environment,
        inputs,
        VectoComfortPolicy(),
    )

    assert explicit == implicit


def test_every_oracle_case_has_exactly_one_golden_result():
    assert [item["name"] for item in CASES] == [item["name"] for item in GOLDEN]


def test_public_result_keeps_non_hvac_load_explicit():
    environment, inputs = _inputs_from_oracle_case(CASES[2])
    result = vecto_auxiliary_power(
        environment=environment,
        inputs=inputs,
        non_hvac_baseline_kw=2.75,
    )

    assert isinstance(result, VectoAuxResult)
    assert result.p_baseline_kw == 2.75
    assert result.p_electrical_kw == pytest.approx(
        result.p_hvac_electrical_kw + 2.75
    )
    assert result.p_hvac_mechanical_kw == 0.0
    assert result.p_heating_delivered_kw <= result.p_heating_demand_kw
    assert result.unmet_thermal_demand_kw == pytest.approx(
        result.p_heating_demand_kw - result.p_heating_delivered_kw
    )
    assert result.mode == "heating"


def test_unmet_heat_is_computed_before_resource_components_are_discarded():
    environment, inputs = _inputs_from_oracle_case(CASES[17])
    result = vecto_auxiliary_power(environment=environment, inputs=inputs)

    assert result.mode == "heating"
    assert result.p_heating_demand_kw > 0.0
    assert result.p_heating_delivered_kw == 0.0
    assert result.unmet_thermal_demand_kw == result.p_heating_demand_kw


def test_legacy_length_only_api_is_rejected():
    with pytest.raises(TypeError):
        vecto_auxiliary_power(temperature_celsius=-5.0, bus_length_m=12.0)


def test_default_environment_is_exact_and_not_interpolated():
    env1 = default_environmental_condition(1)
    env2 = default_environmental_condition(2)

    assert env1.temperature_celsius == -20.0
    assert env1.solar_irradiance_wm2 == 10.0
    assert env1.heat_pump_cop == {"R744": 1.8}
    assert env2.heat_pump_cop["2stage"] == 1.54
    with pytest.raises(ValueError, match="1..11"):
        default_environmental_condition(12)


@pytest.mark.parametrize(
    ("heat_pump", "electric", "fuel", "expected"),
    [
        ("R744", False, False, 1),
        ("R744", True, False, 2),
        ("R744", True, True, 3),
        ("R744", False, True, 4),
        ("2stage", False, False, 5),
        ("3stage", True, False, 6),
        ("4stage", True, True, 7),
        ("continuous", False, True, 8),
        ("none", True, False, 9),
        ("none", True, True, 10),
        ("none", False, True, 11),
        ("none", False, False, 12),
    ],
)
def test_all_official_heating_distribution_cases(
    heat_pump, electric, fuel, expected
):
    assert _heating_distribution_case(heat_pump, electric, fuel) == expected


def test_input_validation_rejects_implicit_or_non_finite_values():
    environment, inputs = _inputs_from_oracle_case(CASES[0])
    with pytest.raises(ValueError, match="finite"):
        VectoEnvironmentalCondition(
            1, float("nan"), 10.0, environment.heat_pump_cop, {}
        )
    with pytest.raises(ValueError, match="non-negative"):
        vecto_auxiliary_power(
            environment=environment, inputs=inputs, non_hvac_baseline_kw=-1.0
        )
    with pytest.raises(ValueError, match="finite"):
        VectoComfortPolicy(heating_calculation_temperature_c=float("nan"))
    with pytest.raises(ValueError, match="non-negative"):
        VectoComfortPolicy(low_floor_max_temperature_delta_k=-1.0)
    with pytest.raises(ValueError, match="boolean"):
        VectoComfortPolicy(heating_enabled=1)  # type: ignore[arg-type]


def test_custom_comfort_policy_changes_only_the_opted_in_call():
    environment, inputs = _inputs_from_oracle_case(CASES[0])
    default_before = vecto_auxiliary_power(environment=environment, inputs=inputs)
    custom = vecto_auxiliary_power(
        environment=environment,
        inputs=inputs,
        comfort_policy=VectoComfortPolicy(
            heating_calculation_temperature_c=15.0,
        ),
    )
    default_after = vecto_auxiliary_power(environment=environment, inputs=inputs)

    assert custom.p_heating_demand_kw < default_before.p_heating_demand_kw
    assert default_after == default_before


def test_comfort_policy_can_disable_a_caller_selected_operating_interval():
    environment, inputs = _inputs_from_oracle_case(CASES[0])
    result = vecto_auxiliary_power(
        environment=environment,
        inputs=inputs,
        comfort_policy=VectoComfortPolicy(heating_enabled=False),
    )

    assert result.p_heating_demand_kw == 0.0
    assert result.p_fuel_kw == 0.0
    assert result.mode != "heating"


def test_backend_service_is_a_compatibility_reexport():
    from app.services import vecto_ssm as compatibility
    from elettra_core import vecto_ssm as shared

    assert compatibility.VectoSsmInputs is shared.VectoSsmInputs
    assert compatibility.vecto_auxiliary_power is shared.vecto_auxiliary_power
    assert compatibility._ssm_calculate is shared._ssm_calculate
