from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import elevation_profiles
from app.services.elevation_profiles import (
    ElevationProfileFormatError,
    ElevationProfileNotReadyError,
)
from app.services.runtime_release import (
    ROAD_SNAP_V3_ALGORITHM,
    RuntimeReleaseConfigurationError,
    default_prediction_model_name,
    enforce_configured_model,
    runtime_release_configuration,
)
from simulation.consumption_prediction import validate_model_feature_contract


PRODUCTION_MODEL = "greybox_qrf_production_core_v2_roaddeck_v3_3_20260828"
ROADS_RELEASE = "swisstlm3d_2026-02-24"


def _configure_v2(monkeypatch):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "roaddeck-v3.3-test")
    monkeypatch.setenv("CONSUMPTION_MODEL_RELEASE", PRODUCTION_MODEL)
    monkeypatch.setenv("ELEVATION_AUX_PROFILE_ALGORITHM", ROAD_SNAP_V3_ALGORITHM)
    monkeypatch.setenv("ELEVATION_AUX_ROADS_RELEASE", ROADS_RELEASE)
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "mutable-aux")
    monkeypatch.setenv("GTFS_ELEVATION_PROFILES_BUCKET", "immutable-gtfs")


def test_release_and_model_are_an_atomic_configuration(monkeypatch):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "roaddeck-v3.3-test")
    monkeypatch.delenv("CONSUMPTION_MODEL_RELEASE", raising=False)

    with pytest.raises(RuntimeReleaseConfigurationError, match="configured or removed together"):
        runtime_release_configuration()


def test_v2_switch_requires_exact_aux_provenance_pins(monkeypatch):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "roaddeck-v3.3-test")
    monkeypatch.setenv("CONSUMPTION_MODEL_RELEASE", PRODUCTION_MODEL)
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "mutable-aux")
    monkeypatch.setenv("GTFS_ELEVATION_PROFILES_BUCKET", "immutable-gtfs")
    monkeypatch.setenv("ELEVATION_AUX_PROFILE_ALGORITHM", "road-snap-v1")
    monkeypatch.setenv("ELEVATION_AUX_ROADS_RELEASE", ROADS_RELEASE)

    with pytest.raises(RuntimeReleaseConfigurationError, match="road-snap-v3.3"):
        runtime_release_configuration()


def test_v2_switch_requires_an_isolated_gtfs_bucket(monkeypatch):
    _configure_v2(monkeypatch)
    monkeypatch.setenv("GTFS_ELEVATION_PROFILES_BUCKET", "mutable-aux")

    with pytest.raises(RuntimeReleaseConfigurationError, match="dedicated"):
        runtime_release_configuration()


def test_aux_provenance_pins_are_atomic_in_compatibility_mode(monkeypatch):
    monkeypatch.delenv("ELEVATION_PROFILES_RELEASE", raising=False)
    monkeypatch.delenv("CONSUMPTION_MODEL_RELEASE", raising=False)
    monkeypatch.setenv("ELEVATION_AUX_PROFILE_ALGORITHM", ROAD_SNAP_V3_ALGORITHM)
    monkeypatch.delenv("ELEVATION_AUX_ROADS_RELEASE", raising=False)

    with pytest.raises(RuntimeReleaseConfigurationError, match="configured or removed together"):
        runtime_release_configuration()


def test_active_model_pin_cannot_be_bypassed(monkeypatch):
    _configure_v2(monkeypatch)
    assert enforce_configured_model(PRODUCTION_MODEL) == PRODUCTION_MODEL
    with pytest.raises(RuntimeReleaseConfigurationError, match="pinned"):
        enforce_configured_model("legacy-model")


def test_request_default_tracks_the_atomic_model_pin(monkeypatch):
    _configure_v2(monkeypatch)
    assert default_prediction_model_name() == PRODUCTION_MODEL


def test_v2_switch_rejects_model_without_contract_metadata(monkeypatch):
    _configure_v2(monkeypatch)
    with pytest.raises(ValueError, match="requires model metadata"):
        validate_model_feature_contract({"selected_features": ["total_distance_m"]})


def test_gtfs_and_aux_buckets_are_independent(monkeypatch):
    monkeypatch.setenv("GTFS_ELEVATION_PROFILES_BUCKET", "immutable-gtfs")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "mutable-aux")
    assert elevation_profiles.gtfs_elevation_profiles_bucket() == "immutable-gtfs"
    assert elevation_profiles.elevation_profiles_bucket() == "mutable-aux"


def test_production_profile_contract_uses_the_same_roads_pin(monkeypatch):
    _configure_v2(monkeypatch)
    manifest = {
        "algorithm_version": ROAD_SNAP_V3_ALGORITHM,
        "profile_contract_version": 2,
        "roads": {"release_id": ROADS_RELEASE},
    }
    elevation_profiles.validate_production_profile_contract(manifest)
    manifest["roads"]["release_id"] = "different-roads"
    with pytest.raises(ElevationProfileFormatError, match="pinned roads release"):
        elevation_profiles.validate_production_profile_contract(manifest)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value


class _JobSession:
    def __init__(self, job):
        self.job = job

    async def execute(self, _statement):
        return _ScalarResult(self.job)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("algorithm", "roads_release", "message"),
    [
        ("road-snap-v1", ROADS_RELEASE, "algorithm"),
        (ROAD_SNAP_V3_ALGORITHM, "old-roads", "roads release"),
    ],
)
async def test_aux_profile_must_match_active_provenance(
    monkeypatch, algorithm, roads_release, message
):
    _configure_v2(monkeypatch)
    trip = SimpleNamespace(id=uuid4(), shape_id="aux-shape", status="depot")
    job = SimpleNamespace(
        status="succeeded",
        last_error=None,
        output_object_name="aux-shape.parquet",
        algorithm_version=algorithm,
        roads_release=roads_release,
    )

    with pytest.raises(ElevationProfileNotReadyError, match="not ready") as caught:
        await elevation_profiles.resolve_trip_profile_location(_JobSession(job), trip)
    assert caught.value.status == "incompatible"
    assert message in str(caught.value.last_error)


@pytest.mark.asyncio
async def test_aux_and_gtfs_resolve_to_separate_buckets(monkeypatch):
    _configure_v2(monkeypatch)
    monkeypatch.setenv("GTFS_ELEVATION_PROFILES_BUCKET", "immutable-gtfs")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "mutable-aux")
    gtfs_trip = SimpleNamespace(id=uuid4(), shape_id="gtfs-shape", status="gtfs")
    aux_trip = SimpleNamespace(id=uuid4(), shape_id="aux-shape", status="depot")
    job = SimpleNamespace(
        status="succeeded",
        last_error=None,
        output_object_name="aux-shape.parquet",
        algorithm_version=ROAD_SNAP_V3_ALGORITHM,
        roads_release=ROADS_RELEASE,
    )

    gtfs_location = await elevation_profiles.resolve_trip_profile_location(
        _JobSession(None), gtfs_trip
    )
    aux_location = await elevation_profiles.resolve_trip_profile_location(
        _JobSession(job), aux_trip
    )

    assert gtfs_location.bucket == "immutable-gtfs"
    assert gtfs_location.object_name.startswith("releases/roaddeck-v3.3-test/")
    assert aux_location.bucket == "mutable-aux"
    assert aux_location.object_name == "aux-shape.parquet"
