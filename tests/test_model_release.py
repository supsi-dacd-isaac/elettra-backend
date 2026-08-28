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
ROADS_RELEASE = "swisstlm3d_2026-02-24"
ROADS_SHA256 = "6a2184d107b093ad7c8ea2ba9ff1cd2768c8a81dce7a5ff12e7bcd5711408a1d"


class _Response:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def close(self):
        pass

    def release_conn(self):
        pass


class _Client:
    def __init__(self, metadata):
        self.metadata_bytes = json.dumps(metadata, sort_keys=True).encode()
        self.revision = "v1"

    def stat_object(self, _bucket, object_name):
        size = 123 if object_name.endswith(".joblib") else len(self.metadata_bytes)
        return SimpleNamespace(size=size, etag=f"{object_name}-{self.revision}", version_id="1")

    def get_object(self, _bucket, object_name):
        assert object_name.endswith("_metadata.json")
        return _Response(self.metadata_bytes)


@pytest.fixture(autouse=True)
def _reset_model_release_cache(monkeypatch):
    for name in (
        "_validated_model",
        "_validated_metadata",
        "_validated_metadata_sha256",
        "_validated_model_identity",
        "_validated_metadata_identity",
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
        "feature_release": {
            "release_id": "energy_v2_roaddeck_core_v2_r3",
            "manifest_sha256": "a" * 64,
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


def _elevation_manifest():
    return {
        "roads": {"release_id": ROADS_RELEASE, "sha256": ROADS_SHA256}
    }


def test_model_release_pins_contract_and_training_provenance(configured):
    client = _Client(_metadata())

    metadata = model_release.validate_configured_model_release(
        _elevation_manifest(), client=client
    )

    assert metadata["model_name"] == MODEL
    assert model_release.model_release_runtime_metadata() == {
        "bucket": "consumption-models",
        "model_release": MODEL,
        "metadata_sha256": hashlib.sha256(client.metadata_bytes).hexdigest(),
        "training_feature_release": "energy_v2_roaddeck_core_v2_r3",
        "training_profile_release": "training-roaddeck-v3.3-r3",
    }
    model_release.probe_configured_model_immutable(client=client)


def test_model_release_rejects_different_roads_asset(configured):
    metadata = _metadata()
    metadata["feature_release"]["profiles"]["roads_asset"]["release_id"] = "old-roads"

    with pytest.raises(ModelReleaseValidationError, match="road-snap v3.3"):
        model_release.validate_configured_model_release(
            _elevation_manifest(), client=_Client(metadata)
        )


def test_model_release_detects_object_replacement_after_startup(configured):
    client = _Client(_metadata())
    model_release.validate_configured_model_release(_elevation_manifest(), client=client)
    client.revision = "v2"

    with pytest.raises(ModelReleaseValidationError, match="changed after startup"):
        model_release.probe_configured_model_immutable(client=client)
