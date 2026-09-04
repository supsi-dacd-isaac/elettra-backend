from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.services.runtime_release import (
    DATA_DRIVEN_AUXILIARY_LOOKUP_SHA256,
    LEGACY_AUXILIARY_ESTIMATOR,
    ROAD_SNAP_V3_ALGORITHM,
    VECTO_COMPLETE_AUXILIARY_ESTIMATOR,
    VECTO_HVAC_AUXILIARY_ESTIMATOR,
    VECTO_G2_TRANSFER_POLICY,
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
from app.services.prediction import (
    _bind_prediction_run_stack,
    _model_passenger_mass_kg,
    physical_bus_mass,
)
from app.services.optimization import (
    _aggregate_prediction_components,
    _prediction_provenance,
)
from app.routers.simulation import (
    _assert_yearly_prediction_stack_compatible,
    _validate_vecto_prediction_request,
)
from app.schemas.requests import PredictionParams, _validated_quantiles
from app.models import PredictionRuns
from simulation.consumption_prediction import ConsumptionPredictor
from simulation.greybox_models import CombinedGreyboxQRF
from elettra_core import (
    FEATURE_CONTRACT_VERSION,
    AuxiliaryEnergyComponents,
    CappedRegenAffineGreyBox,
    HybridGreyboxQRF,
    LinearGreyBox,
    PASSENGER_MASS_KG,
    source_tree_sha256,
)
from elettra_core.vecto_templates import VECTO_TEMPLATE_RELEASE, template_release_sha256


TRAINING_HVAC_ESTIMATOR = "vbz-man-hess-setpoints-v1"
TRAINING_COMFORT_POLICY = {
    "release_id": TRAINING_HVAC_ESTIMATOR,
    "sha256": "289792e25044fedfc414588e17a90d1a7729ae96680c0692cd0942020316e4e0",
    "scope": "training-only",
}
PASSENGER_PRIOR = {
    "source": "vbz-ogd",
    "release_id": "vbz-ogd-prior-v1",
    "sha256": "b" * 64,
    "correction_factor_s": 1.0,
    "qrf_reference_occupancy_percent": 21.5,
    "mass_weighting": "distance",
    "hvac_weighting": "duration",
    "matching_policy": "vbz-ogd-gtfs-v1",
    "primary_secondary_distance_coverage": 0.86,
    "passenger_mass_kg": PASSENGER_MASS_KG,
    "scale_policy": {
        "policy": "ogd-unscaled",
        "calibration_performed": False,
    },
}


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
    monkeypatch.setenv("ELETTRA_CORE_IMAGE_TREE_SHA256", source_tree_sha256())
    monkeypatch.setenv(
        "ENABLE_EXPERIMENTAL_PREDICTION_STACKS", "true" if experimental else "false"
    )


def _stack_contract(stack: str) -> dict[str, str]:
    if stack == "vecto-g2":
        return {
            "stack": stack,
            "deployment_tier": "production",
            "training_auxiliary_estimator": TRAINING_HVAC_ESTIMATOR,
            "inference_auxiliary_estimator": VECTO_HVAC_AUXILIARY_ESTIMATOR,
            "fixed_auxiliary_owner": "model",
            "auxiliary_contract": "vecto-hvac-only",
            "transfer_policy": VECTO_G2_TRANSFER_POLICY,
            "training_comfort_policy": TRAINING_COMFORT_POLICY,
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
    assert half.passenger_weight_kg == 2380
    assert half.total_weight_kg == 15365
    assert full.total_weight_kg - empty.total_weight_kg == 70 * 68
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


def test_model_specific_passenger_mass_preserves_legacy_and_uses_core_for_vecto():
    class Predictor:
        def __init__(self, *, model, metadata):
            self.model = model
            self.metadata = metadata

    legacy_release = type("Release", (), {"stack": PredictionStack.LEGACY})()
    legacy_model = type(
        "LegacyModel",
        (),
        {
            "passenger_load_estimator": {
                "config": {"passenger_weight_kg": 70.0}
            }
        },
    )()
    assert _model_passenger_mass_kg(
        legacy_release, Predictor(model=legacy_model, metadata={})
    ) == 70.0

    g2_release = type("Release", (), {"stack": PredictionStack.VECTO_G2})()
    assert _model_passenger_mass_kg(
        g2_release,
        Predictor(model=object(), metadata={"passenger_mass_kg": 68.0}),
    ) == 68.0
    with pytest.raises(ValueError, match="installed core"):
        _model_passenger_mass_kg(
            g2_release,
            Predictor(model=object(), metadata={"passenger_mass_kg": 70.0}),
        )


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
    else:
        value["transfer_policy"] = VECTO_G2_TRANSFER_POLICY
        value["training_comfort_policy"] = TRAINING_COMFORT_POLICY
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


def test_vecto_registry_rejects_image_with_another_core_source_tree(monkeypatch):
    _registry(monkeypatch)
    monkeypatch.setenv("ELETTRA_CORE_IMAGE_TREE_SHA256", "0" * 64)
    with pytest.raises(RuntimeReleaseConfigurationError, match="installed elettra-core bytes"):
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
            "passenger_prior": PASSENGER_PRIOR,
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
                "passenger_prior": PASSENGER_PRIOR,
            },
        )


def test_g2_manifest_requires_template_v2_and_valid_ogd_prior(monkeypatch):
    _registry(monkeypatch)
    release = resolve_prediction_selection(prediction_stack="vecto-g2")
    contract = _stack_contract("vecto-g2")
    contract["vecto_template_release"] = (
        "vecto-hvac-5.1.3-r744-templates-v1"
    )
    with pytest.raises(RuntimeReleaseConfigurationError, match="vecto_template_release"):
        validate_model_stack_contract(
            release,
            {
                "prediction_stack_contract": contract,
                "auxiliary_estimator": _auxiliary_estimator("vecto-g2"),
                "passenger_prior": PASSENGER_PRIOR,
            },
        )

    invalid_prior = dict(PASSENGER_PRIOR, correction_factor_s=1.2)
    with pytest.raises(RuntimeReleaseConfigurationError, match="passenger_prior"):
        validate_model_stack_contract(
            release,
            {
                "prediction_stack_contract": _stack_contract("vecto-g2"),
                "auxiliary_estimator": _auxiliary_estimator("vecto-g2"),
                "passenger_prior": invalid_prior,
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
    assert dynamic["comfort_policy"]["heating_calculation_temperature_c"] == 18.0
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


@pytest.mark.parametrize("occupancy", [-1.0, 100.01, float("nan"), float("inf")])
def test_prediction_api_rejects_invalid_occupancy_synchronously(
    monkeypatch, occupancy
):
    _registry(monkeypatch)
    with pytest.raises(ValidationError):
        PredictionParams(
            model_name="g2-release",
            prediction_stack="vecto-g2",
            external_temp_celsius=10.0,
            occupancy_percent=occupancy,
        )


def test_prediction_endpoint_preflight_rejects_unsupported_vecto_length(monkeypatch):
    _registry(monkeypatch)
    with pytest.raises(ValueError, match="unsupported bus_length_m"):
        _validate_vecto_prediction_request(
            selected_stack=resolve_prediction_selection(
                prediction_stack="vecto-g2"
            ),
            bus_model_specs={"bus_length_m": 14.0, "max_passengers": 70},
            occupancy_percent=50.0,
            external_temp_celsius=10.0,
            auxiliary_heating_type="default",
        )


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


def test_legacy_wrapper_embeds_hourly_mass_but_runtime_override_wins():
    class CapturingGreybox:
        battery_pack_density = 6.85
        chassis_mass_k1 = 717.5
        chassis_mass_k2 = 3413.5
        params_ = None

        def __init__(self):
            self.mass = None

        def predict(self, frame, override_mass=None):
            self.mass = np.asarray(override_mass, dtype=float)
            return self.mass

    class ZeroQrf:
        def predict(self, frame, quantiles=None):
            if isinstance(quantiles, list):
                return np.zeros((len(frame), len(quantiles)))
            return np.zeros(len(frame))

    greybox = CapturingGreybox()
    model = CombinedGreyboxQRF(
        greybox=greybox,
        qrf=ZeroQrf(),
        selected_features=["greybox_pred_kwh"],
        passenger_load_estimator={
            "config": {
                "time_bin_minutes": 60,
                "prior_load_factor": 0.5,
                "passenger_weight_kg": 70.0,
            },
            "mass_contract": {
                "battery_density_kg_per_kwh": 6.85,
                "chassis_k1_kg_per_m": 717.5,
                "chassis_k2_kg": 3413.5,
            },
            "time_bin_load_factor": {"7": 0.25, "8": 0.75},
        },
    )
    frame = pd.DataFrame(
        {
            "bus_length_m": [12.0, 12.0],
            "bus_battery_kwh": [460.0, 460.0],
            "start_time_minutes": [7 * 60 + 59, 8 * 60],
        }
    )
    capacity = 5258.0 / 68.0
    expected = (
        717.5 * 12.0
        + 3413.5
        + 6.85 * 460.0
        + capacity * np.array([0.25, 0.75]) * 70.0
    )

    assert model.predict(frame).tolist() == pytest.approx(expected.tolist())
    runtime_mass = np.array([12_985.0, 17_885.0])
    assert model.predict(frame, override_mass=runtime_mass).tolist() == pytest.approx(
        runtime_mass.tolist()
    )
    assert greybox.mass.tolist() == runtime_mass.tolist()


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
    metadata = {
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "selected_features": list(
            selected_features
            if selected_features is not None
            else model.selected_features
        ),
        "greybox_params": model.greybox.get_params_dict(),
    }
    if model.prediction_stack == "vecto-g2":
        metadata["passenger_prior"] = PASSENGER_PRIOR
    return metadata


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


def test_anchored_qrf_is_invariant_to_requested_passenger_mass():
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
        qrf_reference_occupancy_percent=21.5,
    )
    auxiliary = lambda value: AuxiliaryEnergyComponents.zeros(len(value))
    specs = {
        "bus_length_m": 12.0,
        "empty_weight_kg": 10_000.0,
        "battery_pack_size_kwh": 40.0,
        "battery_pack_weight_kg": 274.0,
        "max_battery_packs": 10,
        "max_passengers": 70,
    }
    empty_mass = physical_bus_mass(specs, occupancy_percent=0).total_weight_kg
    full_mass = physical_bus_mass(specs, occupancy_percent=100).total_weight_kg
    reference_mass = np.array(
        [
            physical_bus_mass(
                specs,
                occupancy_percent=21.5,
            ).total_weight_kg
        ]
    )

    empty = model.predict_components(
        frame,
        aux_energy_fn=auxiliary,
        override_mass=np.array([empty_mass]),
        qrf_reference_mass=reference_mass,
    )
    full = model.predict_components(
        frame,
        aux_energy_fn=auxiliary,
        override_mass=np.array([full_mass]),
        qrf_reference_mass=reference_mass,
    )

    assert qrf.frames[0]["greybox_pred_kwh"].to_numpy().tobytes() == (
        qrf.frames[1]["greybox_pred_kwh"].to_numpy().tobytes()
    )
    assert full.mechanical_greybox_kwh[0] > empty.mechanical_greybox_kwh[0]
    assert full.qrf_residual_kwh.tobytes() == empty.qrf_residual_kwh.tobytes()
    with pytest.raises(ValueError, match="require qrf_reference_mass"):
        model.predict_components(
            frame,
            aux_energy_fn=auxiliary,
            override_mass=np.array([12_985.0]),
        )


def test_heater_selection_changes_auxiliary_but_not_anchored_drivetrain(monkeypatch):
    _registry(monkeypatch)
    frame = _features().iloc[[0]]
    greybox = CappedRegenAffineGreyBox()
    greybox.theta_ = greybox.initial_bounds()[0]
    model = HybridGreyboxQRF(
        greybox=greybox,
        qrf=_FakeQrf(),
        selected_features=["greybox_pred_kwh"],
        prediction_stack="vecto-g2",
        qrf_reference_occupancy_percent=21.5,
    )
    release = resolve_prediction_selection(prediction_stack="vecto-g2")
    specs = {"bus_length_m": 12.0, "max_passengers": 60}
    common = {
        "stack_release": release,
        "bus_model_specs": specs,
        "occupancy_percent": 50.0,
        "external_temp_celsius": -20.0,
    }
    default = build_vecto_auxiliary_binding(
        **common,
        auxiliary_heating_type="default",
    )
    diesel = build_vecto_auxiliary_binding(
        **common,
        auxiliary_heating_type="diesel",
    )
    prediction_common = {
        "override_mass": np.array([15_000.0]),
        "qrf_reference_mass": np.array([14_000.0]),
    }

    default_components = model.predict_components(
        frame,
        aux_energy_fn=default.energy_fn,
        **prediction_common,
    )
    diesel_components = model.predict_components(
        frame,
        aux_energy_fn=diesel.energy_fn,
        **prediction_common,
    )

    assert default_components.drivetrain_kwh.tolist() == pytest.approx(
        diesel_components.drivetrain_kwh.tolist(), abs=1e-12
    )
    assert default_components.qrf_residual_kwh.tobytes() == (
        diesel_components.qrf_residual_kwh.tobytes()
    )
    assert default_components.mechanical_greybox_kwh.tolist() == pytest.approx(
        diesel_components.mechanical_greybox_kwh.tolist(), abs=1e-12
    )
    assert default_components.hvac_electrical_kwh.tolist() != pytest.approx(
        diesel_components.hvac_electrical_kwh.tolist(), abs=1e-12
    )


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
        qrf_reference_occupancy_percent=21.5,
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
        qrf_reference_occupancy_percent=21.5,
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
        qrf_reference_occupancy_percent=21.5,
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
        qrf_reference_occupancy_percent=21.5,
    )
    metadata = _artifact_metadata(model)
    metadata["greybox_params"]["alpha_roll"] *= 2
    with pytest.raises(ModelReleaseValidationError, match="greybox_params"):
        _validate_loaded_model_artifact(
            model,
            metadata,
            resolve_prediction_selection(prediction_stack="vecto-g2"),
        )


def test_vecto_release_gate_binds_qrf_reference_occupancy(monkeypatch):
    _registry(monkeypatch)
    greybox = CappedRegenAffineGreyBox()
    greybox.theta_ = greybox.initial_bounds()[0]
    model = HybridGreyboxQRF(
        greybox=greybox,
        qrf=_FakeQrf(),
        selected_features=["greybox_pred_kwh"],
        prediction_stack="vecto-g2",
        qrf_reference_occupancy_percent=22.0,
    )

    with pytest.raises(ModelReleaseValidationError, match="reference occupancy"):
        _validate_loaded_model_artifact(
            model,
            _artifact_metadata(model),
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
        qrf_reference_occupancy_percent=21.5,
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
