"""Immutable consumption-model metadata preflight for production contract v2."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from simulation.consumption_prediction import validate_model_feature_contract
from simulation.minio_utils import build_model_path, get_minio_client

from app.services.runtime_release import (
    ROAD_SNAP_V3_ALGORITHM,
    runtime_release_configuration,
)


MODEL_BUCKET = "consumption-models"


class ModelReleaseValidationError(RuntimeError):
    """Raised when a configured production model is absent or incompatible."""


_validated_model: str | None = None
_validated_metadata: dict[str, Any] | None = None
_validated_metadata_sha256: str | None = None
_validated_model_identity: tuple[Any, ...] | None = None
_validated_metadata_identity: tuple[Any, ...] | None = None


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
            f"Unable to read configured model metadata {bucket}/{object_name}: {exc}"
        ) from exc


def _metadata_path(model_name: str) -> str:
    model_path = build_model_path(model_name)
    return model_path.rsplit(".", 1)[0] + "_metadata.json"


def validate_configured_model_release(
    elevation_manifest: dict[str, Any],
    *,
    client=None,
) -> dict[str, Any] | None:
    """Validate and pin the configured model and its training provenance."""

    global _validated_model, _validated_metadata, _validated_metadata_sha256
    global _validated_model_identity, _validated_metadata_identity

    runtime = runtime_release_configuration()
    model_name = runtime.consumption_model_release
    if model_name is None:
        _validated_model = None
        _validated_metadata = None
        _validated_metadata_sha256 = None
        _validated_model_identity = None
        _validated_metadata_identity = None
        return None

    minio_client = client or get_minio_client()
    model_path = build_model_path(model_name)
    metadata_path = _metadata_path(model_name)
    model_identity = _object_identity(minio_client, MODEL_BUCKET, model_path)
    metadata_identity_before = _object_identity(
        minio_client, MODEL_BUCKET, metadata_path
    )
    raw = _read_object(minio_client, MODEL_BUCKET, metadata_path)
    metadata_identity_after = _object_identity(
        minio_client, MODEL_BUCKET, metadata_path
    )
    if metadata_identity_before != metadata_identity_after:
        raise ModelReleaseValidationError(
            "Configured model metadata changed while it was being validated"
        )
    digest = hashlib.sha256(raw).hexdigest()
    if _validated_model == model_name and (
        _validated_metadata_sha256 != digest
        or _validated_model_identity != model_identity
        or _validated_metadata_identity != metadata_identity_after
    ):
        raise ModelReleaseValidationError(
            "Configured model release changed under its immutable name"
        )
    try:
        metadata = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelReleaseValidationError(
            f"Configured model metadata is not valid JSON: {exc}"
        ) from exc
    if not isinstance(metadata, dict):
        raise ModelReleaseValidationError("Configured model metadata must be an object")
    if metadata.get("model_name") != model_name:
        raise ModelReleaseValidationError(
            "Configured model metadata does not declare the pinned model name"
        )
    try:
        validate_model_feature_contract(metadata)
    except ValueError as exc:
        raise ModelReleaseValidationError(str(exc)) from exc

    feature_release = metadata.get("feature_release")
    profiles = (
        feature_release.get("profiles") if isinstance(feature_release, dict) else None
    )
    road_snap = profiles.get("road_snap") if isinstance(profiles, dict) else None
    roads_asset = profiles.get("roads_asset") if isinstance(profiles, dict) else None
    elevation_roads = elevation_manifest.get("roads")
    if (
        not isinstance(feature_release, dict)
        or not isinstance(feature_release.get("release_id"), str)
        or not isinstance(feature_release.get("manifest_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", feature_release["manifest_sha256"])
        is None
        or not isinstance(profiles, dict)
        or profiles.get("profile_contract_version") != 2
        or not isinstance(profiles.get("profile_release"), str)
        or not isinstance(road_snap, dict)
        or road_snap.get("algorithm_version") != ROAD_SNAP_V3_ALGORITHM
        or not isinstance(roads_asset, dict)
        or not isinstance(elevation_roads, dict)
        or roads_asset.get("release_id") != elevation_roads.get("release_id")
        or roads_asset.get("sha256") != elevation_roads.get("sha256")
    ):
        raise ModelReleaseValidationError(
            "Configured model was not trained with the active road-snap v3.3 "
            "feature/profile/roads contract"
        )

    _validated_model = model_name
    _validated_metadata = metadata
    _validated_metadata_sha256 = digest
    _validated_model_identity = model_identity
    _validated_metadata_identity = metadata_identity_after
    return metadata


def probe_configured_model_immutable(*, client=None) -> None:
    """Fail readiness if either object behind the pinned model name changes."""

    runtime = runtime_release_configuration()
    model_name = runtime.consumption_model_release
    if model_name is None:
        return
    if _validated_model != model_name or _validated_metadata is None:
        raise ModelReleaseValidationError(
            "Configured production model has not passed startup validation"
        )
    minio_client = client or get_minio_client()
    current_model = _object_identity(
        minio_client, MODEL_BUCKET, build_model_path(model_name)
    )
    current_metadata = _object_identity(
        minio_client, MODEL_BUCKET, _metadata_path(model_name)
    )
    if (
        current_model != _validated_model_identity
        or current_metadata != _validated_metadata_identity
    ):
        raise ModelReleaseValidationError(
            "Configured model release identity changed after startup"
        )


def model_release_runtime_metadata() -> dict[str, Any]:
    runtime = runtime_release_configuration()
    feature_release = (
        _validated_metadata.get("feature_release")
        if isinstance(_validated_metadata, dict)
        else None
    )
    profiles = feature_release.get("profiles") if isinstance(feature_release, dict) else None
    return {
        "bucket": MODEL_BUCKET,
        "model_release": runtime.consumption_model_release,
        "metadata_sha256": _validated_metadata_sha256,
        "training_feature_release": (
            feature_release.get("release_id") if isinstance(feature_release, dict) else None
        ),
        "training_profile_release": (
            profiles.get("profile_release") if isinstance(profiles, dict) else None
        ),
    }
