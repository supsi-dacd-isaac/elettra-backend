"""Runtime adapter from approved VECTO templates to model energy components."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable

import numpy as np
import pandas as pd

from elettra_core.greybox import AuxiliaryEnergyComponents
from elettra_core.vecto_templates import (
    VECTO_COMPLETE,
    VECTO_DEFAULT_GHI_WM2,
    VECTO_HVAC_ONLY,
    VECTO_TEMPLATE_RELEASE,
    VectoTemplateEstimate,
    load_template_release,
    vecto_template_auxiliary_power,
)

from app.services.diesel_heating import (
    diesel_heating_fuel_profile,
    diesel_liters_from_kwh,
)
from app.services.runtime_release import PredictionStack, PredictionStackRelease
from app.services.hvac_policy import LEGACY_POLICY, calculate_operational_hvac


@dataclass(frozen=True)
class VectoAuxiliaryBinding:
    energy_fn: Callable[[pd.DataFrame], AuxiliaryEnergyComponents]
    estimate: VectoTemplateEstimate
    operational: dict | None = None
    terminal_intervals: tuple = ()

    def metadata(self) -> dict[str, object]:
        result = self.estimate.result
        fuel_profile = diesel_heating_fuel_profile()
        canonical_liters_per_hour = diesel_liters_from_kwh(result.p_fuel_kw)
        metadata = {
            "release_id": self.estimate.release_id,
            "release_sha256": self.estimate.release_sha256,
            "ssm_source_sha256": self.estimate.ssm_source_sha256,
            "auxiliary_contract": self.estimate.auxiliary_contract,
            "auxiliary_heating_type": self.estimate.auxiliary_heating_type,
            "bus_length_m": self.estimate.bus_length_m,
            "template_length_m": self.estimate.template_length_m,
            "number_of_passengers": self.estimate.number_of_passengers,
            "solar_irradiance_wm2": self.estimate.solar_irradiance_wm2,
            "environmental_id": self.estimate.environmental_id,
            "comfort_policy": asdict(self.estimate.comfort_policy),
            "diesel_heater_efficiency": self.estimate.fuel_heater_efficiency,
            "hvac_electrical_power_kw": result.p_hvac_electrical_kw,
            "fixed_auxiliary_power_kw": result.p_baseline_kw,
            "diesel_fuel_power_kw": result.p_fuel_kw,
            "diesel_liters_per_hour": canonical_liters_per_hour,
            "diesel_liters_source": "application_fuel_profile",
            "diesel_fuel_profile_version": fuel_profile["version"],
            "diesel_energy_density_kwh_per_liter": fuel_profile[
                "energy_density_kwh_per_liter"
            ],
            "vecto_release_legacy_diesel_liters_per_hour": (
                self.estimate.fuel_l_per_hour
            ),
            "uncovered_thermal_power_kw": self.estimate.unmet_thermal_demand_kw,
        }
        if self.operational is not None:
            metadata['environmental_id'] = None  # each minute selects its own climatic row
            metadata.pop('vecto_release_legacy_diesel_liters_per_hour', None)
            metadata['operational'] = self.operational
            metadata['power_mode'] = 'trip-time-weighted-mean'
            metadata['environmental_selection'] = 'per-physical-GHI-before-clouding-normalization'
        return metadata


def build_vecto_auxiliary_binding(
    *,
    stack_release: PredictionStackRelease,
    bus_model_specs: dict,
    occupancy_percent: float,
    external_temp_celsius: float,
    auxiliary_heating_type: str,
    hvac_inputs: dict | None = None,
    weather_snapshot: dict | None = None,
    hvac_cache: dict | None = None,
) -> VectoAuxiliaryBinding:
    """Bind a VECTO stack to one bus/scenario, failing on implicit inputs."""

    if stack_release.stack is PredictionStack.VECTO_G2:
        contract = VECTO_HVAC_ONLY
    elif stack_release.stack is PredictionStack.VECTO_G0_TRANSFER:
        contract = VECTO_COMPLETE
    else:
        raise ValueError("A legacy stack cannot use the VECTO auxiliary adapter")
    if auxiliary_heating_type not in {"default", "diesel"}:
        raise ValueError(
            "VECTO auxiliary_heating_type must be 'default' or 'diesel'"
        )
    if "bus_length_m" not in bus_model_specs:
        raise ValueError("VECTO prediction requires bus_model.specs.bus_length_m")
    if "max_passengers" not in bus_model_specs:
        raise ValueError("VECTO prediction requires bus_model.specs.max_passengers")
    occupancy = float(occupancy_percent)
    if not np.isfinite(occupancy) or not 0 <= occupancy <= 100:
        raise ValueError("occupancy_percent must be finite and between 0 and 100")
    max_passengers = float(bus_model_specs["max_passengers"])
    if not np.isfinite(max_passengers) or max_passengers < 0:
        raise ValueError("max_passengers must be finite and non-negative")
    hvac_cache = hvac_cache if hvac_cache is not None else {}
    if 'vecto_template_release' not in hvac_cache:
        hvac_cache['vecto_template_release'] = load_template_release()
    estimate_key = ('vecto_reference_estimate', float(bus_model_specs['bus_length_m']),
                    max_passengers * occupancy / 100., float(external_temp_celsius),
                    contract, auxiliary_heating_type)
    if estimate_key not in hvac_cache:
        hvac_cache[estimate_key] = vecto_template_auxiliary_power(
            bus_length_m=estimate_key[1], number_of_passengers=estimate_key[2],
            temperature_celsius=estimate_key[3], auxiliary_contract=contract,
            auxiliary_heating_type=auxiliary_heating_type,
            solar_irradiance_wm2=VECTO_DEFAULT_GHI_WM2,
            release=hvac_cache['vecto_template_release'],
        )
    estimate = hvac_cache[estimate_key]
    if estimate.release_id != VECTO_TEMPLATE_RELEASE:
        raise ValueError("VECTO template release identity is not the runtime release")

    operational = None
    intervals = []
    energies = None
    if hvac_inputs is not None and hvac_inputs.get('policy_id') != LEGACY_POLICY:
        energies, intervals, operational = calculate_operational_hvac(
            policy_id=hvac_inputs['policy_id'], schedule=hvac_inputs['schedule'],
            length=float(bus_model_specs['bus_length_m']), passengers=max_passengers * occupancy / 100,
            temperature=float(external_temp_celsius), heating_type=auxiliary_heating_type,
            weather=weather_snapshot, dates=hvac_inputs.get('cluster_dates'), cache=hvac_cache,
            weather_sha256=hvac_inputs.get('weather_snapshot_sha256'), saved_origins=hvac_inputs.get('service_origins_utc'))
        hours = operational['trip_minutes'] / 60
        operational['occupancy_percent'] = occupancy
        operational['temperature_clustering'] = hvac_inputs.get('temperature_clustering')
        mean = energies.sum(axis=0) / hours
        result = replace(estimate.result, p_hvac_electrical_kw=float(mean[0]),
                         p_electrical_kw=float(mean[0]) + estimate.result.p_baseline_kw,
                         p_fuel_kw=float(mean[1]), unmet_thermal_demand_kw=float(mean[2]))
        estimate = replace(estimate, result=result, solar_irradiance_wm2=operational['ghi_trip_weighted_mean_wm2'],
                           unmet_thermal_demand_kw=float(mean[2]))

    def energy_fn(frame: pd.DataFrame) -> AuxiliaryEnergyComponents:
        if "total_duration_minutes" not in frame:
            raise ValueError("VECTO auxiliary energy requires total_duration_minutes")
        duration_hours = frame["total_duration_minutes"].to_numpy(dtype=float) / 60.0
        if (
            not np.isfinite(duration_hours).all()
            or (duration_hours < 0).any()
        ):
            raise ValueError("Trip durations must be finite and non-negative")
        result = estimate.result
        if energies is not None:
            expected = np.array([(r['end_minute'] - r['start_minute']) / 60 for r in hvac_inputs['schedule']])
            if len(expected) != len(duration_hours) or not np.allclose(expected, duration_hours, atol=1e-6, rtol=0):
                raise ValueError('HVAC timetable durations do not match prediction occurrences')
        diesel_fuel_kwh = energies[:, 1] if energies is not None else result.p_fuel_kw * duration_hours
        return AuxiliaryEnergyComponents(
            hvac_electrical_kwh=energies[:, 0] if energies is not None else result.p_hvac_electrical_kw * duration_hours,
            fixed_auxiliary_kwh=result.p_baseline_kw * duration_hours,
            diesel_fuel_kwh=diesel_fuel_kwh,
            diesel_liters=np.asarray(
                [diesel_liters_from_kwh(value) for value in diesel_fuel_kwh],
                dtype=float,
            ),
            uncovered_thermal_kwh=(
                energies[:, 2] if energies is not None else estimate.unmet_thermal_demand_kw * duration_hours
            ),
        )

    return VectoAuxiliaryBinding(energy_fn=energy_fn, estimate=estimate,
                                operational=operational, terminal_intervals=tuple(intervals))


__all__ = ["VectoAuxiliaryBinding", "build_vecto_auxiliary_binding"]
