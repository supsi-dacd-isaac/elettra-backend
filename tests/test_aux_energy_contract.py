"""Golden tests freezing the pre-road-snap auxiliary inference behaviour."""

from __future__ import annotations

import pandas as pd
import pytest

from app.services.prediction import build_aux_energy_fn


FROZEN_SPECS = {
    "auxiliary_consumption_kw": {
        "default": {
            "temperature_celsius": [-5, 0, 5, 10, 15, 20, 25],
            "consumption_kw": [24, 16, 12, 8, 9, 10, 16],
        },
        "diesel_heating": {
            "temperature_celsius": [-20, -10, 0, 10, 15, 20, 25],
            "consumption_kw": [8, 8, 8, 8, 9, 10, 16],
        },
    }
}


@pytest.mark.parametrize(
    ("heating_type", "expected_kwh"),
    [
        ("default", [12.0, 8.0, 32.0]),
        ("diesel_heating", [4.0, 8.0, 32.0]),
        ("unknown-existing-fallback", [12.0, 8.0, 32.0]),
    ],
)
def test_existing_auxiliary_curve_outputs_are_frozen(heating_type, expected_kwh):
    frame = pd.DataFrame(
        {
            "avg_temp_outside_celsius": [-5.0, 10.0, 25.0],
            "total_duration_minutes": [30.0, 60.0, 120.0],
        }
    )
    function = build_aux_energy_fn(FROZEN_SPECS, heating_type)

    assert function is not None
    assert function(frame).tolist() == pytest.approx(expected_kwh, abs=1e-12)


def test_vecto_is_not_an_auxiliary_prediction_dependency():
    """Guard the explicit release boundary: VECTO remains an offline oracle."""

    import app.services.prediction as prediction

    assert "vecto" not in prediction.__dict__
    assert "vecto_ssm" not in prediction.__dict__
