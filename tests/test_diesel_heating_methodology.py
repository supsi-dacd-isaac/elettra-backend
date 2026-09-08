from __future__ import annotations

import pytest
from types import SimpleNamespace
from uuid import uuid4

from app.routers.yearly_analysis import _build_energy_summary
from app.schemas.lca import DieselHeatingMethodologyMetadata
from app.services.diesel_heating import (
    derive_diesel_heating_factors,
    diesel_heating_config,
    diesel_heating_factor,
    diesel_liters_from_kwh,
    resolve_diesel_heating_quantity,
)


def test_mobitool_blend_energy_density_is_reproducible() -> None:
    profile = diesel_heating_config()["fuel_profile"]
    expected_mj_per_liter = (
        profile["fossil_diesel_volume_fraction"]
        * profile["fossil_diesel_density_kg_per_liter"]
        * profile["fossil_diesel_lhv_mj_per_kg"]
        + profile["used_cooking_oil_biodiesel_volume_fraction"]
        * profile["biodiesel_density_kg_per_liter"]
        * profile["biodiesel_lhv_mj_per_kg"]
    )
    assert profile["energy_density_mj_per_liter"] == pytest.approx(
        expected_mj_per_liter
    )
    assert profile["energy_density_kwh_per_liter"] == pytest.approx(
        expected_mj_per_liter / 3.6
    )


@pytest.mark.parametrize(
    ("indicator", "expected_total"),
    [
        ("gwp100a", 3001.7325407688),
        ("nox", 5469.256041034),
        ("pm10", 113.00732455774),
        ("primaryEnergy", 46.21888273184),
        ("primaryEnergyNonRenewable", 44.05110959907332),
    ],
)
def test_wtw_factor_reconciles_direct_and_energy_chain(
    indicator: str, expected_total: float
) -> None:
    direct = diesel_heating_factor(indicator, "direct")
    upstream = diesel_heating_factor(indicator, "energyChain")
    total = diesel_heating_factor(indicator)
    assert total == pytest.approx(direct + upstream)
    assert total == pytest.approx(expected_total)


def test_all_factors_are_reproduced_from_source_observations() -> None:
    config = diesel_heating_config()
    derived = derive_diesel_heating_factors(config)

    for indicator, expected in derived.items():
        configured = config["factors"][indicator]
        for key, value in expected.items():
            assert configured[key] == pytest.approx(value)


def test_methodology_metadata_serializes_verified_sources() -> None:
    config = diesel_heating_config()
    metadata = DieselHeatingMethodologyMetadata(
        methodology_version=config["methodology_version"],
        boundary="Well-to-wheel auxiliary diesel heater",
        fuel_profile=config["fuel_profile"],
        factors=config["factors"],
        sources=config["sources"],
        assumptions=config["assumptions"],
        limitations=config["limitations"],
    ).model_dump()

    assert metadata["sources"]["mobitool"]["sha256"] == (
        "778e1a936399ed072f4cc6ce6458b82c045fbea3b181b82b0fbf036bd49f7304"
    )
    assert metadata["sources"]["mobitool"]["workbook_cells"][
        "upstream_characterization"
    ] == "characterization factors!D128:I129"


def test_nox_direct_is_harmonized_to_no2_equivalent() -> None:
    no_mass_g = 10.75
    no2_mass_g = 0.87
    fuel_liters = 5.42
    expected_mg_per_liter = (
        no_mass_g * (46.0055 / 30.0061) + no2_mass_g
    ) / fuel_liters * 1000
    factor = diesel_heating_config()["factors"]["nox"]
    assert factor["direct"] == pytest.approx(expected_mg_per_liter)
    assert factor["unit"] == "mg NO2-eq/L"


def test_vecto_liters_are_recalculated_from_fuel_energy() -> None:
    quantity = resolve_diesel_heating_quantity(
        {
            "total_diesel_fuel_kwh": 98.7701388888889,
            "diesel_heating": {"diesel_liters": 9.9366338},
        },
        prediction_stack="vecto-g2",
        diesel_heating_expected=True,
    )
    assert quantity.liters == pytest.approx(10.0)
    assert quantity.liters_source == "recalculated_from_vecto_fuel_energy"
    assert quantity.data_status == "available"


def test_legacy_liters_keep_persisted_provenance() -> None:
    quantity = resolve_diesel_heating_quantity(
        {"diesel_heating": {"diesel_fuel_kwh": 99.4, "diesel_liters": 10.0}},
        prediction_stack="legacy",
        diesel_heating_expected=True,
    )
    assert quantity.liters == 10.0
    assert quantity.liters_source == "legacy_persisted"


def test_missing_vecto_energy_is_not_zero() -> None:
    quantity = resolve_diesel_heating_quantity(
        {"diesel_heating": {"diesel_liters": 2.0}},
        prediction_stack="vecto-g2",
        diesel_heating_expected=True,
    )
    assert quantity.data_status == "unavailable"
    assert quantity.quantity_state == "unknown"
    assert quantity.liters is None


def test_explicit_vecto_zero_remains_available_zero() -> None:
    quantity = resolve_diesel_heating_quantity(
        {"total_diesel_fuel_kwh": 0.0},
        prediction_stack="vecto-g2",
        diesel_heating_expected=True,
    )
    assert quantity.data_status == "available"
    assert quantity.quantity_state == "zero"
    assert quantity.liters == 0.0


@pytest.mark.parametrize("invalid_energy", [-1.0, float("nan"), float("inf")])
def test_invalid_vecto_energy_is_unavailable_not_zero(invalid_energy: float) -> None:
    quantity = resolve_diesel_heating_quantity(
        {"total_diesel_fuel_kwh": invalid_energy},
        prediction_stack="vecto-g2",
        diesel_heating_expected=True,
    )
    assert quantity.data_status == "unavailable"
    assert quantity.quantity_state == "unknown"
    assert quantity.liters is None


@pytest.mark.asyncio
async def test_yearly_summary_rederives_historical_vecto_liters() -> None:
    analysis = SimpleNamespace(
        id=uuid4(),
        features={
            "config": {"auxiliary_heating_type": "diesel"},
            "scenarios": [{"temperature": -5.0, "occurrences": 10}],
        },
    )
    run = SimpleNamespace(
        id=uuid4(),
        status="completed",
        external_temp_celsius=-5.0,
        prediction_stack="vecto-g2",
        model_name="vecto-test-model",
        auxiliary_estimator_release="vecto-test-aux",
        auxiliary_heating_type="diesel",
        summary={
            "total_consumption_kwh": 100.0,
            "total_distance_km": 50.0,
            "total_auxiliary_kwh": 30.0,
            "total_drivetrain_kwh": 70.0,
            "total_diesel_fuel_kwh": 98.7701388888889,
            "diesel_heating": {
                "diesel_fuel_kwh": 98.7701388888889,
                "diesel_liters": 9.9366338,
                "diesel_heater_efficiency": 0.8,
            },
        },
    )

    result = await _build_energy_summary(analysis, [run])

    assert result["yearly_totals"]["diesel_liters"] == pytest.approx(100.0)
    assert result["yearly_totals"]["diesel_fuel_kwh"] == pytest.approx(
        987.7014
    )
    assert result["diesel_heating_data"]["data_status"] == "available"
    assert result["diesel_heating_data"]["liters_sources"] == [
        "recalculated_from_vecto_fuel_energy"
    ]
