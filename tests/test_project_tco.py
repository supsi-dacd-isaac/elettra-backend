import math

import pytest

from app.services.project_tco import project_design


@pytest.mark.parametrize("rate", [0.0, 1e-18])
def test_zero_and_near_zero_discount_rates(rate):
    result = project_design([("vehicle", 1200, 12)], [("fuel", 20)], 100, 12, rate)

    assert result["initial"] == 1200
    assert result["operating"] == [{"key": "fuel", "annual": 20}]
    assert result["annualOpex"] == 20
    assert result["annuityFactor"] == pytest.approx(12)
    assert result["presentValue"] == pytest.approx(1440)
    assert result["annualEquivalent"] == pytest.approx(120)
    assert result["perKm"] == pytest.approx(1.2)


@pytest.mark.parametrize(("life", "horizon", "replacement_count"), [(4, 12, 2), (1.1, 11, 9)])
def test_replacements_stop_before_exact_terminal_boundary(life, horizon, replacement_count):
    result = project_design([("battery", 100, life)], [], 0, horizon, 0)

    component = result["components"][0]
    assert [event["year"] for event in component["replacements"]] == [
        purchase_number * life for purchase_number in range(1, replacement_count + 1)
    ]
    assert component["residual"] == 0
    assert result["discountedReplacements"] == 100 * replacement_count
    assert result["perKm"] is None


def test_fractional_replacement_and_discounted_residual():
    result = project_design([("points", 700, 2.5), ("connection", 300, 20)], [], 60, 6, 0.07)

    component, connection = result["components"]
    expected_replacements = [
        {"year": year, "cost": 700, "discounted": pytest.approx(700 / 1.07**year)}
        for year in (2.5, 5)
    ]
    assert component["replacements"] == expected_replacements
    assert component["residual"] == pytest.approx(420)
    assert connection["replacements"] == []
    assert connection["residual"] == pytest.approx(210)
    assert result["residual"] == pytest.approx(630)
    assert result["discountedResidual"] == pytest.approx(630 / 1.07**6)
    assert result["presentValue"] == pytest.approx(
        1000 + 700 / 1.07**2.5 + 700 / 1.07**5 - 630 / 1.07**6
    )
    assert result["perKm"] == pytest.approx(result["annualEquivalent"] / 60)


def test_replacement_immediately_before_horizon_is_included():
    life = math.nextafter(6, 0)
    result = project_design([("battery", 120, life)], [], 0, 12, 0)

    assert [item["year"] for item in result["components"][0]["replacements"]] == [
        life,
        2 * life,
    ]


def test_lfp_pack_annual_equivalent_is_unrounded():
    result = project_design([("battery", 132000, 8)], [], 0, 12, 0.03)

    assert result["residual"] == 66000
    assert result["annualEquivalent"] == pytest.approx(19078.8497733602)
    assert result["annualEquivalent"] != round(result["annualEquivalent"], 2)


@pytest.mark.parametrize(
    "overrides",
    [
        {"horizon": 0},
        {"horizon": 201},
        {"horizon": 12.5},
        {"horizon": True},
        {"rate": -0.01},
        {"rate": 1.01},
        {"rate": float("nan")},
        {"annual_km": float("inf")},
        {"annual_km": -1},
        {"assets": [("vehicle", float("inf"), 12)]},
        {"assets": [("vehicle", -1, 12)]},
        {"assets": [("vehicle", 0, 0.5)]},
        {"assets": [("vehicle", 0, float("inf"))]},
        {"operating": [("fuel", -1)]},
        {"operating": [("fuel", float("nan"))]},
    ],
)
def test_invalid_assumptions_raise_value_error(overrides):
    assumptions = {
        "assets": [],
        "operating": [],
        "annual_km": 0,
        "horizon": 12,
        "rate": 0,
    }

    with pytest.raises(ValueError):
        project_design(**(assumptions | overrides))
