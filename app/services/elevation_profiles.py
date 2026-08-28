"""Release-aware access to elevation profiles stored in MinIO.

GTFS profiles are read from an immutable release prefix when
``ELEVATION_PROFILES_RELEASE`` is configured. Auxiliary trips always use their
stable object name and are readable only after their durable PostgreSQL job is
marked ``succeeded``.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import re
from dataclasses import dataclass
from numbers import Real
from typing import Any, Iterable
from uuid import UUID

import pandas as pd
from minio import Minio
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from elettra_core import add_distance_columns
from app.models import ElevationProfileJobs, GtfsRoutes, GtfsTrips, ShiftsStructures
from app.services.runtime_release import (
    ROAD_SNAP_V3_ALGORITHM,
    runtime_release_configuration,
)


DEFAULT_BUCKET = "elevation-profiles"
RELEASE_MANIFEST_NAME = "release.json"
PENDING_RETRY_AFTER_SECONDS = 5
RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ROAD_SNAP_V3_PROFILE_CONTRACT_VERSION = 2
ROAD_SNAP_V3_BUS_POLICY = "swisstlm3d-bus-v1"
ROAD_SNAP_V3_CANDIDATE_SELECTION_POLICY = "topology-strata-v1"
ROAD_SNAP_V3_TOPOLOGY_STITCH_POLICY = "same-structure-aligned-endpoint-v2"
ROAD_SNAP_V3_REQUIRED_ROAD_ATTRIBUTES = (
    "befahrbarkeit",
    "kunstbaute",
    "objektart",
    "richtungsgetrennt",
    "stufe",
    "uuid",
)
ROAD_SNAP_V3_UUID_PATTERN = re.compile(
    r"^\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-"
    r"[0-9A-F]{4}-[0-9A-F]{12}\}$"
)
ROAD_SNAP_LEGACY_RELEASE_PATTERN = re.compile(
    r"^road-snap-v(?:1|2)(?:[-.][A-Za-z0-9][A-Za-z0-9._-]*)?$"
)
ROAD_SNAP_V3_CONDITIONAL_OBJECT_TYPES = frozenset(
    {"2m Weg", "Platz", "Dienstzufahrt"}
)
ROAD_SNAP_V3_ROAD_COLUMNS = (
    "road_deck_altitude_m",
    "road_snap_latitude",
    "road_snap_longitude",
    "road_snap_distance_m",
    "road_objektart",
    "road_uuid",
    "elevation_delta_m",
)


class ElevationProfileError(RuntimeError):
    """Base class for profile resolution and storage errors."""


class ElevationProfileNotReadyError(ElevationProfileError):
    """Raised when an auxiliary profile job has not completed successfully."""

    def __init__(
        self,
        trip_id: UUID,
        status: str,
        *,
        last_error: str | None = None,
    ) -> None:
        self.trip_id = trip_id
        self.status = status
        self.last_error = last_error
        super().__init__(f"Elevation profile for trip {trip_id} is not ready (status={status})")

    def as_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "code": "elevation_profile_not_ready",
            "trip_id": str(self.trip_id),
            "job_status": self.status,
        }
        if self.status in {"pending", "processing", "missing"}:
            detail["retry_after_seconds"] = PENDING_RETRY_AFTER_SECONDS
        if self.last_error:
            detail["last_error"] = self.last_error
        return detail


class ElevationProfileStorageError(ElevationProfileError):
    """Raised when a configured MinIO object cannot be read or removed."""


class ElevationProfileNotFoundError(ElevationProfileStorageError):
    """Raised only when MinIO confirms that an object does not exist."""


class ElevationProfileFormatError(ElevationProfileError):
    """Raised when a profile or release manifest cannot be decoded."""


@dataclass(frozen=True)
class ElevationProfileLocation:
    bucket: str
    object_name: str
    release_id: str | None = None


_validated_release: tuple[str, str] | None = None
_validated_release_manifest: dict[str, Any] | None = None
_validated_release_profiles: dict[str, dict[str, Any]] | None = None
_validated_release_shape_ids: frozenset[str] | None = None
_validated_release_digest: str | None = None
_validated_release_object_identity: tuple[Any, ...] | None = None


def elevation_profiles_bucket() -> str:
    """Return the stable auxiliary-profile bucket."""
    value = os.getenv("ELEVATION_PROFILES_BUCKET", DEFAULT_BUCKET).strip()
    return value or DEFAULT_BUCKET


def gtfs_elevation_profiles_bucket() -> str:
    """Return the immutable GTFS release bucket.

    Falling back to the historical bucket keeps the pre-switch deployment
    compatible.  Production can isolate immutable GTFS releases by setting
    ``GTFS_ELEVATION_PROFILES_BUCKET`` without moving mutable aux objects.
    """
    value = os.getenv("GTFS_ELEVATION_PROFILES_BUCKET", "").strip()
    return value or elevation_profiles_bucket()


def configured_release() -> str | None:
    value = os.getenv("ELEVATION_PROFILES_RELEASE", "").strip()
    return validate_release_id(value) if value else None


def validate_release_id(value: str) -> str:
    if not RELEASE_ID_PATTERN.fullmatch(value):
        raise ElevationProfileFormatError(
            "Invalid elevation release ID; use 1-128 ASCII letters, digits, dots, underscores or hyphens"
        )
    return value


def gtfs_profile_object_name(shape_id: str, release_id: str | None = None) -> str:
    """Return the only allowed object key for a GTFS shape.

    Passing/configuring a release never falls back to the legacy root key.
    """
    release = configured_release() if release_id is None else validate_release_id(release_id)
    if release:
        return f"releases/{release}/{shape_id}.parquet"
    return f"{shape_id}.parquet"


def release_manifest_object_name(release_id: str) -> str:
    return f"releases/{validate_release_id(release_id)}/{RELEASE_MANIFEST_NAME}"


def create_minio_client() -> Minio:
    endpoint = os.getenv("MINIO_ENDPOINT", "minio:9000")
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "minio_user")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "minio_password")
    secure = os.getenv("MINIO_SECURE", "false").lower() in ("1", "true", "yes", "on")
    try:
        return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
    except Exception as exc:
        raise ElevationProfileStorageError(
            f"Unable to configure MinIO client for endpoint {endpoint!r}: {exc}"
        ) from exc


def _read_object(client: Minio, bucket: str, object_name: str) -> bytes:
    try:
        response = client.get_object(bucket, object_name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
    except Exception as exc:
        code = str(getattr(exc, "code", "")).lower()
        status = getattr(exc, "status", None)
        if code in {"nosuchkey", "nosuchobject", "notfound", "no_such_key"} or status == 404:
            raise ElevationProfileNotFoundError(
                f"MinIO object not found: {bucket}/{object_name}"
            ) from exc
        raise ElevationProfileStorageError(
            f"Unable to read MinIO object {bucket}/{object_name}: {exc}"
        ) from exc


def _metadata_value(metadata: Any, key: str) -> str | None:
    if not isinstance(metadata, dict):
        return None
    wanted = key.lower().replace("_", "-")
    for candidate, value in metadata.items():
        normalized = str(candidate).lower().replace("x-amz-meta-", "").replace("_", "-")
        if normalized == wanted:
            return str(value)
    return None


def _release_object_identity(client: Minio, bucket: str, object_name: str) -> tuple[Any, ...]:
    try:
        stat = client.stat_object(bucket, object_name)
    except Exception as exc:
        code = str(getattr(exc, "code", "")).lower()
        status = getattr(exc, "status", None)
        if code in {"nosuchkey", "nosuchobject", "notfound", "no_such_key"} or status == 404:
            raise ElevationProfileNotFoundError(
                f"MinIO object not found: {bucket}/{object_name}"
            ) from exc
        raise ElevationProfileStorageError(
            f"Unable to stat MinIO object {bucket}/{object_name}: {exc}"
        ) from exc
    return (
        int(getattr(stat, "size", -1)),
        getattr(stat, "etag", None),
        getattr(stat, "version_id", None),
        _metadata_value(getattr(stat, "metadata", None), "sha256"),
    )


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_bus_route_type(value: Any) -> bool:
    try:
        route_type = int(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return route_type == 3 or 700 <= route_type <= 719


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _is_v3_release(manifest: dict[str, Any]) -> bool:
    return manifest.get("algorithm_version") == ROAD_SNAP_V3_ALGORITHM


def _validate_v3_matcher(matcher: dict[str, Any]) -> None:
    positive_numbers = (
        "tolerance_m",
        "min_grade_run_m",
        "recovery_tolerance_m",
        "topology_stitch_max_3d_gap_m",
        "topology_stitch_max_vertical_gap_m",
        "topology_stitch_max_alignment_angle_deg",
    )
    nonnegative_numbers = (
        "bbox_buffer_m",
        "w_xy",
        "w_grade",
        "max_grade_pct",
        "hard_grade_pct",
        "w_switch",
        "fallback_emission",
        "topology_path_ratio",
        "topology_path_slack_m",
        "w_topology",
        "conditional_road_penalty",
    )
    positive_ints = ("k_candidates", "recovery_k_candidates", "max_gap_samples")
    runtime_versions = matcher.get("runtime_versions")
    if (
        matcher.get("algorithm_version") != ROAD_SNAP_V3_ALGORITHM
        or matcher.get("bus_compatibility_policy") != ROAD_SNAP_V3_BUS_POLICY
        or matcher.get("candidate_selection_policy")
        != ROAD_SNAP_V3_CANDIDATE_SELECTION_POLICY
        or matcher.get("topology_stitch_policy")
        != ROAD_SNAP_V3_TOPOLOGY_STITCH_POLICY
        or matcher.get("topology_node_precision") != "XYZ millimetre"
        or matcher.get("observed_distance_lower_bound")
        != "max(declared_chainage_step,lv95_chord)"
        or not _is_finite_number(matcher.get("observed_distance_epsilon_m"))
        or float(matcher["observed_distance_epsilon_m"]) != 0.01
        or any(
            not _is_finite_number(matcher.get(name))
            or float(matcher[name]) <= 0
            for name in positive_numbers
        )
        or any(
            not _is_finite_number(matcher.get(name))
            or float(matcher[name]) < 0
            for name in nonnegative_numbers
        )
        or any(not _is_positive_int(matcher.get(name)) for name in positive_ints)
        or float(matcher["recovery_tolerance_m"]) < float(matcher["tolerance_m"])
        or matcher["recovery_k_candidates"] < matcher["k_candidates"]
        or float(matcher["hard_grade_pct"]) <= float(matcher["max_grade_pct"])
        or float(matcher["topology_stitch_max_3d_gap_m"]) > 2.0
        or float(matcher["topology_stitch_max_vertical_gap_m"]) > 0.5
        or float(matcher["topology_stitch_max_vertical_gap_m"])
        > float(matcher["topology_stitch_max_3d_gap_m"])
        or float(matcher["topology_stitch_max_alignment_angle_deg"]) > 45.0
        or not isinstance(runtime_versions, dict)
        or any(
            not isinstance(runtime_versions.get(name), str)
            or not runtime_versions[name]
            for name in ("python", "geos", "proj", "gdal")
        )
        or not isinstance(runtime_versions.get("packages"), dict)
        or not runtime_versions["packages"]
        or any(
            not isinstance(name, str)
            or not name
            or not isinstance(version, str)
            or not version
            for name, version in runtime_versions["packages"].items()
        )
    ):
        raise ElevationProfileFormatError(
            "Release manifest has incomplete or invalid road-snap-v3 topology configuration"
        )


def _validate_roads_manifest(roads: Any, *, require_v3: bool = False) -> None:
    if not isinstance(roads, dict):
        raise ElevationProfileFormatError("Release manifest has no roads provenance")
    required_strings = (
        "release_id",
        "source_url",
        "layer_name",
        "vertical_datum",
        "validator_version",
        "z_validation",
    )
    if (
        roads.get("schema_version") != 2
        or roads.get("has_z") is not True
        or any(not isinstance(roads.get(key), str) or not roads[key] for key in required_strings)
        or str(roads.get("crs", "")).upper().replace(" ", "") not in {"EPSG:2056", "2056"}
        or str(roads.get("vertical_datum", "")).upper() != "LN02"
        or roads.get("z_validation") != "full"
        or roads.get("validator_version") != "pyriadne-road-asset-v1"
        or not _is_positive_int(roads.get("size_bytes"))
        or not _is_positive_int(roads.get("feature_count"))
        or not _is_sha256(roads.get("sha256"))
    ):
        raise ElevationProfileFormatError("Release manifest has invalid roads provenance")
    if require_v3 and roads.get("required_attributes") != list(
        ROAD_SNAP_V3_REQUIRED_ROAD_ATTRIBUTES
    ):
        raise ElevationProfileFormatError(
            "Release manifest does not pin the road-snap-v3 topology attributes"
        )


def _validate_gtfs_provenance(gtfs: Any) -> None:
    if not isinstance(gtfs, dict):
        raise ElevationProfileFormatError("Release manifest has no GTFS provenance")
    snapshots = gtfs.get("snapshots")
    values = snapshots if isinstance(snapshots, list) else [gtfs]
    if not values or any(
        not isinstance(value, dict) or not _is_sha256(value.get("sha256"))
        for value in values
    ):
        raise ElevationProfileFormatError("Release manifest has invalid GTFS provenance")


def _validate_v3_profile_metrics(
    metrics: Any,
    *,
    shape_id: str,
    matched_points: int,
) -> None:
    if not isinstance(metrics, dict):
        raise ElevationProfileFormatError(
            f"Release manifest has no v3 profile metrics for {shape_id!r}"
        )
    matched_with_uuid = metrics.get("matched_with_road_uuid")
    distinct_uuids = metrics.get("distinct_road_uuids")
    recovery_points = metrics.get("recovery_ring_points")
    conditional_points = metrics.get("conditional_bus_tier_points")
    object_counts = metrics.get("matched_by_objektart")
    if (
        not _is_nonnegative_int(matched_with_uuid)
        or matched_with_uuid != matched_points
        or not _is_nonnegative_int(distinct_uuids)
        or distinct_uuids > matched_points
        or (matched_points > 0 and distinct_uuids == 0)
        or not _is_nonnegative_int(recovery_points)
        or recovery_points > matched_points
        or not _is_nonnegative_int(conditional_points)
        or conditional_points > matched_points
        or not isinstance(object_counts, dict)
        or any(
            not isinstance(name, str)
            or not name
            or not _is_positive_int(count)
            for name, count in object_counts.items()
        )
        or sum(object_counts.values()) != matched_points
    ):
        raise ElevationProfileFormatError(
            f"Release manifest contains inconsistent v3 road metrics for {shape_id!r}"
        )


def release_profile_entries(
    manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Validate and index the exact object catalog committed by a release."""
    profiles = manifest.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ElevationProfileFormatError("Release manifest has no non-empty profiles catalog")
    if manifest.get("profile_count") != len(profiles):
        raise ElevationProfileFormatError("Release manifest profile_count does not match profiles")

    entries: dict[str, dict[str, Any]] = {}
    for entry in profiles:
        if not isinstance(entry, dict):
            raise ElevationProfileFormatError("Release manifest profile entry is not an object")
        shape_id = entry.get("shape_id")
        object_name = entry.get("object_name")
        if (
            not isinstance(shape_id, str)
            or not shape_id
            or "/" in shape_id
            or "\\" in shape_id
            or object_name != f"{shape_id}.parquet"
        ):
            raise ElevationProfileFormatError("Release manifest contains an invalid profile entry")
        size_bytes = entry.get("size_bytes")
        sha256 = entry.get("sha256")
        row_count = entry.get("row_count")
        road_deck_points = entry.get("road_deck_points")
        dtm_fallback_points = entry.get("dtm_fallback_points")
        metrics = entry.get("metrics")
        if (
            not _is_positive_int(size_bytes)
            or not _is_sha256(sha256)
            or not _is_positive_int(row_count)
            or not _is_nonnegative_int(road_deck_points)
            or not _is_nonnegative_int(dtm_fallback_points)
            or road_deck_points + dtm_fallback_points != row_count
            or not _is_sha256(entry.get("profile_manifest_sha256"))
            or not _is_sha256(entry.get("route_variant_sha256"))
            or not _is_bus_route_type(entry.get("route_type"))
            or not isinstance(metrics, dict)
            or metrics.get("total_points") != row_count
            or metrics.get("matched_points") != road_deck_points
            or metrics.get("fallback_points") != dtm_fallback_points
        ):
            raise ElevationProfileFormatError(
                f"Release manifest contains invalid integrity metadata for {shape_id!r}"
            )
        if _is_v3_release(manifest):
            _validate_v3_profile_metrics(
                metrics,
                shape_id=shape_id,
                matched_points=road_deck_points,
            )
        if shape_id in entries:
            raise ElevationProfileFormatError(
                f"Release manifest contains duplicate shape_id {shape_id!r}"
            )
        entries[shape_id] = entry
    return entries


def release_profile_shape_ids(manifest: dict[str, Any]) -> frozenset[str]:
    """Return a cached immutable shape set for the validated release manifest."""
    if (
        manifest is _validated_release_manifest
        and _validated_release_shape_ids is not None
    ):
        return _validated_release_shape_ids
    return frozenset(release_profile_entries(manifest))


def validate_configured_release(
    client: Minio | None = None,
    *,
    use_cache: bool = True,
) -> dict[str, Any] | None:
    """Validate the configured immutable release manifest once per process.

    This is deliberately fail-closed. A configured release is usable only if
    ``release.json`` exists, is valid JSON and declares the same release ID.
    """
    global _validated_release, _validated_release_manifest
    global _validated_release_profiles, _validated_release_shape_ids
    global _validated_release_digest, _validated_release_object_identity

    release_id = configured_release()
    if release_id is None:
        _validated_release = None
        _validated_release_manifest = None
        _validated_release_profiles = None
        _validated_release_shape_ids = None
        _validated_release_digest = None
        _validated_release_object_identity = None
        return None

    bucket = gtfs_elevation_profiles_bucket()
    cache_key = (bucket, release_id)
    if (
        use_cache
        and _validated_release == cache_key
        and _validated_release_manifest is not None
        and _validated_release_profiles is not None
        and _validated_release_shape_ids is not None
    ):
        return _validated_release_manifest

    minio_client = client or create_minio_client()
    object_name = release_manifest_object_name(release_id)
    identity_before = (
        _release_object_identity(minio_client, bucket, object_name)
        if hasattr(minio_client, "stat_object")
        else None
    )
    raw = _read_object(minio_client, bucket, object_name)
    identity_after = (
        _release_object_identity(minio_client, bucket, object_name)
        if hasattr(minio_client, "stat_object")
        else None
    )
    if identity_before != identity_after:
        raise ElevationProfileFormatError(
            "Elevation release manifest changed while it was being validated"
        )
    manifest_digest = hashlib.sha256(raw).hexdigest()
    if (
        _validated_release == cache_key
        and _validated_release_digest is not None
        and manifest_digest != _validated_release_digest
    ):
        raise ElevationProfileFormatError(
            "Immutable elevation release manifest changed under the configured release ID"
        )
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ElevationProfileFormatError(
            f"Invalid release manifest for {release_id}: {exc}"
        ) from exc

    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("release_id") != release_id
        or manifest.get("state") != "complete"
    ):
        declared = manifest.get("release_id") if isinstance(manifest, dict) else None
        raise ElevationProfileFormatError(
            "Release manifest is not publishable: "
            f"configured={release_id!r}, declared={declared!r}, "
            f"schema_version={manifest.get('schema_version') if isinstance(manifest, dict) else None!r}, "
            f"state={manifest.get('state') if isinstance(manifest, dict) else None!r}"
        )

    matcher = manifest.get("matcher")
    algorithm_version = manifest.get("algorithm_version")
    sampling_distance = manifest.get("sampling_distance_m")
    if (
        manifest.get("elevation_mode") != "road-deck"
        or not isinstance(algorithm_version, str)
        or not algorithm_version
        or not isinstance(matcher, dict)
        or matcher.get("algorithm_version") != algorithm_version
        or not isinstance(sampling_distance, (int, float))
        or isinstance(sampling_distance, bool)
        or not math.isfinite(float(sampling_distance))
        or float(sampling_distance) <= 0
        or not isinstance(manifest.get("dtm_provider"), str)
        or not manifest["dtm_provider"]
    ):
        raise ElevationProfileFormatError(
            "Release manifest has incompatible road-deck matcher configuration"
        )
    is_v3 = _is_v3_release(manifest)
    if is_v3:
        if (
            manifest.get("profile_contract_version")
            != ROAD_SNAP_V3_PROFILE_CONTRACT_VERSION
        ):
            raise ElevationProfileFormatError(
                "road-snap-v3 release must declare profile_contract_version=2"
            )
        _validate_v3_matcher(matcher)
    elif not ROAD_SNAP_LEGACY_RELEASE_PATTERN.fullmatch(algorithm_version):
        raise ElevationProfileFormatError(
            f"Unsupported elevation profile algorithm_version {algorithm_version!r}"
        )
    elif manifest.get("profile_contract_version") not in (None, 1):
        raise ElevationProfileFormatError(
            "Legacy road-snap-v1/v2 release has an incompatible profile contract"
        )

    profile_entries = release_profile_entries(manifest)
    _validate_roads_manifest(manifest.get("roads"), require_v3=is_v3)
    _validate_gtfs_provenance(manifest.get("gtfs"))
    point_count = sum(entry["row_count"] for entry in profile_entries.values())
    road_deck_points = sum(
        entry["road_deck_points"] for entry in profile_entries.values()
    )
    fallback_points = sum(
        entry["dtm_fallback_points"] for entry in profile_entries.values()
    )
    release_metrics = manifest.get("metrics")
    expected_aggregates = {
        "profile_count": len(profile_entries),
        "point_count": point_count,
        "road_deck_points": road_deck_points,
        "dtm_fallback_points": fallback_points,
    }
    if any(manifest.get(key) != value for key, value in expected_aggregates.items()):
        raise ElevationProfileFormatError(
            "Release manifest aggregate counts do not match its profile catalog"
        )
    if (
        not isinstance(release_metrics, dict)
        or release_metrics.get("profile_release") != release_id
        or release_metrics.get("algorithm_version") != algorithm_version
        or release_metrics.get("roads_release") != manifest["roads"].get("release_id")
        or release_metrics.get("roads_sha256") != manifest["roads"].get("sha256")
        or release_metrics.get("shape_count") != len(profile_entries)
        or release_metrics.get("total_points") != point_count
        or release_metrics.get("matched_points") != road_deck_points
        or release_metrics.get("fallback_points") != fallback_points
    ):
        raise ElevationProfileFormatError(
            "Release manifest aggregate metrics are inconsistent"
        )
    if is_v3:
        expected_v3_aggregates = {
            "matched_with_road_uuid": road_deck_points,
            "recovery_ring_points": sum(
                entry["metrics"]["recovery_ring_points"]
                for entry in profile_entries.values()
            ),
            "conditional_bus_tier_points": sum(
                entry["metrics"]["conditional_bus_tier_points"]
                for entry in profile_entries.values()
            ),
        }
        if any(
            not _is_nonnegative_int(release_metrics.get(key))
            or release_metrics.get(key) != value
            for key, value in expected_v3_aggregates.items()
        ):
            raise ElevationProfileFormatError(
                "Release manifest aggregate v3 road metrics are inconsistent"
            )
    _validated_release = cache_key
    _validated_release_manifest = manifest
    _validated_release_profiles = profile_entries
    _validated_release_shape_ids = frozenset(profile_entries)
    _validated_release_digest = manifest_digest
    _validated_release_object_identity = identity_after
    return manifest


def probe_configured_release_immutable(client: Minio | None = None) -> None:
    """Cheap readiness probe that fails if the pinned release object changes."""
    release_id = configured_release()
    if release_id is None:
        return
    minio_client = client or create_minio_client()
    cache_key = (gtfs_elevation_profiles_bucket(), release_id)
    if _validated_release != cache_key or _validated_release_manifest is None:
        validate_configured_release(minio_client, use_cache=False)
        return
    if _validated_release_object_identity is None:
        # Test doubles and old clients without stat support must use the strong
        # digest check. Production MinIO always takes the cheap stat path.
        validate_configured_release(minio_client, use_cache=False)
        return
    current = _release_object_identity(
        minio_client,
        cache_key[0],
        release_manifest_object_name(release_id),
    )
    if current != _validated_release_object_identity:
        raise ElevationProfileFormatError(
            "Immutable elevation release manifest identity changed after startup"
        )


def elevation_release_runtime_metadata() -> dict[str, Any]:
    """Return non-secret release information suitable for readiness output."""

    runtime = runtime_release_configuration()
    metadata: dict[str, Any] = {
        **runtime.metadata(),
        "aux_bucket": elevation_profiles_bucket(),
        "gtfs_bucket": gtfs_elevation_profiles_bucket(),
        "release_manifest_sha256": _validated_release_digest,
    }
    manifest = _validated_release_manifest
    if manifest is not None:
        roads = manifest.get("roads") if isinstance(manifest.get("roads"), dict) else {}
        metadata.update(
            {
                "profile_algorithm": manifest.get("algorithm_version"),
                "profile_contract_version": manifest.get("profile_contract_version"),
                "roads_release": roads.get("release_id"),
                "roads_sha256": roads.get("sha256"),
                "profile_count": manifest.get("profile_count"),
            }
        )
    return metadata


def validate_production_profile_contract(manifest: dict[str, Any]) -> None:
    """Require the exact v3.3/profile-v2 contract during an atomic v2 switch."""

    runtime = runtime_release_configuration()
    if not runtime.production_v2_active:
        return
    roads = manifest.get("roads")
    if (
        manifest.get("algorithm_version") != ROAD_SNAP_V3_ALGORITHM
        or manifest.get("profile_contract_version")
        != ROAD_SNAP_V3_PROFILE_CONTRACT_VERSION
        or not isinstance(roads, dict)
        or roads.get("release_id") != runtime.aux_roads_release
    ):
        raise ElevationProfileFormatError(
            "Production feature contract v2 requires a road-snap-v3.3-topology "
            "release with profile_contract_version=2 and the pinned roads release"
        )


async def validate_release_covers_database(
    db: AsyncSession,
    manifest: dict[str, Any],
) -> None:
    """Fail a release switch unless every live GTFS shape is committed.

    Production retains more than one GTFS snapshot.  The release namespace is
    global, so its manifest must contain the union of all live GTFS shape IDs;
    validating just the newest snapshot would make older trips unreadable.
    """
    result = await db.execute(
        select(GtfsTrips.shape_id)
        .join(GtfsRoutes, GtfsRoutes.id == GtfsTrips.route_id)
        .where(
            GtfsTrips.status == "gtfs",
            or_(GtfsRoutes.route_type == 3, GtfsRoutes.route_type.between(700, 719)),
        )
        .distinct()
    )
    database_shape_values = result.scalars().all()
    if any(value is None or not str(value).strip() for value in database_shape_values):
        raise ElevationProfileFormatError(
            "Configured elevation release cannot cover live bus GTFS trips "
            "with a missing or blank shape_id"
        )
    database_shapes = {str(value) for value in database_shape_values}
    release_shapes = release_profile_shape_ids(manifest)
    missing = database_shapes - release_shapes
    if missing:
        sample = sorted(missing)[:20]
        raise ElevationProfileFormatError(
            "Configured elevation release does not cover all live bus GTFS snapshots: "
            f"missing={len(missing)} sample={sample}"
        )


def _trip_status(trip: GtfsTrips | Any) -> str:
    value = getattr(trip, "status", "")
    return value.value if hasattr(value, "value") else str(value)


async def get_elevation_profile_job(
    db: AsyncSession,
    trip_id: UUID,
    *,
    for_update: bool = False,
) -> ElevationProfileJobs | None:
    statement = select(ElevationProfileJobs).where(
        ElevationProfileJobs.trip_id == trip_id
    )
    if for_update:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    return result.scalars().first()


async def resolve_trip_profile_location(
    db: AsyncSession,
    trip: GtfsTrips | Any,
) -> ElevationProfileLocation:
    if not getattr(trip, "shape_id", None):
        raise ElevationProfileStorageError(f"Trip {trip.id} has no shape_id")

    if _trip_status(trip) == "gtfs":
        release_id = configured_release()
        return ElevationProfileLocation(
            bucket=gtfs_elevation_profiles_bucket(),
            object_name=gtfs_profile_object_name(trip.shape_id, release_id),
            release_id=release_id,
        )

    job = await get_elevation_profile_job(db, trip.id)
    if job is None:
        raise ElevationProfileNotReadyError(trip.id, "missing")
    if job.status != "succeeded":
        raise ElevationProfileNotReadyError(
            trip.id,
            job.status,
            last_error=job.last_error,
        )
    runtime = runtime_release_configuration()
    if runtime.aux_algorithm is not None and job.algorithm_version != runtime.aux_algorithm:
        raise ElevationProfileNotReadyError(
            trip.id,
            "incompatible",
            last_error=(
                "Auxiliary elevation profile uses an incompatible algorithm: "
                f"job={job.algorithm_version!r}, required={runtime.aux_algorithm!r}"
            ),
        )
    if runtime.aux_roads_release is not None and job.roads_release != runtime.aux_roads_release:
        raise ElevationProfileNotReadyError(
            trip.id,
            "incompatible",
            last_error=(
                "Auxiliary elevation profile uses an incompatible roads release: "
                f"job={job.roads_release!r}, required={runtime.aux_roads_release!r}"
            ),
        )
    return ElevationProfileLocation(
        bucket=elevation_profiles_bucket(), object_name=job.output_object_name
    )


def _with_cumulative_distance(df: pd.DataFrame) -> pd.DataFrame:
    """Preserve the public Parquet schema while filling legacy chainage.

    The feature core derives its explicit horizontal and 3-D columns when it
    consumes this frame. They are intentionally not injected into API records.
    """
    if "cumulative_distance_m" in df.columns:
        return df
    normalized = add_distance_columns(df)
    result = df.copy()
    result["cumulative_distance_m"] = normalized["cumulative_distance_m"]
    return result


def _validate_loaded_profile(
    dataframe: pd.DataFrame,
    *,
    expected_row_count: int | None = None,
    v3_release_entry: dict[str, Any] | None = None,
    v3_matcher: dict[str, Any] | None = None,
) -> None:
    required = {"latitude", "longitude", "altitude_m"}
    missing = required - set(dataframe.columns)
    if missing:
        raise ElevationProfileFormatError(
            f"Elevation profile is missing required columns: {sorted(missing)}"
        )
    if dataframe.empty:
        raise ElevationProfileFormatError("Elevation profile contains no rows")
    if expected_row_count is not None and len(dataframe) != expected_row_count:
        raise ElevationProfileFormatError(
            "Elevation profile row_count does not match its release manifest: "
            f"expected={expected_row_count} actual={len(dataframe)}"
        )
    for column in required:
        values = pd.to_numeric(dataframe[column], errors="coerce")
        if values.isna().any() or not values.map(math.isfinite).all():
            raise ElevationProfileFormatError(
                f"Elevation profile contains non-finite {column} values"
            )
    if v3_release_entry is None:
        return
    if v3_matcher is None:
        raise ElevationProfileFormatError(
            "road-snap-v3 profile cannot be validated without matcher configuration"
        )

    v3_required = {
        "terrain_altitude_m",
        "elevation_source",
        *ROAD_SNAP_V3_ROAD_COLUMNS,
    }
    missing_v3 = v3_required - set(dataframe.columns)
    if missing_v3:
        raise ElevationProfileFormatError(
            "road-snap-v3 profile is missing required columns: "
            f"{sorted(missing_v3)}"
        )

    sources = dataframe["elevation_source"].astype("string")
    matched = sources.eq("road_deck").fillna(False)
    fallback = sources.eq("dtm_fallback").fillna(False)
    if not (matched | fallback).all():
        raise ElevationProfileFormatError(
            "road-snap-v3 profile contains an invalid elevation_source"
        )
    matched_count = int(matched.sum())
    fallback_count = int(fallback.sum())
    if (
        matched_count != v3_release_entry["road_deck_points"]
        or fallback_count != v3_release_entry["dtm_fallback_points"]
    ):
        raise ElevationProfileFormatError(
            "road-snap-v3 profile provenance counts do not match its release manifest"
        )

    terrain = pd.to_numeric(dataframe["terrain_altitude_m"], errors="coerce")
    if terrain.isna().any() or not terrain.map(math.isfinite).all():
        raise ElevationProfileFormatError(
            "road-snap-v3 profile contains non-finite terrain_altitude_m values"
        )
    numeric_road_columns = (
        "road_deck_altitude_m",
        "road_snap_latitude",
        "road_snap_longitude",
        "road_snap_distance_m",
        "elevation_delta_m",
    )
    numeric_road = {
        name: pd.to_numeric(dataframe[name], errors="coerce")
        for name in numeric_road_columns
    }
    if any(
        values.loc[matched].isna().any()
        or not values.loc[matched].map(math.isfinite).all()
        for values in numeric_road.values()
    ):
        raise ElevationProfileFormatError(
            "road-snap-v3 matched row has incomplete or non-finite road values"
        )
    if (numeric_road["road_snap_distance_m"].loc[matched] < 0).any():
        raise ElevationProfileFormatError(
            "road-snap-v3 profile contains a negative road snap distance"
        )

    road_uuid = dataframe["road_uuid"].astype("string")
    if not road_uuid.loc[matched].str.fullmatch(
        ROAD_SNAP_V3_UUID_PATTERN.pattern, na=False
    ).all():
        raise ElevationProfileFormatError(
            "road-snap-v3 matched row has a non-canonical road_uuid"
        )
    road_types = dataframe["road_objektart"].astype("string")
    if (
        road_types.loc[matched].isna().any()
        or road_types.loc[matched].str.strip().eq("").any()
    ):
        raise ElevationProfileFormatError(
            "road-snap-v3 matched row has no road_objektart"
        )
    if any(dataframe[name].loc[fallback].notna().any() for name in ROAD_SNAP_V3_ROAD_COLUMNS):
        raise ElevationProfileFormatError(
            "road-snap-v3 DTM fallback row contains road-only provenance"
        )

    altitude = pd.to_numeric(dataframe["altitude_m"], errors="coerce")
    deck = numeric_road["road_deck_altitude_m"]
    delta = numeric_road["elevation_delta_m"]
    if (
        not (altitude.loc[matched] - deck.loc[matched]).abs().le(1e-6).all()
        or not (altitude.loc[fallback] - terrain.loc[fallback]).abs().le(1e-6).all()
        or not (
            delta.loc[matched] - (deck.loc[matched] - terrain.loc[matched])
        ).abs().le(1e-6).all()
    ):
        raise ElevationProfileFormatError(
            "road-snap-v3 altitude provenance is internally inconsistent"
        )

    metrics = v3_release_entry["metrics"]
    actual_object_counts = {
        str(name): int(count)
        for name, count in road_types.loc[matched]
        .value_counts()
        .sort_index()
        .items()
    }
    actual_metrics = {
        "matched_with_road_uuid": matched_count,
        "distinct_road_uuids": int(road_uuid.loc[matched].nunique()),
        "recovery_ring_points": int(
            (
                numeric_road["road_snap_distance_m"].loc[matched]
                > float(v3_matcher["tolerance_m"])
            ).sum()
        ),
        "conditional_bus_tier_points": int(
            road_types.loc[matched]
            .isin(ROAD_SNAP_V3_CONDITIONAL_OBJECT_TYPES)
            .sum()
        ),
        "matched_by_objektart": actual_object_counts,
    }
    if any(metrics.get(name) != value for name, value in actual_metrics.items()):
        raise ElevationProfileFormatError(
            "road-snap-v3 profile data do not match its release metrics"
        )


async def load_trip_elevation_dataframe(
    db: AsyncSession,
    trip: GtfsTrips | Any,
    *,
    client: Minio | None = None,
) -> pd.DataFrame:
    location = await resolve_trip_profile_location(db, trip)
    minio_client = client or create_minio_client()
    release_entry: dict[str, Any] | None = None
    release_manifest: dict[str, Any] | None = None
    if location.release_id is not None:
        release_manifest = await asyncio.to_thread(
            validate_configured_release, minio_client
        )
        if release_manifest is None:
            raise ElevationProfileFormatError("Configured release manifest is unavailable")
        entries = (
            _validated_release_profiles
            if release_manifest is _validated_release_manifest
            else release_profile_entries(release_manifest)
        )
        release_entry = entries.get(str(trip.shape_id)) if entries is not None else None
        if release_entry is None:
            raise ElevationProfileFormatError(
                f"Shape {trip.shape_id!r} is not committed by release {location.release_id!r}"
            )
    raw = await asyncio.to_thread(
        _read_object, minio_client, location.bucket, location.object_name
    )
    if release_entry is not None:
        actual_digest = hashlib.sha256(raw).hexdigest()
        if (
            len(raw) != release_entry["size_bytes"]
            or actual_digest != release_entry["sha256"]
        ):
            raise ElevationProfileFormatError(
                f"Elevation profile integrity check failed for {location.object_name}"
            )
    try:
        dataframe = await asyncio.to_thread(pd.read_parquet, io.BytesIO(raw))
    except Exception as exc:
        raise ElevationProfileFormatError(
            f"Unable to parse elevation profile {location.object_name}: {exc}"
        ) from exc
    _validate_loaded_profile(
        dataframe,
        expected_row_count=(release_entry["row_count"] if release_entry else None),
        v3_release_entry=(
            release_entry
            if release_manifest is not None and _is_v3_release(release_manifest)
            else None
        ),
        v3_matcher=(
            release_manifest["matcher"]
            if release_manifest is not None and _is_v3_release(release_manifest)
            else None
        ),
    )
    return _with_cumulative_distance(dataframe)


def dataframe_json_records(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    """Return records containing JSON-safe scalars and explicit nulls.

    Nullable numeric Parquet columns round-trip through pandas as NaN.  Python's
    default JSON encoder rejects those values, so every pandas/numpy missing or
    non-finite scalar is converted to ``None`` before FastAPI serializes it.
    """
    records: list[dict[str, Any]] = []
    for raw_record in dataframe.to_dict(orient="records"):
        record: dict[str, Any] = {}
        for key, value in raw_record.items():
            try:
                missing = bool(pd.isna(value))
            except (TypeError, ValueError):
                missing = False
            item = getattr(value, "item", None)
            normalized = item() if callable(item) else value
            if missing or (
                isinstance(normalized, Real)
                and not math.isfinite(float(normalized))
            ):
                record[key] = None
                continue
            record[key] = normalized
        records.append(record)
    return records


async def load_trip_elevation_by_id(
    db: AsyncSession,
    trip_id: UUID,
    *,
    client: Minio | None = None,
) -> tuple[GtfsTrips, pd.DataFrame]:
    trip = await db.get(GtfsTrips, trip_id)
    if trip is None:
        raise LookupError(f"Trip {trip_id} not found")
    return trip, await load_trip_elevation_dataframe(db, trip, client=client)


async def ensure_shift_profiles_ready(
    db: AsyncSession,
    shift_ids: Iterable[UUID],
) -> None:
    ids = list(shift_ids)
    if not ids:
        return
    result = await db.execute(
        select(GtfsTrips)
        .join(ShiftsStructures, ShiftsStructures.trip_id == GtfsTrips.id)
        .where(ShiftsStructures.shift_id.in_(ids))
        .distinct()
    )
    for trip in result.scalars().all():
        if _trip_status(trip) != "gtfs":
            await resolve_trip_profile_location(db, trip)


def remove_profile_objects(
    objects: Iterable[tuple[str, str]],
    *,
    client: Minio | None = None,
) -> list[str]:
    """Best-effort cleanup used after the owning trip was deleted.

    Returns human-readable errors so callers can report/log orphan cleanup
    without pretending the already-committed database deletion failed.
    """
    try:
        minio_client = client or create_minio_client()
    except Exception as exc:
        return [f"MinIO client initialization failed: {exc}"]
    errors: list[str] = []
    for bucket, object_name in sorted(set(objects)):
        try:
            minio_client.remove_object(bucket, object_name)
        except Exception as exc:
            errors.append(f"{bucket}/{object_name}: {exc}")
    return errors
