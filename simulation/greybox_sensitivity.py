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
) -> np.ndarray:
    """
    Compute dE_mech / d(bus_battery_kwh) for each row in `features`.

    The mechanical greybox model is:

        m = rho_batt * bus_battery_kwh + k1 * bus_length_m + k2

        E_mech = alpha_roll  * m * L
               + alpha_aero  * L * v^2 * (driving_time / total_duration)
               + alpha_up    * m * h_up
               + alpha_down  * m * h_down

    Since the aero term does not depend on mass, and the mass-dependent terms
    are linear in bus_battery_kwh, the derivative w.r.t. bus_battery_kwh is:

        dE_mech / d(bus_battery_kwh)
            = rho_batt * (alpha_roll * L + alpha_up * h_up + alpha_down * h_down)

    Args:
        features: DataFrame with trip-level features
        greybox_params: Parameters from model metadata
        battery_pack_density_override: If provided, uses actual
            battery_pack_weight_kg / battery_pack_size_kwh from bus specs
            instead of the model's fitted battery_pack_density.
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
    alpha_down = float(greybox_params.get("alpha_down"))

    if battery_pack_density_override is not None:
        rho_batt = float(battery_pack_density_override)
    else:
        rho_batt = float(greybox_params.get("battery_pack_density", 6.0))

    sensitivity = rho_batt * (
        alpha_roll * L
        + alpha_up * h_up
        + alpha_down * h_down
    )

    return sensitivity


