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


def test_legacy_curve_path_does_not_invoke_vecto_adapter(monkeypatch):
    """The legacy estimator remains isolated when VECTO stacks are installed."""
    import app.services.prediction as prediction

    def forbidden_adapter(**_kwargs):
        raise AssertionError("legacy auxiliary evaluation invoked VECTO")

    monkeypatch.setattr(
        prediction, "build_vecto_auxiliary_binding", forbidden_adapter
    )
    frame = pd.DataFrame(
        {
            "avg_temp_outside_celsius": [10.0],
            "total_duration_minutes": [60.0],
        }
    )
    function = prediction.build_aux_energy_fn(FROZEN_SPECS, "default")
    assert function(frame).tolist() == pytest.approx([8.0])
