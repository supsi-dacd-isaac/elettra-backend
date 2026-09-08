"""Canonical application-layer diesel-heater fuel profile and conversions.

The frozen VECTO template release still exposes its historical litre estimate.
Application consumers must instead use the fuel-energy result as the physical
source of truth and apply the versioned profile in ``emission_defaults.json``.
Legacy prediction stacks retain their persisted litre quantity and provenance.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


_EMISSION_CONFIG = (
    Path(__file__).resolve().parents[2] / "config" / "emission_defaults.json"
)


@lru_cache(maxsize=1)
def diesel_heating_config() -> dict[str, Any]:
    with open(_EMISSION_CONFIG, encoding="utf-8") as handle:
        config = json.load(handle)["diesel_heating"]
    _validate_diesel_heating_config(config)
    return config


def diesel_heating_fuel_profile() -> dict[str, Any]:
    return dict(diesel_heating_config()["fuel_profile"])


def diesel_heating_factor(indicator: str, phase: str = "total") -> float:
    return float(diesel_heating_config()["factors"][indicator][phase])


def _blend_factor_per_liter(
    profile: Mapping[str, Any],
    per_kg: Mapping[str, Any],
    indicator: str,
) -> float:
    return (
        float(profile["fossil_diesel_volume_fraction"])
        * float(profile["fossil_diesel_density_kg_per_liter"])
        * float(per_kg["fossil_diesel"][indicator])
        + float(profile["used_cooking_oil_biodiesel_volume_fraction"])
        * float(profile["biodiesel_density_kg_per_liter"])
        * float(per_kg["used_cooking_oil_biodiesel"][indicator])
    )


def derive_diesel_heating_factors(
    config: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, float]]:
    """Reproduce the configured factors from the minimum source observations."""

    resolved = config if config is not None else diesel_heating_config()
    profile = resolved["fuel_profile"]
    source = resolved["source_data"]
    upstream_per_kg = source["upstream_per_kg"]
    direct = source["direct"]

    upstream = {
        indicator: _blend_factor_per_liter(profile, upstream_per_kg, indicator)
        for indicator in (
            "gwp100a",
            "nox",
            "pm10",
            "primaryEnergy",
            "primaryEnergyNonRenewable",
        )
    }

    nox = direct["nox"]
    no_mass_g = sum(float(value) for value in nox["no_g"])
    no2_mass_g = sum(float(value) for value in nox["no2_g"])
    nox_fuel_liters = sum(float(value) for value in nox["fuel_liters"])
    nox_direct = (
        no_mass_g * float(nox["no_to_no2_molecular_mass_ratio"]) + no2_mass_g
    ) / nox_fuel_liters * 1000.0

    pm10 = direct["pm10_proxy"]
    tpm_mg = [float(value) for value in pm10["tpm_mg"]]
    pm_fuel_liters = [float(value) for value in pm10["fuel_liters"]]
    pm10_direct = sum(tpm_mg) / sum(pm_fuel_liters)
    pm10_observed_wtw = [
        mass / liters + upstream["pm10"]
        for mass, liters in zip(tpm_mg, pm_fuel_liters, strict=True)
    ]

    direct_factors = {
        "gwp100a": (
            float(profile["fossil_diesel_volume_fraction"])
            * float(profile["fossil_diesel_density_kg_per_liter"])
            * float(direct["gwp100a"]["fossil_co2_kg_per_kg"])
            * 1000.0
        ),
        "nox": nox_direct,
        "pm10": pm10_direct,
        "primaryEnergy": 0.0,
        "primaryEnergyNonRenewable": 0.0,
    }

    result = {
        indicator: {
            "direct": direct_value,
            "energyChain": upstream[indicator],
            "total": direct_value + upstream[indicator],
        }
        for indicator, direct_value in direct_factors.items()
    }
    result["pm10"]["observed_wtw_min"] = min(pm10_observed_wtw)
    result["pm10"]["observed_wtw_max"] = max(pm10_observed_wtw)
    return result


def _validate_diesel_heating_config(config: Mapping[str, Any]) -> None:
    profile = config["fuel_profile"]
    fraction_sum = (
        float(profile["fossil_diesel_volume_fraction"])
        + float(profile["used_cooking_oil_biodiesel_volume_fraction"])
    )
    if not math.isclose(fraction_sum, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("diesel-heating fuel fractions must sum to one")

    energy_density = (
        float(profile["fossil_diesel_volume_fraction"])
        * float(profile["fossil_diesel_density_kg_per_liter"])
        * float(profile["fossil_diesel_lhv_mj_per_kg"])
        + float(profile["used_cooking_oil_biodiesel_volume_fraction"])
        * float(profile["biodiesel_density_kg_per_liter"])
        * float(profile["biodiesel_lhv_mj_per_kg"])
    )
    if not math.isclose(
        float(profile["energy_density_mj_per_liter"]),
        energy_density,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ) or not math.isclose(
        float(profile["energy_density_kwh_per_liter"]),
        energy_density / 3.6,
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ValueError("diesel-heating energy density does not match its profile")

    derived = derive_diesel_heating_factors(config)
    for indicator, expected in derived.items():
        configured = config["factors"][indicator]
        for key, expected_value in expected.items():
            if not math.isclose(
                float(configured[key]),
                expected_value,
                rel_tol=1e-10,
                abs_tol=1e-9,
            ):
                raise ValueError(
                    f"diesel-heating factor {indicator}.{key} is not reproducible"
                )


def diesel_liters_from_kwh(fuel_kwh: float) -> float:
    value = float(fuel_kwh)
    if not math.isfinite(value) or value < 0:
        raise ValueError("diesel fuel energy must be finite and non-negative")
    density = float(diesel_heating_config()["fuel_profile"]["energy_density_kwh_per_liter"])
    if not math.isfinite(density) or density <= 0:
        raise ValueError("diesel-heating energy density must be finite and positive")
    return value / density


def _optional_non_negative(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or result < 0:
        return None
    return result


@dataclass(frozen=True)
class DieselHeatingQuantity:
    fuel_kwh: float | None
    liters: float | None
    data_status: str
    quantity_state: str
    liters_source: str | None
    reason: str | None

    def metadata(self) -> dict[str, object]:
        profile = diesel_heating_fuel_profile()
        return {
            "data_status": self.data_status,
            "quantity_state": self.quantity_state,
            "liters_source": self.liters_source,
            "reason": self.reason,
            "fuel_profile_version": profile["version"],
            "energy_density_kwh_per_liter": profile[
                "energy_density_kwh_per_liter"
            ],
        }


def resolve_diesel_heating_quantity(
    summary: Mapping[str, Any],
    *,
    prediction_stack: str,
    diesel_heating_expected: bool,
) -> DieselHeatingQuantity:
    """Resolve one prediction summary without conflating zero and missing data."""

    heating = summary.get("diesel_heating")
    heating_map = heating if isinstance(heating, Mapping) else {}
    stack = str(prediction_stack or "legacy")

    if stack.startswith("vecto-"):
        raw_energy = summary.get("total_diesel_fuel_kwh")
        if raw_energy is None:
            raw_energy = heating_map.get("diesel_fuel_kwh")
        energy = _optional_non_negative(raw_energy)
        if energy is not None:
            liters = diesel_liters_from_kwh(energy)
            return DieselHeatingQuantity(
                fuel_kwh=energy,
                liters=liters,
                data_status="available",
                quantity_state="zero" if energy == 0 else "positive",
                liters_source="recalculated_from_vecto_fuel_energy",
                reason=None,
            )
        if not diesel_heating_expected:
            return DieselHeatingQuantity(
                fuel_kwh=0.0,
                liters=0.0,
                data_status="available",
                quantity_state="zero",
                liters_source="not_applicable",
                reason=None,
            )
        return DieselHeatingQuantity(
            fuel_kwh=None,
            liters=None,
            data_status="unavailable",
            quantity_state="unknown",
            liters_source=None,
            reason="vecto_fuel_energy_missing_or_invalid",
        )

    energy_present = "diesel_fuel_kwh" in heating_map
    liters_present = "diesel_liters" in heating_map
    energy = _optional_non_negative(heating_map.get("diesel_fuel_kwh"))
    liters = _optional_non_negative(heating_map.get("diesel_liters"))
    if energy_present and liters_present and energy is not None and liters is not None:
        return DieselHeatingQuantity(
            fuel_kwh=energy,
            liters=liters,
            data_status="available",
            quantity_state="zero" if energy == 0 and liters == 0 else "positive",
            liters_source="legacy_persisted",
            reason=None,
        )
    if not diesel_heating_expected:
        return DieselHeatingQuantity(
            fuel_kwh=0.0,
            liters=0.0,
            data_status="available",
            quantity_state="zero",
            liters_source="not_applicable",
            reason=None,
        )
    return DieselHeatingQuantity(
        fuel_kwh=energy,
        liters=liters,
        data_status="unavailable",
        quantity_state="unknown",
        liters_source="legacy_persisted" if liters is not None else None,
        reason="legacy_diesel_quantity_missing_or_invalid",
    )


__all__ = [
    "DieselHeatingQuantity",
    "derive_diesel_heating_factors",
    "diesel_heating_config",
    "diesel_heating_factor",
    "diesel_heating_fuel_profile",
    "diesel_liters_from_kwh",
    "resolve_diesel_heating_quantity",
]
