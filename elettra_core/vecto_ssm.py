# SPDX-FileCopyrightText: 2012-2022 European Commission, DG_CLIMA
# SPDX-FileCopyrightText: 2026 SUPSI-DACD-ISAAC
# SPDX-FileContributor: 2026 SUPSI-DACD-ISAAC
# SPDX-License-Identifier: EUPL-1.2
"""Faithful VECTO 5.1.3 bus-HVAC steady-state model (SSM).

This is a behavioural transcription of ``SSMRun`` and ``SSMCalculate`` from
VECTO 5.1.3, commit ``cef1f3d260afa7f7c6ec09981d821e545d21b249``.

VECTO does not derive a complete SSM declaration from ambient temperature and
bus length. Geometry, HVAC layout, capacities, technology, COP and heater
efficiencies are inputs. They are therefore explicit here: no COP interpolation,
passenger/geometry heuristic, diesel-heater assumption or non-HVAC baseline is
hidden in this module.

Source files mirrored here (EUPL-1.2): ``SSMRun.cs``, ``SSMCalculate.cs``,
``SSMInputs.cs``, ``DefaultClimatic.aenv`` and ``HeatingDistribution*.csv``.

Modification notice: SUPSI-DACD-ISAAC created this Python behavioural
transcription on 2026-08-31.  It translates the upstream C# equations and
climatic tables, adds Python input validation and typed result objects, and
supports one explicit environmental condition per call.  The upstream C#
files are not embedded in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Literal, Mapping

FloorType = Literal["LowFloor", "SemiLowFloor", "HighFloor"]
HeatPumpType = Literal["none", "R744", "2stage", "3stage", "4stage", "continuous"]
ElectricHeaterType = Literal["water", "air", "other"]

_HEATING_BOUNDARY_TEMP_C = 18.0
_COOLING_BOUNDARY_TEMP_C = 23.0
_COOLING_TURNS_OFF_TEMP_C = 17.0
_PASSENGER_BOUNDARY_TEMP_C = 17.0
_MAX_DELTA_LOW_FLOOR_K = 3.0
_SOLAR_CLOUDING_LOW = 0.65
_SOLAR_CLOUDING_HIGH = 0.80
_HEAT_PER_PASSENGER_LOW_W = 50.0
_HEAT_PER_PASSENGER_HIGH_W = 80.0
_SOLAR_OCCUPANCY_FACTOR = 0.25
_G_FACTOR = 0.95
_MAX_TECHNOLOGY_BENEFIT = 0.5

_VALID_FLOOR_TYPES = {"LowFloor", "SemiLowFloor", "HighFloor"}
_VALID_HEAT_PUMPS = {"none", "R744", "2stage", "3stage", "4stage", "continuous"}
_VALID_ELECTRIC_HEATERS = {"water", "air", "other"}


def _require_finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


def _require_non_negative(name: str, value: float) -> None:
    _require_finite(name, value)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _require_positive(name: str, value: float) -> None:
    _require_finite(name, value)
    if value <= 0:
        raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class VectoEnvironmentalCondition:
    """One VECTO environmental-map entry.

    The ID selects a row in ``HeatingDistribution.csv``. VECTO does not infer
    COP or heater efficiency from temperature.
    """

    environmental_id: int
    temperature_celsius: float
    solar_irradiance_wm2: float
    heat_pump_cop: Mapping[HeatPumpType, float] = field(default_factory=dict)
    heater_efficiency: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.environmental_id not in range(1, 12):
            raise ValueError("environmental_id must be in VECTO's range 1..11")
        _require_finite("temperature_celsius", self.temperature_celsius)
        _require_non_negative("solar_irradiance_wm2", self.solar_irradiance_wm2)
        for technology, cop in self.heat_pump_cop.items():
            if technology not in _VALID_HEAT_PUMPS - {"none"}:
                raise ValueError(f"unsupported heat-pump technology: {technology!r}")
            _require_positive(f"heat_pump_cop[{technology!r}]", cop)
        for heater, efficiency in self.heater_efficiency.items():
            if heater not in _VALID_ELECTRIC_HEATERS | {"fuel"}:
                raise ValueError(f"unsupported heater type: {heater!r}")
            _require_positive(f"heater_efficiency[{heater!r}]", efficiency)


@dataclass(frozen=True)
class VectoComfortPolicy:
    """Caller-supplied cabin-temperature policy for controlled scenarios.

    The default values are the constants used by VECTO 5.1.3.  Fleet-specific
    training code may resolve a documented setpoint curve for one ambient
    condition and pass the resulting numeric policy here.  The policy is
    deliberately generic: manufacturer names, private curves and lookup logic
    do not belong in the shared runtime package.

    ``heating_enabled`` and ``cooling_enabled`` model an explicit operating
    interval selected by the caller.  They never default from ambient weather.
    """

    heating_calculation_temperature_c: float = _HEATING_BOUNDARY_TEMP_C
    cooling_calculation_temperature_c: float = _COOLING_BOUNDARY_TEMP_C
    cooling_activation_temperature_c: float = _COOLING_TURNS_OFF_TEMP_C
    low_floor_max_temperature_delta_k: float = _MAX_DELTA_LOW_FLOOR_K
    heating_enabled: bool = True
    cooling_enabled: bool = True

    def __post_init__(self) -> None:
        for name in (
            "heating_calculation_temperature_c",
            "cooling_calculation_temperature_c",
            "cooling_activation_temperature_c",
            "low_floor_max_temperature_delta_k",
        ):
            _require_finite(name, getattr(self, name))
        if self.low_floor_max_temperature_delta_k < 0:
            raise ValueError(
                "low_floor_max_temperature_delta_k must be non-negative"
            )
        if not isinstance(self.heating_enabled, bool):
            raise ValueError("heating_enabled must be boolean")
        if not isinstance(self.cooling_enabled, bool):
            raise ValueError("cooling_enabled must be boolean")


VECTO_DEFAULT_COMFORT_POLICY = VectoComfortPolicy()


@dataclass(frozen=True)
class VectoSsmInputs:
    """Explicit declaration inputs consumed by VECTO's HVAC SSM."""

    number_of_passengers: float
    floor_type: FloorType
    surface_area_m2: float
    window_surface_m2: float
    volume_m3: float
    u_value_w_per_k_m2: float
    hvac_configuration: int
    driver_heat_pump: HeatPumpType
    passenger_heat_pump: HeatPumpType
    electric_heaters: tuple[ElectricHeaterType, ...]
    driver_compartment_length_m: float
    passenger_compartment_length_m: float
    max_cooling_power_driver_w: float
    max_cooling_power_passenger_w: float
    max_heating_power_driver_w: float
    max_heating_power_passenger_w: float
    fuel_heater_capacity_w: float
    ventilation_rate_per_hour: float
    ventilation_rate_heating_per_hour: float
    specific_ventilation_power_wh_per_m3: float
    ventilation_on_during_heating: bool = True
    ventilation_during_cooling: bool = True
    ventilation_when_inactive: bool = True
    engine_waste_heat_w: float = 0.0
    heating_variation: float = 0.0
    heating_ventilation_variation: float = 0.0
    inactive_ventilation_variation: float = 0.0
    cooling_ventilation_variation: float = 0.0
    cooling_variation: float = 0.0

    def __post_init__(self) -> None:
        if self.floor_type not in _VALID_FLOOR_TYPES:
            raise ValueError(f"unsupported floor_type: {self.floor_type!r}")
        if self.hvac_configuration not in range(1, 11):
            raise ValueError("hvac_configuration must be in VECTO's range 1..10")
        if self.driver_heat_pump not in _VALID_HEAT_PUMPS:
            raise ValueError(f"unsupported driver_heat_pump: {self.driver_heat_pump!r}")
        if self.passenger_heat_pump not in _VALID_HEAT_PUMPS:
            raise ValueError(f"unsupported passenger_heat_pump: {self.passenger_heat_pump!r}")
        if len(set(self.electric_heaters)) != len(self.electric_heaters):
            raise ValueError("electric_heaters must not contain duplicates")
        invalid_heaters = set(self.electric_heaters) - _VALID_ELECTRIC_HEATERS
        if invalid_heaters:
            raise ValueError(f"unsupported electric heater(s): {sorted(invalid_heaters)!r}")
        for name in (
            "number_of_passengers", "surface_area_m2", "window_surface_m2", "volume_m3",
            "u_value_w_per_k_m2", "driver_compartment_length_m",
            "passenger_compartment_length_m", "max_cooling_power_driver_w",
            "max_cooling_power_passenger_w", "max_heating_power_driver_w",
            "max_heating_power_passenger_w", "fuel_heater_capacity_w",
            "ventilation_rate_per_hour", "ventilation_rate_heating_per_hour",
            "specific_ventilation_power_wh_per_m3", "engine_waste_heat_w",
        ):
            _require_non_negative(name, getattr(self, name))
        if self.surface_area_m2 == 0 or self.volume_m3 == 0 or self.u_value_w_per_k_m2 == 0:
            raise ValueError("surface_area_m2, volume_m3 and u_value_w_per_k_m2 must be positive")
        if self.driver_compartment_length_m + self.passenger_compartment_length_m <= 0:
            raise ValueError("at least one HVAC compartment length must be positive")
        for name in (
            "heating_variation", "heating_ventilation_variation",
            "inactive_ventilation_variation", "cooling_ventilation_variation",
            "cooling_variation",
        ):
            _require_finite(name, getattr(self, name))


@dataclass(frozen=True)
class VectoAuxResult:
    """SSM result; powers are positive demands in kW."""

    p_electrical_kw: float
    p_fuel_kw: float
    p_hvac_electrical_kw: float
    p_hvac_mechanical_kw: float
    p_baseline_kw: float
    p_heating_demand_kw: float
    p_heating_delivered_kw: float
    unmet_thermal_demand_kw: float
    mode: Literal["heating", "cooling", "ventilation", "off"]


@dataclass(frozen=True)
class _SsmBreakdown:
    electrical_cooling_and_ventilation_w: float
    mechanical_cooling_w: float
    required_heating_power_w: float
    electrical_heat_pump_w: float
    mechanical_heat_pump_w: float
    electric_heater_w: float
    fuel_heater_w: float
    delivered_heating_power_w: float
    unmet_heating_power_w: float
    mode: Literal["heating", "cooling", "ventilation", "off"]


# Exact contents of VECTO 5.1.3 DefaultClimatic.aenv. Missing cells are omitted.
_HEATING_EFFICIENCIES = {
    "water": 0.93,
    "air": 0.93,
    "other": 0.93,
    "fuel": 0.80,
}
_DEFAULT_CLIMATIC_ROWS: dict[int, tuple[float, float, dict[str, float], dict[str, float]]] = {
    1: (-20.0, 10.0, {"R744": 1.80}, _HEATING_EFFICIENCIES),
    2: (
        -5.0,
        30.0,
        {"R744": 2.04, "2stage": 1.54, "3stage": 1.64, "4stage": 1.68, "continuous": 1.78},
        _HEATING_EFFICIENCIES,
    ),
    3: (
        2.0,
        30.0,
        {"R744": 2.50, "2stage": 2.00, "3stage": 2.10, "4stage": 2.10, "continuous": 2.22},
        _HEATING_EFFICIENCIES,
    ),
    4: (
        8.0,
        20.0,
        {"R744": 2.98, "2stage": 2.70, "3stage": 2.80, "4stage": 2.82, "continuous": 2.94},
        _HEATING_EFFICIENCIES,
    ),
    5: (
        8.0,
        155.0,
        {"R744": 2.98, "2stage": 2.70, "3stage": 2.80, "4stage": 2.82, "continuous": 2.94},
        _HEATING_EFFICIENCIES,
    ),
    6: (
        14.0,
        30.0,
        {"R744": 3.38, "2stage": 3.24, "3stage": 3.34, "4stage": 3.36, "continuous": 3.50},
        _HEATING_EFFICIENCIES,
    ),
    7: (
        14.0,
        175.0,
        {"R744": 3.38, "2stage": 3.24, "3stage": 3.34, "4stage": 3.36, "continuous": 3.50},
        _HEATING_EFFICIENCIES,
    ),
    8: (
        20.5,
        30.0,
        {"R744": 3.80, "2stage": 3.62, "3stage": 3.74, "4stage": 3.74, "continuous": 3.88},
        {},
    ),
    9: (
        20.5,
        200.0,
        {"R744": 3.80, "2stage": 3.62, "3stage": 3.74, "4stage": 3.74, "continuous": 3.88},
        {},
    ),
    10: (
        26.0,
        150.0,
        {"R744": 2.82, "2stage": 3.12, "3stage": 3.22, "4stage": 3.24, "continuous": 3.36},
        {},
    ),
    11: (
        33.0,
        150.0,
        {"R744": 2.14, "2stage": 2.50, "3stage": 2.60, "4stage": 2.62, "continuous": 2.74},
        {},
    ),
}


def default_environmental_condition(environmental_id: int) -> VectoEnvironmentalCondition:
    """Return one exact row from VECTO 5.1.3 ``DefaultClimatic.aenv``."""
    try:
        temperature, solar, cop, efficiencies = _DEFAULT_CLIMATIC_ROWS[environmental_id]
    except KeyError as exc:
        raise ValueError("environmental_id must be in VECTO's range 1..11") from exc
    return VectoEnvironmentalCondition(
        environmental_id,
        temperature,
        solar,
        dict(cop),
        dict(efficiencies),
    )


def _limit_technology_variation(value: float) -> float:
    return min(max(value, -_MAX_TECHNOLOGY_BENEFIT), _MAX_TECHNOLOGY_BENEFIT)


def _driver_required(configuration: int) -> bool:
    return configuration in {2, 4, 7, 9}


def _passenger_required(configuration: int) -> bool:
    return configuration not in {1, 2, 3, 4}


def _is_electrical_heat_pump(heat_pump: HeatPumpType) -> bool:
    return heat_pump in {"R744", "continuous"}


def _is_mechanical_heat_pump(heat_pump: HeatPumpType) -> bool:
    # VECTO implements this as !IsElectrical(), including ``none``.
    return not _is_electrical_heat_pump(heat_pump)


def _thermal_balance_w(
    environment_temperature_c: float,
    solar_wm2: float,
    calculation_temperature_c: float,
    inputs: VectoSsmInputs,
) -> float:
    q_wall = (
        (environment_temperature_c - calculation_temperature_c)
        * inputs.surface_area_m2
        * inputs.u_value_w_per_k_m2
    )
    heat_per_passenger = (
        _HEAT_PER_PASSENGER_LOW_W
        if environment_temperature_c < _PASSENGER_BOUNDARY_TEMP_C
        else _HEAT_PER_PASSENGER_HIGH_W
    )
    q_passengers = inputs.number_of_passengers * heat_per_passenger
    clouding = (
        _SOLAR_CLOUDING_LOW
        if environment_temperature_c < _PASSENGER_BOUNDARY_TEMP_C
        else _SOLAR_CLOUDING_HIGH
    )
    q_solar = (
        solar_wm2
        * inputs.window_surface_m2
        * _G_FACTOR
        * clouding
        * _SOLAR_OCCUPANCY_FACTOR
    )
    return q_wall + q_passengers + q_solar


def _run_totals(
    environment: VectoEnvironmentalCondition,
    inputs: VectoSsmInputs,
    comfort_policy: VectoComfortPolicy,
) -> tuple[float, float]:
    run1 = _thermal_balance_w(
        environment.temperature_celsius,
        environment.solar_irradiance_wm2,
        comfort_policy.heating_calculation_temperature_c,
        inputs,
    )
    run2_temperature = (
        max(
            comfort_policy.cooling_calculation_temperature_c,
            environment.temperature_celsius
            - comfort_policy.low_floor_max_temperature_delta_k,
        )
        if inputs.floor_type == "LowFloor"
        else comfort_policy.cooling_calculation_temperature_c
    )
    run2 = _thermal_balance_w(
        environment.temperature_celsius,
        environment.solar_irradiance_wm2,
        run2_temperature,
        inputs,
    )
    return run1, run2


def _vent_power_w(inputs: VectoSsmInputs, *, heating: bool) -> float:
    rate = (
        inputs.ventilation_rate_heating_per_hour
        if heating
        else inputs.ventilation_rate_per_hour
    )
    # VECTO stores 1/s and J/m³; 3600 cancels in the resource units below.
    return inputs.volume_m3 * rate * inputs.specific_ventilation_power_wh_per_m3


def _cooling_cop(
    environment: VectoEnvironmentalCondition,
    inputs: VectoSsmInputs,
) -> float | None:
    if _driver_required(inputs.hvac_configuration) and _passenger_required(
        inputs.hvac_configuration
    ):
        driver_cop = environment.heat_pump_cop.get(inputs.driver_heat_pump)
        passenger_cop = environment.heat_pump_cop.get(inputs.passenger_heat_pump)
        if driver_cop is None or passenger_cop is None:
            return None
        denominator = (
            inputs.max_cooling_power_driver_w
            + inputs.max_cooling_power_passenger_w
        )
        if denominator == 0:
            return None
        return (
            inputs.max_cooling_power_driver_w * driver_cop
            + inputs.max_cooling_power_passenger_w * passenger_cop
        ) / denominator
    if _passenger_required(inputs.hvac_configuration):
        return environment.heat_pump_cop.get(inputs.passenger_heat_pump)
    if _driver_required(inputs.hvac_configuration):
        return environment.heat_pump_cop.get(inputs.driver_heat_pump)
    return None


def _compartment_contributions(inputs: VectoSsmInputs) -> tuple[float, float]:
    total = inputs.driver_compartment_length_m + inputs.passenger_compartment_length_m
    if total == 0:
        return 0.0, 0.0
    return (
        inputs.driver_compartment_length_m / total,
        inputs.passenger_compartment_length_m / total,
    )


def _base_cooling_loads_w(
    environment: VectoEnvironmentalCondition,
    inputs: VectoSsmInputs,
    run1: float,
    run2: float,
    comfort_policy: VectoComfortPolicy,
) -> tuple[float, float]:
    if (
        not comfort_policy.cooling_enabled
        or environment.temperature_celsius
        < comfort_policy.cooling_activation_temperature_c
        or run1 <= 0
        or run2 <= 0
    ):
        return 0.0, 0.0
    cooling = min(run1, run2)
    driver_fraction, passenger_fraction = _compartment_contributions(inputs)
    driver = cooling * driver_fraction
    passenger = cooling * passenger_fraction
    electrical = (
        driver if _is_electrical_heat_pump(inputs.driver_heat_pump) else 0.0
    ) + (
        passenger if _is_electrical_heat_pump(inputs.passenger_heat_pump) else 0.0
    )
    mechanical = (
        driver if _is_mechanical_heat_pump(inputs.driver_heat_pump) else 0.0
    ) + (
        passenger if _is_mechanical_heat_pump(inputs.passenger_heat_pump) else 0.0
    )
    return electrical, mechanical


def _base_ventilation_loads_w(
    environment: VectoEnvironmentalCondition,
    inputs: VectoSsmInputs,
    run1: float,
    run2: float,
    comfort_policy: VectoComfortPolicy,
) -> tuple[float, float, float]:
    heating = (
        _vent_power_w(inputs, heating=True)
        if comfort_policy.heating_enabled
        and run1 < 0
        and run2 < 0
        and inputs.ventilation_on_during_heating
        else 0.0
    )
    cooling = (
        _vent_power_w(inputs, heating=False)
        if comfort_policy.cooling_enabled
        and environment.temperature_celsius
        >= comfort_policy.cooling_activation_temperature_c
        and run1 > 0
        and run2 > 0
        and inputs.ventilation_during_cooling
        else 0.0
    )
    inactive_condition = (
        (
            environment.temperature_celsius
            < comfort_policy.cooling_activation_temperature_c
            and run1 > 0
            and run2 > 0
        )
        or (run1 > 0 and run2 < 0)
    )
    inactive = (
        _vent_power_w(inputs, heating=False)
        if inactive_condition and inputs.ventilation_when_inactive
        else 0.0
    )
    return heating, cooling, inactive


def _heating_distribution_case(
    heat_pump: HeatPumpType,
    has_electric_heater: bool,
    has_fuel_heater: bool,
) -> int:
    flags = has_electric_heater, has_fuel_heater
    if heat_pump == "R744":
        return {
            (False, False): 1,
            (True, False): 2,
            (True, True): 3,
            (False, True): 4,
        }[flags]
    if heat_pump in {"2stage", "3stage", "4stage", "continuous"}:
        return {
            (False, False): 5,
            (True, False): 6,
            (True, True): 7,
            (False, True): 8,
        }[flags]
    return {
        (True, False): 9,
        (True, True): 10,
        (False, True): 11,
        (False, False): 12,
    }[flags]


def _heating_distribution(
    case: int, environmental_id: int
) -> tuple[float, float, float]:
    """Return heat-pump, electric-heater and fuel-heater contributions."""
    if case in {1, 5}:
        return 1.0, 0.0, 0.0
    if case == 2:
        return (0.5, 0.5, 0.0) if environmental_id == 1 else (1.0, 0.0, 0.0)
    if case == 3:
        return {
            1: (0.4, 0.2, 0.4),
            2: (0.7, 0.0, 0.3),
            3: (0.8, 0.0, 0.2),
        }.get(environmental_id, (1.0, 0.0, 0.0))
    if case == 4:
        return {
            1: (0.4, 0.0, 0.6),
            2: (0.7, 0.0, 0.3),
            3: (0.8, 0.0, 0.2),
        }.get(environmental_id, (1.0, 0.0, 0.0))
    if case == 6:
        return (0.0, 1.0, 0.0) if environmental_id == 1 else (1.0, 0.0, 0.0)
    if case == 7:
        return {
            1: (0.0, 0.2, 0.8),
            2: (0.7, 0.0, 0.3),
            3: (0.8, 0.0, 0.2),
        }.get(environmental_id, (1.0, 0.0, 0.0))
    if case == 8:
        return {
            1: (0.0, 0.0, 1.0),
            2: (0.7, 0.0, 0.3),
            3: (0.8, 0.0, 0.2),
        }.get(environmental_id, (1.0, 0.0, 0.0))
    if case == 9:
        return 0.0, 1.0, 0.0
    if case == 10:
        return (
            (0.0, 0.2, 0.8)
            if environmental_id == 1
            else (0.0, 0.0, 1.0)
        )
    if case == 11:
        return 0.0, 0.0, 1.0
    if case == 12:
        return 0.0, 0.0, 0.0
    raise ValueError(f"invalid VECTO heating distribution case: HD{case}")


def _limited_heating_distribution_w(
    demand_w: float,
    max_heating_power_w: float,
    distribution: tuple[float, float, float],
) -> tuple[float, float, float]:
    heat_pump_fraction, electric_fraction, fuel_fraction = distribution
    fuel_demand = demand_w * fuel_fraction
    limited_non_fuel_power = min(
        max_heating_power_w,
        demand_w * (heat_pump_fraction + electric_fraction),
    )
    non_fuel_fraction = 1.0 - fuel_fraction
    scale = (
        limited_non_fuel_power
        if non_fuel_fraction == 0
        else limited_non_fuel_power / non_fuel_fraction
    )
    return (
        scale * heat_pump_fraction,
        scale * electric_fraction,
        fuel_demand,
    )


def _heating_loads_w(
    environment: VectoEnvironmentalCondition,
    inputs: VectoSsmInputs,
    run1: float,
    run2: float,
    comfort_policy: VectoComfortPolicy,
) -> tuple[float, float, float, float, float, float]:
    if not comfort_policy.heating_enabled or run1 >= 0 or run2 >= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    demand = max(abs(max(run1, run2)) - inputs.engine_waste_heat_w, 0.0)
    driver_fraction, passenger_fraction = _compartment_contributions(inputs)
    total_max_heating_power = (
        inputs.max_heating_power_driver_w + inputs.max_heating_power_passenger_w
    )
    has_electric = bool(inputs.electric_heaters)
    has_fuel = inputs.fuel_heater_capacity_w > 0

    compartment_results: list[tuple[HeatPumpType, float, float, float]] = []
    # SSMCalculate does not pass each declared compartment limit through here.
    # It multiplies the *combined* maximum by that compartment's length share.
    for heat_pump, fraction in (
        (inputs.driver_heat_pump, driver_fraction),
        (inputs.passenger_heat_pump, passenger_fraction),
    ):
        case = _heating_distribution_case(heat_pump, has_electric, has_fuel)
        distribution = _heating_distribution(case, environment.environmental_id)
        hp, electric, fuel = _limited_heating_distribution_w(
            demand * fraction,
            total_max_heating_power * fraction,
            distribution,
        )
        compartment_results.append((heat_pump, hp, electric, fuel))

    hp_electrical = 0.0
    hp_mechanical = 0.0
    electric_heater_demand = 0.0
    fuel_heater_demand = 0.0
    delivered_heat_pump_w = 0.0
    for heat_pump, hp_demand, electric_demand, fuel_demand in compartment_results:
        cop = environment.heat_pump_cop.get(heat_pump)
        if cop is not None:
            delivered_heat_pump_w += hp_demand
            if _is_electrical_heat_pump(heat_pump):
                hp_electrical += hp_demand / cop
            else:
                hp_mechanical += hp_demand / cop
        electric_heater_demand += electric_demand
        fuel_heater_demand += fuel_demand

    efficiencies = [
        environment.heater_efficiency[heater]
        for heater in inputs.electric_heaters
        if heater in environment.heater_efficiency
    ]
    electric_heater_efficiency = (
        sum(efficiencies) / len(efficiencies) if efficiencies else None
    )
    electric_heater = (
        electric_heater_demand / electric_heater_efficiency
        if electric_heater_efficiency is not None
        else 0.0
    )
    fuel_efficiency = environment.heater_efficiency.get("fuel")
    delivered_electric_heater_w = (
        electric_heater_demand
        if electric_heater_efficiency is not None
        else 0.0
    )
    delivered_fuel_heater_w = (
        min(inputs.fuel_heater_capacity_w, fuel_heater_demand)
        if fuel_efficiency is not None
        else 0.0
    )
    fuel_heater = (
        delivered_fuel_heater_w
        / fuel_efficiency
        * (1.0 - _limit_technology_variation(inputs.heating_variation))
        if fuel_efficiency is not None
        else 0.0
    )
    delivered = (
        # These are thermal contributions before efficiency/resource
        # conversion. Technology variation changes resource use, not the heat
        # made available to meet the SSM demand.
        delivered_heat_pump_w
        + delivered_electric_heater_w
        + delivered_fuel_heater_w
    )
    return (
        demand,
        hp_electrical,
        hp_mechanical,
        electric_heater,
        fuel_heater,
        delivered,
    )


def _ssm_calculate(
    environment: VectoEnvironmentalCondition,
    inputs: VectoSsmInputs,
    comfort_policy: VectoComfortPolicy | None = None,
) -> _SsmBreakdown:
    """Mirror the official ``SSMCalculate`` outputs for one map entry."""
    policy = comfort_policy or VECTO_DEFAULT_COMFORT_POLICY
    run1, run2 = _run_totals(environment, inputs, policy)
    electrical_cooling, mechanical_cooling = _base_cooling_loads_w(
        environment, inputs, run1, run2, policy
    )
    heating_vent, cooling_vent, inactive_vent = _base_ventilation_loads_w(
        environment, inputs, run1, run2, policy
    )
    cop = _cooling_cop(environment, inputs)
    max_cooling_power = (
        inputs.max_cooling_power_driver_w + inputs.max_cooling_power_passenger_w
    )
    cooling_variation = _limit_technology_variation(inputs.cooling_variation)
    if cop is None:
        electrical_cooling_power = 0.0
        mechanical_cooling_power = 0.0
    else:
        electrical_cooling_power = min(
            electrical_cooling * (1.0 - cooling_variation), max_cooling_power
        ) / cop
        mechanical_cooling_power = min(
            mechanical_cooling * (1.0 - cooling_variation), max_cooling_power
        ) / cop

    electrical_cooling_and_ventilation = (
        electrical_cooling_power
        + heating_vent
        * (1.0 - _limit_technology_variation(inputs.heating_ventilation_variation))
        + cooling_vent
        * (1.0 - _limit_technology_variation(inputs.cooling_ventilation_variation))
        + inactive_vent
        * (1.0 - _limit_technology_variation(inputs.inactive_ventilation_variation))
    )
    heating = _heating_loads_w(environment, inputs, run1, run2, policy)

    if policy.heating_enabled and run1 < 0 and run2 < 0:
        mode: Literal["heating", "cooling", "ventilation", "off"] = "heating"
    elif (
        policy.cooling_enabled
        and environment.temperature_celsius
        >= policy.cooling_activation_temperature_c
        and run1 > 0
        and run2 > 0
    ):
        mode = "cooling"
    elif (
        (
            environment.temperature_celsius
            < policy.cooling_activation_temperature_c
            and run1 > 0
            and run2 > 0
        )
        or (run1 > 0 and run2 < 0)
    ):
        mode = "ventilation"
    else:
        mode = "off"

    return _SsmBreakdown(
        electrical_cooling_and_ventilation_w=electrical_cooling_and_ventilation,
        mechanical_cooling_w=mechanical_cooling_power,
        required_heating_power_w=heating[0],
        electrical_heat_pump_w=heating[1],
        mechanical_heat_pump_w=heating[2],
        electric_heater_w=heating[3],
        fuel_heater_w=heating[4],
        delivered_heating_power_w=heating[5],
        unmet_heating_power_w=max(heating[0] - heating[5], 0.0),
        mode=mode,
    )


def vecto_auxiliary_power(
    *,
    environment: VectoEnvironmentalCondition,
    inputs: VectoSsmInputs,
    non_hvac_baseline_kw: float = 0.0,
    comfort_policy: VectoComfortPolicy | None = None,
) -> VectoAuxResult:
    """Calculate one VECTO SSM condition.

    ``non_hvac_baseline_kw`` is caller-owned. VECTO's HVAC SSM neither
    calculates it nor supplies a default.
    """
    _require_non_negative("non_hvac_baseline_kw", non_hvac_baseline_kw)
    result = _ssm_calculate(environment, inputs, comfort_policy)
    hvac_electrical_kw = (
        result.electrical_cooling_and_ventilation_w
        + result.electrical_heat_pump_w
        + result.electric_heater_w
    ) / 1000.0
    return VectoAuxResult(
        p_electrical_kw=hvac_electrical_kw + non_hvac_baseline_kw,
        p_fuel_kw=result.fuel_heater_w / 1000.0,
        p_hvac_electrical_kw=hvac_electrical_kw,
        p_hvac_mechanical_kw=(
            result.mechanical_cooling_w + result.mechanical_heat_pump_w
        )
        / 1000.0,
        p_baseline_kw=non_hvac_baseline_kw,
        p_heating_demand_kw=result.required_heating_power_w / 1000.0,
        p_heating_delivered_kw=result.delivered_heating_power_w / 1000.0,
        unmet_thermal_demand_kw=result.unmet_heating_power_w / 1000.0,
        mode=result.mode,
    )


__all__ = [
    "ElectricHeaterType",
    "FloorType",
    "HeatPumpType",
    "VectoAuxResult",
    "VectoComfortPolicy",
    "VectoEnvironmentalCondition",
    "VectoSsmInputs",
    "VECTO_DEFAULT_COMFORT_POLICY",
    "default_environmental_condition",
    "vecto_auxiliary_power",
]
