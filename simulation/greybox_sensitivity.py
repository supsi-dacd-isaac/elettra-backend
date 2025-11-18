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
) -> np.ndarray:
    """
    Compute dE_mech / d(bus_battery_kwh) for each row in `features`.

    The mechanical greybox model is:

        m = rho_batt * bus_battery_kwh + k1 * bus_length_m + k2

        E_mech = alpha_roll  * m * L
               + alpha_aero  * L * v^2
               + alpha_up    * m * h_up
               + alpha_down  * m * h_down

    where:
        L      = total_distance_m
        v      = driving_average_speed_kmh / 3.6  [m/s]
        h_up   = total_ascent_m
        h_down = total_descent_m

    Since E_mech is linear in bus_battery_kwh, its derivative w.r.t.
    bus_battery_kwh is:

        dE_mech / d(bus_battery_kwh)
            = rho_batt * (alpha_roll * L + alpha_up * h_up + alpha_down * h_down)

    This routine implements exactly that expression using the parameters
    stored in model metadata (`greybox_params`).
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

    # Prefer explicit battery_pack_density from greybox_params, fall back to 6.0
    rho_batt = float(greybox_params.get("battery_pack_density", 6.0))

    sensitivity = rho_batt * (
        alpha_roll * L
        + alpha_up * h_up
        + alpha_down * h_down
    )

    return sensitivity


