from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services.runtime_release import (
    DATA_DRIVEN_AUXILIARY_LOOKUP_SHA256,
    LEGACY_AUXILIARY_ESTIMATOR,
    ROAD_SNAP_V3_ALGORITHM,
    VECTO_COMPLETE_AUXILIARY_ESTIMATOR,
    VECTO_HVAC_AUXILIARY_ESTIMATOR,
    PredictionStack,
    RuntimeReleaseConfigurationError,
    resolve_prediction_selection,
    runtime_release_configuration,
    validate_model_stack_contract,
)
from app.services.vecto_auxiliary import build_vecto_auxiliary_binding
from app.services.model_release import (
    ModelReleaseValidationError,
    _validate_loaded_model_artifact,
)
from app.services.prediction import _bind_prediction_run_stack, physical_bus_mass
from app.services.optimization import (
    _aggregate_prediction_components,
    _prediction_provenance,
)
from app.routers.simulation import _assert_yearly_prediction_stack_compatible
from app.schemas.requests import _validated_quantiles
from app.models import PredictionRuns
from simulation.consumption_prediction import ConsumptionPredictor
from elettra_core import (
    AuxiliaryEnergyComponents,
    CappedRegenAffineGreyBox,
    HybridGreyboxQRF,
    LinearGreyBox,
)
from elettra_core.vecto_templates import VECTO_TEMPLATE_RELEASE, template_release_sha256


def _registry(monkeypatch, *, experimental: bool = False) -> None:
    monkeypatch.delenv("CONSUMPTION_MODEL_RELEASE", raising=False)
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "roaddeck-test")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "aux")
    monkeypatch.setenv("GTFS_ELEVATION_PROFILES_BUCKET", "gtfs")
    monkeypatch.setenv("ELEVATION_AUX_PROFILE_ALGORITHM", ROAD_SNAP_V3_ALGORITHM)
    monkeypatch.setenv("ELEVATION_AUX_ROADS_RELEASE", "roads-test")
    monkeypatch.setenv("LEGACY_CONSUMPTION_MODEL_RELEASE", "legacy-release")
    monkeypatch.setenv("VECTO_G2_CONSUMPTION_MODEL_RELEASE", "g2-release")
    monkeypatch.setenv("VECTO_G0_TRANSFER_MODEL_RELEASE", "g0-release")
    monkeypatch.setenv("DEFAULT_PREDICTION_STACK", "legacy")
    monkeypatch.setenv("ELETTRA_CORE_SOURCE_COMMIT", "c" * 40)
    monkeypatch.setenv("ELETTRA_CORE_IMAGE_COMMIT", "c" * 40)
    monkeypatch.setenv(
        "ENABLE_EXPERIMENTAL_PREDICTION_STACKS", "true" if experimental else "false"
    )


def _stack_contract(stack: str) -> dict[str, str]:
    if stack == "vecto-g2":
        return {
            "stack": stack,
            "deployment_tier": "production",
            "training_auxiliary_estimator": VECTO_HVAC_AUXILIARY_ESTIMATOR,
            "inference_auxiliary_estimator": VECTO_HVAC_AUXILIARY_ESTIMATOR,
            "fixed_auxiliary_owner": "model",
            "auxiliary_contract": "vecto-hvac-only",
            "vecto_template_release": VECTO_TEMPLATE_RELEASE,
            "vecto_template_sha256": template_release_sha256(),
        }
    return {
        "stack": stack,
        "deployment_tier": "experimental",
        "training_auxiliary_estimator": "data-driven-by-bus",
        "inference_auxiliary_estimator": VECTO_COMPLETE_AUXILIARY_ESTIMATOR,
        "fixed_auxiliary_owner": "template",
        "auxiliary_contract": "vecto-complete",
        "training_auxiliary_estimator_sha256": DATA_DRIVEN_AUXILIARY_LOOKUP_SHA256,
        "vecto_template_release": VECTO_TEMPLATE_RELEASE,
        "vecto_template_sha256": template_release_sha256(),
    }


def test_runtime_physical_mass_counts_requested_passengers_exactly_once():
    specs = {
        "bus_length_m": 13,
        "empty_weight_kg": 10235,
        "battery_pack_size_kwh": 46,
        "battery_pack_weight_kg": 275,
        "max_battery_packs": 10,
        "max_passengers": 70,
    }

    empty = physical_bus_mass(specs, occupancy_percent=0)
    half = physical_bus_mass(specs, occupancy_percent=50)
    full = physical_bus_mass(specs, occupancy_percent=100)

    assert empty.battery_weight_kg == 2750
    assert empty.total_weight_kg == 12985
    assert half.passenger_count == 35
    assert half.passenger_weight_kg == 2450
    assert half.total_weight_kg == 15435
    assert full.total_weight_kg - empty.total_weight_kg == 70 * 70
    assert half.battery_capacity_kwh == 460
    assert half.battery_density_kg_per_kwh == pytest.approx(275 / 46)


def test_runtime_physical_mass_fails_closed_on_missing_or_invalid_specs():
    with pytest.raises(ValueError, match="missing specs"):
        physical_bus_mass({}, occupancy_percent=50)
    specs = {
        "bus_length_m": 13,
        "empty_weight_kg": 10235,
        "battery_pack_size_kwh": 46,
        "battery_pack_weight_kg": 275,
        "max_battery_packs": 10,
        "max_passengers": 70,
    }
    with pytest.raises(ValueError, match="between 0 and 100"):
        physical_bus_mass(specs, occupancy_percent=101)
    with pytest.raises(ValueError, match="between 1 and 10"):
        physical_bus_mass(specs, occupancy_percent=50, num_battery_packs=11)


def _auxiliary_estimator(stack: str) -> dict[str, str]:
    contract = _stack_contract(stack)
    value = {
        "training": contract["training_auxiliary_estimator"],
        "inference": contract["inference_auxiliary_estimator"],
        "fixed_auxiliary_owner": contract["fixed_auxiliary_owner"],
        "auxiliary_contract": contract["auxiliary_contract"],
        "vecto_template_release": contract["vecto_template_release"],
        "vecto_template_sha256": contract["vecto_template_sha256"],
    }
    if stack == "vecto-g0-transfer":
        value["training_sha256"] = contract[
            "training_auxiliary_estimator_sha256"
        ]
    return value


def test_registry_resolves_model_and_stack_as_one_pair(monkeypatch):
    _registry(monkeypatch)
    runtime = runtime_release_configuration()
    assert runtime.default_prediction_stack is PredictionStack.LEGACY
    assert resolve_prediction_selection().auxiliary_estimator == LEGACY_AUXILIARY_ESTIMATOR
    assert resolve_prediction_selection(prediction_stack="vecto-g2").model_release == "g2-release"
    assert resolve_prediction_selection(model_name="g2-release").stack is PredictionStack.VECTO_G2
    with pytest.raises(RuntimeReleaseConfigurationError, match="different"):
        resolve_prediction_selection(prediction_stack="vecto-g2", model_name="legacy-release")


def test_experimental_stack_is_explicitly_gated_and_never_default(monkeypatch):
    _registry(monkeypatch)
    with pytest.raises(RuntimeReleaseConfigurationError, match="experimental"):
        resolve_prediction_selection(prediction_stack="vecto-g0-transfer")
    monkeypatch.setenv("ENABLE_EXPERIMENTAL_PREDICTION_STACKS", "true")
    assert resolve_prediction_selection(prediction_stack="vecto-g0-transfer").experimental
    monkeypatch.setenv("DEFAULT_PREDICTION_STACK", "vecto-g0-transfer")
    with pytest.raises(RuntimeReleaseConfigurationError, match="cannot be the default"):
        runtime_release_configuration()


def test_registry_and_singleton_are_mutually_exclusive(monkeypatch):
    _registry(monkeypatch)
    monkeypatch.setenv("CONSUMPTION_MODEL_RELEASE", "old-singleton")
    with pytest.raises(RuntimeReleaseConfigurationError, match="cannot be combined"):
        runtime_release_configuration()


def test_vecto_registry_requires_exact_core_commit(monkeypatch):
    _registry(monkeypatch)
    monkeypatch.delenv("ELETTRA_CORE_SOURCE_COMMIT")
    with pytest.raises(RuntimeReleaseConfigurationError, match="SOURCE_COMMIT"):
        runtime_release_configuration()
    monkeypatch.setenv("ELETTRA_CORE_SOURCE_COMMIT", "not-a-commit")
    with pytest.raises(RuntimeReleaseConfigurationError, match="SOURCE_COMMIT"):
        runtime_release_configuration()


def test_vecto_registry_rejects_image_built_from_another_core_commit(monkeypatch):
    _registry(monkeypatch)
    monkeypatch.setenv("ELETTRA_CORE_IMAGE_COMMIT", "d" * 40)
    with pytest.raises(RuntimeReleaseConfigurationError, match="baked"):
        runtime_release_configuration()


def test_vecto_registry_eagerly_validates_packaged_templates(monkeypatch):
    _registry(monkeypatch)

    def invalid_release():
        raise ValueError("tampered")

    monkeypatch.setattr(
        "app.services.runtime_release.load_template_release", invalid_release
    )
    with pytest.raises(RuntimeReleaseConfigurationError, match="missing or invalid"):
        runtime_release_configuration()


def test_model_metadata_is_bound_to_stack_semantics(monkeypatch):
    _registry(monkeypatch, experimental=True)
    g2 = resolve_prediction_selection(prediction_stack="vecto-g2")
    validate_model_stack_contract(
        g2,
        {
            "prediction_stack_contract": _stack_contract("vecto-g2"),
            "auxiliary_estimator": _auxiliary_estimator("vecto-g2"),
        },
    )
    wrong = _stack_contract("vecto-g2")
    wrong["fixed_auxiliary_owner"] = "template"
    with pytest.raises(RuntimeReleaseConfigurationError, match="fixed_auxiliary_owner"):
        validate_model_stack_contract(
            g2,
            {
                "prediction_stack_contract": wrong,
                "auxiliary_estimator": _auxiliary_estimator("vecto-g2"),
            },
        )


def test_vecto_runtime_binding_separates_hvac_and_fixed_load(monkeypatch):
    _registry(monkeypatch, experimental=True)
    specs = {"bus_length_m": 12.0, "max_passengers": 60}
    frame = pd.DataFrame({"total_duration_minutes": [30.0, 60.0]})
    g2 = build_vecto_auxiliary_binding(
        stack_release=resolve_prediction_selection(prediction_stack="vecto-g2"),
        bus_model_specs=specs,
        occupancy_percent=50,
        external_temp_celsius=-10,
        auxiliary_heating_type="diesel",
    )
    g0 = build_vecto_auxiliary_binding(
        stack_release=resolve_prediction_selection(prediction_stack="vecto-g0-transfer"),
        bus_model_specs=specs,
        occupancy_percent=50,
        external_temp_celsius=-10,
        auxiliary_heating_type="diesel",
    )
    g2_components = g2.energy_fn(frame)
    g0_components = g0.energy_fn(frame)
    assert np.all(g2_components.fixed_auxiliary_kwh == 0)
    assert g0_components.fixed_auxiliary_kwh.tolist() == pytest.approx([1.25, 2.5])
    assert g0_components.hvac_electrical_kwh.tolist() == pytest.approx(
        g2_components.hvac_electrical_kwh.tolist()
    )
    assert np.all(g2_components.diesel_fuel_kwh >= 0)
    dynamic = g2.metadata()
    assert dynamic["bus_length_m"] == 12.0
    assert dynamic["template_length_m"] == 12.0
    assert dynamic["number_of_passengers"] == 30.0
    assert dynamic["solar_irradiance_wm2"] == 100.0
    assert dynamic["auxiliary_heating_type"] == "diesel"
    assert "uncovered_thermal_power_kw" in dynamic

    # Process health reports only immutable/static release readiness.  These
    # request-specific values are persisted with the prediction instead.
    static = runtime_release_configuration().metadata()["prediction_stacks"][
        "vecto-g2"
    ]
    for request_key in (
        "bus_length_m",
        "template_length_m",
        "number_of_passengers",
        "solar_irradiance_wm2",
        "auxiliary_heating_type",
        "uncovered_thermal_power_kw",
    ):
        assert request_key not in static


class _FakeQrf:
    n_features_in_ = 1
    estimators_ = [object()]
    feature_names_in_ = np.asarray(["greybox_pred_kwh"])

    def predict(self, frame, quantiles=None):
        if isinstance(quantiles, list):
            return np.tile(np.asarray(quantiles, dtype=float), (len(frame), 1))
        return np.full(len(frame), 0.25 if quantiles == "mean" else 0.2)


class _SingleQuantileQrf(_FakeQrf):
    def predict(self, frame, quantiles=None):
        if isinstance(quantiles, list) and len(quantiles) == 1:
            return np.full(len(frame), quantiles[0])
        return super().predict(frame, quantiles=quantiles)


def _features() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bus_length_m": [12.0, 18.0],
            "bus_battery_kwh": [400.0, 500.0],
            "total_distance_m": [4000.0, 6000.0],
            "driving_average_speed_kmh": [20.0, 25.0],
            "total_ascent_m": [20.0, 30.0],
            "total_descent_m": [10.0, 40.0],
            "driving_time_minutes": [15.0, 20.0],
            "total_duration_minutes": [20.0, 30.0],
            "pct_downhill_segments": [20.0, 40.0],
        }
    )


def _artifact_metadata(model, selected_features=None):
    return {
        "selected_features": list(
            selected_features
            if selected_features is not None
            else model.selected_features
        ),
        "greybox_params": model.greybox.get_params_dict(),
    }


def test_hybrid_wrapper_has_an_auditable_energy_identity():
    frame = _features()
    greybox = CappedRegenAffineGreyBox()
    greybox.theta_ = greybox.initial_bounds()[0]
    model = HybridGreyboxQRF(
        greybox=greybox,
        qrf=_FakeQrf(),
        selected_features=["greybox_pred_kwh"],
        prediction_stack="vecto-g2",
    )

    def hvac_only(_frame):
        return AuxiliaryEnergyComponents(
            hvac_electrical_kwh=np.array([1.0, 2.0]),
            fixed_auxiliary_kwh=np.zeros(2),
            diesel_fuel_kwh=np.array([2.0, 3.0]),
            diesel_liters=np.array([0.2, 0.3]),
            uncovered_thermal_kwh=np.zeros(2),
        )

    components = model.predict_components(frame, aux_energy_fn=hvac_only)
    assert components.total_kwh.tolist() == pytest.approx(
        (components.drivetrain_kwh + components.auxiliary_kwh).tolist()
    )
    assert np.all(components.fixed_auxiliary_kwh > 0)
    quantiles = model.predict(frame, quantiles=[0.1, 0.9], aux_energy_fn=hvac_only)
    assert quantiles.shape == (2, 2)


def test_qrf_receives_greybox_prediction_recomputed_with_runtime_mass():
    class CapturingQrf(_FakeQrf):
        def __init__(self):
            self.frames = []

        def predict(self, frame, quantiles=None):
            self.frames.append(frame.copy())
            return super().predict(frame, quantiles=quantiles)

    frame = _features().iloc[[0]]
    greybox = CappedRegenAffineGreyBox()
    greybox.theta_ = greybox.initial_bounds()[0]
    qrf = CapturingQrf()
    model = HybridGreyboxQRF(
        greybox=greybox,
        qrf=qrf,
        selected_features=["greybox_pred_kwh"],
        prediction_stack="vecto-g2",
    )
    auxiliary = lambda value: AuxiliaryEnergyComponents.zeros(len(value))

    light = model.predict_components(
        frame,
        aux_energy_fn=auxiliary,
        override_mass=np.array([12_985.0]),
    )
    heavy = model.predict_components(
        frame,
        aux_energy_fn=auxiliary,
        override_mass=np.array([17_885.0]),
    )

    qrf_light = qrf.frames[0]["greybox_pred_kwh"].iloc[0]
    qrf_heavy = qrf.frames[1]["greybox_pred_kwh"].iloc[0]
    assert qrf_light == pytest.approx(
        light.mechanical_greybox_kwh[0] + light.fixed_auxiliary_kwh[0]
    )
    assert qrf_heavy == pytest.approx(
        heavy.mechanical_greybox_kwh[0] + heavy.fixed_auxiliary_kwh[0]
    )
    assert qrf_heavy > qrf_light


def test_predictor_normalizes_one_quantile_qrf_output():
    greybox = CappedRegenAffineGreyBox()
    greybox.theta_ = greybox.initial_bounds()[0]
    model = HybridGreyboxQRF(
        greybox=greybox,
        qrf=_SingleQuantileQrf(),
        selected_features=["greybox_pred_kwh"],
        prediction_stack="vecto-g2",
    )
    predictor = ConsumptionPredictor()
    predictor.model = model
    predictor.metadata = _artifact_metadata(model)
    predictor.required_features = model.selected_features
    predictor.is_greybox = True
    predictor.is_hybrid_greybox = True
    result = predictor.predict(
        _features(),
        quantiles=[0.5],
        aux_energy_fn=lambda frame: AuxiliaryEnergyComponents.zeros(len(frame)),
    )
    assert result["quantile_0.50"].shape == (2,)


def test_quantile_contract_rejects_persisted_key_collisions():
    assert _validated_quantiles([0.05, 0.5, 0.95]) == [0.05, 0.5, 0.95]
    with pytest.raises(ValueError, match="unique"):
        _validated_quantiles([0.101, 0.104])


def test_g0_transfer_requires_external_complete_auxiliary():
    frame = _features()
    greybox = LinearGreyBox()
    greybox.theta_ = greybox.initial_bounds()[0]
    model = HybridGreyboxQRF(
        greybox=greybox,
        qrf=_FakeQrf(),
        selected_features=["greybox_pred_kwh"],
        prediction_stack="vecto-g0-transfer",
    )
    with pytest.raises(ValueError, match="requires template fixed"):
        model.predict_components(
            frame,
            aux_energy_fn=lambda _frame: AuxiliaryEnergyComponents.zeros(2),
        )


def test_release_gate_binds_fitted_hybrid_artifact_to_stack(monkeypatch):
    _registry(monkeypatch, experimental=True)
    g2_greybox = CappedRegenAffineGreyBox()
    g2_greybox.theta_ = g2_greybox.initial_bounds()[0]
    g2_model = HybridGreyboxQRF(
        greybox=g2_greybox,
        qrf=_FakeQrf(),
        selected_features=["greybox_pred_kwh"],
        prediction_stack="vecto-g2",
    )
    g2_release = resolve_prediction_selection(prediction_stack="vecto-g2")
    _validate_loaded_model_artifact(
        g2_model, _artifact_metadata(g2_model), g2_release
    )

    g0_greybox = LinearGreyBox()
    g0_greybox.theta_ = g0_greybox.initial_bounds()[0]
    g0_model = HybridGreyboxQRF(
        greybox=g0_greybox,
        qrf=_FakeQrf(),
        selected_features=["greybox_pred_kwh"],
        prediction_stack="vecto-g0-transfer",
    )
    _validate_loaded_model_artifact(
        g0_model,
        _artifact_metadata(g0_model),
        resolve_prediction_selection(prediction_stack="vecto-g0-transfer"),
    )


def test_release_gate_rejects_unfitted_or_semantically_mismatched_artifact(monkeypatch):
    _registry(monkeypatch, experimental=True)
    release = resolve_prediction_selection(prediction_stack="vecto-g2")
    greybox = CappedRegenAffineGreyBox()
    model = HybridGreyboxQRF(
        greybox=greybox,
        qrf=_FakeQrf(),
        selected_features=["greybox_pred_kwh"],
        prediction_stack="vecto-g2",
    )
    with pytest.raises(ModelReleaseValidationError, match="unfitted"):
        _validate_loaded_model_artifact(
            model, _artifact_metadata(model), release
        )
    greybox.theta_ = greybox.initial_bounds()[0]
    model.prediction_stack = "vecto-g0-transfer"
    with pytest.raises(ModelReleaseValidationError, match="prediction_stack"):
        _validate_loaded_model_artifact(
            model, _artifact_metadata(model), release
        )
    model.prediction_stack = "vecto-g2"
    with pytest.raises(ModelReleaseValidationError, match="selected_features"):
        _validate_loaded_model_artifact(
            model, _artifact_metadata(model, ["different"]), release
        )


@pytest.mark.parametrize(
    "feature",
    ["manufacturer", "bus_number", "bus_battery_kwh", "unknown_feature"],
)
def test_vecto_release_gate_rejects_unservable_or_identity_features(
    monkeypatch, feature
):
    _registry(monkeypatch)
    greybox = CappedRegenAffineGreyBox()
    greybox.theta_ = greybox.initial_bounds()[0]
    model = HybridGreyboxQRF(
        greybox=greybox,
        qrf=_FakeQrf(),
        selected_features=[feature],
        prediction_stack="vecto-g2",
    )
    with pytest.raises(ModelReleaseValidationError, match="cannot be served"):
        _validate_loaded_model_artifact(
            model,
            _artifact_metadata(model),
            resolve_prediction_selection(prediction_stack="vecto-g2"),
        )


def test_vecto_release_gate_rejects_stale_greybox_parameters(monkeypatch):
    _registry(monkeypatch)
    greybox = CappedRegenAffineGreyBox()
    greybox.theta_ = greybox.initial_bounds()[0]
    model = HybridGreyboxQRF(
        greybox=greybox,
        qrf=_FakeQrf(),
        selected_features=["greybox_pred_kwh"],
        prediction_stack="vecto-g2",
    )
    metadata = _artifact_metadata(model)
    metadata["greybox_params"]["alpha_roll"] *= 2
    with pytest.raises(ModelReleaseValidationError, match="greybox_params"):
        _validate_loaded_model_artifact(
            model,
            metadata,
            resolve_prediction_selection(prediction_stack="vecto-g2"),
        )


def test_vecto_release_gate_rejects_qrf_feature_order_mismatch(monkeypatch):
    _registry(monkeypatch)
    greybox = CappedRegenAffineGreyBox()
    greybox.theta_ = greybox.initial_bounds()[0]
    qrf = _FakeQrf()
    qrf.feature_names_in_ = np.asarray(["total_distance_m"])
    model = HybridGreyboxQRF(
        greybox=greybox,
        qrf=qrf,
        selected_features=["greybox_pred_kwh"],
        prediction_stack="vecto-g2",
    )
    with pytest.raises(ModelReleaseValidationError, match="feature order"):
        _validate_loaded_model_artifact(
            model,
            _artifact_metadata(model),
            resolve_prediction_selection(prediction_stack="vecto-g2"),
        )


def test_optimization_provenance_rejects_auxiliary_mismatch(monkeypatch):
    _registry(monkeypatch)
    valid = type(
        "PredictionRunFixture",
        (),
        {
            "id": "run-1",
            "prediction_stack": "vecto-g2",
            "model_name": "g2-release",
            "auxiliary_estimator_release": VECTO_HVAC_AUXILIARY_ESTIMATOR,
        },
    )()
    assert _prediction_provenance([valid]) == {
        "prediction_stack": "vecto-g2",
        "model_release": "g2-release",
        "auxiliary_estimator_release": VECTO_HVAC_AUXILIARY_ESTIMATOR,
        "prediction_run_ids": ["run-1"],
    }
    valid.auxiliary_estimator_release = VECTO_COMPLETE_AUXILIARY_ESTIMATOR
    with pytest.raises(ValueError, match="auxiliary estimator"):
        _prediction_provenance([valid])


def test_prediction_execution_repairs_only_legacy_null_auxiliary(monkeypatch):
    _registry(monkeypatch)
    legacy = PredictionRuns(
        prediction_stack="legacy",
        model_name="legacy-release",
        auxiliary_estimator_release=None,
    )
    _bind_prediction_run_stack(
        legacy, resolve_prediction_selection(prediction_stack="legacy")
    )
    assert legacy.auxiliary_estimator_release == LEGACY_AUXILIARY_ESTIMATOR

    vecto = PredictionRuns(
        prediction_stack="vecto-g2",
        model_name="g2-release",
        auxiliary_estimator_release=None,
    )
    with pytest.raises(ValueError, match="no persisted auxiliary estimator"):
        _bind_prediction_run_stack(
            vecto, resolve_prediction_selection(prediction_stack="vecto-g2")
        )


def test_optimization_propagates_mean_component_breakdown():
    value = {
        "mechanical_greybox_kwh": 3.0,
        "qrf_residual_kwh": 0.5,
        "fixed_auxiliary_kwh": 0.4,
        "hvac_electrical_kwh": 0.6,
        "diesel_fuel_kwh": 1.0,
        "diesel_liters": 0.1,
        "uncovered_thermal_kwh": 0.0,
    }
    summary = _aggregate_prediction_components(
        [value, value], solver_consumption="0.90"
    )
    assert summary["basis"] == "mean_prediction_components"
    assert summary["solver_consumption"] == "0.90"
    assert summary["totals"]["hvac_electrical_kwh"] == 1.2
    with pytest.raises(ValueError, match="partially"):
        _aggregate_prediction_components(
            [value, None], solver_consumption="median"
        )


def test_yearly_creation_rejects_a_mixed_stack(monkeypatch):
    _registry(monkeypatch)
    selected = resolve_prediction_selection(prediction_stack="vecto-g2")
    _assert_yearly_prediction_stack_compatible(
        [
            (
                "vecto-g2",
                "g2-release",
                VECTO_HVAC_AUXILIARY_ESTIMATOR,
                "diesel",
            )
        ],
        selected,
        auxiliary_heating_type="diesel",
    )
    with pytest.raises(ValueError, match="cannot mix"):
        _assert_yearly_prediction_stack_compatible(
            [
                (
                    "legacy",
                    "legacy-release",
                    LEGACY_AUXILIARY_ESTIMATOR,
                    "diesel",
                )
            ],
            selected,
            auxiliary_heating_type="diesel",
        )


def test_prediction_stack_migration_and_orm_are_fail_closed():
    constraint_names = {
        constraint.name for constraint in PredictionRuns.__table__.constraints
    }
    assert "prediction_runs_prediction_stack_check" in constraint_names
    migration = (
        Path(__file__).parents[1]
        / "db"
        / "migrations"
        / "008_add_prediction_stacks.sql"
    ).read_text(encoding="utf-8")
    assert "prediction_stack text NOT NULL DEFAULT 'legacy'" in migration
    assert "component_breakdown jsonb" in migration
    assert "'legacy', 'vecto-g2', 'vecto-g0-transfer'" in migration
