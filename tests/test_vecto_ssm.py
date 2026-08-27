"""
Tests for app.services.vecto_ssm — VECTO SSM auxiliary power model.

Validates the Python re-implementation against known VECTO 5.1.3 outputs
(city_10m bus, HD7, engine_waste_heat=8kW, weighted average over EU conditions).
"""

import pytest

from app.services.vecto_ssm import (
    BusGeometry,
    VectoAuxResult,
    vecto_auxiliary_power,
    _ssm_calculate,
    _interpolate_cop,
    _thermal_balance,
    _HEATING_BOUNDARY_TEMP,
)


# ---------------------------------------------------------------------------
# Validation against VECTO 5.1.3 weighted-average output
# ---------------------------------------------------------------------------

class TestVectoValidation:
    """Validate against known VECTO 5.1.3 simulation outputs."""

    def test_weighted_average_matches_vecto(self):
        """The weighted EU-average for a 10.6m bus (HD7, EWH=8kW) should match
        the VECTO output within 5%: P_el=0.966 kW, P_fuel=0.044 kW."""
        from app.services.vecto_ssm import _ENV_CONDITIONS_MAP

        bus = BusGeometry(length=10.6, n_passengers=50, floor_type="LowFloor")
        engine_waste_heat = 8000  # W

        total_weight = sum(ec["weight"] for ec in _ENV_CONDITIONS_MAP)
        w_el = 0.0
        w_fuel = 0.0

        for ec in _ENV_CONDITIONS_MAP:
            w = ec["weight"] / total_weight
            from app.services.vecto_ssm import (
                _thermal_balance, _interpolate_cop,
                _HEATING_BOUNDARY_TEMP, _COOLING_BOUNDARY_TEMP,
                _MAX_DELTA_LOW_FLOOR, _HEATING_DISTRIBUTIONS,
                _FUEL_HEATER_CAPACITY, _ENV_CONDITIONS_MAP as ecm,
            )
            # Replicate full calc with engine waste heat
            r1 = _thermal_balance(ec["temp"], ec["solar"], _HEATING_BOUNDARY_TEMP, bus)
            t_calc_r2 = max(_COOLING_BOUNDARY_TEMP, ec["temp"] - _MAX_DELTA_LOW_FLOOR)
            r2 = _thermal_balance(ec["temp"], ec["solar"], t_calc_r2, bus)

            cop = _interpolate_cop(ec["temp"], "2stage")
            p_el = 0.0
            p_fuel = 0.0

            if r1 > 0 and r2 > 0:
                q_demand = min(min(r1, r2), bus.max_cooling_power)
                if cop:
                    p_el = q_demand / cop
                p_el += bus.vent_power_cooling
            elif r1 < 0 and r2 < 0:
                raw_demand = abs(max(r1, r2))
                q_demand = max(0, raw_demand - engine_waste_heat)
                dist_map = _HEATING_DISTRIBUTIONS["HD7"]
                hp_frac, fuel_frac = dist_map.get(ec["id"], (1.0, 0.0))
                hp_demand = q_demand * hp_frac
                fuel_demand = q_demand * fuel_frac
                if cop and hp_demand > 0:
                    p_el = hp_demand / cop
                if fuel_demand > 0:
                    fuel_eff = ec.get("heater_eff", {}).get("fuel", 0.80)
                    if fuel_eff:
                        p_fuel = min(fuel_demand, _FUEL_HEATER_CAPACITY) / fuel_eff
                p_el += bus.vent_power_heating
            else:
                p_el = bus.vent_power_cooling

            w_el += p_el * w
            w_fuel += p_fuel * w

        p_el_kw = w_el / 1000.0
        p_fuel_kw = w_fuel / 1000.0

        # VECTO reference: P_el = 0.966 kW, P_fuel = 0.044 kW, Total = 1.010 kW
        assert abs(p_el_kw - 0.966) < 0.05, f"P_el={p_el_kw:.3f}, expected ~0.966"
        assert abs(p_fuel_kw - 0.044) < 0.02, f"P_fuel={p_fuel_kw:.3f}, expected ~0.044"
        assert abs((p_el_kw + p_fuel_kw) - 1.010) < 0.06


# ---------------------------------------------------------------------------
# Unit tests for the public API
# ---------------------------------------------------------------------------

class TestVectoAuxiliaryPower:
    """Test the convenience wrapper vecto_auxiliary_power()."""

    def test_returns_dataclass(self):
        result = vecto_auxiliary_power(10.0, 12.0)
        assert isinstance(result, VectoAuxResult)

    def test_cold_diesel_heater_reduces_electric(self):
        r_hp = vecto_auxiliary_power(-5.0, 18.0, diesel_heater=False)
        r_dh = vecto_auxiliary_power(-5.0, 18.0, diesel_heater=True)
        assert r_dh.p_electrical_kw < r_hp.p_electrical_kw
        assert r_dh.p_fuel_kw > 0
        assert r_hp.p_fuel_kw == 0

    def test_warm_no_fuel(self):
        r = vecto_auxiliary_power(25.0, 12.0)
        assert r.p_fuel_kw == 0
        assert r.mode == "cooling"

    def test_baseline_included(self):
        r = vecto_auxiliary_power(15.0, 18.0)
        assert r.p_baseline_kw > 0
        assert r.p_electrical_kw >= r.p_baseline_kw

    def test_default_passengers_by_length(self):
        r10 = vecto_auxiliary_power(10.0, 10.0)
        r12 = vecto_auxiliary_power(10.0, 12.0)
        r18 = vecto_auxiliary_power(10.0, 18.0)
        # Larger buses should have higher baseline
        assert r18.p_baseline_kw > r10.p_baseline_kw

    def test_custom_passengers(self):
        r_empty = vecto_auxiliary_power(-5.0, 12.0, n_passengers=0, diesel_heater=False)
        r_full = vecto_auxiliary_power(-5.0, 12.0, n_passengers=80, diesel_heater=False)
        # More passengers = more body heat = less heating needed
        assert r_full.p_hvac_electrical_kw < r_empty.p_hvac_electrical_kw

    def test_solar_affects_cooling(self):
        r_dark = vecto_auxiliary_power(28.0, 12.0, solar_irradiance_wm2=0)
        r_sunny = vecto_auxiliary_power(28.0, 12.0, solar_irradiance_wm2=300)
        # More sun = more cooling needed
        assert r_sunny.p_electrical_kw > r_dark.p_electrical_kw

    def test_custom_baseline(self):
        r = vecto_auxiliary_power(15.0, 12.0, non_hvac_baseline_kw=5.0)
        assert r.p_baseline_kw == 5.0

    def test_hp_technology_variants(self):
        r_r744 = vecto_auxiliary_power(-5.0, 12.0, hp_technology="R744", diesel_heater=False)
        r_2stage = vecto_auxiliary_power(-5.0, 12.0, hp_technology="2stage", diesel_heater=False)
        # R744 has better COP at low temps → less electrical
        assert r_r744.p_hvac_electrical_kw < r_2stage.p_hvac_electrical_kw


# ---------------------------------------------------------------------------
# BusGeometry tests
# ---------------------------------------------------------------------------

class TestBusGeometry:

    def test_surface_area_positive(self):
        bus = BusGeometry(length=12.0)
        assert bus.surface_area > 0

    def test_volume_scales_with_length(self):
        b10 = BusGeometry(length=10.0)
        b18 = BusGeometry(length=18.0)
        assert b18.volume > b10.volume

    def test_u_value_by_floor_type(self):
        assert BusGeometry(length=12.0, floor_type="LowFloor").u_value == 4.0
        assert BusGeometry(length=12.0, floor_type="HighFloor").u_value == 3.0


# ---------------------------------------------------------------------------
# COP interpolation
# ---------------------------------------------------------------------------

class TestCOPInterpolation:

    def test_returns_float_for_valid_tech(self):
        cop = _interpolate_cop(10.0, "2stage")
        assert isinstance(cop, float)
        assert cop > 0

    def test_clamps_below_range(self):
        cop = _interpolate_cop(-30.0, "2stage")
        assert cop == _interpolate_cop(-5.0, "2stage")  # first valid point for 2stage

    def test_none_for_unsupported_at_extreme(self):
        # 2stage is None at -20°C (env id 1)
        # but interpolation uses only non-None points, so it should
        # return the lowest available value
        cop = _interpolate_cop(-20.0, "2stage")
        assert cop is not None  # extrapolates from first valid point
