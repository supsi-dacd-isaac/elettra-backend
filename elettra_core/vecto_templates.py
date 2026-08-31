"""Versioned VECTO HVAC templates shared by training and inference.

The low-level :mod:`elettra_core.vecto_ssm` implementation deliberately needs
an explicit VECTO declaration.  This module defines the scenario declarations
approved for Elettra.  They are deterministic engineering templates, not
vehicle homologation declarations.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib import resources
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

from .vecto_ssm import (
    VectoAuxResult,
    VectoEnvironmentalCondition,
    VectoSsmInputs,
    default_environmental_condition,
    vecto_auxiliary_power,
)

VECTO_TEMPLATE_SCHEMA_VERSION = 1
VECTO_TEMPLATE_RELEASE = "vecto-hvac-5.1.3-r744-templates-v1"
VECTO_UPSTREAM_VERSION = "5.1.3"
VECTO_UPSTREAM_COMMIT = "cef1f3d260afa7f7c6ec09981d821e545d21b249"
VECTO_ENVIRONMENT_POLICY = "nearest-default-climatic-row-v1"
VECTO_DEFAULT_GHI_WM2 = 100.0
DIESEL_ENERGY_KWH_PER_L = 9.94
VECTO_HVAC_ONLY = "vecto-hvac-only"
VECTO_COMPLETE = "vecto-complete"

VectoAuxiliaryContract = Literal["vecto-hvac-only", "vecto-complete"]
VectoAuxiliaryHeatingType = Literal["default", "diesel"]

_SUPPORTED_TEMPLATE_LENGTHS_M = (9.0, 10.0, 12.0, 18.0)
_NON_HVAC_BASELINE_KW = MappingProxyType(
    {9.0: 2.0, 10.0: 2.2, 12.0: 2.5, 18.0: 3.1}
)
_DRIVER_COMPARTMENT_LENGTH_M = 1.2
_HVAC_CAPACITY_W_PER_M3 = 250.0
_FUEL_HEATER_CAPACITY_W = 30_000.0
_SPECIFIC_VENTILATION_POWER_WH_PER_M3 = 0.56
_PACKAGED_RELEASE_PARTS = (
    "data",
    "vecto_hvac_5_1_3_r744_templates_v1.json",
)


def _require_finite(name: str, value: float) -> float:
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


def _require_non_negative(name: str, value: float) -> float:
    converted = _require_finite(name, value)
    if converted < 0:
        raise ValueError(f"{name} must be non-negative")
    return converted


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def vecto_ssm_source_sha256() -> str:
    """Hash the installed faithful transcription bound to the templates."""
    source = resources.files("elettra_core").joinpath("vecto_ssm.py").read_bytes()
    return hashlib.sha256(source).hexdigest()


@dataclass(frozen=True)
class VectoHvacTemplate:
    """One length-class declaration plus its caller-owned fixed baseline."""

    template_length_m: float
    surface_area_m2: float
    window_surface_m2: float
    volume_m3: float
    driver_compartment_length_m: float
    passenger_compartment_length_m: float
    max_cooling_power_driver_w: float
    max_cooling_power_passenger_w: float
    max_heating_power_driver_w: float
    max_heating_power_passenger_w: float
    non_hvac_baseline_kw: float

    def ssm_inputs(
        self,
        *,
        number_of_passengers: float,
        auxiliary_heating_type: VectoAuxiliaryHeatingType,
    ) -> VectoSsmInputs:
        """Create explicit SSM inputs for one prediction/training scenario."""
        passengers = _require_non_negative(
            "number_of_passengers", number_of_passengers
        )
        if auxiliary_heating_type not in {"default", "diesel"}:
            raise ValueError(
                "auxiliary_heating_type must be 'default' or 'diesel'"
            )
        return VectoSsmInputs(
            number_of_passengers=passengers,
            floor_type="LowFloor",
            surface_area_m2=self.surface_area_m2,
            window_surface_m2=self.window_surface_m2,
            volume_m3=self.volume_m3,
            u_value_w_per_k_m2=4.0,
            hvac_configuration=9,
            driver_heat_pump="R744",
            passenger_heat_pump="R744",
            electric_heaters=(),
            driver_compartment_length_m=self.driver_compartment_length_m,
            passenger_compartment_length_m=self.passenger_compartment_length_m,
            max_cooling_power_driver_w=self.max_cooling_power_driver_w,
            max_cooling_power_passenger_w=self.max_cooling_power_passenger_w,
            max_heating_power_driver_w=self.max_heating_power_driver_w,
            max_heating_power_passenger_w=self.max_heating_power_passenger_w,
            fuel_heater_capacity_w=(
                _FUEL_HEATER_CAPACITY_W
                if auxiliary_heating_type == "diesel"
                else 0.0
            ),
            ventilation_rate_per_hour=20.0,
            ventilation_rate_heating_per_hour=10.0,
            specific_ventilation_power_wh_per_m3=(
                _SPECIFIC_VENTILATION_POWER_WH_PER_M3
            ),
        )


@dataclass(frozen=True)
class VectoTemplateRelease:
    """Validated packaged declaration release."""

    release_id: str
    content_sha256: str
    templates_sha256: str
    ssm_source_sha256: str
    templates: Mapping[float, VectoHvacTemplate]

    def template_for_bus_length(self, bus_length_m: float) -> VectoHvacTemplate:
        return self.templates[template_length_for_bus(bus_length_m)]


@dataclass(frozen=True)
class VectoTemplateEstimate:
    """One result with all provenance required for an energy breakdown."""

    release_id: str
    release_sha256: str
    ssm_source_sha256: str
    auxiliary_contract: VectoAuxiliaryContract
    auxiliary_heating_type: VectoAuxiliaryHeatingType
    bus_length_m: float
    template_length_m: float
    number_of_passengers: float
    solar_irradiance_wm2: float
    environmental_id: int
    fuel_heater_efficiency: float | None
    fuel_l_per_hour: float
    unmet_thermal_demand_kw: float
    result: VectoAuxResult


def template_length_for_bus(bus_length_m: float) -> float:
    """Map an actual bus length onto a supported declaration class."""
    length = _require_finite("bus_length_m", bus_length_m)
    if 9.0 <= length < 9.5:
        return 9.0
    if 9.5 <= length < 11.0:
        return 10.0
    if 11.0 <= length <= 13.0:
        return 12.0
    if 17.0 <= length <= 19.0:
        return 18.0
    raise ValueError(
        f"unsupported bus_length_m {length:g}; supported intervals are "
        "[9, 9.5), [9.5, 11), [11, 13], and [17, 19] metres"
    )


def build_vecto_hvac_template(template_length_m: float) -> VectoHvacTemplate:
    """Derive one template from the frozen release formulas."""
    length = _require_finite("template_length_m", template_length_m)
    if length not in _SUPPORTED_TEMPLATE_LENGTHS_M:
        raise ValueError(
            f"template_length_m must be one of {_SUPPORTED_TEMPLATE_LENGTHS_M}"
        )
    # Six decimal places are more precise than the source assumptions and
    # prevent binary floating-point noise from entering the release JSON.
    surface_area = round(9.7 * length + 10.8, 6)
    window_surface = round(1.5 * length + 3.2, 6)
    volume = round(5.865 * length - 7.038, 6)
    passenger_length = round(length - _DRIVER_COMPARTMENT_LENGTH_M, 6)
    total_capacity = _HVAC_CAPACITY_W_PER_M3 * volume
    driver_capacity = round(
        total_capacity * _DRIVER_COMPARTMENT_LENGTH_M / length, 6
    )
    passenger_capacity = round(total_capacity * passenger_length / length, 6)
    return VectoHvacTemplate(
        template_length_m=length,
        surface_area_m2=surface_area,
        window_surface_m2=window_surface,
        volume_m3=volume,
        driver_compartment_length_m=_DRIVER_COMPARTMENT_LENGTH_M,
        passenger_compartment_length_m=passenger_length,
        max_cooling_power_driver_w=driver_capacity,
        max_cooling_power_passenger_w=passenger_capacity,
        max_heating_power_driver_w=driver_capacity,
        max_heating_power_passenger_w=passenger_capacity,
        non_hvac_baseline_kw=_NON_HVAC_BASELINE_KW[length],
    )


def _template_payload(template: VectoHvacTemplate) -> dict[str, Any]:
    return {
        "template_length_m": template.template_length_m,
        "surface_area_m2": template.surface_area_m2,
        "window_surface_m2": template.window_surface_m2,
        "volume_m3": template.volume_m3,
        "driver_compartment_length_m": template.driver_compartment_length_m,
        "passenger_compartment_length_m": template.passenger_compartment_length_m,
        "max_cooling_power_driver_w": template.max_cooling_power_driver_w,
        "max_cooling_power_passenger_w": template.max_cooling_power_passenger_w,
        "max_heating_power_driver_w": template.max_heating_power_driver_w,
        "max_heating_power_passenger_w": template.max_heating_power_passenger_w,
        "non_hvac_baseline_kw": template.non_hvac_baseline_kw,
        "floor_type": "LowFloor",
        "u_value_w_per_k_m2": 4.0,
        "hvac_configuration": 9,
        "driver_heat_pump": "R744",
        "passenger_heat_pump": "R744",
        "electric_heaters": [],
        "fuel_heater_capacity_w": _FUEL_HEATER_CAPACITY_W,
        "ventilation_rate_per_hour": 20.0,
        "ventilation_rate_heating_per_hour": 10.0,
        "specific_ventilation_power_wh_per_m3": (
            _SPECIFIC_VENTILATION_POWER_WH_PER_M3
        ),
    }


def build_template_release_payload() -> dict[str, Any]:
    """Build the canonical, timestamp-free declaration release payload."""
    templates = {
        f"{length:g}": _template_payload(build_vecto_hvac_template(length))
        for length in _SUPPORTED_TEMPLATE_LENGTHS_M
    }
    templates_sha256 = hashlib.sha256(
        _canonical_json_bytes(templates)
    ).hexdigest()
    return {
        "schema_version": VECTO_TEMPLATE_SCHEMA_VERSION,
        "release_id": VECTO_TEMPLATE_RELEASE,
        "status": "production-scenario",
        "upstream": {
            "name": "VECTO",
            "version": VECTO_UPSTREAM_VERSION,
            "commit": VECTO_UPSTREAM_COMMIT,
            "scope": "bus HVAC steady-state model only",
        },
        "generator": {
            "surface_area_m2": "9.7 * length_m + 10.8",
            "window_surface_m2": "1.5 * length_m + 3.2",
            "volume_m3": "5.865 * length_m - 7.038",
            "driver_compartment_length_m": _DRIVER_COMPARTMENT_LENGTH_M,
            "hvac_capacity_w": "250 * volume_m3, split by compartment length",
            "baseline_policy": "explicit-elettra-scenario-v1",
        },
        "environment_policy": {
            "id": VECTO_ENVIRONMENT_POLICY,
            "default_solar_irradiance_wm2": VECTO_DEFAULT_GHI_WM2,
            "description": (
                "Select the nearest DefaultClimatic.aenv row by ambient "
                "temperature, then irradiance and row id; retain COP and "
                "heater efficiencies while replacing temperature and "
                "irradiance with scenario values. COP is not interpolated."
            ),
        },
        "fuel_policy": {
            "fuel": "diesel",
            "energy_density_kwh_per_l": DIESEL_ENERGY_KWH_PER_L,
            "conversion": "fuel_l_per_hour = p_fuel_kw / energy_density_kwh_per_l",
        },
        "implementation": {
            "module": "elettra_core.vecto_ssm",
            "source_sha256": vecto_ssm_source_sha256(),
        },
        "length_mapping": [
            {"minimum_m": 9.0, "maximum_m": 9.5, "maximum_inclusive": False, "template_m": 9.0},
            {"minimum_m": 9.5, "maximum_m": 11.0, "maximum_inclusive": False, "template_m": 10.0},
            {"minimum_m": 11.0, "maximum_m": 13.0, "maximum_inclusive": True, "template_m": 12.0},
            {"minimum_m": 17.0, "maximum_m": 19.0, "maximum_inclusive": True, "template_m": 18.0},
        ],
        "contracts": {
            VECTO_HVAC_ONLY: {
                "electrical_power": "p_hvac_electrical_kw",
                "non_hvac_baseline_included": False,
            },
            VECTO_COMPLETE: {
                "electrical_power": "p_hvac_electrical_kw + non_hvac_baseline_kw",
                "non_hvac_baseline_included": True,
            },
        },
        "templates_sha256": templates_sha256,
        "templates": templates,
    }


def canonical_template_release_bytes() -> bytes:
    return _canonical_json_bytes(build_template_release_payload())


def template_release_sha256() -> str:
    return hashlib.sha256(canonical_template_release_bytes()).hexdigest()


def _template_from_payload(payload: Mapping[str, Any]) -> VectoHvacTemplate:
    fields = {
        field_name: payload[field_name]
        for field_name in VectoHvacTemplate.__dataclass_fields__
    }
    return VectoHvacTemplate(**fields)


def load_template_release(path: str | Path | None = None) -> VectoTemplateRelease:
    """Load and strictly validate the packaged immutable template release."""
    if path is None:
        raw = resources.files("elettra_core").joinpath(
            *_PACKAGED_RELEASE_PARTS
        ).read_bytes()
    else:
        raw = Path(path).read_bytes()
    expected = canonical_template_release_bytes()
    if raw != expected:
        raise ValueError(
            "VECTO template release is not the canonical generated artifact; "
            "run scripts/generate_vecto_hvac_templates.py --check"
        )
    payload = json.loads(raw)
    template_payload = payload["templates"]
    templates_digest = hashlib.sha256(
        _canonical_json_bytes(template_payload)
    ).hexdigest()
    if templates_digest != payload["templates_sha256"]:
        raise ValueError("VECTO template payload checksum mismatch")
    templates = {
        float(length): _template_from_payload(template)
        for length, template in template_payload.items()
    }
    return VectoTemplateRelease(
        release_id=payload["release_id"],
        content_sha256=hashlib.sha256(raw).hexdigest(),
        templates_sha256=templates_digest,
        ssm_source_sha256=payload["implementation"]["source_sha256"],
        templates=MappingProxyType(templates),
    )


def environmental_condition_for_scenario(
    *,
    temperature_celsius: float,
    solar_irradiance_wm2: float = VECTO_DEFAULT_GHI_WM2,
) -> VectoEnvironmentalCondition:
    """Bind dynamic weather to the nearest official VECTO climatic row."""
    temperature = _require_finite("temperature_celsius", temperature_celsius)
    irradiance = _require_non_negative(
        "solar_irradiance_wm2", solar_irradiance_wm2
    )
    rows = [default_environmental_condition(index) for index in range(1, 12)]
    selected = min(
        rows,
        key=lambda row: (
            abs(row.temperature_celsius - temperature),
            abs(row.solar_irradiance_wm2 - irradiance),
            row.environmental_id,
        ),
    )
    return VectoEnvironmentalCondition(
        environmental_id=selected.environmental_id,
        temperature_celsius=temperature,
        solar_irradiance_wm2=irradiance,
        heat_pump_cop=dict(selected.heat_pump_cop),
        heater_efficiency=dict(selected.heater_efficiency),
    )


def vecto_template_auxiliary_power(
    *,
    bus_length_m: float,
    number_of_passengers: float,
    temperature_celsius: float,
    auxiliary_contract: VectoAuxiliaryContract,
    auxiliary_heating_type: VectoAuxiliaryHeatingType,
    solar_irradiance_wm2: float = VECTO_DEFAULT_GHI_WM2,
    release: VectoTemplateRelease | None = None,
) -> VectoTemplateEstimate:
    """Evaluate one approved declaration without hiding contract ownership."""
    if auxiliary_contract not in {VECTO_HVAC_ONLY, VECTO_COMPLETE}:
        raise ValueError(
            "auxiliary_contract must be 'vecto-hvac-only' or 'vecto-complete'"
        )
    actual_length = _require_finite("bus_length_m", bus_length_m)
    passengers = _require_non_negative(
        "number_of_passengers", number_of_passengers
    )
    declarations = release or load_template_release()
    template = declarations.template_for_bus_length(actual_length)
    environment = environmental_condition_for_scenario(
        temperature_celsius=temperature_celsius,
        solar_irradiance_wm2=solar_irradiance_wm2,
    )
    inputs = template.ssm_inputs(
        number_of_passengers=passengers,
        auxiliary_heating_type=auxiliary_heating_type,
    )
    result = vecto_auxiliary_power(
        environment=environment,
        inputs=inputs,
        non_hvac_baseline_kw=(
            template.non_hvac_baseline_kw
            if auxiliary_contract == VECTO_COMPLETE
            else 0.0
        ),
    )
    return VectoTemplateEstimate(
        release_id=declarations.release_id,
        release_sha256=declarations.content_sha256,
        ssm_source_sha256=declarations.ssm_source_sha256,
        auxiliary_contract=auxiliary_contract,
        auxiliary_heating_type=auxiliary_heating_type,
        bus_length_m=actual_length,
        template_length_m=template.template_length_m,
        number_of_passengers=passengers,
        solar_irradiance_wm2=environment.solar_irradiance_wm2,
        environmental_id=environment.environmental_id,
        fuel_heater_efficiency=environment.heater_efficiency.get("fuel"),
        fuel_l_per_hour=result.p_fuel_kw / DIESEL_ENERGY_KWH_PER_L,
        unmet_thermal_demand_kw=result.unmet_thermal_demand_kw,
        result=result,
    )


__all__ = [
    "DIESEL_ENERGY_KWH_PER_L",
    "VECTO_COMPLETE",
    "VECTO_DEFAULT_GHI_WM2",
    "VECTO_ENVIRONMENT_POLICY",
    "VECTO_HVAC_ONLY",
    "VECTO_TEMPLATE_RELEASE",
    "VECTO_TEMPLATE_SCHEMA_VERSION",
    "VECTO_UPSTREAM_COMMIT",
    "VECTO_UPSTREAM_VERSION",
    "VectoAuxiliaryContract",
    "VectoAuxiliaryHeatingType",
    "VectoHvacTemplate",
    "VectoTemplateEstimate",
    "VectoTemplateRelease",
    "build_template_release_payload",
    "build_vecto_hvac_template",
    "canonical_template_release_bytes",
    "environmental_condition_for_scenario",
    "load_template_release",
    "template_length_for_bus",
    "template_release_sha256",
    "vecto_ssm_source_sha256",
    "vecto_template_auxiliary_power",
]
