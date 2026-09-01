from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers.yearly_analysis import _build_energy_summary
from app.services.yearly_weather_recalculation import _build_energy_summary_blob
from app.services.yearly_weather_recalculation import (
    prediction_run_provenance,
    require_uniform_prediction_provenance,
)
from app.services.runtime_release import VECTO_HVAC_AUXILIARY_ESTIMATOR


def _run():
    return SimpleNamespace(
        id=uuid4(),
        external_temp_celsius=-5.0,
        auxiliary_heating_type="diesel",
        prediction_stack="vecto-g2",
        model_name="g2-release",
        auxiliary_estimator_release=VECTO_HVAC_AUXILIARY_ESTIMATOR,
        summary={
            "total_consumption_kwh": 12.0,
            "total_distance_km": 10.0,
            "total_auxiliary_kwh": 3.0,
            "total_drivetrain_kwh": 9.0,
            "total_mechanical_greybox_kwh": 8.0,
            "total_qrf_residual_kwh": 1.0,
            "total_fixed_auxiliary_kwh": 1.5,
            "total_hvac_electrical_kwh": 1.5,
            "total_uncovered_thermal_kwh": 0.25,
            "diesel_heating": {
                "diesel_fuel_kwh": 2.0,
                "diesel_liters": 0.2,
                "diesel_heater_efficiency": 0.8,
            },
        },
    )


@pytest.mark.asyncio
async def test_yearly_summary_propagates_stack_and_energy_components():
    run = _run()
    analysis = SimpleNamespace(
        id=uuid4(),
        features={
            "config": {"auxiliary_heating_type": "diesel"},
            "scenarios": [{"temperature": -5.0, "occurrences": 10}],
        },
    )
    summary = await _build_energy_summary(analysis, [run])
    assert summary["prediction_stacks"] == ["vecto-g2"]
    assert summary["model_releases"] == ["g2-release"]
    assert summary["auxiliary_estimator_releases"] == [
        VECTO_HVAC_AUXILIARY_ESTIMATOR
    ]
    assert summary["scenarios"][0]["daily_components"] == {
        "mechanical_greybox_kwh": 8.0,
        "qrf_residual_kwh": 1.0,
        "fixed_auxiliary_kwh": 1.5,
        "hvac_electrical_kwh": 1.5,
        "uncovered_thermal_kwh": 0.25,
    }
    assert summary["yearly_totals"]["mechanical_greybox_kwh"] == 80.0


def test_weather_recalculation_blob_keeps_same_component_contract():
    run = _run()
    blob = _build_energy_summary_blob(
        {"config": {"auxiliary_heating_type": "diesel"}},
        [{"temperature": -5.0, "occurrences": 10}],
        [run],
    )
    assert blob["prediction_stacks"] == ["vecto-g2"]
    assert blob["model_releases"] == ["g2-release"]
    assert blob["scenarios"][0]["annual_components"][
        "hvac_electrical_kwh"
    ] == 15.0
    assert blob["yearly_totals"]["uncovered_thermal_kwh"] == 2.5


def test_yearly_aggregation_rejects_mixed_prediction_semantics():
    first = _run()
    second = _run()
    second.model_name = "another-release"
    with pytest.raises(ValueError, match="cannot mix"):
        require_uniform_prediction_provenance([first, second])


def test_legacy_yearly_provenance_is_present_without_component_breakdown():
    legacy = SimpleNamespace(
        prediction_stack="legacy",
        model_name="legacy-release",
        auxiliary_estimator_release=None,
    )
    assert prediction_run_provenance(legacy) == (
        "legacy",
        "legacy-release",
        "legacy-curves-v1",
        "default",
    )


@pytest.mark.asyncio
async def test_yearly_summary_rejects_unfinished_prediction_runs():
    run = _run()
    run.status = "failed"
    analysis = SimpleNamespace(
        id=uuid4(),
        features={
            "config": {"auxiliary_heating_type": "diesel"},
            "scenarios": [{"temperature": -5.0, "occurrences": 10}],
        },
    )
    with pytest.raises(HTTPException) as exc_info:
        await _build_energy_summary(analysis, [run])
    assert exc_info.value.status_code == 409
