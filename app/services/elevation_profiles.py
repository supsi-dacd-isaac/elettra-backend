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

from app.models import ElevationProfileJobs, GtfsRoutes, GtfsTrips, ShiftsStructures


DEFAULT_BUCKET = "elevation-profiles"
RELEASE_MANIFEST_NAME = "release.json"
PENDING_RETRY_AFTER_SECONDS = 5
RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


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
    value = os.getenv("ELEVATION_PROFILES_BUCKET", DEFAULT_BUCKET).strip()
    return value or DEFAULT_BUCKET


def configured_release() -> str | None:
    value = os.getenv("ELEVATION_PROFILES_RELEASE", "").strip()
    if not value:
        return None
    return validate_release_id(value)


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


def _validate_roads_manifest(roads: Any) -> None:
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

    bucket = elevation_profiles_bucket()
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

    profile_entries = release_profile_entries(manifest)
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
    _validate_roads_manifest(manifest.get("roads"))
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
    cache_key = (elevation_profiles_bucket(), release_id)
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

    bucket = elevation_profiles_bucket()
    if _trip_status(trip) == "gtfs":
        release_id = configured_release()
        return ElevationProfileLocation(
            bucket=bucket,
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
    return ElevationProfileLocation(bucket=bucket, object_name=job.output_object_name)


def _with_cumulative_distance(df: pd.DataFrame) -> pd.DataFrame:
    if "cumulative_distance_m" in df.columns:
        return df
    result = df.copy()
    if len(result) <= 1:
        result["cumulative_distance_m"] = [0.0] * len(result)
        return result

    required = {"latitude", "longitude"}
    if not required.issubset(result.columns):
        return result

    distances = [0.0]
    for index in range(1, len(result)):
        lat1 = math.radians(float(result.iloc[index - 1]["latitude"]))
        lon1 = math.radians(float(result.iloc[index - 1]["longitude"]))
        lat2 = math.radians(float(result.iloc[index]["latitude"]))
        lon2 = math.radians(float(result.iloc[index]["longitude"]))
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        haversine = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        )
        distance = (
            2
            * math.asin(min(1.0, math.sqrt(max(0.0, haversine))))
            * 6_371_000
        )
        distances.append(distances[-1] + distance)
    result["cumulative_distance_m"] = distances
    return result


def _validate_loaded_profile(
    dataframe: pd.DataFrame,
    *,
    expected_row_count: int | None = None,
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


async def load_trip_elevation_dataframe(
    db: AsyncSession,
    trip: GtfsTrips | Any,
    *,
    client: Minio | None = None,
) -> pd.DataFrame:
    location = await resolve_trip_profile_location(db, trip)
    minio_client = client or create_minio_client()
    release_entry: dict[str, Any] | None = None
    if location.release_id is not None:
        manifest = await asyncio.to_thread(
            validate_configured_release, minio_client
        )
        if manifest is None:
            raise ElevationProfileFormatError("Configured release manifest is unavailable")
        entries = (
            _validated_release_profiles
            if manifest is _validated_release_manifest
            else release_profile_entries(manifest)
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
