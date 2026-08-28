"""Fail-closed runtime configuration for the production prediction stack."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from elettra_core import FEATURE_CONTRACT_VERSION


ROAD_SNAP_V3_ALGORITHM = "road-snap-v3.3-topology"
LEGACY_DEFAULT_MODEL = "greybox_qrf_production_crps_optimized_3"
RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RuntimeReleaseConfigurationError(RuntimeError):
    """Raised when release/model settings could select incompatible semantics."""


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _validate_identifier(name: str, value: str | None) -> str | None:
    if value is not None and not RELEASE_ID_PATTERN.fullmatch(value):
        raise RuntimeReleaseConfigurationError(
            f"{name} must contain 1-128 ASCII letters, digits, dots, underscores or hyphens"
        )
    return value


@dataclass(frozen=True)
class RuntimeReleaseConfiguration:
    elevation_release: str | None
    consumption_model_release: str | None
    aux_algorithm: str | None
    aux_roads_release: str | None

    @property
    def production_v2_active(self) -> bool:
        return self.elevation_release is not None

    def metadata(self) -> dict[str, str | bool | None]:
        return {
            "production_v2_active": self.production_v2_active,
            "elevation_release": self.elevation_release,
            "consumption_model_release": self.consumption_model_release,
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
            "aux_algorithm": self.aux_algorithm,
            "aux_roads_release": self.aux_roads_release,
        }


def runtime_release_configuration() -> RuntimeReleaseConfiguration:
    """Return validated settings, rejecting every partial production switch.

    Compatibility mode is represented by both the elevation release and model
    release being absent.  Activating contract v2 requires all four pins so a
    restart cannot combine new profile semantics with a legacy model or stale
    auxiliary profiles.
    """

    elevation_release = _validate_identifier(
        "ELEVATION_PROFILES_RELEASE", _optional_env("ELEVATION_PROFILES_RELEASE")
    )
    model_release = _validate_identifier(
        "CONSUMPTION_MODEL_RELEASE", _optional_env("CONSUMPTION_MODEL_RELEASE")
    )
    aux_algorithm = _optional_env("ELEVATION_AUX_PROFILE_ALGORITHM")
    aux_roads_release = _validate_identifier(
        "ELEVATION_AUX_ROADS_RELEASE", _optional_env("ELEVATION_AUX_ROADS_RELEASE")
    )

    if (elevation_release is None) != (model_release is None):
        raise RuntimeReleaseConfigurationError(
            "ELEVATION_PROFILES_RELEASE and CONSUMPTION_MODEL_RELEASE must be "
            "configured or removed together"
        )
    if (aux_algorithm is None) != (aux_roads_release is None):
        raise RuntimeReleaseConfigurationError(
            "ELEVATION_AUX_PROFILE_ALGORITHM and ELEVATION_AUX_ROADS_RELEASE "
            "must be configured or removed together"
        )
    if elevation_release is not None:
        aux_bucket = (
            os.getenv("ELEVATION_PROFILES_BUCKET", "elevation-profiles").strip()
            or "elevation-profiles"
        )
        gtfs_bucket = os.getenv("GTFS_ELEVATION_PROFILES_BUCKET", "").strip()
        if not gtfs_bucket or gtfs_bucket == aux_bucket:
            raise RuntimeReleaseConfigurationError(
                "production feature contract v2 requires a dedicated "
                "GTFS_ELEVATION_PROFILES_BUCKET distinct from ELEVATION_PROFILES_BUCKET"
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
        consumption_model_release=model_release,
        aux_algorithm=aux_algorithm,
        aux_roads_release=aux_roads_release,
    )


def enforce_configured_model(model_name: str) -> str:
    """Reject request/database model names that bypass the active release pin."""

    configured = runtime_release_configuration().consumption_model_release
    if configured is not None and model_name != configured:
        raise RuntimeReleaseConfigurationError(
            "Prediction model is pinned by CONSUMPTION_MODEL_RELEASE: "
            f"requested={model_name!r}, configured={configured!r}"
        )
    return model_name


def default_prediction_model_name() -> str:
    """Select the pinned model, retaining the historical default before switch."""

    return (
        runtime_release_configuration().consumption_model_release
        or LEGACY_DEFAULT_MODEL
    )
