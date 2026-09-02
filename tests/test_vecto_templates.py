"""Tests for deterministic Elettra VECTO scenario declarations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from elettra_core.vecto_templates import (
    DIESEL_ENERGY_KWH_PER_L,
    VECTO_COMPLETE,
    VECTO_HVAC_ONLY,
    VECTO_TEMPLATE_RELEASE,
    build_vecto_hvac_template,
    canonical_template_release_bytes,
    environmental_condition_for_scenario,
    load_template_release,
    template_length_for_bus,
    template_release_sha256,
    vecto_ssm_source_sha256,
    vecto_template_auxiliary_power,
)
from elettra_core.vecto_ssm import VectoComfortPolicy

ROOT = Path(__file__).resolve().parents[1]
PACKAGED_RELEASE = (
    ROOT
    / "elettra_core"
    / "data"
    / "vecto_hvac_5_1_3_r744_templates_v2.json"
)
LEGACY_V1_RELEASE = (
    ROOT
    / "elettra_core"
    / "data"
    / "vecto_hvac_5_1_3_r744_templates_v1.json"
)


@pytest.mark.parametrize(
    ("actual_length", "template_length"),
    [
        (9.0, 9.0),
        (9.499, 9.0),
        (9.5, 10.0),
        (10.8, 10.0),
        (10.999, 10.0),
        (11.0, 12.0),
        (13.0, 12.0),
        (17.0, 18.0),
        (19.0, 18.0),
    ],
)
def test_explicit_length_mapping(actual_length, template_length):
    assert template_length_for_bus(actual_length) == template_length


@pytest.mark.parametrize(
    "actual_length",
    [8.999, 13.0001, 16.999, 19.001, float("nan"), float("inf")],
)
def test_unsupported_or_non_finite_length_fails_closed(actual_length):
    with pytest.raises(ValueError):
        template_length_for_bus(actual_length)


@pytest.mark.parametrize(
    (
        "length",
        "surface",
        "window",
        "volume",
        "driver_capacity",
        "passenger_capacity",
        "baseline",
    ),
    [
        (9.0, 98.1, 16.7, 45.747, 1524.9, 9911.85, 2.0),
        (10.0, 107.8, 18.2, 51.612, 1548.36, 11354.64, 2.2),
        (12.0, 127.2, 21.2, 63.342, 1583.55, 14251.95, 2.5),
        (18.0, 185.4, 30.2, 98.532, 1642.2, 22990.8, 3.1),
    ],
)
def test_template_formulas_are_frozen(
    length,
    surface,
    window,
    volume,
    driver_capacity,
    passenger_capacity,
    baseline,
):
    template = build_vecto_hvac_template(length)
    assert template.surface_area_m2 == surface
    assert template.window_surface_m2 == window
    assert template.volume_m3 == volume
    assert template.max_cooling_power_driver_w == driver_capacity
    assert template.max_cooling_power_passenger_w == passenger_capacity
    assert template.max_heating_power_driver_w == driver_capacity
    assert template.max_heating_power_passenger_w == passenger_capacity
    assert template.non_hvac_baseline_kw == baseline


def test_packaged_release_is_canonical_and_checksum_is_stable():
    expected = canonical_template_release_bytes()
    assert PACKAGED_RELEASE.read_bytes() == expected
    assert template_release_sha256() == hashlib.sha256(expected).hexdigest()
    assert template_release_sha256() == (
        "68dae71d01f93f372d04471f0604b483ab629aa606edb7ac2dcf75cca0541c51"
    )
    assert template_release_sha256() == hashlib.sha256(expected).hexdigest()

    payload = json.loads(expected)
    assert payload["release_id"] == VECTO_TEMPLATE_RELEASE
    assert payload["fuel_policy"]["energy_density_kwh_per_l"] == (
        DIESEL_ENERGY_KWH_PER_L
    )
    assert payload["implementation"]["source_sha256"] == (
        vecto_ssm_source_sha256()
    )
    assert set(payload["templates"]) == {"9", "10", "12", "18"}
    loaded = load_template_release()
    assert loaded.release_id == VECTO_TEMPLATE_RELEASE
    assert loaded.content_sha256 == template_release_sha256()
    assert loaded.ssm_source_sha256 == vecto_ssm_source_sha256()


def test_legacy_v1_artifact_remains_immutable_with_its_original_identity():
    raw = LEGACY_V1_RELEASE.read_bytes()
    payload = json.loads(raw)

    assert hashlib.sha256(raw).hexdigest() == (
        "982e4bc7fa65053dcfef943b8cc7fe60de64c834fc82bd4425e8ce0a36b5e5d2"
    )
    assert payload["release_id"] == "vecto-hvac-5.1.3-r744-templates-v1"
    assert payload["implementation"]["source_sha256"] == (
        "195981d937822a8e4d001a1936a5d4712b7763858906101ab227da47cfc487bb"
    )


def test_generator_check_is_read_only_and_passes():
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_vecto_hvac_templates.py"),
            "--check",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert template_release_sha256() in result.stdout


def test_noncanonical_release_is_rejected(tmp_path):
    changed = json.loads(PACKAGED_RELEASE.read_text())
    changed["templates"]["12"]["non_hvac_baseline_kw"] = 2.6
    path = tmp_path / "changed.json"
    path.write_text(json.dumps(changed))

    with pytest.raises(ValueError, match="not the canonical generated artifact"):
        load_template_release(path)


def test_environment_adapter_retains_selected_maps_but_uses_scenario_weather():
    environment = environmental_condition_for_scenario(
        temperature_celsius=11.0,
        solar_irradiance_wm2=100.0,
    )

    # Rows 4--7 are equally distant in temperature; row 5 is closest in GHI.
    assert environment.environmental_id == 5
    assert environment.temperature_celsius == 11.0
    assert environment.solar_irradiance_wm2 == 100.0
    assert environment.heat_pump_cop["R744"] == 2.98
    assert environment.heater_efficiency["fuel"] == 0.8


def test_contracts_differ_only_by_explicit_non_hvac_baseline():
    common = {
        "bus_length_m": 12.0,
        "number_of_passengers": 30.0,
        "temperature_celsius": -5.0,
        "solar_irradiance_wm2": 100.0,
        "auxiliary_heating_type": "diesel",
    }
    hvac = vecto_template_auxiliary_power(
        **common, auxiliary_contract=VECTO_HVAC_ONLY
    )
    complete = vecto_template_auxiliary_power(
        **common, auxiliary_contract=VECTO_COMPLETE
    )

    assert hvac.template_length_m == complete.template_length_m == 12.0
    assert hvac.ssm_source_sha256 == vecto_ssm_source_sha256()
    assert hvac.environmental_id == complete.environmental_id == 2
    assert hvac.result.p_hvac_electrical_kw == pytest.approx(
        complete.result.p_hvac_electrical_kw
    )
    assert hvac.result.p_fuel_kw == pytest.approx(complete.result.p_fuel_kw)
    assert hvac.result.p_baseline_kw == 0.0
    assert complete.result.p_baseline_kw == 2.5
    assert complete.result.p_electrical_kw - hvac.result.p_electrical_kw == (
        pytest.approx(2.5)
    )


def test_diesel_mode_changes_fuel_only_through_explicit_ssm_input():
    common = {
        "bus_length_m": 12.0,
        "number_of_passengers": 30.0,
        "temperature_celsius": -20.0,
        "auxiliary_contract": VECTO_HVAC_ONLY,
    }
    diesel = vecto_template_auxiliary_power(
        **common, auxiliary_heating_type="diesel"
    )
    no_diesel = vecto_template_auxiliary_power(
        **common, auxiliary_heating_type="default"
    )

    assert diesel.result.p_fuel_kw > 0.0
    assert diesel.fuel_l_per_hour == pytest.approx(
        diesel.result.p_fuel_kw / DIESEL_ENERGY_KWH_PER_L
    )
    assert no_diesel.result.p_fuel_kw == 0.0
    assert no_diesel.fuel_l_per_hour == 0.0
    assert diesel.unmet_thermal_demand_kw == (
        diesel.result.unmet_thermal_demand_kw
    )
    assert no_diesel.unmet_thermal_demand_kw > 0.0


def test_passenger_count_is_dynamic_not_embedded_in_template():
    common = {
        "bus_length_m": 10.8,
        "temperature_celsius": -5.0,
        "auxiliary_contract": VECTO_HVAC_ONLY,
        "auxiliary_heating_type": "diesel",
    }
    empty = vecto_template_auxiliary_power(**common, number_of_passengers=0.0)
    occupied = vecto_template_auxiliary_power(
        **common, number_of_passengers=50.0
    )

    assert empty.template_length_m == occupied.template_length_m == 10.0
    assert empty.result.p_heating_demand_kw > occupied.result.p_heating_demand_kw


def test_training_can_supply_a_generic_comfort_policy_without_changing_default():
    common = {
        "bus_length_m": 12.0,
        "number_of_passengers": 30.0,
        "temperature_celsius": -10.0,
        "auxiliary_contract": VECTO_HVAC_ONLY,
        "auxiliary_heating_type": "diesel",
    }
    default = vecto_template_auxiliary_power(**common)
    custom = vecto_template_auxiliary_power(
        **common,
        comfort_policy=VectoComfortPolicy(
            heating_calculation_temperature_c=15.0,
        ),
    )

    assert default.comfort_policy == VectoComfortPolicy()
    assert custom.comfort_policy.heating_calculation_temperature_c == 15.0
    assert custom.result.p_heating_demand_kw < default.result.p_heating_demand_kw


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("number_of_passengers", -1.0),
        ("number_of_passengers", float("nan")),
        ("temperature_celsius", float("inf")),
        ("solar_irradiance_wm2", -1.0),
    ],
)
def test_scenario_inputs_are_validated(argument, value):
    kwargs = {
        "bus_length_m": 12.0,
        "number_of_passengers": 30.0,
        "temperature_celsius": -5.0,
        "solar_irradiance_wm2": 100.0,
        "auxiliary_contract": VECTO_HVAC_ONLY,
        "auxiliary_heating_type": "diesel",
    }
    kwargs[argument] = value
    with pytest.raises(ValueError):
        vecto_template_auxiliary_power(**kwargs)


def test_contract_and_heating_type_are_not_silently_coerced():
    common = {
        "bus_length_m": 12.0,
        "number_of_passengers": 30.0,
        "temperature_celsius": -5.0,
    }
    with pytest.raises(ValueError, match="auxiliary_contract"):
        vecto_template_auxiliary_power(
            **common,
            auxiliary_contract="legacy",  # type: ignore[arg-type]
            auxiliary_heating_type="diesel",
        )
    with pytest.raises(ValueError, match="auxiliary_heating_type"):
        vecto_template_auxiliary_power(
            **common,
            auxiliary_contract=VECTO_HVAC_ONLY,
            auxiliary_heating_type="fuel",  # type: ignore[arg-type]
        )
