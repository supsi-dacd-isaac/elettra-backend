"""Manifest-last consumption-model preflight for production contract v2."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from elettra_core import FEATURE_CONTRACT_VERSION
from simulation.consumption_prediction import validate_model_feature_contract
from simulation.minio_utils import build_model_path, get_minio_client

from app.services.runtime_release import (
    ROAD_SNAP_V3_ALGORITHM,
    runtime_release_configuration,
)


MODEL_BUCKET = "consumption-models"
MODEL_RELEASE_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ModelReleaseValidationError(RuntimeError):
    """Raised when a configured production model release is not committed."""


_validated_model: str | None = None
_validated_metadata: dict[str, Any] | None = None
_validated_metadata_sha256: str | None = None
_validated_release_manifest: dict[str, Any] | None = None
_validated_release_manifest_sha256: str | None = None
_validated_release_manifest_identity: tuple[Any, ...] | None = None
_validated_artifact_identities: dict[str, tuple[Any, ...]] | None = None


def _object_identity(client, bucket: str, object_name: str) -> tuple[Any, ...]:
    try:
        stat = client.stat_object(bucket, object_name)
    except Exception as exc:
        raise ModelReleaseValidationError(
            f"Unable to stat configured model object {bucket}/{object_name}: {exc}"
        ) from exc
    size = int(getattr(stat, "size", -1))
    if size <= 0:
        raise ModelReleaseValidationError(
            f"Configured model object is empty: {bucket}/{object_name}"
        )
    return (
        size,
        getattr(stat, "etag", None),
        getattr(stat, "version_id", None),
    )


def _read_object(client, bucket: str, object_name: str) -> bytes:
    try:
        response = client.get_object(bucket, object_name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
    except Exception as exc:
        raise ModelReleaseValidationError(
            f"Unable to read configured model object {bucket}/{object_name}: {exc}"
        ) from exc


def _artifact_paths(model_name: str) -> dict[str, str]:
    prefix = f"models/{model_name}/"
    return {
        "model": build_model_path(model_name),
        "metadata": f"{prefix}{model_name}_metadata.json",
        "importance": f"{prefix}{model_name}_feature_importance.csv",
        "acceptance": f"{prefix}{model_name}_acceptance.json",
        "manifest": f"{prefix}{model_name}_release.json",
    }


def _parse_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelReleaseValidationError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ModelReleaseValidationError(f"{label} must be a JSON object")
    return value


def _validate_artifact_entry(entry: Any, *, filename: str) -> tuple[int, str]:
    if (
        not isinstance(entry, dict)
        or not isinstance(entry.get("size_bytes"), int)
        or isinstance(entry.get("size_bytes"), bool)
        or entry["size_bytes"] <= 0
        or not isinstance(entry.get("sha256"), str)
        or _SHA256_PATTERN.fullmatch(entry["sha256"]) is None
    ):
        raise ModelReleaseValidationError(
            f"Model release manifest has invalid integrity data for {filename!r}"
        )
    return int(entry["size_bytes"]), str(entry["sha256"])


def _validate_and_read_artifact(
    client,
    *,
    object_name: str,
    entry: Any,
    collect: bool = False,
) -> tuple[bytes | None, tuple[Any, ...], str]:
    filename = object_name.rsplit("/", 1)[-1]
    expected_size, expected_sha256 = _validate_artifact_entry(entry, filename=filename)
    identity_before = _object_identity(client, MODEL_BUCKET, object_name)
    if identity_before[0] != expected_size:
        raise ModelReleaseValidationError(
            f"Model artifact size mismatch for {filename!r}: "
            f"manifest={expected_size}, object={identity_before[0]}"
        )
    try:
        response = client.get_object(MODEL_BUCKET, object_name)
        digest = hashlib.sha256()
        size = 0
        chunks: list[bytes] | None = [] if collect else None
        try:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                if chunks is not None:
                    chunks.append(chunk)
        finally:
            response.close()
            response.release_conn()
    except Exception as exc:
        raise ModelReleaseValidationError(
            f"Unable to read configured model artifact {MODEL_BUCKET}/{object_name}: {exc}"
        ) from exc
    identity_after = _object_identity(client, MODEL_BUCKET, object_name)
    if identity_before != identity_after:
        raise ModelReleaseValidationError(
            f"Model artifact changed while it was validated: {filename!r}"
        )
    actual_sha256 = digest.hexdigest()
    if size != expected_size or actual_sha256 != expected_sha256:
        raise ModelReleaseValidationError(
            f"Model artifact integrity mismatch for {filename!r}"
        )
    return (b"".join(chunks) if chunks is not None else None), identity_after, actual_sha256


def _clear_validation_cache() -> None:
    global _validated_model, _validated_metadata, _validated_metadata_sha256
    global _validated_release_manifest, _validated_release_manifest_sha256
    global _validated_release_manifest_identity, _validated_artifact_identities
    _validated_model = None
    _validated_metadata = None
    _validated_metadata_sha256 = None
    _validated_release_manifest = None
    _validated_release_manifest_sha256 = None
    _validated_release_manifest_identity = None
    _validated_artifact_identities = None


def validate_configured_model_release(
    elevation_manifest: dict[str, Any],
    *,
    client=None,
) -> dict[str, Any] | None:
    """Require a complete manifest-last release and verify every artifact."""

    global _validated_model, _validated_metadata, _validated_metadata_sha256
    global _validated_release_manifest, _validated_release_manifest_sha256
    global _validated_release_manifest_identity, _validated_artifact_identities

    runtime = runtime_release_configuration()
    model_name = runtime.consumption_model_release
    if model_name is None:
        _clear_validation_cache()
        return None

    minio_client = client or get_minio_client()
    paths = _artifact_paths(model_name)
    manifest_identity_before = _object_identity(
        minio_client, MODEL_BUCKET, paths["manifest"]
    )
    manifest_raw = _read_object(minio_client, MODEL_BUCKET, paths["manifest"])
    manifest_identity_after = _object_identity(
        minio_client, MODEL_BUCKET, paths["manifest"]
    )
    if (
        manifest_identity_before != manifest_identity_after
        or len(manifest_raw) != manifest_identity_after[0]
    ):
        raise ModelReleaseValidationError(
            "Model release manifest changed while it was being validated"
        )
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    if _validated_model == model_name and (
        _validated_release_manifest_sha256 != manifest_sha256
        or _validated_release_manifest_identity != manifest_identity_after
    ):
        raise ModelReleaseValidationError(
            "Configured model release manifest changed under its immutable name"
        )
    release = _parse_json(manifest_raw, label="Model release manifest")
    expected_prefix = f"models/{model_name}/"
    publication = release.get("publication")
    if (
        release.get("schema_version") != MODEL_RELEASE_SCHEMA_VERSION
        or release.get("release_id") != model_name
        or release.get("feature_contract_version") != FEATURE_CONTRACT_VERSION
        or not isinstance(release.get("categorical_feature_contract"), dict)
        or not isinstance(publication, dict)
        or publication.get("object_prefix") != expected_prefix
        or publication.get("manifest_last") is not True
        or publication.get("immutable") is not True
        or not isinstance(release.get("acceptance"), dict)
        or release["acceptance"].get("decision") != "passed"
    ):
        raise ModelReleaseValidationError(
            "Configured model release manifest is incomplete or not publishable"
        )

    expected_filenames = {
        paths["model"].rsplit("/", 1)[-1],
        paths["metadata"].rsplit("/", 1)[-1],
        paths["importance"].rsplit("/", 1)[-1],
        paths["acceptance"].rsplit("/", 1)[-1],
    }
    artifacts = release.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != expected_filenames:
        raise ModelReleaseValidationError(
            "Model release manifest must commit exactly model, metadata, "
            "feature-importance and acceptance artifacts"
        )

    metadata_raw: bytes | None = None
    artifact_identities: dict[str, tuple[Any, ...]] = {}
    artifact_sha256: dict[str, str] = {}
    for role in ("model", "metadata", "importance", "acceptance"):
        object_name = paths[role]
        filename = object_name.rsplit("/", 1)[-1]
        raw, identity, digest = _validate_and_read_artifact(
            minio_client,
            object_name=object_name,
            entry=artifacts[filename],
            collect=role == "metadata",
        )
        if role == "metadata":
            metadata_raw = raw
        artifact_identities[object_name] = identity
        artifact_sha256[role] = digest

    manifest_identity_final = _object_identity(
        minio_client, MODEL_BUCKET, paths["manifest"]
    )
    if manifest_identity_final != manifest_identity_after:
        raise ModelReleaseValidationError(
            "Model release manifest changed while artifacts were validated"
        )

    if metadata_raw is None:  # pragma: no cover - protected by the fixed role loop
        raise ModelReleaseValidationError("Model metadata was not collected")
    metadata = _parse_json(metadata_raw, label="Model metadata")
    if metadata.get("model_name") != model_name:
        raise ModelReleaseValidationError(
            "Configured model metadata does not declare the manifest release ID"
        )
    try:
        validate_model_feature_contract(metadata)
    except ValueError as exc:
        raise ModelReleaseValidationError(str(exc)) from exc

    feature_release = metadata.get("feature_release")
    profiles = feature_release.get("profiles") if isinstance(feature_release, dict) else None
    road_snap = profiles.get("road_snap") if isinstance(profiles, dict) else None
    roads_asset = profiles.get("roads_asset") if isinstance(profiles, dict) else None
    elevation_roads = elevation_manifest.get("roads")
    release_feature = release.get("feature_release")
    acceptance_filename = paths["acceptance"].rsplit("/", 1)[-1]
    if (
        metadata.get("feature_contract_version") != release.get("feature_contract_version")
        or metadata.get("categorical_feature_contract")
        != release.get("categorical_feature_contract")
        or metadata.get("auxiliary_estimator") != release.get("auxiliary_estimator")
        or metadata.get("training_software") != release.get("training_software")
        or not isinstance(feature_release, dict)
        or not isinstance(release_feature, dict)
        or any(
            feature_release.get(key) != release_feature.get(key)
            for key in ("release_id", "manifest_sha256", "row_identity_sha256")
        )
        or not isinstance(feature_release.get("manifest_sha256"), str)
        or _SHA256_PATTERN.fullmatch(feature_release["manifest_sha256"]) is None
        or not isinstance(profiles, dict)
        or profiles.get("profile_contract_version") != 2
        or not isinstance(profiles.get("profile_release"), str)
        or not isinstance(road_snap, dict)
        or road_snap.get("algorithm_version") != ROAD_SNAP_V3_ALGORITHM
        or not isinstance(roads_asset, dict)
        or not isinstance(elevation_roads, dict)
        or roads_asset.get("release_id") != elevation_roads.get("release_id")
        or roads_asset.get("sha256") != elevation_roads.get("sha256")
        or release["acceptance"].get("sha256")
        != artifacts[acceptance_filename].get("sha256")
        or release["acceptance"].get("size_bytes")
        != artifacts[acceptance_filename].get("size_bytes")
    ):
        raise ModelReleaseValidationError(
            "Configured model metadata does not match its release manifest or "
            "the active road-snap v3.3 feature/profile/roads contract"
        )

    _validated_model = model_name
    _validated_metadata = metadata
    _validated_metadata_sha256 = artifact_sha256["metadata"]
    _validated_release_manifest = release
    _validated_release_manifest_sha256 = manifest_sha256
    _validated_release_manifest_identity = manifest_identity_final
    _validated_artifact_identities = artifact_identities
    return metadata


def probe_configured_model_immutable(*, client=None) -> None:
    """Re-read the commit marker and fail if any pinned object was replaced."""

    runtime = runtime_release_configuration()
    model_name = runtime.consumption_model_release
    if model_name is None:
        return
    if (
        _validated_model != model_name
        or _validated_metadata is None
        or _validated_release_manifest_sha256 is None
        or _validated_release_manifest_identity is None
        or _validated_artifact_identities is None
    ):
        raise ModelReleaseValidationError(
            "Configured production model has not passed startup validation"
        )
    minio_client = client or get_minio_client()
    paths = _artifact_paths(model_name)
    manifest_identity_before = _object_identity(
        minio_client, MODEL_BUCKET, paths["manifest"]
    )
    manifest_raw = _read_object(minio_client, MODEL_BUCKET, paths["manifest"])
    manifest_identity_after = _object_identity(
        minio_client, MODEL_BUCKET, paths["manifest"]
    )
    if (
        manifest_identity_before != manifest_identity_after
        or len(manifest_raw) != manifest_identity_after[0]
        or manifest_identity_after != _validated_release_manifest_identity
        or hashlib.sha256(manifest_raw).hexdigest()
        != _validated_release_manifest_sha256
    ):
        raise ModelReleaseValidationError(
            "Configured model release manifest changed after startup"
        )
    for object_name, expected_identity in _validated_artifact_identities.items():
        if _object_identity(minio_client, MODEL_BUCKET, object_name) != expected_identity:
            raise ModelReleaseValidationError(
                f"Configured model artifact changed after startup: {object_name}"
            )


def model_release_runtime_metadata() -> dict[str, Any]:
    runtime = runtime_release_configuration()
    feature_release = (
        _validated_metadata.get("feature_release")
        if isinstance(_validated_metadata, dict)
        else None
    )
    profiles = feature_release.get("profiles") if isinstance(feature_release, dict) else None
    identity = _validated_release_manifest_identity
    return {
        "bucket": MODEL_BUCKET,
        "model_release": runtime.consumption_model_release,
        "release_manifest_sha256": _validated_release_manifest_sha256,
        "release_manifest_identity": (
            {
                "size_bytes": identity[0],
                "etag": identity[1],
                "version_id": identity[2],
            }
            if identity is not None
            else None
        ),
        "metadata_sha256": _validated_metadata_sha256,
        "artifact_count": (
            len(_validated_artifact_identities)
            if _validated_artifact_identities is not None
            else 0
        ),
        "training_feature_release": (
            feature_release.get("release_id") if isinstance(feature_release, dict) else None
        ),
        "training_profile_release": (
            profiles.get("profile_release") if isinstance(profiles, dict) else None
        ),
    }
