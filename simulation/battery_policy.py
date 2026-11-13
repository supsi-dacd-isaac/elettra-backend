"""Battery assumptions independent of the solver, database and forecast model.

Defaults here describe historical inputs. New-request defaults belong to the API.
Reference SOC bounds are never overwritten with the derived operating bounds.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from numbers import Real

ASSUMPTIONS_VERSION = "battery-timing-v1"
TOL = 1e-12
HISTORICAL_DEFAULTS = {
    "state_of_health": 1.0, "battery_soc_policy": "residual_soc",
    "min_soc": .4, "max_soc": .9, "allowed_min_soc": .1, "allowed_max_soc": .95,
    "session_connection_minutes": 0, "layover_reduction_minutes": 0,
}


def historical_battery_params(params):
    return {**params, **{key: default for key, default in HISTORICAL_DEFAULTS.items()
                        if params.get(key) is None}}


@dataclass(frozen=True)
class BatteryPolicy:
    battery_soc_policy: str
    state_of_health: float
    reference_min_soc: float
    reference_max_soc: float
    allowed_min_soc: float
    allowed_max_soc: float
    min_soc: float
    max_soc: float

    @property
    def usable_fraction_initial(self):
        return self.state_of_health * (self.max_soc - self.min_soc)

    def as_dict(self):
        return {**asdict(self), "usable_fraction_initial": self.usable_fraction_initial,
                "low_soc_warning": self.min_soc < .2 - TOL,
                "preferred_max_soc_exceeded": self.max_soc > self.reference_max_soc + TOL}

    def energy(self, nominal_kwh):
        if isinstance(nominal_kwh, bool) or not isinstance(nominal_kwh, Real) or not math.isfinite(nominal_kwh) or nominal_kwh < 0:
            raise ValueError("Nominal battery capacity must be finite and nonnegative")
        residual = nominal_kwh * self.state_of_health
        return {"nominal_capacity_kwh": nominal_kwh, "residual_capacity_kwh": residual,
                "usable_capacity_kwh": nominal_kwh * self.usable_fraction_initial,
                "min_energy_kwh": residual * self.min_soc, "max_energy_kwh": residual * self.max_soc}


def resolve_battery_policy(*, battery_soc_policy="residual_soc", state_of_health=1.,
                           min_soc=.4, max_soc=.9, allowed_min_soc=.1, allowed_max_soc=.95):
    values = (state_of_health, min_soc, max_soc, allowed_min_soc, allowed_max_soc)
    if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in values):
        raise ValueError("SOH and SOC limits must be finite numbers")
    h, a, b, low, high = map(float, values)
    if not 0 < h <= 1 or not 0 <= a < b <= 1 or not 0 <= low < high <= 1:
        raise ValueError("A positive SOH and ordered SOC limits within [0, 1] are required")
    if battery_soc_policy not in {"residual_soc", "constant_initial_usable"}:
        raise ValueError("Unknown battery SOC policy")
    minimum, maximum = a, b
    if battery_soc_policy == "constant_initial_usable":
        if a < low - TOL or b > high + TOL:
            raise ValueError("Initial SOC window must be within the allowed expansion limits")
        width = (b - a) / h
        minimum = b - width
        if minimum < low:
            minimum, maximum = low, low + width
        if maximum > high + TOL:
            raise ValueError(
                f"Requested usable energy is {(b-a)*100:.6g}% of initial capacity; "
                f"maximum available is {h*(high-low)*100:.6g}% with SOH {h*100:.6g}% "
                f"and allowed SOC {low*100:.6g}–{high*100:.6g}%")
        maximum = min(maximum, high)
    return BatteryPolicy(battery_soc_policy, h, a, b, low, high, minimum, maximum)


def policy_from_params(params):
    params = historical_battery_params(params)
    return resolve_battery_policy(**{key: params[key] for key in (
        "battery_soc_policy", "state_of_health", "min_soc", "max_soc", "allowed_min_soc", "allowed_max_soc")})


def operating_assumptions(params):
    params = historical_battery_params(params)
    return {"version": ASSUMPTIONS_VERSION, **policy_from_params(params).as_dict(),
            "session_connection_minutes": params["session_connection_minutes"],
            "layover_reduction_minutes": params["layover_reduction_minutes"],
            "timing_policy": "scheduled_occupancy_initial_no_power_buffer",
            "charging_power_policy": "configured_limits_without_soc_taper"}


def session_timing(duration, technical, reduction, active_minutes):
    """Disjoint accounting; zero power is not necessarily a technical interval."""
    technical = min(duration, technical)
    reduction = min(duration - technical, reduction)
    available = duration - technical - reduction
    return {"technical_minutes": technical, "layover_reduction_minutes": reduction,
            "available_charging_minutes": available, "active_charging_minutes": active_minutes,
            "idle_available_minutes": max(0, available - active_minutes)}
