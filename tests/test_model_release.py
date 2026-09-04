from __future__ import annotations

import hashlib
import io
import json
from types import SimpleNamespace

import pytest
import joblib
import numpy as np

from app.services import model_release
from app.services.model_release import ModelReleaseValidationError
from app.services.runtime_release import (
    DATA_DRIVEN_AUXILIARY_LOOKUP_SHA256,
    LEGACY_AUXILIARY_ESTIMATOR,
    ROAD_SNAP_V3_ALGORITHM,
    VECTO_COMPLETE_AUXILIARY_ESTIMATOR,
    VECTO_HVAC_AUXILIARY_ESTIMATOR,
    VECTO_G2_TRANSFER_POLICY,
    PredictionStack,
    PredictionStackRelease,
)
from elettra_core import (
    FEATURE_CONTRACT_VERSION,
    AuxiliaryEnergyComponents,
    CappedRegenAffineGreyBox,
    HybridGreyboxQRF,
    LinearGreyBox,
    PASSENGER_MASS_KG,
    __version__ as ELETTRA_CORE_VERSION,
    categorical_feature_contract,
    source_tree_sha256,
)
from elettra_core.vecto_templates import (
    VECTO_TEMPLATE_RELEASE,
    template_release_sha256,
)


MODEL = "greybox_qrf_production_core_v2_roaddeck_v3_3_20260828"
PREFIX = f"models/{MODEL}/"
ROADS_RELEASE = "swisstlm3d_2026-02-24"
ROADS_SHA256 = "6a2184d107b093ad7c8ea2ba9ff1cd2768c8a81dce7a5ff12e7bcd5711408a1d"
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


class _LegacyModelFixture:
    selected_features = ["total_distance_m"]
    n_features_in_ = 1
    estimators_ = ["fitted-tree"]

    def predict(self, frame):
        return [0.0] * len(frame)


class _QrfFixture:
    n_features_in_ = 1
    estimators_ = ["fitted-tree"]
    feature_names_in_ = np.asarray(["greybox_pred_kwh"])

    def predict(self, frame, quantiles=None):
        return [0.0] * len(frame)


class _Response:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def read(self, amount=None):
        if amount is None:
            result = self.data[self.offset :]
            self.offset = len(self.data)
            return result
        result = self.data[self.offset : self.offset + amount]
        self.offset += len(result)
        return result

    def close(self):
        pass

    def release_conn(self):
        pass


class _Client:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.revisions = {name: "v1" for name in objects}

    def stat_object(self, _bucket, object_name):
        if object_name not in self.objects:
            raise RuntimeError(f"NoSuchKey: {object_name}")
        return SimpleNamespace(
            size=len(self.objects[object_name]),
            etag=f"{object_name}-{self.revisions[object_name]}",
            version_id=self.revisions[object_name],
        )

    def get_object(self, _bucket, object_name):
        if object_name not in self.objects:
            raise RuntimeError(f"NoSuchKey: {object_name}")
        return _Response(self.objects[object_name])

    def replace(self, object_name, data):
        self.objects[object_name] = data
        self.revisions[object_name] = "v2"


@pytest.fixture(autouse=True)
def _reset_model_release_cache(monkeypatch):
    for name in (
        "_validated_model",
        "_validated_metadata",
        "_validated_metadata_sha256",
        "_validated_release_manifest",
        "_validated_release_manifest_sha256",
        "_validated_release_manifest_identity",
        "_validated_artifact_identities",
    ):
        monkeypatch.setattr(model_release, name, None)
    monkeypatch.setattr(model_release, "_validated_model_releases", {})


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "production-roaddeck-v3")
    monkeypatch.setenv("CONSUMPTION_MODEL_RELEASE", MODEL)
    monkeypatch.setenv("ELEVATION_AUX_PROFILE_ALGORITHM", ROAD_SNAP_V3_ALGORITHM)
    monkeypatch.setenv("ELEVATION_AUX_ROADS_RELEASE", ROADS_RELEASE)
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "mutable-aux")
    monkeypatch.setenv("GTFS_ELEVATION_PROFILES_BUCKET", "immutable-gtfs")


def _metadata():
    return {
        "model_name": MODEL,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "categorical_feature_contract": categorical_feature_contract(),
        "selected_features": ["total_distance_m"],
        "auxiliary_estimator": {"sha256": "f" * 64},
        "training_software": {"source_commit": "1" * 40, "dirty": False},
        "feature_release": {
            "release_id": "energy_v2_roaddeck_core_v2_r3",
            "manifest_sha256": "a" * 64,
            "row_identity_sha256": "b" * 64,
            "profiles": {
                "profile_release": "training-roaddeck-v3.3-r3",
                "profile_contract_version": 2,
                "road_snap": {"algorithm_version": ROAD_SNAP_V3_ALGORITHM},
                "roads_asset": {
                    "release_id": ROADS_RELEASE,
                    "sha256": ROADS_SHA256,
                },
            },
        },
    }


def _entry(data):
    return {"size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _release_objects():
    metadata = _metadata()
    acceptance = json.dumps({"schema_version": 1, "candidate_model": MODEL}).encode()
    model_buffer = io.BytesIO()
    joblib.dump(_LegacyModelFixture(), model_buffer)
    objects = {
        f"{PREFIX}{MODEL}.joblib": model_buffer.getvalue(),
        f"{PREFIX}{MODEL}_metadata.json": json.dumps(metadata, sort_keys=True).encode(),
        f"{PREFIX}{MODEL}_feature_importance.csv": b"feature,importance\ntotal_distance_m,1.0\n",
        f"{PREFIX}{MODEL}_acceptance.json": acceptance,
    }
    artifacts = {
        name.rsplit("/", 1)[-1]: _entry(data) for name, data in objects.items()
    }
    release = {
        "schema_version": 1,
        "release_id": MODEL,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "categorical_feature_contract": categorical_feature_contract(),
        "feature_release": {
            key: metadata["feature_release"][key]
            for key in ("release_id", "manifest_sha256", "row_identity_sha256")
        },
        "auxiliary_estimator": metadata["auxiliary_estimator"],
        "training_software": metadata["training_software"],
        "acceptance": {**_entry(acceptance), "decision": "passed"},
        "artifacts": artifacts,
        "publication": {
            "object_prefix": PREFIX,
            "manifest_last": True,
            "immutable": True,
        },
    }
    objects[f"{PREFIX}{MODEL}_release.json"] = json.dumps(
        release, sort_keys=True, separators=(",", ":")
    ).encode()
    return objects, release


def _vecto_release_objects(
    *,
    model_name: str,
    stack: PredictionStack,
    core_commit: str = "c" * 40,
    template_sha256: str | None = None,
    lookup_sha256: str | None = None,
):
    template_sha = template_sha256 or template_release_sha256()
    if stack is PredictionStack.VECTO_G2:
        contract = {
            "stack": stack.value,
            "deployment_tier": "production",
            "training_auxiliary_estimator": TRAINING_HVAC_ESTIMATOR,
            "inference_auxiliary_estimator": VECTO_HVAC_AUXILIARY_ESTIMATOR,
            "fixed_auxiliary_owner": "model",
            "auxiliary_contract": "vecto-hvac-only",
            "transfer_policy": VECTO_G2_TRANSFER_POLICY,
            "training_comfort_policy": TRAINING_COMFORT_POLICY,
            "vecto_template_release": VECTO_TEMPLATE_RELEASE,
            "vecto_template_sha256": template_sha,
        }
        greybox = CappedRegenAffineGreyBox()
        auxiliary_estimator = {
            "training": TRAINING_HVAC_ESTIMATOR,
            "inference": VECTO_HVAC_AUXILIARY_ESTIMATOR,
            "fixed_auxiliary_owner": "model",
            "auxiliary_contract": "vecto-hvac-only",
            "transfer_policy": VECTO_G2_TRANSFER_POLICY,
            "training_comfort_policy": TRAINING_COMFORT_POLICY,
            "vecto_template_release": VECTO_TEMPLATE_RELEASE,
            "vecto_template_sha256": template_sha,
        }
    else:
        contract = {
            "stack": stack.value,
            "deployment_tier": "experimental",
            "training_auxiliary_estimator": "data-driven-by-bus",
            "training_auxiliary_estimator_sha256": (
                lookup_sha256 or DATA_DRIVEN_AUXILIARY_LOOKUP_SHA256
            ),
            "inference_auxiliary_estimator": VECTO_COMPLETE_AUXILIARY_ESTIMATOR,
            "fixed_auxiliary_owner": "template",
            "auxiliary_contract": "vecto-complete",
            "vecto_template_release": VECTO_TEMPLATE_RELEASE,
            "vecto_template_sha256": template_sha,
        }
        greybox = LinearGreyBox()
        auxiliary_estimator = {
            "training": "data-driven-by-bus",
            "training_sha256": contract[
                "training_auxiliary_estimator_sha256"
            ],
            "inference": VECTO_COMPLETE_AUXILIARY_ESTIMATOR,
            "fixed_auxiliary_owner": "template",
            "auxiliary_contract": "vecto-complete",
            "vecto_template_release": VECTO_TEMPLATE_RELEASE,
            "vecto_template_sha256": template_sha,
        }
    greybox.theta_ = greybox.initial_bounds()[0]
    model = HybridGreyboxQRF(
        greybox=greybox,
        qrf=_QrfFixture(),
        selected_features=["greybox_pred_kwh"],
        prediction_stack=stack.value,
        qrf_reference_occupancy_percent=(
            21.5 if stack is PredictionStack.VECTO_G2 else None
        ),
    )
    core = {
        "package_version": ELETTRA_CORE_VERSION,
        "tag": f"elettra-core-v{ELETTRA_CORE_VERSION}",
        "source_commit": core_commit,
        "source_tree_sha256": source_tree_sha256(),
    }
    metadata = _metadata()
    metadata.update(
        {
            "model_name": model_name,
            "model_type": "HybridGreyboxQRF",
            "selected_features": ["greybox_pred_kwh"],
            "prediction_stack_contract": contract,
            "elettra_core": core,
            "auxiliary_estimator": auxiliary_estimator,
            "greybox_params": greybox.get_params_dict(),
        }
    )
    if stack is PredictionStack.VECTO_G2:
        metadata["passenger_prior"] = PASSENGER_PRIOR
    acceptance_value = {
        "schema_version": 1,
        "acceptance_type": "vecto-model-release-acceptance-v1",
        "release_id": model_name,
        "prediction_stack": stack.value,
        "deployment_tier": contract["deployment_tier"],
        "decision": "passed",
        "evaluation_manifest": {"sha256": "e" * 64},
        "test_set": {
            "source_row_identity_sha256": metadata["feature_release"][
                "row_identity_sha256"
            ]
        },
        "candidate": {
            "model_name": model_name,
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
            "feature_release_manifest_sha256": metadata["feature_release"][
                "manifest_sha256"
            ],
            "prediction_stack": stack.value,
            "deployment_tier": contract["deployment_tier"],
        },
    }
    acceptance = json.dumps(acceptance_value, sort_keys=True).encode()
    model_buffer = io.BytesIO()
    joblib.dump(model, model_buffer)
    prefix = f"models/{model_name}/"
    objects = {
        f"{prefix}{model_name}.joblib": model_buffer.getvalue(),
        f"{prefix}{model_name}_metadata.json": json.dumps(
            metadata, sort_keys=True
        ).encode(),
        f"{prefix}{model_name}_feature_importance.csv": (
            b"feature,importance\ngreybox_pred_kwh,1.0\n"
        ),
        f"{prefix}{model_name}_acceptance.json": acceptance,
    }
    artifacts = {
        name.rsplit("/", 1)[-1]: _entry(data) for name, data in objects.items()
    }
    acceptance_value["artifacts"] = {
        "model_sha256": artifacts[f"{model_name}.joblib"]["sha256"],
        "metadata_sha256": artifacts[f"{model_name}_metadata.json"]["sha256"],
        "feature_importance_sha256": artifacts[
            f"{model_name}_feature_importance.csv"
        ]["sha256"],
    }
    acceptance_value["candidate"].update(acceptance_value["artifacts"])
    acceptance = json.dumps(acceptance_value, sort_keys=True).encode()
    objects[f"{prefix}{model_name}_acceptance.json"] = acceptance
    artifacts[f"{model_name}_acceptance.json"] = _entry(acceptance)
    release = {
        "schema_version": 1,
        "release_id": model_name,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "categorical_feature_contract": categorical_feature_contract(),
        "feature_release": {
            key: metadata["feature_release"][key]
            for key in ("release_id", "manifest_sha256", "row_identity_sha256")
        },
        "prediction_stack_contract": contract,
        "elettra_core": core,
        "auxiliary_estimator": auxiliary_estimator,
        **(
            {"passenger_prior": PASSENGER_PRIOR}
            if stack is PredictionStack.VECTO_G2
            else {}
        ),
        "training_software": metadata["training_software"],
        "acceptance": {**_entry(acceptance), "decision": "passed"},
        "artifacts": artifacts,
        "publication": {
            "object_prefix": prefix,
            "manifest_last": True,
            "immutable": True,
        },
    }
    objects[f"{prefix}{model_name}_release.json"] = json.dumps(
        release, sort_keys=True, separators=(",", ":")
    ).encode()
    return objects


def _configure_vecto_registry(monkeypatch):
    monkeypatch.delenv("CONSUMPTION_MODEL_RELEASE", raising=False)
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "production-roaddeck-v3")
    monkeypatch.setenv("ELEVATION_AUX_PROFILE_ALGORITHM", ROAD_SNAP_V3_ALGORITHM)
    monkeypatch.setenv("ELEVATION_AUX_ROADS_RELEASE", ROADS_RELEASE)
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "mutable-aux")
    monkeypatch.setenv("GTFS_ELEVATION_PROFILES_BUCKET", "immutable-gtfs")
    monkeypatch.setenv("LEGACY_CONSUMPTION_MODEL_RELEASE", "legacy-release")
    monkeypatch.setenv("VECTO_G2_CONSUMPTION_MODEL_RELEASE", "g2-release")
    monkeypatch.setenv("VECTO_G0_TRANSFER_MODEL_RELEASE", "g0-release")
    monkeypatch.setenv("DEFAULT_PREDICTION_STACK", "legacy")
    monkeypatch.setenv("ENABLE_EXPERIMENTAL_PREDICTION_STACKS", "true")
    monkeypatch.setenv("ELETTRA_CORE_SOURCE_COMMIT", "c" * 40)
    monkeypatch.setenv("ELETTRA_CORE_IMAGE_COMMIT", "c" * 40)
    monkeypatch.setenv("ELETTRA_CORE_IMAGE_TREE_SHA256", source_tree_sha256())


def _elevation_manifest():
    return {"roads": {"release_id": ROADS_RELEASE, "sha256": ROADS_SHA256}}


def test_model_release_pins_manifest_and_all_artifacts(configured):
    objects, _release = _release_objects()
    client = _Client(objects)

    metadata = model_release.validate_configured_model_release(
        _elevation_manifest(), client=client
    )

    manifest_path = f"{PREFIX}{MODEL}_release.json"
    assert metadata["model_name"] == MODEL
    assert model_release.model_release_runtime_metadata() == {
        "bucket": "consumption-models",
        "model_release": MODEL,
        "release_manifest_sha256": hashlib.sha256(objects[manifest_path]).hexdigest(),
        "release_manifest_identity": {
            "size_bytes": len(objects[manifest_path]),
            "etag": f"{manifest_path}-v1",
            "version_id": "v1",
        },
        "metadata_sha256": hashlib.sha256(
            objects[f"{PREFIX}{MODEL}_metadata.json"]
        ).hexdigest(),
        "artifact_count": 4,
        "training_feature_release": "energy_v2_roaddeck_core_v2_r3",
        "training_profile_release": "training-roaddeck-v3.3-r3",
    }
    model_release.probe_configured_model_immutable(client=client)


def test_health_contract_exposes_vecto_mass_and_setpoint_policy():
    release = PredictionStackRelease(
        stack=PredictionStack.VECTO_G2,
        model_release="g2-release",
        auxiliary_estimator=VECTO_HVAC_AUXILIARY_ESTIMATOR,
        fixed_auxiliary_owner="model",
        deployment_tier="production",
    )
    contract = model_release._validated_runtime_contract(
        {
            "stack_release": release,
            "metadata": {
                "passenger_mass_kg": PASSENGER_MASS_KG,
                "passenger_prior": {
                    "qrf_reference_occupancy_percent": 21.5,
                },
                "prediction_stack_contract": {
                    "training_comfort_policy": TRAINING_COMFORT_POLICY,
                    "transfer_policy": VECTO_G2_TRANSFER_POLICY,
                },
            },
        }
    )
    assert contract == {
        "prediction_stack": "vecto-g2",
        "deployment_tier": "production",
        "passenger_mass_kg": 68.0,
        "qrf_reference_occupancy_percent": 21.5,
        "training_comfort_policy": TRAINING_COMFORT_POLICY,
        "transfer_policy": VECTO_G2_TRANSFER_POLICY,
    }


def test_health_contract_preserves_legacy_serialized_passenger_mass():
    release = PredictionStackRelease(
        stack=PredictionStack.LEGACY,
        model_release="legacy-release",
        auxiliary_estimator=LEGACY_AUXILIARY_ESTIMATOR,
        fixed_auxiliary_owner="legacy",
        deployment_tier="production",
    )
    contract = model_release._validated_runtime_contract(
        {
            "stack_release": release,
            "metadata": {
                "passenger_load_estimator": {
                    "config": {"passenger_weight_kg": 70.0}
                }
            },
        }
    )
    assert contract["passenger_mass_kg"] == 70.0
    assert contract["prediction_stack"] == "legacy"


def test_model_release_rejects_missing_commit_manifest(configured):
    objects, _release = _release_objects()
    del objects[f"{PREFIX}{MODEL}_release.json"]

    with pytest.raises(ModelReleaseValidationError, match="release.json"):
        model_release.validate_configured_model_release(
            _elevation_manifest(), client=_Client(objects)
        )


def test_model_release_rejects_partial_manifest(configured):
    objects, release = _release_objects()
    release["artifacts"].pop(f"{MODEL}_feature_importance.csv")
    objects[f"{PREFIX}{MODEL}_release.json"] = json.dumps(release).encode()

    with pytest.raises(ModelReleaseValidationError, match="exactly model"):
        model_release.validate_configured_model_release(
            _elevation_manifest(), client=_Client(objects)
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("schema_version", 2), ("release_id", "different-model")],
)
def test_model_release_rejects_wrong_schema_or_release_id(configured, field, value):
    objects, release = _release_objects()
    release[field] = value
    objects[f"{PREFIX}{MODEL}_release.json"] = json.dumps(release).encode()

    with pytest.raises(ModelReleaseValidationError, match="not publishable"):
        model_release.validate_configured_model_release(
            _elevation_manifest(), client=_Client(objects)
        )


def test_model_release_rejects_artifact_hash_mismatch(configured):
    objects, _release = _release_objects()
    model_path = f"{PREFIX}{MODEL}.joblib"
    original = objects[model_path]
    objects[model_path] = bytes([original[0] ^ 0x01]) + original[1:]

    with pytest.raises(ModelReleaseValidationError, match="integrity mismatch"):
        model_release.validate_configured_model_release(
            _elevation_manifest(), client=_Client(objects)
        )


def test_model_release_rejects_metadata_manifest_mismatch(configured):
    objects, release = _release_objects()
    release["auxiliary_estimator"] = {"sha256": "0" * 64}
    objects[f"{PREFIX}{MODEL}_release.json"] = json.dumps(release).encode()

    with pytest.raises(ModelReleaseValidationError, match="does not match"):
        model_release.validate_configured_model_release(
            _elevation_manifest(), client=_Client(objects)
        )


def test_model_release_rejects_undecodable_joblib(configured, monkeypatch):
    objects, _release = _release_objects()

    def fail_load(_source):
        raise ValueError("pickle fixture cannot load")

    monkeypatch.setattr(model_release.joblib, "load", fail_load)
    with pytest.raises(ModelReleaseValidationError, match="cannot be deserialized"):
        model_release.validate_configured_model_release(
            _elevation_manifest(), client=_Client(objects)
        )


def test_legacy_release_gate_rejects_deserializable_non_model():
    release = PredictionStackRelease(
        stack=PredictionStack.LEGACY,
        model_release=MODEL,
        auxiliary_estimator=LEGACY_AUXILIARY_ESTIMATOR,
        fixed_auxiliary_owner="legacy-curve",
        deployment_tier="production",
    )
    with pytest.raises(ModelReleaseValidationError, match="predict method"):
        model_release._validate_loaded_model_artifact(
            {"not": "a model"}, _metadata(), release
        )


@pytest.mark.parametrize(
    ("stack", "model_name"),
    [
        (PredictionStack.VECTO_G2, "g2-release"),
        (PredictionStack.VECTO_G0_TRANSFER, "g0-release"),
    ],
)
def test_vecto_release_passes_full_startup_preflight(
    monkeypatch, stack, model_name
):
    _configure_vecto_registry(monkeypatch)
    stack_release = model_release.runtime_release_configuration().prediction_stacks[
        stack
    ]
    objects = _vecto_release_objects(model_name=model_name, stack=stack)

    metadata = model_release._validate_one_model_release(
        _elevation_manifest(),
        model_name=model_name,
        stack_release=stack_release,
        client=_Client(objects),
    )
    loaded, loaded_metadata = model_release.get_validated_model_artifact(model_name)
    assert isinstance(loaded, HybridGreyboxQRF)
    assert loaded.prediction_stack == stack.value
    assert loaded_metadata is metadata


@pytest.mark.parametrize(
    ("stack", "kwargs", "message"),
    [
        (
            PredictionStack.VECTO_G2,
            {"core_commit": "d" * 40},
            "source commit",
        ),
        (
            PredictionStack.VECTO_G2,
            {"template_sha256": "0" * 64},
            "vecto_template_sha256",
        ),
        (
            PredictionStack.VECTO_G0_TRANSFER,
            {"lookup_sha256": "0" * 64},
            "training_auxiliary_estimator_sha256",
        ),
    ],
)
def test_vecto_full_preflight_rejects_wrong_provenance(
    monkeypatch, stack, kwargs, message
):
    _configure_vecto_registry(monkeypatch)
    model_name = (
        "g2-release"
        if stack is PredictionStack.VECTO_G2
        else "g0-release"
    )
    stack_release = model_release.runtime_release_configuration().prediction_stacks[
        stack
    ]
    objects = _vecto_release_objects(
        model_name=model_name, stack=stack, **kwargs
    )
    with pytest.raises(ModelReleaseValidationError, match=message):
        model_release._validate_one_model_release(
            _elevation_manifest(),
            model_name=model_name,
            stack_release=stack_release,
            client=_Client(objects),
        )


def test_vecto_controlled_regression_approval_binds_evaluation_hash(monkeypatch):
    _configure_vecto_registry(monkeypatch)
    model_name = "g2-release"
    stack_release = model_release.runtime_release_configuration().prediction_stacks[
        PredictionStack.VECTO_G2
    ]
    objects = _vecto_release_objects(
        model_name=model_name, stack=PredictionStack.VECTO_G2
    )
    acceptance_path = f"models/{model_name}/{model_name}_acceptance.json"
    manifest_path = f"models/{model_name}/{model_name}_release.json"
    release = json.loads(objects[manifest_path])
    acceptance = json.loads(objects[acceptance_path])
    acceptance["decision"] = "approved_with_documented_regression"
    objects[acceptance_path] = json.dumps(acceptance, sort_keys=True).encode()
    acceptance_entry = _entry(objects[acceptance_path])
    release["artifacts"][f"{model_name}_acceptance.json"] = acceptance_entry
    release["acceptance"].update(acceptance_entry)
    release["acceptance"]["decision"] = "approved_with_documented_regression"
    release["acceptance"]["documented_approval"] = {
        "approved_by": "release-owner",
        "approved_at": "2026-08-31T12:00:00Z",
        "reason": "Approved extrapolation trade-off",
        "evaluation_sha256": "0" * 64,
    }
    objects[manifest_path] = json.dumps(release).encode()
    with pytest.raises(ModelReleaseValidationError, match="acceptance evaluation"):
        model_release._validate_one_model_release(
            _elevation_manifest(),
            model_name=model_name,
            stack_release=stack_release,
            client=_Client(objects),
        )

    release["acceptance"]["documented_approval"]["evaluation_sha256"] = "e" * 64
    objects[manifest_path] = json.dumps(release).encode()
    assert model_release._validate_one_model_release(
        _elevation_manifest(),
        model_name=model_name,
        stack_release=stack_release,
        client=_Client(objects),
    )["model_name"] == model_name


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("decision", "approved_with_documented_regression"),
        ("release_id", "another-release"),
        ("prediction_stack", "vecto-g0-transfer"),
        ("deployment_tier", "experimental"),
    ],
)
def test_vecto_preflight_rejects_semantically_unbound_acceptance(
    monkeypatch, field, value
):
    _configure_vecto_registry(monkeypatch)
    model_name = "g2-release"
    stack_release = model_release.runtime_release_configuration().prediction_stacks[
        PredictionStack.VECTO_G2
    ]
    objects = _vecto_release_objects(
        model_name=model_name, stack=PredictionStack.VECTO_G2
    )
    prefix = f"models/{model_name}/"
    acceptance_path = f"{prefix}{model_name}_acceptance.json"
    manifest_path = f"{prefix}{model_name}_release.json"
    acceptance = json.loads(objects[acceptance_path])
    acceptance[field] = value
    objects[acceptance_path] = json.dumps(acceptance, sort_keys=True).encode()
    release = json.loads(objects[manifest_path])
    entry = _entry(objects[acceptance_path])
    release["artifacts"][f"{model_name}_acceptance.json"] = entry
    release["acceptance"].update(entry)
    objects[manifest_path] = json.dumps(release).encode()

    with pytest.raises(ModelReleaseValidationError, match="acceptance artifact"):
        model_release._validate_one_model_release(
            _elevation_manifest(),
            model_name=model_name,
            stack_release=stack_release,
            client=_Client(objects),
        )


def test_vecto_preflight_reports_malformed_feature_release(monkeypatch):
    _configure_vecto_registry(monkeypatch)
    model_name = "g2-release"
    stack_release = model_release.runtime_release_configuration().prediction_stacks[
        PredictionStack.VECTO_G2
    ]
    objects = _vecto_release_objects(
        model_name=model_name, stack=PredictionStack.VECTO_G2
    )
    prefix = f"models/{model_name}/"
    metadata_path = f"{prefix}{model_name}_metadata.json"
    manifest_path = f"{prefix}{model_name}_release.json"
    metadata = json.loads(objects[metadata_path])
    metadata["feature_release"] = None
    objects[metadata_path] = json.dumps(metadata).encode()
    release = json.loads(objects[manifest_path])
    release["artifacts"][metadata_path.rsplit("/", 1)[-1]] = _entry(
        objects[metadata_path]
    )
    objects[manifest_path] = json.dumps(release).encode()

    with pytest.raises(ModelReleaseValidationError, match="feature_release"):
        model_release._validate_one_model_release(
            _elevation_manifest(),
            model_name=model_name,
            stack_release=stack_release,
            client=_Client(objects),
        )


def test_controlled_regression_requires_immutable_approval(configured):
    objects, release = _release_objects()
    release["acceptance"]["decision"] = "approved_with_documented_regression"
    manifest_path = f"{PREFIX}{MODEL}_release.json"
    objects[manifest_path] = json.dumps(release).encode()
    with pytest.raises(ModelReleaseValidationError, match="documented approval"):
        model_release.validate_configured_model_release(
            _elevation_manifest(), client=_Client(objects)
        )

    release["acceptance"]["documented_approval"] = {
        "approved_by": "release-owner",
        "approved_at": "2026-08-31T12:00:00Z",
        "reason": "Approved documented extrapolation trade-off",
        "evaluation_sha256": "e" * 64,
    }
    objects, _ = _release_objects()
    objects[manifest_path] = json.dumps(release).encode()
    metadata = model_release.validate_configured_model_release(
        _elevation_manifest(), client=_Client(objects)
    )
    assert metadata["model_name"] == MODEL


def test_model_release_detects_manifest_replacement_after_startup(configured):
    objects, release = _release_objects()
    client = _Client(objects)
    model_release.validate_configured_model_release(_elevation_manifest(), client=client)
    manifest_path = f"{PREFIX}{MODEL}_release.json"
    release["created_at"] = "changed"
    client.replace(manifest_path, json.dumps(release).encode())

    with pytest.raises(ModelReleaseValidationError, match="manifest changed"):
        model_release.probe_configured_model_immutable(client=client)


def test_model_release_detects_artifact_replacement_after_startup(configured):
    objects, _release = _release_objects()
    client = _Client(objects)
    model_release.validate_configured_model_release(_elevation_manifest(), client=client)
    importance_path = f"{PREFIX}{MODEL}_feature_importance.csv"
    client.replace(importance_path, objects[importance_path])

    with pytest.raises(ModelReleaseValidationError, match="artifact changed"):
        model_release.probe_configured_model_immutable(client=client)


def test_prediction_binding_uses_validated_bytes_not_replaced_minio_object(configured):
    objects, _release = _release_objects()
    client = _Client(objects)
    model_release.validate_configured_model_release(
        _elevation_manifest(), client=client
    )
    model_path = f"{PREFIX}{MODEL}.joblib"
    client.replace(model_path, b"attacker-controlled replacement")

    loaded, metadata = model_release.get_validated_model_artifact(MODEL)

    assert isinstance(loaded, _LegacyModelFixture)
    assert metadata["model_name"] == MODEL
    with pytest.raises(ModelReleaseValidationError, match="artifact changed"):
        model_release.probe_configured_model_immutable(client=client)


def test_registry_validates_and_probes_every_configured_model(monkeypatch):
    legacy = PredictionStackRelease(
        stack=PredictionStack.LEGACY,
        model_release="legacy-release",
        auxiliary_estimator=LEGACY_AUXILIARY_ESTIMATOR,
        fixed_auxiliary_owner="legacy-curve",
        deployment_tier="production",
    )
    g2 = PredictionStackRelease(
        stack=PredictionStack.VECTO_G2,
        model_release="g2-release",
        auxiliary_estimator=VECTO_HVAC_AUXILIARY_ESTIMATOR,
        fixed_auxiliary_owner="model",
        deployment_tier="production",
    )
    runtime = SimpleNamespace(
        consumption_model_release=legacy.model_release,
        prediction_stacks={
            PredictionStack.LEGACY: legacy,
            PredictionStack.VECTO_G2: g2,
        },
        default_prediction_stack=PredictionStack.LEGACY,
    )
    monkeypatch.setattr(model_release, "runtime_release_configuration", lambda: runtime)
    calls = []

    def validate_one(_manifest, *, model_name, stack_release, client=None):
        calls.append((model_name, stack_release.stack))
        return {"model_name": model_name}

    monkeypatch.setattr(model_release, "_validate_one_model_release", validate_one)
    result = model_release.validate_configured_model_release({}, client=object())
    assert calls == [
        ("g2-release", PredictionStack.VECTO_G2),
        ("legacy-release", PredictionStack.LEGACY),
    ]
    assert result == {"model_name": "legacy-release"}

    objects = {}
    validated = {}
    for name in ("g2-release", "legacy-release"):
        paths = model_release._artifact_paths(name)
        objects[paths["manifest"]] = f"manifest-{name}".encode()
        artifact_path = paths["model"]
        objects[artifact_path] = f"artifact-{name}".encode()
    client = _Client(objects)
    for name in ("g2-release", "legacy-release"):
        paths = model_release._artifact_paths(name)
        manifest_raw = objects[paths["manifest"]]
        validated[name] = {
            "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "manifest_identity": model_release._object_identity(
                client, model_release.MODEL_BUCKET, paths["manifest"]
            ),
            "artifact_identities": {
                paths["model"]: model_release._object_identity(
                    client, model_release.MODEL_BUCKET, paths["model"]
                )
            },
        }
    monkeypatch.setattr(model_release, "_validated_model_releases", validated)
    model_release.probe_configured_model_immutable(client=client)
    client.replace(model_release._artifact_paths("g2-release")["model"], objects[model_release._artifact_paths("g2-release")["model"]])
    with pytest.raises(ModelReleaseValidationError, match="g2-release"):
        model_release.probe_configured_model_immutable(client=client)
