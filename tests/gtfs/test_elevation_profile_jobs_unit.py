from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pandas as pd
import pytest
from fastapi import HTTPException

from app.models import (
    ElevationProfileCleanupJobs,
    ElevationProfileJobs,
    GtfsCalendar,
    GtfsRoutes,
    GtfsStops,
    GtfsStopsTimes,
    GtfsTrips,
)
from app.routers import gtfs as gtfs_router
from app.routers.gtfs import (
    create_aux_trip,
    create_trip,
    delete_trip,
    get_elevation_profile_by_trip,
    router,
    update_trip,
)
from app.schemas.database import GtfsTripsCreate, GtfsTripsUpdate
from app.schemas.requests import AuxTripCreate
from app.services import elevation_profiles
from app.services.elevation_profiles import (
    ElevationProfileFormatError,
    ElevationProfileNotFoundError,
    ElevationProfileNotReadyError,
    ElevationProfileStorageError,
    dataframe_json_records,
    ensure_shift_profiles_ready,
    gtfs_profile_object_name,
    load_trip_elevation_dataframe,
    resolve_trip_profile_location,
    validate_configured_release,
    validate_release_covers_database,
)


class FakeMinioResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def close(self):
        pass

    def release_conn(self):
        pass


def release_entry(
    shape_id: str,
    *,
    data: bytes = b"x",
    row_count: int = 1,
) -> dict:
    return {
        "shape_id": shape_id,
        "object_name": f"{shape_id}.parquet",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "row_count": row_count,
        "road_deck_points": row_count,
        "dtm_fallback_points": 0,
        "profile_manifest_sha256": "b" * 64,
        "route_variant_sha256": "c" * 64,
        "route_type": 3,
        "metrics": {
            "total_points": row_count,
            "matched_points": row_count,
            "fallback_points": 0,
        },
    }


def release_manifest(release_id: str, entries: list[dict]) -> dict:
    point_count = sum(entry["row_count"] for entry in entries)
    matched = sum(entry["road_deck_points"] for entry in entries)
    fallback = sum(entry["dtm_fallback_points"] for entry in entries)
    roads = {
        "schema_version": 2,
        "release_id": "roads-2026",
        "source_url": "https://example.invalid/roads.gpkg.zip",
        "layer_name": "roads",
        "crs": "EPSG:2056",
        "vertical_datum": "LN02",
        "has_z": True,
        "feature_count": 1,
        "z_validation": "full",
        "validator_version": "pyriadne-road-asset-v1",
        "size_bytes": 100,
        "sha256": "a" * 64,
    }
    return {
        "schema_version": 1,
        "release_id": release_id,
        "state": "complete",
        "gtfs": {"sha256": "d" * 64},
        "elevation_mode": "road-deck",
        "algorithm_version": "road-snap-v1",
        "matcher": {"algorithm_version": "road-snap-v1"},
        "sampling_distance_m": 10.0,
        "dtm_provider": "swisstopo-height-v1",
        "roads": roads,
        "profile_count": len(entries),
        "point_count": point_count,
        "road_deck_points": matched,
        "dtm_fallback_points": fallback,
        "profiles": entries,
        "metrics": {
            "profile_release": release_id,
            "algorithm_version": "road-snap-v1",
            "roads_release": roads["release_id"],
            "roads_sha256": roads["sha256"],
            "shape_count": len(entries),
            "total_points": point_count,
            "matched_points": matched,
            "fallback_points": fallback,
        },
    }


def v3_release_manifest(release_id: str, entries: list[dict]) -> dict:
    manifest = release_manifest(release_id, entries)
    manifest["profile_contract_version"] = 2
    manifest["algorithm_version"] = "road-snap-v3.3-topology"
    manifest["matcher"] = {
        "algorithm_version": "road-snap-v3.3-topology",
        "tolerance_m": 15.0,
        "bbox_buffer_m": 50.0,
        "k_candidates": 5,
        "w_xy": 1.0,
        "w_grade": 1.0,
        "max_grade_pct": 12.0,
        "hard_grade_pct": 30.0,
        "min_grade_run_m": 1.0,
        "w_switch": 2.0,
        "fallback_emission": 25.0,
        "recovery_tolerance_m": 25.0,
        "recovery_k_candidates": 10,
        "max_gap_samples": 5,
        "topology_path_ratio": 4.0,
        "topology_path_slack_m": 20.0,
        "w_topology": 0.25,
        "conditional_road_penalty": 20.0,
        "bus_compatibility_policy": "swisstlm3d-bus-v1",
        "candidate_selection_policy": "topology-strata-v1",
        "topology_stitch_policy": "same-structure-aligned-endpoint-v2",
        "topology_stitch_max_3d_gap_m": 2.0,
        "topology_stitch_max_vertical_gap_m": 0.5,
        "topology_stitch_max_alignment_angle_deg": 45.0,
        "topology_node_precision": "XYZ millimetre",
        "observed_distance_lower_bound": "max(declared_chainage_step,lv95_chord)",
        "observed_distance_epsilon_m": 0.01,
        "runtime_versions": {
            "python": "3.12.0",
            "packages": {"shapely": "2.1.1"},
            "geos": "3.13.1",
            "proj": "9.6.0",
            "gdal": "3.10.3",
        },
    }
    manifest["roads"]["required_attributes"] = [
        "befahrbarkeit",
        "kunstbaute",
        "objektart",
        "richtungsgetrennt",
        "stufe",
        "uuid",
    ]
    for entry in entries:
        matched = entry["road_deck_points"]
        entry["metrics"].update(
            {
                "matched_with_road_uuid": matched,
                "distinct_road_uuids": 1 if matched else 0,
                "recovery_ring_points": 0,
                "conditional_bus_tier_points": 0,
                "matched_by_objektart": {"4m Strasse": matched} if matched else {},
            }
        )
    manifest["metrics"].update(
        {
            "algorithm_version": "road-snap-v3.3-topology",
            "matched_with_road_uuid": manifest["road_deck_points"],
            "recovery_ring_points": 0,
            "conditional_bus_tier_points": 0,
        }
    )
    return manifest


def reset_release_cache(monkeypatch) -> None:
    for name in (
        "_validated_release",
        "_validated_release_manifest",
        "_validated_release_profiles",
        "_validated_release_shape_ids",
        "_validated_release_digest",
        "_validated_release_object_identity",
    ):
        monkeypatch.setattr(elevation_profiles, name, None)


def v3_profile_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "latitude": [46.0, 46.1],
            "longitude": [7.0, 7.1],
            "altitude_m": [504.0, 501.0],
            "terrain_altitude_m": [500.0, 501.0],
            "road_deck_altitude_m": [504.0, None],
            "road_snap_latitude": [46.00001, None],
            "road_snap_longitude": [7.00001, None],
            "road_snap_distance_m": [5.0, None],
            "road_objektart": ["4m Strasse", None],
            "road_uuid": ["{00000000-0000-0000-0000-000000000001}", None],
            "elevation_delta_m": [4.0, None],
            "elevation_source": ["road_deck", "dtm_fallback"],
        }
    )


class FakeMinio:
    def __init__(self, objects: dict[tuple[str, str], bytes]):
        self.objects = objects
        self.reads: list[tuple[str, str]] = []

    def get_object(self, bucket: str, object_name: str):
        self.reads.append((bucket, object_name))
        return FakeMinioResponse(self.objects[(bucket, object_name)])


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value

    def all(self):
        return list(self.value) if isinstance(self.value, list) else [self.value]


class FakeJobSession:
    def __init__(self, job):
        self.job = job

    async def execute(self, _statement):
        return FakeScalarResult(self.job)


def test_gtfs_release_key_has_no_legacy_fallback(monkeypatch):
    monkeypatch.delenv("ELEVATION_PROFILES_RELEASE", raising=False)
    assert gtfs_profile_object_name("shape-1") == "shape-1.parquet"

    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "roads-2026.08-v1")
    assert (
        gtfs_profile_object_name("shape-1")
        == "releases/roads-2026.08-v1/shape-1.parquet"
    )
    with pytest.raises(ElevationProfileFormatError):
        gtfs_profile_object_name("shape-1", "../escape")


def test_release_manifest_must_be_complete(monkeypatch):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "release-1")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    monkeypatch.setattr(elevation_profiles, "_validated_release", None)
    monkeypatch.setattr(elevation_profiles, "_validated_release_manifest", None)
    client = FakeMinio(
        {
            ("profiles", "releases/release-1/release.json"): (
                b'{"schema_version": 1, "release_id": "release-1", "state": "uploading"}'
            )
        }
    )

    with pytest.raises(ElevationProfileFormatError, match="not publishable"):
        validate_configured_release(client)


def test_release_manifest_rejects_non_road_deck_contract(monkeypatch):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "release-1")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    monkeypatch.setattr(elevation_profiles, "_validated_release", None)
    monkeypatch.setattr(elevation_profiles, "_validated_release_manifest", None)
    monkeypatch.setattr(elevation_profiles, "_validated_release_digest", None)
    manifest = release_manifest("release-1", [release_entry("shape-1")])
    manifest["elevation_mode"] = "dtm"
    client = FakeMinio(
        {
            ("profiles", "releases/release-1/release.json"): json.dumps(manifest).encode()
        }
    )

    with pytest.raises(ElevationProfileFormatError, match="matcher configuration"):
        validate_configured_release(client, use_cache=False)


def test_v3_release_manifest_accepts_complete_profile_contract(monkeypatch):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "release-v3")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    reset_release_cache(monkeypatch)
    manifest = v3_release_manifest(
        "release-v3", [release_entry("shape-1", row_count=2)]
    )
    client = FakeMinio(
        {
            ("profiles", "releases/release-v3/release.json"): json.dumps(
                manifest
            ).encode()
        }
    )

    assert manifest["algorithm_version"] == "road-snap-v3.3-topology"
    assert (
        manifest["matcher"]["candidate_selection_policy"]
        == "topology-strata-v1"
    )
    assert manifest["matcher"]["topology_stitch_policy"] == (
        "same-structure-aligned-endpoint-v2"
    )
    assert manifest["matcher"]["topology_stitch_max_3d_gap_m"] == 2.0
    assert manifest["matcher"]["topology_stitch_max_vertical_gap_m"] == 0.5
    assert manifest["matcher"]["topology_stitch_max_alignment_angle_deg"] == 45.0
    assert validate_configured_release(client, use_cache=False) == manifest


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda manifest: manifest.pop("profile_contract_version"),
            "profile_contract_version=2",
        ),
        (
            lambda manifest: manifest["matcher"].update(
                {"bus_compatibility_policy": "unknown-policy"}
            ),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["matcher"].pop(
                "candidate_selection_policy"
            ),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["matcher"].update(
                {"candidate_selection_policy": "nearest-only-v1"}
            ),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["matcher"].pop("topology_stitch_policy"),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["matcher"].update(
                {"topology_stitch_policy": "xy-nearest-v1"}
            ),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["matcher"].pop(
                "topology_stitch_max_3d_gap_m"
            ),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["matcher"].pop(
                "topology_stitch_max_vertical_gap_m"
            ),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["matcher"].pop(
                "topology_stitch_max_alignment_angle_deg"
            ),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["matcher"].update(
                {"topology_stitch_max_3d_gap_m": float("inf")}
            ),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["matcher"].update(
                {"topology_stitch_max_vertical_gap_m": 0.0}
            ),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["matcher"].update(
                {"topology_stitch_max_alignment_angle_deg": 45.001}
            ),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["matcher"].update(
                {"observed_distance_epsilon_m": 0.1}
            ),
            "topology configuration",
        ),
        (
            lambda manifest: manifest["roads"].pop("required_attributes"),
            "topology attributes",
        ),
        (
            lambda manifest: manifest["profiles"][0]["metrics"].update(
                {"matched_with_road_uuid": 0}
            ),
            "v3 road metrics",
        ),
        (
            lambda manifest: manifest["metrics"].update(
                {"recovery_ring_points": 1}
            ),
            "aggregate v3 road metrics",
        ),
    ],
)
def test_v3_release_manifest_fails_closed(monkeypatch, mutation, message):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "release-v3")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    reset_release_cache(monkeypatch)
    manifest = v3_release_manifest(
        "release-v3", [release_entry("shape-1", row_count=2)]
    )
    mutation(manifest)
    client = FakeMinio(
        {
            ("profiles", "releases/release-v3/release.json"): json.dumps(
                manifest
            ).encode()
        }
    )

    with pytest.raises(ElevationProfileFormatError, match=message):
        validate_configured_release(client, use_cache=False)


@pytest.mark.parametrize("algorithm", ["road-snap-v1", "road-snap-v2-grade-safe"])
def test_legacy_v1_v2_release_contract_remains_supported(monkeypatch, algorithm):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "release-legacy")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    reset_release_cache(monkeypatch)
    manifest = release_manifest("release-legacy", [release_entry("shape-1")])
    manifest["algorithm_version"] = algorithm
    manifest["matcher"]["algorithm_version"] = algorithm
    manifest["metrics"]["algorithm_version"] = algorithm
    client = FakeMinio(
        {
            ("profiles", "releases/release-legacy/release.json"): json.dumps(
                manifest
            ).encode()
        }
    )

    assert validate_configured_release(client, use_cache=False) == manifest


@pytest.mark.parametrize(
    "algorithm",
    [
        "experimental-snap",
        "road-snap-v3-topology",
        "road-snap-v3.1-topology",
    ],
)
def test_release_manifest_rejects_unknown_algorithm(monkeypatch, algorithm):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "release-unknown")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    reset_release_cache(monkeypatch)
    manifest = release_manifest("release-unknown", [release_entry("shape-1")])
    manifest["algorithm_version"] = algorithm
    manifest["matcher"]["algorithm_version"] = algorithm
    manifest["metrics"]["algorithm_version"] = algorithm
    client = FakeMinio(
        {
            (
                "profiles",
                "releases/release-unknown/release.json",
            ): json.dumps(manifest).encode()
        }
    )

    with pytest.raises(ElevationProfileFormatError, match="Unsupported.*algorithm"):
        validate_configured_release(client, use_cache=False)


def test_release_id_is_digest_pinned_for_process_lifetime(monkeypatch):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "release-1")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    for name in (
        "_validated_release",
        "_validated_release_manifest",
        "_validated_release_profiles",
        "_validated_release_shape_ids",
        "_validated_release_digest",
        "_validated_release_object_identity",
    ):
        monkeypatch.setattr(elevation_profiles, name, None)
    key = ("profiles", "releases/release-1/release.json")
    manifest = release_manifest("release-1", [release_entry("shape-1")])
    client = FakeMinio({key: json.dumps(manifest, sort_keys=True).encode()})
    validate_configured_release(client, use_cache=False)
    manifest["created_at"] = "mutated-under-same-id"
    client.objects[key] = json.dumps(manifest, sort_keys=True).encode()

    with pytest.raises(ElevationProfileFormatError, match="manifest changed"):
        validate_configured_release(client, use_cache=False)


@pytest.mark.asyncio
async def test_release_loader_reads_manifest_then_only_release_object(monkeypatch):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "release-1")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    monkeypatch.setattr(elevation_profiles, "_validated_release", None)
    monkeypatch.setattr(elevation_profiles, "_validated_release_manifest", None)
    client = FakeMinio(
        {
            ("profiles", "releases/release-1/release.json"): (
                json.dumps(
                    release_manifest(
                        "release-1", [release_entry("shape-1", data=b"parquet")]
                    )
                ).encode()
            ),
            ("profiles", "releases/release-1/shape-1.parquet"): b"parquet",
        }
    )
    monkeypatch.setattr(
        elevation_profiles.pd,
        "read_parquet",
        lambda _stream: pd.DataFrame(
            [{"latitude": 46.0, "longitude": 7.0, "altitude_m": 500.0}]
        ),
    )
    trip = SimpleNamespace(id=uuid4(), status="gtfs", shape_id="shape-1")

    dataframe = await load_trip_elevation_dataframe(SimpleNamespace(), trip, client=client)

    assert dataframe["altitude_m"].tolist() == [500.0]
    assert client.reads == [
        ("profiles", "releases/release-1/release.json"),
        ("profiles", "releases/release-1/shape-1.parquet"),
    ]


@pytest.mark.asyncio
async def test_v3_release_loader_validates_profile_rows_and_metrics(monkeypatch):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "release-v3")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    reset_release_cache(monkeypatch)
    entry = release_entry("shape-1", data=b"parquet", row_count=2)
    entry.update({"road_deck_points": 1, "dtm_fallback_points": 1})
    entry["metrics"].update(
        {"matched_points": 1, "fallback_points": 1}
    )
    manifest = v3_release_manifest("release-v3", [entry])
    client = FakeMinio(
        {
            ("profiles", "releases/release-v3/release.json"): json.dumps(
                manifest
            ).encode(),
            ("profiles", "releases/release-v3/shape-1.parquet"): b"parquet",
        }
    )
    monkeypatch.setattr(
        elevation_profiles.pd,
        "read_parquet",
        lambda _stream: v3_profile_dataframe(),
    )
    trip = SimpleNamespace(id=uuid4(), status="gtfs", shape_id="shape-1")

    dataframe = await load_trip_elevation_dataframe(SimpleNamespace(), trip, client=client)

    assert dataframe["road_uuid"].tolist() == [
        "{00000000-0000-0000-0000-000000000001}",
        None,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate_frame", "message"),
    [
        (
            lambda frame: frame.assign(
                road_uuid=["{00000000-0000-0000-0000-00000000000a}", None]
            ),
            "non-canonical road_uuid",
        ),
        (
            lambda frame: frame.assign(
                road_uuid=[
                    "{00000000-0000-0000-0000-000000000001}",
                    "{00000000-0000-0000-0000-000000000002}",
                ]
            ),
            "fallback row contains road-only provenance",
        ),
        (
            lambda frame: frame.assign(road_objektart=["6m Strasse", None]),
            "do not match its release metrics",
        ),
        (
            lambda frame: frame.drop(columns=["road_uuid"]),
            "missing required columns",
        ),
    ],
)
async def test_v3_release_loader_rejects_untrusted_profile_content(
    monkeypatch, mutate_frame, message
):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "release-v3-invalid")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    reset_release_cache(monkeypatch)
    entry = release_entry("shape-1", data=b"parquet", row_count=2)
    entry.update({"road_deck_points": 1, "dtm_fallback_points": 1})
    entry["metrics"].update(
        {"matched_points": 1, "fallback_points": 1}
    )
    manifest = v3_release_manifest("release-v3-invalid", [entry])
    client = FakeMinio(
        {
            (
                "profiles",
                "releases/release-v3-invalid/release.json",
            ): json.dumps(manifest).encode(),
            (
                "profiles",
                "releases/release-v3-invalid/shape-1.parquet",
            ): b"parquet",
        }
    )
    monkeypatch.setattr(
        elevation_profiles.pd,
        "read_parquet",
        lambda _stream: mutate_frame(v3_profile_dataframe()),
    )
    trip = SimpleNamespace(id=uuid4(), status="gtfs", shape_id="shape-1")

    with pytest.raises(ElevationProfileFormatError, match=message):
        await load_trip_elevation_dataframe(SimpleNamespace(), trip, client=client)


@pytest.mark.asyncio
async def test_release_loader_rejects_tampered_profile_bytes(monkeypatch):
    monkeypatch.setenv("ELEVATION_PROFILES_RELEASE", "release-tampered")
    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    for name in (
        "_validated_release",
        "_validated_release_manifest",
        "_validated_release_profiles",
        "_validated_release_shape_ids",
        "_validated_release_digest",
        "_validated_release_object_identity",
    ):
        monkeypatch.setattr(elevation_profiles, name, None)
    manifest = release_manifest(
        "release-tampered", [release_entry("shape-1", data=b"expected")]
    )
    client = FakeMinio(
        {
            ("profiles", "releases/release-tampered/release.json"): json.dumps(manifest).encode(),
            ("profiles", "releases/release-tampered/shape-1.parquet"): b"tampered",
        }
    )
    trip = SimpleNamespace(id=uuid4(), status="gtfs", shape_id="shape-1")

    with pytest.raises(ElevationProfileFormatError, match="integrity check"):
        await load_trip_elevation_dataframe(SimpleNamespace(), trip, client=client)


@pytest.mark.asyncio
async def test_release_gate_requires_union_of_live_gtfs_snapshots():
    class ShapeSession:
        async def execute(self, _statement):
            return FakeScalarResult(["20250101_2025_shape-a", "20260826_2026_shape-b"])

    complete_manifest = {
        "profile_count": 2,
        "profiles": [
            release_entry("20250101_2025_shape-a"),
            release_entry("20260826_2026_shape-b"),
        ],
    }
    await validate_release_covers_database(ShapeSession(), complete_manifest)

    incomplete_manifest = {
        "profile_count": 1,
        "profiles": complete_manifest["profiles"][:1],
    }
    with pytest.raises(ElevationProfileFormatError, match="exactly match live bus GTFS shapes"):
        await validate_release_covers_database(ShapeSession(), incomplete_manifest)


@pytest.mark.asyncio
async def test_release_gate_rejects_shapes_not_present_in_database():
    class ShapeSession:
        async def execute(self, _statement):
            return FakeScalarResult(["shape-a"])

    manifest = {
        "profile_count": 2,
        "profiles": [release_entry("shape-a"), release_entry("unexpected-shape")],
    }

    with pytest.raises(
        ElevationProfileFormatError,
        match=r"unexpected=1 unexpected_sample=\['unexpected-shape'\]",
    ):
        await validate_release_covers_database(ShapeSession(), manifest)


@pytest.mark.asyncio
async def test_release_gate_rejects_live_bus_gtfs_trip_without_shape_id():
    class ShapeSession:
        async def execute(self, _statement):
            return FakeScalarResult(["shape-a", None])

    manifest = {
        "profile_count": 1,
        "profiles": [release_entry("shape-a")],
    }

    with pytest.raises(ElevationProfileFormatError, match="missing or blank shape_id"):
        await validate_release_covers_database(ShapeSession(), manifest)


def test_nullable_road_deck_records_are_strict_json_safe():
    dataframe = pd.DataFrame(
        {
            "latitude": [46.0, 46.1],
            "longitude": [7.0, 7.1],
            "altitude_m": [500.0, 501.0],
            "road_deck_altitude_m": [504.0, float("nan")],
            "road_snap_distance_m": [1.2, float("inf")],
            "elevation_source": ["road_deck", "dtm_fallback"],
        }
    )
    records = dataframe_json_records(dataframe)
    assert records[1]["road_deck_altitude_m"] is None
    assert records[1]["road_snap_distance_m"] is None
    json.dumps(records, allow_nan=False)


@pytest.mark.asyncio
async def test_aux_profile_is_blocked_until_job_succeeds(monkeypatch):
    monkeypatch.delenv("ELEVATION_PROFILES_RELEASE", raising=False)
    trip_id = uuid4()
    trip = SimpleNamespace(id=trip_id, status="depot", shape_id="depot-shape")
    pending_job = SimpleNamespace(status="pending", last_error=None)

    with pytest.raises(ElevationProfileNotReadyError) as raised:
        await resolve_trip_profile_location(FakeJobSession(pending_job), trip)
    assert raised.value.as_detail() == {
        "code": "elevation_profile_not_ready",
        "trip_id": str(trip_id),
        "job_status": "pending",
        "retry_after_seconds": 5,
    }

    succeeded_job = SimpleNamespace(
        status="succeeded",
        last_error=None,
        output_object_name="depot-shape.parquet",
    )
    location = await resolve_trip_profile_location(FakeJobSession(succeeded_job), trip)
    assert location.object_name == "depot-shape.parquet"


@pytest.mark.asyncio
async def test_shift_preflight_blocks_pending_aux_trip(monkeypatch):
    monkeypatch.delenv("ELEVATION_PROFILES_RELEASE", raising=False)
    trip_id = uuid4()
    trip = SimpleNamespace(id=trip_id, status="depot", shape_id="depot-shape")

    class ShiftSession:
        def __init__(self):
            self.results = [
                FakeScalarResult([trip]),
                FakeScalarResult(SimpleNamespace(status="pending", last_error=None)),
            ]

        async def execute(self, _statement):
            return self.results.pop(0)

    with pytest.raises(ElevationProfileNotReadyError) as raised:
        await ensure_shift_profiles_ready(ShiftSession(), [uuid4()])
    assert raised.value.trip_id == trip_id


@pytest.mark.asyncio
async def test_profile_endpoint_returns_explicit_409_for_pending_aux(monkeypatch):
    monkeypatch.delenv("ELEVATION_PROFILES_RELEASE", raising=False)
    trip_id = uuid4()
    trip = SimpleNamespace(id=trip_id, status="transfer", shape_id="transfer-shape")

    class Session(FakeJobSession):
        async def get(self, model, pk):
            assert model is GtfsTrips
            assert pk == trip_id
            return trip

    with pytest.raises(HTTPException) as raised:
        await get_elevation_profile_by_trip(
            trip_id,
            db=Session(SimpleNamespace(status="processing", last_error=None)),
            current_user=SimpleNamespace(id=uuid4()),
        )
    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "elevation_profile_not_ready"
    assert raised.value.detail["job_status"] == "processing"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ElevationProfileNotFoundError("missing object"), 404),
        (ElevationProfileStorageError("MinIO unavailable"), 503),
    ],
)
async def test_profile_endpoint_distinguishes_missing_object_from_outage(
    monkeypatch, error, expected_status
):
    trip_id = uuid4()
    trip = SimpleNamespace(id=trip_id, status="gtfs", shape_id="shape-1")

    class Session:
        async def get(self, model, pk):
            assert model is GtfsTrips
            assert pk == trip_id
            return trip

    async def fail_load(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(gtfs_router, "load_trip_elevation_dataframe", fail_load)
    with pytest.raises(HTTPException) as raised:
        await get_elevation_profile_by_trip(
            trip_id,
            db=Session(),
            current_user=SimpleNamespace(id=uuid4()),
        )
    assert raised.value.status_code == expected_status


class FakeOSRMResponse:
    status_code = 200
    text = "ok"

    def json(self):
        return {
            "code": "Ok",
            "routes": [
                {
                    "geometry": "encoded-polyline",
                    "distance": 1234.5,
                    "duration": 321.0,
                }
            ],
        }


class FakeOSRMClient:
    def __init__(self, *args, **kwargs):
        self.url = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url):
        self.url = url
        return FakeOSRMResponse()


class FakeAuxSession:
    def __init__(self, departure_stop, arrival_stop, calendar, route=None):
        self.departure_stop = departure_stop
        self.arrival_stop = arrival_stop
        self.calendar = calendar
        self.route = route or SimpleNamespace(id=uuid4(), route_type=3)
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model, pk):
        if model is GtfsRoutes:
            return self.route if pk == self.route.id else None
        assert model is GtfsStops
        if pk == self.departure_stop.id:
            return self.departure_stop
        if pk == self.arrival_stop.id:
            return self.arrival_stop
        return None

    async def execute(self, _statement):
        return FakeScalarResult(self.calendar)

    def _prepare(self, instance):
        now = datetime.now(timezone.utc)
        if isinstance(instance, GtfsTrips) and instance.id is None:
            instance.id = uuid4()
        if isinstance(instance, ElevationProfileJobs):
            instance.id = instance.id or uuid4()
            instance.attempts = 0
            instance.available_at = now
            instance.lease_expires_at = None
            instance.worker_id = None
            instance.last_error = None
            instance.algorithm_version = None
            instance.roads_release = None
            instance.created_at = now
            instance.updated_at = now
            instance.completed_at = None
        self.added.append(instance)

    def add(self, instance):
        self._prepare(instance)

    def add_all(self, instances):
        for instance in instances:
            self._prepare(instance)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _instance):
        pass


@pytest.mark.asyncio
async def test_aux_creation_is_queued_in_one_commit(monkeypatch):
    monkeypatch.setattr("app.routers.gtfs.httpx.AsyncClient", FakeOSRMClient)
    dep_id, arr_id, route_id = uuid4(), uuid4(), uuid4()
    route = SimpleNamespace(id=route_id, route_type=3)
    session = FakeAuxSession(
        SimpleNamespace(
            id=dep_id,
            stop_lat=46.0,
            stop_lon=7.0,
            stop_name="Depot",
        ),
        SimpleNamespace(
            id=arr_id,
            stop_lat=46.1,
            stop_lon=7.1,
            stop_name="Terminus",
        ),
        SimpleNamespace(id=uuid4(), service_id="auxiliary"),
        route,
    )

    response = await create_aux_trip(
        AuxTripCreate(
            departure_stop_id=dep_id,
            arrival_stop_id=arr_id,
            departure_time="08:00:00",
            arrival_time="08:15:00",
            route_id=route_id,
            status="depot",
        ),
        db=session,
        current_user=SimpleNamespace(id=uuid4()),
    )

    assert session.commits == 1
    assert session.rollbacks == 0
    assert sum(isinstance(row, GtfsTrips) for row in session.added) == 1
    assert sum(isinstance(row, GtfsStopsTimes) for row in session.added) == 2
    jobs = [row for row in session.added if isinstance(row, ElevationProfileJobs)]
    assert len(jobs) == 1
    job = jobs[0]
    assert job.payload["source"] == {
        "kind": "polyline",
        "encoded": "encoded-polyline",
        "precision": 5,
    }
    assert job.output_object_name == f"{response.trip.shape_id}.parquet"
    assert response.elevation_job.status == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "expected_status", "expected_detail"),
    [
        (None, 404, "Route not found"),
        (SimpleNamespace(id=None, route_type=900), 400, "require a bus route"),
    ],
)
async def test_aux_creation_rejects_missing_or_non_bus_route_before_osrm_or_writes(
    monkeypatch, route, expected_status, expected_detail
):
    class UnexpectedOSRMClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("OSRM must not be called for an invalid route")

    monkeypatch.setattr("app.routers.gtfs.httpx.AsyncClient", UnexpectedOSRMClient)
    dep_id, arr_id, route_id = uuid4(), uuid4(), uuid4()
    if route is not None:
        route.id = route_id
    session = FakeAuxSession(
        SimpleNamespace(id=dep_id, stop_lat=46.0, stop_lon=7.0),
        SimpleNamespace(id=arr_id, stop_lat=46.1, stop_lon=7.1),
        SimpleNamespace(id=uuid4(), service_id="auxiliary"),
        route=route,
    )
    if route is None:
        session.route = SimpleNamespace(id=uuid4(), route_type=3)

    with pytest.raises(HTTPException) as raised:
        await create_aux_trip(
            AuxTripCreate(
                departure_stop_id=dep_id,
                arrival_stop_id=arr_id,
                departure_time="08:00:00",
                arrival_time="08:15:00",
                route_id=route_id,
                status="depot",
            ),
            db=session,
            current_user=SimpleNamespace(id=uuid4()),
        )

    assert raised.value.status_code == expected_status
    assert expected_detail in raised.value.detail
    assert session.added == []
    assert session.commits == 0


def test_aux_route_is_declared_as_202():
    aux_route = next(route for route in router.routes if route.path == "/aux-trip")
    assert aux_route.status_code == 202


class FakeTripCrudSession:
    def __init__(self, trip=None):
        self.trip = trip
        self.added = []
        self.commits = 0

    async def get(self, model, _pk):
        assert model is GtfsTrips
        return self.trip

    def add(self, instance):
        instance.id = instance.id or uuid4()
        self.added.append(instance)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _instance):
        pass


def _trip_create_payload(status: str, shape_id: str | None = None) -> GtfsTripsCreate:
    return GtfsTripsCreate(
        route_id=uuid4(),
        service_id=uuid4(),
        gtfs_service_id="service",
        trip_id=f"trip-{uuid4()}",
        status=status,
        shape_id=shape_id,
    )


@pytest.mark.asyncio
async def test_generic_trip_post_rejects_aux_status_with_guidance():
    session = FakeTripCrudSession()
    with pytest.raises(HTTPException) as raised:
        await create_trip(
            _trip_create_payload("depot"),
            db=session,
            current_user=SimpleNamespace(id=uuid4()),
        )
    assert raised.value.status_code == 400
    assert "/api/v1/gtfs/aux-trip" in raised.value.detail
    assert session.added == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_generic_trip_post_still_accepts_gtfs():
    session = FakeTripCrudSession()
    created = await create_trip(
        _trip_create_payload("gtfs"),
        db=session,
        current_user=SimpleNamespace(id=uuid4()),
    )
    assert created.status == "gtfs"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_generic_trip_post_rejects_shape_outside_active_release_before_add(monkeypatch):
    monkeypatch.setattr(
        gtfs_router,
        "validate_configured_release",
        lambda: {
            "profile_count": 1,
            "profiles": [release_entry("committed")],
        },
    )
    session = FakeTripCrudSession()

    with pytest.raises(HTTPException) as raised:
        await create_trip(
            _trip_create_payload("gtfs", shape_id="not-published"),
            db=session,
            current_user=SimpleNamespace(id=uuid4()),
        )

    assert raised.value.status_code == 409
    assert "not present" in raised.value.detail
    assert session.added == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_generic_trip_post_accepts_shape_in_active_release(monkeypatch):
    monkeypatch.setattr(
        gtfs_router,
        "validate_configured_release",
        lambda: {
            "profile_count": 1,
            "profiles": [release_entry("committed")],
        },
    )
    session = FakeTripCrudSession()
    created = await create_trip(
        _trip_create_payload("gtfs", shape_id="committed"),
        db=session,
        current_user=SimpleNamespace(id=uuid4()),
    )
    assert created.shape_id == "committed"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_generic_trip_post_requires_shape_while_release_is_active(monkeypatch):
    monkeypatch.setattr(
        gtfs_router,
        "validate_configured_release",
        lambda: {
            "profile_count": 1,
            "profiles": [release_entry("committed")],
        },
    )
    session = FakeTripCrudSession()

    with pytest.raises(HTTPException) as raised:
        await create_trip(
            _trip_create_payload("gtfs", shape_id=None),
            db=session,
            current_user=SimpleNamespace(id=uuid4()),
        )

    assert raised.value.status_code == 409
    assert "non-empty shape_id" in raised.value.detail
    assert session.added == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_trip_put_rejects_status_transition():
    trip = GtfsTrips(
        id=uuid4(),
        route_id=uuid4(),
        service_id=uuid4(),
        gtfs_service_id="service",
        trip_id="trip-1",
        status="gtfs",
        shape_id="shape-1",
    )
    session = FakeTripCrudSession(trip)
    with pytest.raises(HTTPException) as raised:
        await update_trip(
            trip.id,
            GtfsTripsUpdate(status="depot"),
            db=session,
            current_user=SimpleNamespace(id=uuid4()),
        )
    assert raised.value.status_code == 400
    assert "cannot transition" in raised.value.detail
    assert session.commits == 0


@pytest.mark.asyncio
async def test_trip_put_rejects_gtfs_shape_outside_release_without_mutation(monkeypatch):
    monkeypatch.setattr(
        gtfs_router,
        "validate_configured_release",
        lambda: {
            "profile_count": 1,
            "profiles": [release_entry("committed")],
        },
    )
    trip = GtfsTrips(
        id=uuid4(), route_id=uuid4(), service_id=uuid4(),
        gtfs_service_id="service", trip_id="trip-1", status="gtfs", shape_id="committed",
    )
    session = FakeTripCrudSession(trip)

    with pytest.raises(HTTPException) as raised:
        await update_trip(
            trip.id,
            GtfsTripsUpdate(shape_id="not-published", trip_headsign="must-not-apply"),
            db=session,
            current_user=SimpleNamespace(id=uuid4()),
        )

    assert raised.value.status_code == 409
    assert trip.shape_id == "committed"
    assert trip.trip_headsign is None
    assert session.commits == 0


@pytest.mark.asyncio
async def test_trip_put_protects_aux_shape_id_but_allows_other_fields():
    trip = GtfsTrips(
        id=uuid4(),
        route_id=uuid4(),
        service_id=uuid4(),
        gtfs_service_id="auxiliary",
        trip_id="depot-1",
        status="depot",
        shape_id="depot-shape",
    )
    session = FakeTripCrudSession(trip)
    with pytest.raises(HTTPException) as raised:
        await update_trip(
            trip.id,
            GtfsTripsUpdate(shape_id="replacement-shape"),
            db=session,
            current_user=SimpleNamespace(id=uuid4()),
        )
    assert raised.value.status_code == 400
    assert "shape_id cannot be changed" in raised.value.detail
    assert session.commits == 0

    updated = await update_trip(
        trip.id,
        GtfsTripsUpdate(
            status="depot",
            shape_id="depot-shape",
            trip_headsign="Updated",
        ),
        db=session,
        current_user=SimpleNamespace(id=uuid4()),
    )
    assert updated.trip_headsign == "Updated"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_delete_aux_cleans_output_and_dtm_backup(monkeypatch):
    trip_id = uuid4()
    trip = SimpleNamespace(id=trip_id, status="depot", shape_id="depot-shape")
    lease_expires_at = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id=uuid4(),
        output_object_name="depot-shape.parquet",
        payload={"source": {"kind": "polyline", "encoded": "abc", "precision": 5}},
        lease_expires_at=lease_expires_at,
    )
    removed = []

    class DeleteSession:
        def __init__(self):
            self.added = []
            self.deleted = []
            self.commits = 0
            self.flushes = 0

        def add(self, instance):
            self.added.append(instance)

        async def flush(self):
            self.flushes += 1

        async def get(self, model, pk):
            assert model is GtfsTrips
            assert pk == trip_id
            return trip

        async def execute(self, _statement):
            return FakeScalarResult([])

        async def delete(self, instance):
            self.deleted.append(instance)

        async def commit(self):
            self.commits += 1

    async def fake_get_job(_db, _trip_id, *, for_update=False):
        assert for_update is True
        return job

    def fake_remove(objects):
        removed.extend(objects)
        return []

    monkeypatch.setenv("ELEVATION_PROFILES_BUCKET", "profiles")
    monkeypatch.setenv("ELEVATION_PROFILE_CLEANUP_FAST_PATH", "true")
    monkeypatch.setattr(gtfs_router, "get_elevation_profile_job", fake_get_job)
    monkeypatch.setattr(gtfs_router, "remove_profile_objects", fake_remove)
    session = DeleteSession()

    response = await delete_trip(
        trip_id,
        db=session,
        current_user=SimpleNamespace(id=uuid4()),
    )

    assert session.commits == 1
    assert session.flushes == 1
    assert trip in session.deleted
    assert len(session.added) == 1
    cleanup_job = session.added[0]
    assert isinstance(cleanup_job, ElevationProfileCleanupJobs)
    assert cleanup_job.trip_id == trip_id
    assert cleanup_job.available_at >= lease_expires_at + timedelta(seconds=30)
    assert cleanup_job.payload == {
        "schema_version": 1,
        "trip_id": str(trip_id),
        "shape_id": "depot-shape",
        "profile_job_id": str(job.id),
        "objects": [
            {"bucket": "profiles", "object_name": "backups/dtm/depot-shape.parquet"},
            {"bucket": "profiles", "object_name": "depot-shape.parquet"},
        ],
        "prefixes": [
            {
                "bucket": "profiles",
                "prefix": f"._staging/elevation-jobs/{job.id}/",
            }
        ],
    }
    assert set(removed) == {
        ("profiles", "depot-shape.parquet"),
        ("profiles", "backups/dtm/depot-shape.parquet"),
    }
    assert response["elevation_cleanup_queued"] is True
    assert response["elevation_cleanup_errors"] == []


def test_cleanup_outbox_has_no_trip_foreign_key_and_is_claimable_by_availability():
    table = ElevationProfileCleanupJobs.__table__
    assert list(table.foreign_keys) == []
    assert any(
        index.name == "elevation_profile_cleanup_jobs_status_available_at_idx"
        and [column.name for column in index.columns] == ["status", "available_at"]
        for index in table.indexes
    )


@pytest.mark.asyncio
async def test_cleanup_delete_locks_generation_job_before_cascade():
    captured = []

    class Session:
        async def execute(self, statement):
            captured.append(statement)
            return FakeScalarResult(None)

    await elevation_profiles.get_elevation_profile_job(
        Session(), uuid4(), for_update=True
    )
    compiled = str(captured[0].compile()).upper()
    assert "FOR UPDATE" in compiled
