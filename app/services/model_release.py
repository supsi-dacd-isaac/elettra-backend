"""Manifest-last consumption-model preflight for production contract v2."""

from __future__ import annotations

import hashlib
import io
import json
import re
from typing import Any

import joblib
import numpy as np

from elettra_core import (
    FEATURE_CONTRACT_VERSION,
    GREYBOX_PRED_FEATURE,
    RAW_MODEL_FEATURE_COLUMNS,
    __version__ as ELETTRA_CORE_VERSION,
    categorical_feature_contract,
)
from elettra_core.greybox import (
    CappedRegenAffineGreyBox,
    HybridGreyboxQRF,
    LinearGreyBox,
)
from simulation.consumption_prediction import (
    _register_legacy_pickle_symbols,
    validate_model_feature_contract,
)
from simulation.minio_utils import build_model_path, get_minio_client

from app.services.runtime_release import (
    PredictionStackRelease,
    PredictionStack,
    ROAD_SNAP_V3_ALGORITHM,
    runtime_release_configuration,
    validate_g2_passenger_prior,
    validate_model_stack_contract,
)


MODEL_BUCKET = "consumption-models"
MODEL_RELEASE_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
ELETTRA_CORE_TAG = f"elettra-core-v{ELETTRA_CORE_VERSION}"


class ModelReleaseValidationError(RuntimeError):
    """Raised when a configured production model release is not committed."""


_validated_model: str | None = None
_validated_metadata: dict[str, Any] | None = None
_validated_metadata_sha256: str | None = None
_validated_release_manifest: dict[str, Any] | None = None
_validated_release_manifest_sha256: str | None = None
_validated_release_manifest_identity: tuple[Any, ...] | None = None
_validated_artifact_identities: dict[str, tuple[Any, ...]] | None = None
_validated_model_releases: dict[str, dict[str, Any]] = {}


def _servable_selected_features() -> set[str]:
    categorical = categorical_feature_contract()["elevation_profile_type"]
    return (
        set(RAW_MODEL_FEATURE_COLUMNS)
        - {"elevation_profile_type"}
        | set(categorical["dummy_columns"])
        | {GREYBOX_PRED_FEATURE}
    )


def _validate_selected_features(selected_features: Any) -> list[str]:
    if (
        not isinstance(selected_features, list)
        or not selected_features
        or not all(isinstance(value, str) and value for value in selected_features)
        or len(selected_features) != len(set(selected_features))
    ):
        raise ModelReleaseValidationError(
            "VECTO model metadata must declare unique non-empty selected_features"
        )
    forbidden = {
        "bus_number",
        "manufacturer",
        "manufacturer_name",
        "bus_id",
        # The shared HybridGreyboxQRF deliberately removes this context from
        # the residual learner so extrapolation follows the physical mass
        # model rather than a learned battery-size shortcut.
        "bus_battery_kwh",
    }
    rejected = sorted(set(selected_features) & forbidden)
    unavailable = sorted(set(selected_features) - _servable_selected_features())
    if rejected or unavailable:
        raise ModelReleaseValidationError(
            "VECTO model selected_features cannot be served by feature contract v2: "
            f"forbidden={rejected}, unavailable={unavailable}"
        )
    return selected_features


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
    global _validated_model_releases
    _validated_model = None
    _validated_metadata = None
    _validated_metadata_sha256 = None
    _validated_release_manifest = None
    _validated_release_manifest_sha256 = None
    _validated_release_manifest_identity = None
    _validated_artifact_identities = None
    _validated_model_releases = {}


def _validate_loaded_model_artifact(
    model: Any,
    metadata: dict[str, Any],
    stack_release: PredictionStackRelease,
) -> None:
    """Bind the deserialized Python artifact to its registered semantics."""

    if stack_release.stack is PredictionStack.LEGACY:
        if isinstance(model, HybridGreyboxQRF):
            raise ModelReleaseValidationError(
                "A VECTO HybridGreyboxQRF artifact cannot be registered as legacy"
            )
        if not callable(getattr(model, "predict", None)):
            raise ModelReleaseValidationError(
                "Legacy model artifact has no callable predict method"
            )
        selected_features = metadata.get("selected_features")
        if not isinstance(selected_features, list) or not selected_features:
            raise ModelReleaseValidationError(
                "Legacy model metadata must declare selected_features"
            )
        artifact_features = getattr(model, "selected_features", None)
        if artifact_features is not None and list(artifact_features) != list(
            selected_features
        ):
            raise ModelReleaseValidationError(
                "Legacy model selected_features do not match metadata"
            )
        estimator = getattr(model, "qrf", model)
        n_features = getattr(estimator, "n_features_in_", None)
        estimators = getattr(estimator, "estimators_", None)
        if (
            not isinstance(n_features, (int, np.integer))
            or int(n_features) != len(selected_features)
            or not isinstance(estimators, (list, tuple))
            or not estimators
        ):
            raise ModelReleaseValidationError(
                "Legacy model artifact contains an unfitted or incompatible estimator"
            )
        greybox = getattr(model, "greybox", None)
        if greybox is not None and getattr(greybox, "params_", None) is None:
            raise ModelReleaseValidationError(
                "Legacy model artifact contains an unfitted grey-box estimator"
            )
        return
    if not isinstance(model, HybridGreyboxQRF):
        raise ModelReleaseValidationError(
            f"Stack {stack_release.stack.value!r} requires HybridGreyboxQRF"
        )
    selected_features = _validate_selected_features(
        metadata.get("selected_features")
    )
    if model.prediction_stack != stack_release.stack.value:
        raise ModelReleaseValidationError(
            "Model artifact prediction_stack does not match its registry entry"
        )
    artifact_reference_occupancy = getattr(
        model,
        "qrf_reference_occupancy_percent",
        None,
    )
    if stack_release.stack is PredictionStack.VECTO_G2:
        prior = validate_g2_passenger_prior(metadata)
        declared_reference_occupancy = float(
            prior["qrf_reference_occupancy_percent"]
        )
        if (
            artifact_reference_occupancy is None
            or float(artifact_reference_occupancy)
            != declared_reference_occupancy
            or GREYBOX_PRED_FEATURE not in model.selected_features
        ):
            raise ModelReleaseValidationError(
                "G2 artifact QRF reference occupancy does not match metadata"
            )
    elif artifact_reference_occupancy is not None:
        raise ModelReleaseValidationError(
            "Only vecto-g2 artifacts may declare QRF reference occupancy"
        )
    expected_greybox_type = (
        CappedRegenAffineGreyBox
        if stack_release.stack is PredictionStack.VECTO_G2
        else LinearGreyBox
    )
    if not isinstance(model.greybox, expected_greybox_type):
        raise ModelReleaseValidationError(
            f"Stack {stack_release.stack.value!r} contains the wrong grey-box class"
        )
    theta = getattr(model.greybox, "theta_", None)
    if (
        theta is None
        or np.asarray(theta).shape != (len(model.greybox.parameter_names()),)
        or not np.isfinite(np.asarray(theta, dtype=float)).all()
    ):
        raise ModelReleaseValidationError(
            "Model artifact contains an unfitted or invalid grey-box estimator"
        )
    declared_greybox_params = metadata.get("greybox_params")
    artifact_greybox_params = model.greybox.get_params_dict()
    if (
        not isinstance(declared_greybox_params, dict)
        or set(declared_greybox_params) != set(artifact_greybox_params)
        or any(
            isinstance(declared_greybox_params.get(name), bool)
            or not isinstance(declared_greybox_params.get(name), (int, float))
            or not np.isfinite(float(declared_greybox_params[name]))
            or float(declared_greybox_params[name]) != float(value)
            for name, value in artifact_greybox_params.items()
        )
    ):
        raise ModelReleaseValidationError(
            "Model metadata greybox_params do not exactly match the fitted artifact"
        )
    if list(model.selected_features) != selected_features:
        raise ModelReleaseValidationError(
            "Model artifact selected_features do not match metadata"
        )
    if not callable(getattr(model.qrf, "predict", None)):
        raise ModelReleaseValidationError("Model artifact QRF has no predict method")
    qrf_features = getattr(model.qrf, "n_features_in_", None)
    qrf_estimators = getattr(model.qrf, "estimators_", None)
    qrf_feature_names = getattr(model.qrf, "feature_names_in_", None)
    if (
        not isinstance(qrf_features, (int, np.integer))
        or int(qrf_features) != len(model.selected_features)
        or not isinstance(qrf_estimators, (list, tuple))
        or not qrf_estimators
        or qrf_feature_names is None
        or list(qrf_feature_names) != list(model.selected_features)
    ):
        raise ModelReleaseValidationError(
            "Model artifact contains an unfitted or incompatible QRF estimator "
            "or feature order"
        )


def _validate_one_model_release(
    elevation_manifest: dict[str, Any],
    *,
    model_name: str,
    stack_release: PredictionStackRelease,
    client=None,
) -> dict[str, Any]:
    """Require a complete manifest-last release and verify every artifact."""

    global _validated_model, _validated_metadata, _validated_metadata_sha256
    global _validated_release_manifest, _validated_release_manifest_sha256
    global _validated_release_manifest_identity, _validated_artifact_identities
    global _validated_model_releases

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
    previously_validated = _validated_model_releases.get(model_name)
    if previously_validated is not None and (
        previously_validated["manifest_sha256"] != manifest_sha256
        or previously_validated["manifest_identity"] != manifest_identity_after
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
        or release["acceptance"].get("decision")
        not in {"passed", "approved_with_documented_regression"}
    ):
        raise ModelReleaseValidationError(
            "Configured model release manifest is incomplete or not publishable"
        )
    acceptance_decision = release["acceptance"].get("decision")
    documented_approval = release["acceptance"].get("documented_approval")
    if acceptance_decision == "approved_with_documented_regression" and (
        not isinstance(documented_approval, dict)
        or not all(
            isinstance(documented_approval.get(key), str)
            and documented_approval[key].strip()
            for key in ("approved_by", "approved_at", "reason", "evaluation_sha256")
        )
        or _SHA256_PATTERN.fullmatch(documented_approval["evaluation_sha256"])
        is None
    ):
        raise ModelReleaseValidationError(
            "A controlled-regression release requires immutable documented approval"
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
    model_raw: bytes | None = None
    acceptance_raw: bytes | None = None
    artifact_identities: dict[str, tuple[Any, ...]] = {}
    artifact_sha256: dict[str, str] = {}
    for role in ("model", "metadata", "importance", "acceptance"):
        object_name = paths[role]
        filename = object_name.rsplit("/", 1)[-1]
        raw, identity, digest = _validate_and_read_artifact(
            minio_client,
            object_name=object_name,
            entry=artifacts[filename],
            collect=role in {"model", "metadata", "acceptance"},
        )
        if role == "metadata":
            metadata_raw = raw
        elif role == "model":
            model_raw = raw
        elif role == "acceptance":
            acceptance_raw = raw
        artifact_identities[object_name] = identity
        artifact_sha256[role] = digest

    manifest_identity_final = _object_identity(
        minio_client, MODEL_BUCKET, paths["manifest"]
    )
    if manifest_identity_final != manifest_identity_after:
        raise ModelReleaseValidationError(
            "Model release manifest changed while artifacts were validated"
        )

    if (  # pragma: no cover - fixed loop
        metadata_raw is None or model_raw is None or acceptance_raw is None
    ):
        raise ModelReleaseValidationError(
            "Model, metadata or acceptance artifact was not collected"
        )
    metadata = _parse_json(metadata_raw, label="Model metadata")
    acceptance_report = _parse_json(
        acceptance_raw, label="Model acceptance report"
    )
    if metadata.get("model_name") != model_name:
        raise ModelReleaseValidationError(
            "Configured model metadata does not declare the manifest release ID"
        )
    try:
        validate_model_feature_contract(metadata)
        validate_model_stack_contract(stack_release, metadata)
    except ValueError as exc:
        raise ModelReleaseValidationError(str(exc)) from exc
    except RuntimeError as exc:
        raise ModelReleaseValidationError(str(exc)) from exc
    try:
        _register_legacy_pickle_symbols()
        model = joblib.load(io.BytesIO(model_raw))
    except Exception as exc:
        raise ModelReleaseValidationError(
            f"Configured model artifact cannot be deserialized: {exc}"
        ) from exc
    _validate_loaded_model_artifact(model, metadata, stack_release)

    core_provenance = metadata.get("elettra_core")
    runtime_core_commit = runtime_release_configuration().elettra_core_source_commit
    if stack_release.stack is not PredictionStack.LEGACY and (
        not isinstance(core_provenance, dict)
        or core_provenance.get("package_version") != ELETTRA_CORE_VERSION
        or core_provenance.get("tag") != ELETTRA_CORE_TAG
        or not isinstance(core_provenance.get("source_commit"), str)
        or _GIT_SHA_PATTERN.fullmatch(core_provenance["source_commit"]) is None
        or core_provenance["source_commit"] != runtime_core_commit
        or release.get("elettra_core") != core_provenance
    ):
        raise ModelReleaseValidationError(
            "VECTO model metadata/manifest does not pin the runtime "
            f"{ELETTRA_CORE_TAG} release and source commit"
        )

    feature_release = metadata.get("feature_release")
    if not isinstance(feature_release, dict):
        raise ModelReleaseValidationError(
            "Model metadata feature_release must be an object"
        )
    profiles = feature_release.get("profiles") if isinstance(feature_release, dict) else None
    road_snap = profiles.get("road_snap") if isinstance(profiles, dict) else None
    roads_asset = profiles.get("roads_asset") if isinstance(profiles, dict) else None
    elevation_roads = elevation_manifest.get("roads")
    release_feature = release.get("feature_release")
    acceptance_filename = paths["acceptance"].rsplit("/", 1)[-1]
    if stack_release.stack is not PredictionStack.LEGACY:
        acceptance_candidate = acceptance_report.get("candidate")
        acceptance_test_set = acceptance_report.get("test_set")
        acceptance_evaluation = acceptance_report.get("evaluation_manifest")
        if (
            acceptance_report.get("schema_version") != 1
            or not isinstance(acceptance_candidate, dict)
            or acceptance_candidate.get("model_name") != model_name
            or acceptance_candidate.get("feature_contract_version")
            != FEATURE_CONTRACT_VERSION
            or acceptance_candidate.get("feature_release_manifest_sha256")
            != feature_release.get("manifest_sha256")
            or not isinstance(acceptance_test_set, dict)
            or acceptance_test_set.get("source_row_identity_sha256")
            != feature_release.get("row_identity_sha256")
            or not isinstance(acceptance_evaluation, dict)
            or not isinstance(acceptance_evaluation.get("sha256"), str)
            or _SHA256_PATTERN.fullmatch(acceptance_evaluation["sha256"]) is None
        ):
            raise ModelReleaseValidationError(
                "Model acceptance artifact is not bound to this candidate release"
            )
        if acceptance_decision == "approved_with_documented_regression" and (
            documented_approval["evaluation_sha256"]
            != acceptance_evaluation["sha256"]
        ):
            raise ModelReleaseValidationError(
                "Documented regression approval is not bound to the acceptance evaluation"
            )
    if (
        metadata.get("feature_contract_version") != release.get("feature_contract_version")
        or metadata.get("categorical_feature_contract")
        != release.get("categorical_feature_contract")
        or metadata.get("auxiliary_estimator") != release.get("auxiliary_estimator")
        or metadata.get("prediction_stack_contract")
        != release.get("prediction_stack_contract")
        or metadata.get("passenger_prior") != release.get("passenger_prior")
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
    _validated_model_releases[model_name] = {
        # Keep the exact hash-validated bytes for lazy binding.  Expanding all
        # three 450-tree forests at startup would retain substantial RSS;
        # loading from these bytes still closes the MinIO replacement window.
        "model": None,
        "model_bytes": model_raw,
        "stack_release": stack_release,
        "metadata": metadata,
        "metadata_sha256": artifact_sha256["metadata"],
        "manifest": release,
        "manifest_sha256": manifest_sha256,
        "manifest_identity": manifest_identity_final,
        "artifact_identities": artifact_identities,
    }
    return metadata


def get_validated_model_artifact(
    model_name: str,
) -> tuple[Any, dict[str, Any]]:
    """Return the exact in-memory artifact decoded by startup preflight."""

    validated = _validated_model_releases.get(model_name)
    if validated is None:
        raise ModelReleaseValidationError(
            f"Configured model release has not passed startup validation: {model_name}"
        )
    model = validated["model"]
    if model is None:
        raw = validated.get("model_bytes")
        if not isinstance(raw, bytes):  # pragma: no cover - internal invariant
            raise ModelReleaseValidationError(
                f"Validated model bytes are unavailable: {model_name}"
            )
        try:
            _register_legacy_pickle_symbols()
            model = joblib.load(io.BytesIO(raw))
        except Exception as exc:
            raise ModelReleaseValidationError(
                f"Validated model artifact cannot be deserialized: {exc}"
            ) from exc
        _validate_loaded_model_artifact(
            model, validated["metadata"], validated["stack_release"]
        )
        validated["model"] = model
        validated["model_bytes"] = None
    return model, validated["metadata"]


def validate_configured_model_release(
    elevation_manifest: dict[str, Any],
    *,
    client=None,
) -> dict[str, Any] | None:
    """Validate every configured stack release, with the default cached last."""

    global _validated_model_releases
    runtime = runtime_release_configuration()
    if runtime.consumption_model_release is None:
        _clear_validation_cache()
        return None
    # A cleared/default cache means this is a fresh process/test preflight.
    # Registry entries are retained only while the legacy-compatible primary
    # cache is alive, avoiding stale identities after an explicit cache reset.
    if _validated_model is None:
        _validated_model_releases = {}
    configured = list(runtime.prediction_stacks.values())
    default = runtime.prediction_stacks[runtime.default_prediction_stack]
    ordered = [entry for entry in configured if entry != default] + [default]
    default_metadata: dict[str, Any] | None = None
    for entry in ordered:
        metadata = _validate_one_model_release(
            elevation_manifest,
            model_name=entry.model_release,
            stack_release=entry,
            client=client,
        )
        if entry == default:
            default_metadata = metadata
    configured_names = {entry.model_release for entry in configured}
    for stale_name in set(_validated_model_releases) - configured_names:
        del _validated_model_releases[stale_name]
    return default_metadata


def probe_configured_model_immutable(*, client=None) -> None:
    """Re-read the commit marker and fail if any pinned object was replaced."""

    runtime = runtime_release_configuration()
    if runtime.consumption_model_release is None:
        return
    configured_names = {
        release.model_release for release in runtime.prediction_stacks.values()
    }
    if set(_validated_model_releases) != configured_names:
        raise ModelReleaseValidationError(
            "One or more configured production models have not passed startup validation"
        )
    minio_client = client or get_minio_client()
    for model_name in sorted(configured_names):
        validated = _validated_model_releases[model_name]
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
            or manifest_identity_after != validated["manifest_identity"]
            or hashlib.sha256(manifest_raw).hexdigest()
            != validated["manifest_sha256"]
        ):
            raise ModelReleaseValidationError(
                f"Configured model release manifest changed after startup: {model_name}"
            )
        for object_name, expected_identity in validated["artifact_identities"].items():
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
    metadata = {
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
    if runtime.registry_active:
        metadata.update(
            {
                "default_prediction_stack": runtime.default_prediction_stack.value,
                "prediction_stacks": {
                    stack.value: release.metadata()
                    for stack, release in runtime.prediction_stacks.items()
                },
                "validated_models": {
                    model_name: {
                        "release_manifest_sha256": validated["manifest_sha256"],
                        "metadata_sha256": validated["metadata_sha256"],
                        "artifact_count": len(validated["artifact_identities"]),
                    }
                    for model_name, validated in sorted(
                        _validated_model_releases.items()
                    )
                },
            }
        )
    return metadata
