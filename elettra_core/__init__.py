"""Stable, shared primitives for Elettra training and inference."""

from .features import (
    FEATURE_CONTRACT_VERSION,
    MIN_GRADE_RUN_M,
    add_distance_columns,
    combine_elevation_profiles,
    combine_trip_schedules,
    compute_global_trip_statistics_combined,
    dur_sec,
    extract_route_difficulty_metrics_from_elevation,
    extract_stop_to_stop_statistics_for_schedule,
    haversine_distance,
    parse_gtfs_hms_to_seconds,
)
from .preprocessing import (
    ELEVATION_PROFILE_BASELINE,
    ELEVATION_PROFILE_CATEGORIES,
    MODEL_CONTEXT_COLUMNS,
    RAW_MODEL_FEATURE_COLUMNS,
    RAW_TRIP_FEATURE_COLUMNS,
    categorical_feature_contract,
    encode_categorical_features,
    prepare_model_feature_frame,
    prepare_model_feature_row,
)

__version__ = "2.0.0"

__all__ = [
    "FEATURE_CONTRACT_VERSION",
    "MIN_GRADE_RUN_M",
    "add_distance_columns",
    "combine_elevation_profiles",
    "combine_trip_schedules",
    "compute_global_trip_statistics_combined",
    "dur_sec",
    "extract_route_difficulty_metrics_from_elevation",
    "extract_stop_to_stop_statistics_for_schedule",
    "haversine_distance",
    "parse_gtfs_hms_to_seconds",
    "ELEVATION_PROFILE_BASELINE",
    "ELEVATION_PROFILE_CATEGORIES",
    "MODEL_CONTEXT_COLUMNS",
    "RAW_MODEL_FEATURE_COLUMNS",
    "RAW_TRIP_FEATURE_COLUMNS",
    "categorical_feature_contract",
    "encode_categorical_features",
    "prepare_model_feature_frame",
    "prepare_model_feature_row",
]
