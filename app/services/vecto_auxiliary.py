"""Runtime adapter from approved VECTO templates to model energy components."""

from __future__ import annotations

from dataclasses import asdict, dataclass
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
    vecto_template_auxiliary_power,
)

from app.services.runtime_release import PredictionStack, PredictionStackRelease


@dataclass(frozen=True)
class VectoAuxiliaryBinding:
    energy_fn: Callable[[pd.DataFrame], AuxiliaryEnergyComponents]
    estimate: VectoTemplateEstimate

    def metadata(self) -> dict[str, object]:
        result = self.estimate.result
        return {
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
            "diesel_liters_per_hour": self.estimate.fuel_l_per_hour,
            "uncovered_thermal_power_kw": self.estimate.unmet_thermal_demand_kw,
        }


def build_vecto_auxiliary_binding(
    *,
    stack_release: PredictionStackRelease,
    bus_model_specs: dict,
    occupancy_percent: float,
    external_temp_celsius: float,
    auxiliary_heating_type: str,
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
    estimate = vecto_template_auxiliary_power(
        bus_length_m=float(bus_model_specs["bus_length_m"]),
        number_of_passengers=max_passengers * occupancy / 100.0,
        temperature_celsius=float(external_temp_celsius),
        auxiliary_contract=contract,
        auxiliary_heating_type=auxiliary_heating_type,
        solar_irradiance_wm2=VECTO_DEFAULT_GHI_WM2,
    )
    if estimate.release_id != VECTO_TEMPLATE_RELEASE:
        raise ValueError("VECTO template release identity is not the runtime release")

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
        return AuxiliaryEnergyComponents(
            hvac_electrical_kwh=result.p_hvac_electrical_kw * duration_hours,
            fixed_auxiliary_kwh=result.p_baseline_kw * duration_hours,
            diesel_fuel_kwh=result.p_fuel_kw * duration_hours,
            diesel_liters=estimate.fuel_l_per_hour * duration_hours,
            uncovered_thermal_kwh=(
                estimate.unmet_thermal_demand_kw * duration_hours
            ),
        )

    return VectoAuxiliaryBinding(energy_fn=energy_fn, estimate=estimate)


__all__ = ["VectoAuxiliaryBinding", "build_vecto_auxiliary_binding"]
