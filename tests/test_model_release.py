from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.services import model_release
from app.services.model_release import ModelReleaseValidationError
from app.services.runtime_release import ROAD_SNAP_V3_ALGORITHM
from elettra_core import FEATURE_CONTRACT_VERSION, categorical_feature_contract


MODEL = "greybox_qrf_production_core_v2_roaddeck_v3_3_20260828"
PREFIX = f"models/{MODEL}/"
ROADS_RELEASE = "swisstlm3d_2026-02-24"
ROADS_SHA256 = "6a2184d107b093ad7c8ea2ba9ff1cd2768c8a81dce7a5ff12e7bcd5711408a1d"


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
    objects = {
        f"{PREFIX}{MODEL}.joblib": b"joblib-fixture",
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
    objects[model_path] = b"tampered-bytes"

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
