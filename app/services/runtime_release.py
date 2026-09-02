"""Fail-closed runtime configuration for elevation and prediction releases.

The historical runtime pinned one consumption model. The prediction-stack
registry keeps that compatibility mode while allowing a legacy stack and two
VECTO stacks to coexist without mixing model and auxiliary contracts.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from elettra_core import (
    FEATURE_CONTRACT_VERSION,
    PASSENGER_MASS_KG,
    __version__ as ELETTRA_CORE_VERSION,
    source_tree_sha256,
)
from elettra_core.vecto_templates import (
    VECTO_TEMPLATE_RELEASE,
    load_template_release,
    template_release_sha256,
)


ROAD_SNAP_V3_ALGORITHM = "road-snap-v3.3-topology"
LEGACY_DEFAULT_MODEL = "greybox_qrf_production_crps_optimized_3"
RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ELETTRA_CORE_IMAGE_COMMIT_PATH = Path("/etc/elettra-core-image-commit")
ELETTRA_CORE_IMAGE_TREE_SHA256_PATH = Path(
    "/etc/elettra-core-image-tree-sha256"
)

LEGACY_AUXILIARY_ESTIMATOR = "legacy-curves-v1"
VECTO_HVAC_AUXILIARY_ESTIMATOR = VECTO_TEMPLATE_RELEASE
VECTO_COMPLETE_AUXILIARY_ESTIMATOR = "vecto-complete-5.1.3-r744-templates-v2"
VECTO_G2_TRANSFER_POLICY = "fleet-setpoints-to-vecto-default-v1"
VECTO_G2_PASSENGER_PRIOR_SOURCE = "vbz-ogd"
VECTO_G2_MATCHING_POLICY = "vbz-ogd-gtfs-v1"
DATA_DRIVEN_AUXILIARY_LOOKUP_SHA256 = (
    "8ae333170a856adcd938b5a259f21cc5a216743a8eb0c34c5542fb0e6532cfb9"
)


class PredictionStack(str, Enum):
    LEGACY = "legacy"
    VECTO_G2 = "vecto-g2"
    VECTO_G0_TRANSFER = "vecto-g0-transfer"


class RuntimeReleaseConfigurationError(RuntimeError):
    """Raised when releases could select incompatible model semantics."""


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _bool_env(name: str, *, default: bool = False) -> bool:
    value = _optional_env(name)
    if value is None:
        return default
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeReleaseConfigurationError(
        f"{name} must be one of true/false, 1/0, yes/no or on/off"
    )


def _validate_identifier(name: str, value: str | None) -> str | None:
    if value is not None and not RELEASE_ID_PATTERN.fullmatch(value):
        raise RuntimeReleaseConfigurationError(
            f"{name} must contain 1-128 ASCII letters, digits, dots, underscores or hyphens"
        )
    return value


def _image_core_commit() -> str | None:
    """Read the revision baked into the container image.

    Unit tests and source-tree development may supply the equivalent value via
    ``ELETTRA_CORE_IMAGE_COMMIT`` when the image-owned file does not exist.
    Production images always contain the file and therefore ignore that
    environment variable.
    """

    if ELETTRA_CORE_IMAGE_COMMIT_PATH.is_file():
        try:
            value = ELETTRA_CORE_IMAGE_COMMIT_PATH.read_text(
                encoding="ascii"
            ).strip()
        except OSError as exc:  # pragma: no cover - container filesystem failure
            raise RuntimeReleaseConfigurationError(
                "Cannot read the elettra-core revision baked into the image"
            ) from exc
        return value or None
    return _optional_env("ELETTRA_CORE_IMAGE_COMMIT")


def _image_core_tree_sha256() -> str | None:
    if ELETTRA_CORE_IMAGE_TREE_SHA256_PATH.is_file():
        try:
            value = ELETTRA_CORE_IMAGE_TREE_SHA256_PATH.read_text(
                encoding="ascii"
            ).strip()
        except OSError as exc:  # pragma: no cover - container filesystem failure
            raise RuntimeReleaseConfigurationError(
                "Cannot read the elettra-core source-tree hash baked into the image"
            ) from exc
        return value or None
    return _optional_env("ELETTRA_CORE_IMAGE_TREE_SHA256")


@dataclass(frozen=True)
class PredictionStackRelease:
    stack: PredictionStack
    model_release: str
    auxiliary_estimator: str
    fixed_auxiliary_owner: str
    deployment_tier: str

    @property
    def experimental(self) -> bool:
        return self.deployment_tier == "experimental"

    def metadata(self) -> dict[str, str | bool]:
        metadata: dict[str, str | bool] = {
            "stack": self.stack.value,
            "model_release": self.model_release,
            "auxiliary_estimator": self.auxiliary_estimator,
            "fixed_auxiliary_owner": self.fixed_auxiliary_owner,
            "deployment_tier": self.deployment_tier,
            "experimental": self.experimental,
        }
        if self.stack is not PredictionStack.LEGACY:
            metadata.update(
                {
                    "auxiliary_contract": (
                        "vecto-hvac-only"
                        if self.stack is PredictionStack.VECTO_G2
                        else "vecto-complete"
                    ),
                    "vecto_template_release": VECTO_TEMPLATE_RELEASE,
                    "vecto_template_sha256": template_release_sha256(),
                }
            )
        return metadata


@dataclass(frozen=True)
class RuntimeReleaseConfiguration:
    elevation_release: str | None
    consumption_model_release: str | None
    aux_algorithm: str | None
    aux_roads_release: str | None
    default_prediction_stack: PredictionStack
    prediction_stacks: Mapping[PredictionStack, PredictionStackRelease]
    registry_active: bool
    experimental_prediction_stacks_enabled: bool
    elettra_core_source_commit: str | None
    elettra_core_image_commit: str | None
    elettra_core_source_tree_sha256: str | None
    elettra_core_image_tree_sha256: str | None

    @property
    def production_v2_active(self) -> bool:
        return self.elevation_release is not None

    def metadata(self) -> dict[str, object]:
        return {
            "production_v2_active": self.production_v2_active,
            "elevation_release": self.elevation_release,
            "consumption_model_release": self.consumption_model_release,
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
            "aux_algorithm": self.aux_algorithm,
            "aux_roads_release": self.aux_roads_release,
            "prediction_registry_active": self.registry_active,
            "default_prediction_stack": self.default_prediction_stack.value,
            "experimental_prediction_stacks_enabled": (
                self.experimental_prediction_stacks_enabled
            ),
            "elettra_core_source_commit": self.elettra_core_source_commit,
            "elettra_core_image_commit": self.elettra_core_image_commit,
            "elettra_core_source_tree_sha256": self.elettra_core_source_tree_sha256,
            "elettra_core_image_tree_sha256": self.elettra_core_image_tree_sha256,
            "prediction_stacks": {
                stack.value: release.metadata()
                for stack, release in self.prediction_stacks.items()
            },
        }

    def stack_release(self, stack: PredictionStack | str) -> PredictionStackRelease:
        try:
            parsed = stack if isinstance(stack, PredictionStack) else PredictionStack(stack)
        except ValueError as exc:
            raise RuntimeReleaseConfigurationError(
                f"Unsupported prediction_stack {stack!r}"
            ) from exc
        release = self.prediction_stacks.get(parsed)
        if release is None:
            raise RuntimeReleaseConfigurationError(
                f"Prediction stack {parsed.value!r} has no configured model release"
            )
        if release.experimental and not self.experimental_prediction_stacks_enabled:
            raise RuntimeReleaseConfigurationError(
                f"Prediction stack {parsed.value!r} is experimental and disabled"
            )
        return release


def _build_prediction_registry(
    *, legacy_singleton: str | None
) -> tuple[
    bool,
    PredictionStack,
    dict[PredictionStack, PredictionStackRelease],
    bool,
]:
    names = {
        PredictionStack.LEGACY: _validate_identifier(
            "LEGACY_CONSUMPTION_MODEL_RELEASE",
            _optional_env("LEGACY_CONSUMPTION_MODEL_RELEASE"),
        ),
        PredictionStack.VECTO_G2: _validate_identifier(
            "VECTO_G2_CONSUMPTION_MODEL_RELEASE",
            _optional_env("VECTO_G2_CONSUMPTION_MODEL_RELEASE"),
        ),
        PredictionStack.VECTO_G0_TRANSFER: _validate_identifier(
            "VECTO_G0_TRANSFER_MODEL_RELEASE",
            _optional_env("VECTO_G0_TRANSFER_MODEL_RELEASE"),
        ),
    }
    default_raw = _optional_env("DEFAULT_PREDICTION_STACK")
    experimental_enabled = _bool_env("ENABLE_EXPERIMENTAL_PREDICTION_STACKS")
    registry_active = default_raw is not None or any(value is not None for value in names.values())

    if not registry_active:
        model = legacy_singleton or LEGACY_DEFAULT_MODEL
        return (
            False,
            PredictionStack.LEGACY,
            {
                PredictionStack.LEGACY: PredictionStackRelease(
                    stack=PredictionStack.LEGACY,
                    model_release=model,
                    auxiliary_estimator=LEGACY_AUXILIARY_ESTIMATOR,
                    fixed_auxiliary_owner="legacy-curve",
                    deployment_tier="production",
                )
            },
            False,
        )

    if legacy_singleton is not None:
        raise RuntimeReleaseConfigurationError(
            "CONSUMPTION_MODEL_RELEASE cannot be combined with the prediction-stack registry"
        )
    if names[PredictionStack.LEGACY] is None:
        raise RuntimeReleaseConfigurationError(
            "LEGACY_CONSUMPTION_MODEL_RELEASE is required when the prediction-stack registry is active"
        )
    try:
        default_stack = PredictionStack(default_raw or PredictionStack.LEGACY.value)
    except ValueError as exc:
        raise RuntimeReleaseConfigurationError(
            "DEFAULT_PREDICTION_STACK must be legacy, vecto-g2 or vecto-g0-transfer"
        ) from exc
    if default_stack is PredictionStack.VECTO_G0_TRANSFER:
        raise RuntimeReleaseConfigurationError(
            "vecto-g0-transfer is experimental and cannot be the default prediction stack"
        )

    registry: dict[PredictionStack, PredictionStackRelease] = {
        PredictionStack.LEGACY: PredictionStackRelease(
            stack=PredictionStack.LEGACY,
            model_release=str(names[PredictionStack.LEGACY]),
            auxiliary_estimator=LEGACY_AUXILIARY_ESTIMATOR,
            fixed_auxiliary_owner="legacy-curve",
            deployment_tier="production",
        )
    }
    if names[PredictionStack.VECTO_G2] is not None:
        registry[PredictionStack.VECTO_G2] = PredictionStackRelease(
            stack=PredictionStack.VECTO_G2,
            model_release=str(names[PredictionStack.VECTO_G2]),
            auxiliary_estimator=VECTO_HVAC_AUXILIARY_ESTIMATOR,
            fixed_auxiliary_owner="model",
            deployment_tier="production",
        )
    if names[PredictionStack.VECTO_G0_TRANSFER] is not None:
        registry[PredictionStack.VECTO_G0_TRANSFER] = PredictionStackRelease(
            stack=PredictionStack.VECTO_G0_TRANSFER,
            model_release=str(names[PredictionStack.VECTO_G0_TRANSFER]),
            auxiliary_estimator=VECTO_COMPLETE_AUXILIARY_ESTIMATOR,
            fixed_auxiliary_owner="template",
            deployment_tier="experimental",
        )

    if default_stack not in registry:
        raise RuntimeReleaseConfigurationError(
            f"DEFAULT_PREDICTION_STACK={default_stack.value} has no configured model release"
        )
    releases = [entry.model_release for entry in registry.values()]
    if len(releases) != len(set(releases)):
        raise RuntimeReleaseConfigurationError(
            "Each prediction stack must reference a distinct model release"
        )
    return registry_active, default_stack, registry, experimental_enabled


def runtime_release_configuration() -> RuntimeReleaseConfiguration:
    """Return validated settings, rejecting partial or ambiguous switches."""

    elevation_release = _validate_identifier(
        "ELEVATION_PROFILES_RELEASE", _optional_env("ELEVATION_PROFILES_RELEASE")
    )
    singleton_model = _validate_identifier(
        "CONSUMPTION_MODEL_RELEASE", _optional_env("CONSUMPTION_MODEL_RELEASE")
    )
    registry_active, default_stack, registry, experimental_enabled = (
        _build_prediction_registry(legacy_singleton=singleton_model)
    )
    default_model = registry[default_stack].model_release
    # A fallback model name in compatibility mode is not a release pin.
    effective_model_pin = default_model if registry_active else singleton_model
    core_source_commit = _optional_env("ELETTRA_CORE_SOURCE_COMMIT")
    core_image_commit = _image_core_commit()
    core_tree_sha256 = source_tree_sha256()
    core_image_tree_sha256 = _image_core_tree_sha256()
    has_vecto_stack = any(
        stack is not PredictionStack.LEGACY for stack in registry
    )
    if has_vecto_stack and (
        core_source_commit is None
        or GIT_COMMIT_PATTERN.fullmatch(core_source_commit) is None
    ):
        raise RuntimeReleaseConfigurationError(
            "VECTO prediction stacks require ELETTRA_CORE_SOURCE_COMMIT as "
            "the exact 40-character commit behind "
            f"elettra-core-v{ELETTRA_CORE_VERSION}"
        )
    if has_vecto_stack and (
        core_image_tree_sha256 is None
        or re.fullmatch(r"[0-9a-f]{64}", core_image_tree_sha256) is None
        or core_image_tree_sha256 != core_tree_sha256
    ):
        raise RuntimeReleaseConfigurationError(
            "VECTO prediction stacks require the installed elettra-core bytes "
            "to match the source-tree identity baked into the backend image"
        )
    if has_vecto_stack and (
        core_image_commit is None
        or GIT_COMMIT_PATTERN.fullmatch(core_image_commit) is None
        or core_image_commit != core_source_commit
    ):
        raise RuntimeReleaseConfigurationError(
            "VECTO prediction stacks require the deployment's "
            "ELETTRA_CORE_SOURCE_COMMIT to equal the exact revision baked "
            "into the backend image"
        )
    if has_vecto_stack:
        try:
            packaged_templates = load_template_release()
        except (OSError, ValueError) as exc:
            raise RuntimeReleaseConfigurationError(
                "The packaged VECTO template release is missing or invalid"
            ) from exc
        if (
            packaged_templates.release_id != VECTO_TEMPLATE_RELEASE
            or packaged_templates.content_sha256 != template_release_sha256()
        ):
            raise RuntimeReleaseConfigurationError(
                "The packaged VECTO template release identity is incompatible"
            )

    aux_algorithm = _optional_env("ELEVATION_AUX_PROFILE_ALGORITHM")
    aux_roads_release = _validate_identifier(
        "ELEVATION_AUX_ROADS_RELEASE", _optional_env("ELEVATION_AUX_ROADS_RELEASE")
    )

    if not registry_active and (elevation_release is None) != (singleton_model is None):
        raise RuntimeReleaseConfigurationError(
            "ELEVATION_PROFILES_RELEASE and CONSUMPTION_MODEL_RELEASE must be configured or removed together"
        )
    if registry_active and elevation_release is None:
        raise RuntimeReleaseConfigurationError(
            "The prediction-stack registry requires ELEVATION_PROFILES_RELEASE"
        )
    if (aux_algorithm is None) != (aux_roads_release is None):
        raise RuntimeReleaseConfigurationError(
            "ELEVATION_AUX_PROFILE_ALGORITHM and ELEVATION_AUX_ROADS_RELEASE must be configured or removed together"
        )
    if elevation_release is not None:
        aux_bucket = os.getenv("ELEVATION_PROFILES_BUCKET", "elevation-profiles").strip() or "elevation-profiles"
        gtfs_bucket = os.getenv("GTFS_ELEVATION_PROFILES_BUCKET", "").strip()
        if not gtfs_bucket or gtfs_bucket == aux_bucket:
            raise RuntimeReleaseConfigurationError(
                "production feature contract v2 requires a dedicated GTFS_ELEVATION_PROFILES_BUCKET distinct from ELEVATION_PROFILES_BUCKET"
            )
        if aux_algorithm != ROAD_SNAP_V3_ALGORITHM:
            raise RuntimeReleaseConfigurationError(
                "production feature contract v2 requires "
                f"ELEVATION_AUX_PROFILE_ALGORITHM={ROAD_SNAP_V3_ALGORITHM}"
            )
        if aux_roads_release is None:
            raise RuntimeReleaseConfigurationError(
                "production feature contract v2 requires ELEVATION_AUX_ROADS_RELEASE"
            )
    elif aux_algorithm is not None and aux_algorithm != ROAD_SNAP_V3_ALGORITHM:
        raise RuntimeReleaseConfigurationError(
            "ELEVATION_AUX_PROFILE_ALGORITHM has an unsupported value"
        )

    return RuntimeReleaseConfiguration(
        elevation_release=elevation_release,
        consumption_model_release=effective_model_pin,
        aux_algorithm=aux_algorithm,
        aux_roads_release=aux_roads_release,
        default_prediction_stack=default_stack,
        prediction_stacks=registry,
        registry_active=registry_active,
        experimental_prediction_stacks_enabled=experimental_enabled,
        elettra_core_source_commit=core_source_commit,
        elettra_core_image_commit=core_image_commit,
        elettra_core_source_tree_sha256=core_tree_sha256,
        elettra_core_image_tree_sha256=core_image_tree_sha256,
    )


def resolve_prediction_selection(
    *,
    prediction_stack: PredictionStack | str | None = None,
    model_name: str | None = None,
) -> PredictionStackRelease:
    """Resolve an API selection and reject every model/stack mismatch."""

    runtime = runtime_release_configuration()
    if not runtime.registry_active and model_name is not None:
        if prediction_stack not in (None, PredictionStack.LEGACY, PredictionStack.LEGACY.value):
            raise RuntimeReleaseConfigurationError(
                "Only the legacy prediction stack is available in compatibility mode"
            )
        if (
            runtime.consumption_model_release is not None
            and model_name != runtime.consumption_model_release
        ):
            raise RuntimeReleaseConfigurationError(
                "Prediction model is pinned by CONSUMPTION_MODEL_RELEASE: "
                f"requested={model_name!r}, configured={runtime.consumption_model_release!r}"
            )
        return PredictionStackRelease(
            stack=PredictionStack.LEGACY,
            model_release=model_name,
            auxiliary_estimator=LEGACY_AUXILIARY_ESTIMATOR,
            fixed_auxiliary_owner="legacy-curve",
            deployment_tier="production",
        )
    if prediction_stack is not None:
        release = runtime.stack_release(prediction_stack)
        if model_name is not None and model_name != release.model_release:
            raise RuntimeReleaseConfigurationError(
                "prediction_stack and model_name select different configured releases"
            )
        return release
    if model_name is None:
        return runtime.stack_release(runtime.default_prediction_stack)
    matches = [
        release
        for release in runtime.prediction_stacks.values()
        if release.model_release == model_name
    ]
    if len(matches) != 1:
        raise RuntimeReleaseConfigurationError(
            f"Model {model_name!r} is not registered in the active prediction configuration"
        )
    release = matches[0]
    if release.experimental and not runtime.experimental_prediction_stacks_enabled:
        raise RuntimeReleaseConfigurationError(
            f"Prediction stack {release.stack.value!r} is experimental and disabled"
        )
    return release


def enforce_configured_model(model_name: str) -> str:
    """Reject request/database model names outside the active registry."""

    return resolve_prediction_selection(model_name=model_name).model_release


def default_prediction_model_name() -> str:
    return resolve_prediction_selection().model_release


def default_prediction_stack_name() -> str:
    return runtime_release_configuration().default_prediction_stack.value


def validate_model_stack_contract(
    release: PredictionStackRelease, metadata: Mapping[str, object] | None
) -> None:
    """Verify that model metadata owns exactly the selected aux semantics."""

    if release.stack is PredictionStack.LEGACY:
        # Historical artifacts predate this contract. New legacy manifests may
        # declare it, in which case mismatches are still rejected.
        contract = metadata.get("prediction_stack_contract") if metadata else None
        if contract is None:
            return
    else:
        contract = metadata.get("prediction_stack_contract") if metadata else None
        if not isinstance(contract, Mapping):
            raise RuntimeReleaseConfigurationError(
                f"Model {release.model_release!r} has no prediction_stack_contract"
            )

    assert isinstance(contract, Mapping)
    expected_training = (
        "data-driven-by-bus"
        if release.stack is PredictionStack.VECTO_G0_TRANSFER
        else release.auxiliary_estimator
    )
    training_comfort_policy: Mapping[str, object] | None = None
    if release.stack is PredictionStack.VECTO_G2:
        candidate = contract.get("training_auxiliary_estimator")
        if (
            not isinstance(candidate, str)
            or RELEASE_ID_PATTERN.fullmatch(candidate) is None
            or candidate == release.auxiliary_estimator
        ):
            raise RuntimeReleaseConfigurationError(
                "vecto-g2 requires a distinct, versioned training auxiliary estimator"
            )
        expected_training = candidate
        training_comfort_policy = contract.get("training_comfort_policy")
        if (
            not isinstance(training_comfort_policy, Mapping)
            or set(training_comfort_policy) != {"release_id", "sha256", "scope"}
            or not isinstance(training_comfort_policy.get("release_id"), str)
            or RELEASE_ID_PATTERN.fullmatch(
                str(training_comfort_policy.get("release_id"))
            )
            is None
            or not isinstance(training_comfort_policy.get("sha256"), str)
            or re.fullmatch(
                r"[0-9a-f]{64}", str(training_comfort_policy.get("sha256"))
            )
            is None
            or training_comfort_policy.get("scope") != "training-only"
        ):
            raise RuntimeReleaseConfigurationError(
                "vecto-g2 requires an immutable training-only comfort policy"
            )
    expected = {
        "stack": release.stack.value,
        "deployment_tier": release.deployment_tier,
        "training_auxiliary_estimator": expected_training,
        "inference_auxiliary_estimator": release.auxiliary_estimator,
        "fixed_auxiliary_owner": release.fixed_auxiliary_owner,
    }
    if release.stack is PredictionStack.VECTO_G2:
        expected.update(
            {
                "auxiliary_contract": "vecto-hvac-only",
                "transfer_policy": VECTO_G2_TRANSFER_POLICY,
                "training_comfort_policy": training_comfort_policy,
            }
        )
    elif release.stack is PredictionStack.VECTO_G0_TRANSFER:
        expected.update(
            {
                "auxiliary_contract": "vecto-complete",
                "training_auxiliary_estimator_sha256": (
                    DATA_DRIVEN_AUXILIARY_LOOKUP_SHA256
                ),
            }
        )
    mismatches = {
        key: (contract.get(key), value)
        for key, value in expected.items()
        if contract.get(key) != value
    }
    if release.stack is not PredictionStack.LEGACY:
        vecto_expected = {
            "vecto_template_release": VECTO_TEMPLATE_RELEASE,
            "vecto_template_sha256": template_release_sha256(),
        }
        mismatches.update(
            {
                key: (contract.get(key), value)
                for key, value in vecto_expected.items()
                if contract.get(key) != value
            }
        )
    if mismatches:
        details = ", ".join(
            f"{key}=model:{actual!r}/runtime:{wanted!r}"
            for key, (actual, wanted) in sorted(mismatches.items())
        )
        raise RuntimeReleaseConfigurationError(
            f"Model {release.model_release!r} is incompatible with stack "
            f"{release.stack.value!r}: {details}"
        )
    if release.stack is not PredictionStack.LEGACY:
        expected_auxiliary = {
            "training": expected_training,
            "inference": release.auxiliary_estimator,
            "fixed_auxiliary_owner": release.fixed_auxiliary_owner,
            "auxiliary_contract": expected["auxiliary_contract"],
            "vecto_template_release": VECTO_TEMPLATE_RELEASE,
            "vecto_template_sha256": template_release_sha256(),
        }
        if release.stack is PredictionStack.VECTO_G2:
            expected_auxiliary.update(
                {
                    "transfer_policy": VECTO_G2_TRANSFER_POLICY,
                    "training_comfort_policy": training_comfort_policy,
                }
            )
        if release.stack is PredictionStack.VECTO_G0_TRANSFER:
            expected_auxiliary["training_sha256"] = (
                DATA_DRIVEN_AUXILIARY_LOOKUP_SHA256
            )
        if metadata is None or metadata.get("auxiliary_estimator") != expected_auxiliary:
            raise RuntimeReleaseConfigurationError(
                f"Model {release.model_release!r} has an incompatible "
                "auxiliary_estimator declaration"
            )
    if release.stack is PredictionStack.VECTO_G2:
        validate_g2_passenger_prior(metadata)


def validate_g2_passenger_prior(
    metadata: Mapping[str, object] | None,
) -> Mapping[str, object]:
    prior = metadata.get("passenger_prior") if metadata else None
    if not isinstance(prior, Mapping):
        raise RuntimeReleaseConfigurationError(
            "vecto-g2 metadata must declare passenger_prior"
        )
    required = {
        "source",
        "release_id",
        "sha256",
        "correction_factor_s",
        "qrf_reference_occupancy_percent",
        "mass_weighting",
        "hvac_weighting",
        "matching_policy",
        "primary_secondary_distance_coverage",
        "passenger_mass_kg",
        "scale_policy",
    }
    if not required.issubset(prior):
        raise RuntimeReleaseConfigurationError(
            "vecto-g2 passenger_prior is incomplete"
        )
    release_id = prior.get("release_id")
    digest = prior.get("sha256")
    correction = prior.get("correction_factor_s")
    reference = prior.get("qrf_reference_occupancy_percent")
    coverage = prior.get("primary_secondary_distance_coverage")
    scale_policy = prior.get("scale_policy")
    if (
        prior.get("source") != VECTO_G2_PASSENGER_PRIOR_SOURCE
        or not isinstance(release_id, str)
        or RELEASE_ID_PATTERN.fullmatch(release_id) is None
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or isinstance(correction, bool)
        or not isinstance(correction, (int, float))
        or float(correction) != 1.0
        or isinstance(reference, bool)
        or not isinstance(reference, (int, float))
        or not 0 <= float(reference) <= 100
        or prior.get("mass_weighting") != "distance"
        or prior.get("hvac_weighting") != "duration"
        or prior.get("matching_policy") != VECTO_G2_MATCHING_POLICY
        or isinstance(prior.get("passenger_mass_kg"), bool)
        or prior.get("passenger_mass_kg") != PASSENGER_MASS_KG
        or not isinstance(scale_policy, Mapping)
        or scale_policy.get("policy") != "ogd-unscaled"
        or scale_policy.get("calibration_performed") is not False
        or isinstance(coverage, bool)
        or not isinstance(coverage, (int, float))
        or not 0.8 <= float(coverage) <= 1.0
    ):
        raise RuntimeReleaseConfigurationError(
            "vecto-g2 passenger_prior violates the VBZ OGD deployment contract"
        )
    return prior
