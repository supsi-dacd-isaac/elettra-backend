"""Shared economic defaults and electric bus body pricing."""

import json
from bisect import bisect_left
from functools import lru_cache
from pathlib import Path

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "economic_defaults.json"
_MAX_PRICED_BUS_LENGTH_M = 19.0


@lru_cache(maxsize=1)
def load_economic_defaults() -> dict:
    with _CONFIG_PATH.open() as file:
        return json.load(file)


def electric_bus_body_cost(
    length_m: float,
    quad_coeff: float | None = None,
    lin_coeff: float | None = None,
    const_coeff: float | None = None,
) -> float:
    """Estimate the body cost, honoring explicit legacy coefficient overrides."""
    defaults = load_economic_defaults()
    if quad_coeff is not None or lin_coeff is not None or const_coeff is not None:
        a = quad_coeff if quad_coeff is not None else defaults["bus_no_batt_quad_coeff"]
        b = lin_coeff if lin_coeff is not None else defaults["bus_no_batt_lin_coeff"]
        c = const_coeff if const_coeff is not None else defaults["bus_no_batt_const"]
        return a * length_m ** 2 + b * length_m + c

    points = defaults["bus_body_price_points"]
    priced_length_m = min(max(length_m, points[0]["length_m"]), _MAX_PRICED_BUS_LENGTH_M)
    upper_index = bisect_left(points, priced_length_m, key=lambda point: point["length_m"])
    upper_index = min(max(upper_index, 1), len(points) - 1)
    lower, upper = points[upper_index - 1], points[upper_index]
    fraction = (priced_length_m - lower["length_m"]) / (upper["length_m"] - lower["length_m"])
    return lower["cost_chf"] + fraction * (upper["cost_chf"] - lower["cost_chf"])
