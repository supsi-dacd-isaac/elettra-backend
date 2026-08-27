"""
VECTO Bus Auxiliary SSM (Simplified Steady-state Model).

Re-implemented from the VECTO 5.1.3 source code (EUPL licensed):
  https://code.europa.eu/vecto/vecto/-/archive/Release/v5.1.3/

Provides a physics-based estimate of HVAC electrical and fuel auxiliary
consumption for electric buses as a function of ambient temperature, solar
irradiance, passenger load, and heating system configuration.

Usage:
    from app.services.vecto_ssm import vecto_auxiliary_power

    result = vecto_auxiliary_power(
        temperature_celsius=-5.0,
        bus_length_m=18.0,
        solar_irradiance_wm2=100,
        n_passengers=80,
        diesel_heater=True,
    )
    print(result.p_electrical_kw)  # electric from battery
    print(result.p_fuel_kw)        # diesel heater fuel power
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Constants from VectoCore/Configuration/Constants.cs
# ---------------------------------------------------------------------------
_HEATING_BOUNDARY_TEMP = 18.0
_COOLING_BOUNDARY_TEMP = 23.0
_MAX_DELTA_LOW_FLOOR = 4.0
_PASSENGER_BOUNDARY_TEMP = 17.0

_SOLAR_CLOUDING_LOW = 0.65
_SOLAR_CLOUDING_HIGH = 0.80
_HEAT_PER_PASSENGER_LOW = 50.0   # W
_HEAT_PER_PASSENGER_HIGH = 80.0  # W
_GFACTOR = 0.95
_SOLAR_OCCUPANCY = 0.25

_UVALUE = {"LowFloor": 4.0, "SemiLowFloor": 3.5, "HighFloor": 3.0}

_WINDOW_HEIGHT_SINGLE = 1.5       # m
_WINDOW_HEIGHT_DOUBLE = 2.5       # m
_FRONT_REAR_WINDOW_SINGLE = 5.0   # m²
_FRONT_REAR_WINDOW_DOUBLE = 8.0   # m²
_DRIVER_COMPARTMENT_LENGTH = 1.2  # m

_VENT_RATE_COOLING = 20.0   # 1/h
_VENT_RATE_HEATING = 10.0   # 1/h
_SPEC_VENT_POWER = 0.56     # Wh/m³

_FUEL_HEATER_CAPACITY = 30_000  # W

# ---------------------------------------------------------------------------
# Environmental Conditions Map (DefaultClimatic.aenv from VectoCore.dll)
# ---------------------------------------------------------------------------
_ENV_CONDITIONS_MAP = [
    {"id": 1,  "temp": -20.0, "solar": 10,  "weight": 0.0053, "mode": "heating",
     "COP": {"R744": 1.80, "2stage": None, "3stage": None, "4stage": None, "cont": None},
     "heater_eff": {"elec": 0.93, "fuel": 0.80}},
    {"id": 2,  "temp": -5.0,  "solar": 30,  "weight": 0.0826, "mode": "heating",
     "COP": {"R744": 2.04, "2stage": 1.54, "3stage": 1.64, "4stage": 1.68, "cont": 1.78},
     "heater_eff": {"elec": 0.93, "fuel": 0.80}},
    {"id": 3,  "temp": 2.0,   "solar": 30,  "weight": 0.0826, "mode": "heating",
     "COP": {"R744": 2.50, "2stage": 2.00, "3stage": 2.10, "4stage": 2.10, "cont": 2.22},
     "heater_eff": {"elec": 0.93, "fuel": 0.80}},
    {"id": 4,  "temp": 8.0,   "solar": 20,  "weight": 0.1661, "mode": "heating",
     "COP": {"R744": 2.98, "2stage": 2.70, "3stage": 2.80, "4stage": 2.82, "cont": 2.94},
     "heater_eff": {"elec": 0.93, "fuel": 0.80}},
    {"id": 5,  "temp": 8.0,   "solar": 155, "weight": 0.0826, "mode": "heating",
     "COP": {"R744": 2.98, "2stage": 2.70, "3stage": 2.80, "4stage": 2.82, "cont": 2.94},
     "heater_eff": {"elec": 0.93, "fuel": 0.80}},
    {"id": 6,  "temp": 14.0,  "solar": 30,  "weight": 0.0826, "mode": "heating",
     "COP": {"R744": 3.38, "2stage": 3.24, "3stage": 3.34, "4stage": 3.36, "cont": 3.50},
     "heater_eff": {"elec": 0.93, "fuel": 0.80}},
    {"id": 7,  "temp": 14.0,  "solar": 175, "weight": 0.1243, "mode": "heating",
     "COP": {"R744": 3.38, "2stage": 3.24, "3stage": 3.34, "4stage": 3.36, "cont": 3.50},
     "heater_eff": {"elec": 0.93, "fuel": 0.80}},
    {"id": 8,  "temp": 20.5,  "solar": 30,  "weight": 0.1243, "mode": "cooling",
     "COP": {"R744": 3.80, "2stage": 3.62, "3stage": 3.74, "4stage": 3.74, "cont": 3.88},
     "heater_eff": {}},
    {"id": 9,  "temp": 20.5,  "solar": 200, "weight": 0.1243, "mode": "cooling",
     "COP": {"R744": 3.80, "2stage": 3.62, "3stage": 3.74, "4stage": 3.74, "cont": 3.88},
     "heater_eff": {}},
    {"id": 10, "temp": 26.0,  "solar": 150, "weight": 0.0826, "mode": "cooling",
     "COP": {"R744": 2.82, "2stage": 3.12, "3stage": 3.22, "4stage": 3.24, "cont": 3.36},
     "heater_eff": {}},
    {"id": 11, "temp": 33.0,  "solar": 150, "weight": 0.0427, "mode": "cooling",
     "COP": {"R744": 2.14, "2stage": 2.50, "3stage": 2.60, "4stage": 2.62, "cont": 2.74},
     "heater_eff": {}},
]

# HeatingDistribution cases (from HeatingDistribution.csv)
_HEATING_DISTRIBUTIONS = {
    "HD1": {i: (1.0, 0.0) for i in range(1, 12)},
    "HD2": {1: (0.4, 0.6), 2: (0.7, 0.3), 3: (0.8, 0.2), **{i: (1.0, 0.0) for i in range(4, 12)}},
    "HD4": {1: (0.4, 0.6), 2: (0.7, 0.3), 3: (0.8, 0.2), **{i: (1.0, 0.0) for i in range(4, 12)}},
    "HD7": {1: (0.0, 1.0), 2: (0.4, 0.6), 3: (0.6, 0.4), **{i: (1.0, 0.0) for i in range(4, 12)}},
    "HD9": {i: (0.0, 0.0) for i in range(1, 12)},
    "HD11": {i: (0.0, 1.0) for i in range(1, 12)},
}

# Non-HVAC electrical baseline by bus length (derived from VECTO .vsum outputs)
_NON_HVAC_BASELINE_KW = [
    (11.0, 2.2),
    (13.0, 2.5),
    (float("inf"), 3.1),
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BusGeometry:
    """Bus physical parameters derived from vehicle dimensions."""

    length: float
    width: float = 2.55
    body_height: float = 3.2
    internal_height: float = 2.3
    floor_type: str = "LowFloor"
    double_decker: bool = False
    n_passengers: int = 60

    @property
    def internal_length(self) -> float:
        return self.length - _DRIVER_COMPARTMENT_LENGTH

    @property
    def surface_area(self) -> float:
        dd_factor = 2.0 if self.double_decker else 1.0
        return 2 * (self.length * self.width +
                    self.internal_length * self.internal_height +
                    dd_factor * self.width * self.body_height)

    @property
    def window_surface(self) -> float:
        wh = _WINDOW_HEIGHT_DOUBLE if self.double_decker else _WINDOW_HEIGHT_SINGLE
        frw = _FRONT_REAR_WINDOW_DOUBLE if self.double_decker else _FRONT_REAR_WINDOW_SINGLE
        return wh * self.internal_length + frw

    @property
    def volume(self) -> float:
        return self.internal_length * self.width * self.internal_height

    @property
    def u_value(self) -> float:
        return _UVALUE[self.floor_type]

    @property
    def max_cooling_power(self) -> float:
        return 250.0 * self.volume

    @property
    def max_heating_power(self) -> float:
        return 250.0 * self.volume

    @property
    def vent_power_cooling(self) -> float:
        return self.volume * _VENT_RATE_COOLING * _SPEC_VENT_POWER

    @property
    def vent_power_heating(self) -> float:
        return self.volume * _VENT_RATE_HEATING * _SPEC_VENT_POWER


@dataclass
class VectoAuxResult:
    """Result of VECTO auxiliary power estimation."""

    p_electrical_kw: float
    p_fuel_kw: float
    p_hvac_electrical_kw: float
    p_baseline_kw: float
    mode: str


# ---------------------------------------------------------------------------
# Core SSM functions
# ---------------------------------------------------------------------------

def _thermal_balance(t_ext: float, solar: float, t_calc: float,
                     bus: BusGeometry) -> float:
    """Thermal balance (SSMRun.TotalW). Positive = cooling, negative = heating."""
    q_wall = (t_ext - t_calc) * bus.surface_area * bus.u_value

    hpp = _HEAT_PER_PASSENGER_LOW if t_ext < _PASSENGER_BOUNDARY_TEMP else _HEAT_PER_PASSENGER_HIGH
    q_passengers = bus.n_passengers * hpp

    sc = _SOLAR_CLOUDING_LOW if t_ext < _PASSENGER_BOUNDARY_TEMP else _SOLAR_CLOUDING_HIGH
    q_solar = solar * bus.window_surface * _GFACTOR * sc * _SOLAR_OCCUPANCY

    return q_wall + q_passengers + q_solar


def _interpolate_cop(t_ext: float, tech: str) -> Optional[float]:
    """Interpolate COP from the environmental conditions map."""
    temps = [(ec["temp"], ec["COP"].get(tech)) for ec in _ENV_CONDITIONS_MAP
             if ec["COP"].get(tech) is not None]
    if not temps:
        return None

    temps.sort(key=lambda x: x[0])
    if t_ext <= temps[0][0]:
        return temps[0][1]
    if t_ext >= temps[-1][0]:
        return temps[-1][1]

    for i in range(len(temps) - 1):
        t0, c0 = temps[i]
        t1, c1 = temps[i + 1]
        if t0 <= t_ext <= t1:
            frac = (t_ext - t0) / (t1 - t0) if t1 != t0 else 0
            return c0 + (c1 - c0) * frac
    return temps[-1][1]


def _ssm_calculate(t_ext: float, solar: float, bus: BusGeometry,
                   hp_tech: str, heating_dist: str) -> tuple[float, float, str]:
    """
    Core SSM calculation for electric bus (no engine waste heat).

    Returns (p_electrical_W, p_fuel_W, mode).
    """
    r1 = _thermal_balance(t_ext, solar, _HEATING_BOUNDARY_TEMP, bus)
    t_calc_r2 = (max(_COOLING_BOUNDARY_TEMP, t_ext - _MAX_DELTA_LOW_FLOOR)
                 if bus.floor_type == "LowFloor" else _COOLING_BOUNDARY_TEMP)
    r2 = _thermal_balance(t_ext, solar, t_calc_r2, bus)

    cop = _interpolate_cop(t_ext, hp_tech)
    p_el = 0.0
    p_fuel = 0.0
    p_vent = 0.0

    if r1 > 0 and r2 > 0:
        mode = "cooling"
        q_demand = min(min(r1, r2), bus.max_cooling_power)
        if cop:
            p_el = q_demand / cop
        p_vent = bus.vent_power_cooling

    elif r1 < 0 and r2 < 0:
        mode = "heating"
        q_demand = abs(max(r1, r2))

        dist_map = _HEATING_DISTRIBUTIONS.get(heating_dist, _HEATING_DISTRIBUTIONS["HD7"])
        nearest_env = min(_ENV_CONDITIONS_MAP, key=lambda e: abs(e["temp"] - t_ext))
        hp_frac, fuel_frac = dist_map.get(nearest_env["id"], (1.0, 0.0))

        hp_demand = q_demand * hp_frac
        fuel_demand = q_demand * fuel_frac

        if cop and hp_demand > 0:
            p_el = hp_demand / cop
        if fuel_demand > 0:
            fuel_eff = nearest_env.get("heater_eff", {}).get("fuel", 0.80)
            if fuel_eff:
                p_fuel = min(fuel_demand, _FUEL_HEATER_CAPACITY) / fuel_eff

        p_vent = bus.vent_power_heating
    else:
        mode = "ventilation"
        p_vent = bus.vent_power_cooling

    return (p_el + p_vent, p_fuel, mode)


# ---------------------------------------------------------------------------
# Convenience defaults
# ---------------------------------------------------------------------------

def _default_passengers(bus_length_m: float) -> int:
    if bus_length_m <= 11.0:
        return 50
    if bus_length_m <= 13.0:
        return 60
    return 100


def _default_baseline(bus_length_m: float) -> float:
    for threshold, value in _NON_HVAC_BASELINE_KW:
        if bus_length_m <= threshold:
            return value
    return 3.1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def vecto_auxiliary_power(
    temperature_celsius: float,
    bus_length_m: float,
    solar_irradiance_wm2: float = 100.0,
    n_passengers: Optional[int] = None,
    diesel_heater: bool = True,
    hp_technology: str = "2stage",
    non_hvac_baseline_kw: Optional[float] = None,
) -> VectoAuxResult:
    """
    Estimate auxiliary electrical and fuel power using the VECTO SSM model.

    Args:
        temperature_celsius: Ambient temperature [°C].
        bus_length_m: Total bus length [m]. Used to derive geometry defaults.
        solar_irradiance_wm2: Global horizontal irradiance [W/m²]. Default 100.
        n_passengers: Number of passengers on board. None uses size-based default.
        diesel_heater: If True, uses HD2 heating distribution (heat pump + diesel
            auxiliary heater). If False, uses HD1 (heat pump only).
        hp_technology: Heat pump refrigerant technology. One of
            "R744", "2stage", "3stage", "4stage", "cont".
        non_hvac_baseline_kw: Constant non-HVAC electric baseline [kW].
            None derives from bus length.

    Returns:
        VectoAuxResult with electrical, fuel, and breakdown values.
    """
    if n_passengers is None:
        n_passengers = _default_passengers(bus_length_m)
    if non_hvac_baseline_kw is None:
        non_hvac_baseline_kw = _default_baseline(bus_length_m)

    bus = BusGeometry(length=bus_length_m, n_passengers=n_passengers)
    heating_dist = "HD2" if diesel_heater else "HD1"

    p_hvac_el_w, p_fuel_w, mode = _ssm_calculate(
        temperature_celsius, solar_irradiance_wm2, bus, hp_technology, heating_dist
    )

    p_hvac_el_kw = p_hvac_el_w / 1000.0
    p_fuel_kw = p_fuel_w / 1000.0

    return VectoAuxResult(
        p_electrical_kw=round(p_hvac_el_kw + non_hvac_baseline_kw, 3),
        p_fuel_kw=round(p_fuel_kw, 3),
        p_hvac_electrical_kw=round(p_hvac_el_kw, 3),
        p_baseline_kw=round(non_hvac_baseline_kw, 3),
        mode=mode,
    )
