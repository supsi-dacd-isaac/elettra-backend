"""
Helper utilities for computing greybox battery-size sensitivities.

These functions are kept separate from the core greybox model definition to
avoid circular imports and to make it easy to reuse the sensitivity logic
where needed (e.g., in prediction and optimization code).
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


def compute_battery_sensitivity_from_metadata(
    features: pd.DataFrame,
    greybox_params: Dict[str, float],
    battery_pack_density_override: float | None = None,
    override_mass: np.ndarray | None = None,
) -> np.ndarray:
    """
    Compute dE_mech / d(bus_battery_kwh) for each row in `features`.

    For legacy/G0 models the mechanical greybox model is:

        m = rho_batt * bus_battery_kwh + k1 * bus_length_m + k2

        E_mech = alpha_roll  * m * L
               + alpha_aero  * L * v^2 * (driving_time / total_duration)
               + alpha_up    * m * h_up
               + alpha_down  * m * h_down

    G2 replaces the independent downhill term by linked regeneration, capped
    by battery C-rate and downhill time. Its derivative follows the active
    minimum branch (potential energy or power cap). Since the aero term does
    not depend on mass, the uncapped G0 derivative is:

        dE_mech / d(bus_battery_kwh)
            = rho_batt * (alpha_roll * L + alpha_up * h_up + alpha_down * h_down)

    Args:
        features: DataFrame with trip-level features
        greybox_params: Parameters from model metadata
        battery_pack_density_override: If provided, uses actual
            battery_pack_weight_kg / battery_pack_size_kwh from bus specs
            instead of the model's fitted battery_pack_density.
        override_mass: Current row-aligned physical mass, used to select the
            active G2 regeneration branch. The derivative still uses battery
            pack density because optimization changes capacity and pack mass
            together.
    """
    required_cols = {"total_distance_m", "total_ascent_m", "total_descent_m"}
    missing = required_cols - set(features.columns)
    if missing:
        raise ValueError(f"Missing required columns for greybox sensitivity: {missing}")

    L = features["total_distance_m"].to_numpy(dtype=float)
    h_up = features["total_ascent_m"].to_numpy(dtype=float)
    h_down = features["total_descent_m"].to_numpy(dtype=float)

    alpha_roll = float(greybox_params.get("alpha_roll"))
    alpha_up = float(greybox_params.get("alpha_up"))

    if battery_pack_density_override is not None:
        rho_batt = float(battery_pack_density_override)
    else:
        rho_batt = float(greybox_params.get("battery_pack_density", 6.0))

    positive_sensitivity = rho_batt * (alpha_roll * L + alpha_up * h_up)
    if "regen_ratio" in greybox_params and "regen_c_rate" in greybox_params:
        extra_required = {
            "bus_length_m",
            "bus_battery_kwh",
            "driving_time_minutes",
            "pct_downhill_segments",
        }
        missing_extra = extra_required - set(features.columns)
        if missing_extra:
            raise ValueError(
                f"Missing required columns for G2 sensitivity: {missing_extra}"
            )
        battery = features["bus_battery_kwh"].to_numpy(dtype=float)
        downhill_hours = (
            features["driving_time_minutes"].to_numpy(dtype=float)
            / 60.0
            * np.clip(
                features["pct_downhill_segments"].to_numpy(dtype=float),
                0.0,
                100.0,
            )
            / 100.0
        )
        if override_mass is None:
            mass = (
                rho_batt * battery
                + float(greybox_params.get("k1", 0.0))
                * features["bus_length_m"].to_numpy(dtype=float)
                + float(greybox_params.get("k2", 0.0))
            )
        else:
            mass = np.asarray(override_mass, dtype=float).reshape(-1)
            if len(mass) != len(features):
                raise ValueError("override_mass must be row-aligned")
        regen_ratio = float(greybox_params["regen_ratio"])
        regen_c_rate = float(greybox_params["regen_c_rate"])
        potential = regen_ratio * alpha_up * mass * h_down
        power_cap = regen_c_rate * battery * downhill_hours
        potential_sensitivity = regen_ratio * alpha_up * rho_batt * h_down
        cap_sensitivity = regen_c_rate * downhill_hours
        recovery_sensitivity = np.where(
            potential <= power_cap,
            potential_sensitivity,
            cap_sensitivity,
        )
        sensitivity = positive_sensitivity - recovery_sensitivity
    else:
        alpha_down = float(greybox_params.get("alpha_down"))
        sensitivity = positive_sensitivity + rho_batt * alpha_down * h_down

    return sensitivity
