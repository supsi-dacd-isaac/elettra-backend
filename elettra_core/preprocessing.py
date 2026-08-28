"""Deterministic model-input preprocessing shared by train and serve."""

from __future__ import annotations

from typing import Any

import pandas as pd


ELEVATION_PROFILE_CATEGORIES = (
    "flat",
    "ascent_only",
    "descent_only",
    "mixed",
)
ELEVATION_PROFILE_BASELINE = ELEVATION_PROFILE_CATEGORIES[0]

# Ordered raw model contract: 61 route/schedule statistics plus three pieces of
# inference context. Contract v2 releases contain exactly these 64 columns.
RAW_TRIP_FEATURE_COLUMNS = (
    "start_time_minutes", "end_time_minutes", "total_number_of_stops",
    "total_duration_minutes", "total_dwell_time_minutes", "driving_time_minutes",
    "total_horizontal_distance_m", "total_distance_m", "average_speed_kmh",
    "driving_average_speed_kmh", "elevation_range_m", "mean_elevation_m",
    "min_elevation_m", "max_elevation_m", "total_ascent_m", "total_descent_m",
    "mean_gradient", "net_elevation_change_m", "ascent_descent_ratio",
    "elevation_profile_type", "num_segments", "mean_segment_distance_m",
    "median_segment_distance_m", "min_segment_distance_m",
    "max_segment_distance_m", "std_segment_distance_m",
    "mean_segment_horizontal_distance_m", "mean_segment_duration_minutes",
    "median_segment_duration_minutes", "min_segment_duration_minutes",
    "max_segment_duration_minutes", "mean_segment_speed_kmh",
    "median_segment_speed_kmh", "min_segment_speed_kmh", "max_segment_speed_kmh",
    "mean_segment_ascent_m", "median_segment_ascent_m", "max_segment_ascent_m",
    "mean_segment_descent_m", "median_segment_descent_m", "max_segment_descent_m",
    "mean_segment_gradient", "median_segment_gradient", "std_segment_gradient",
    "max_segment_gradient", "mean_dwell_time_minutes", "median_dwell_time_minutes",
    "num_steep_segments_5pct_threshold", "num_steep_segments_10pct_threshold",
    "variance_segment_gradients", "roughness_index", "pct_uphill_segments",
    "pct_downhill_segments", "pct_flat_segments", "ratio_gradient_negative",
    "ratio_gradient_0_3", "ratio_gradient_3_6", "ratio_gradient_6_plus",
    "significant_elevation_changes", "elevation_change_frequency_per_km",
    "route_complexity_score",
)
MODEL_CONTEXT_COLUMNS = (
    "bus_length_m",
    "bus_battery_kwh",
    "avg_temp_outside_celsius",
)
RAW_MODEL_FEATURE_COLUMNS = RAW_TRIP_FEATURE_COLUMNS + MODEL_CONTEXT_COLUMNS


def prepare_model_feature_frame(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Validate and order an unencoded contract-v2 model feature frame."""
    missing = [column for column in RAW_MODEL_FEATURE_COLUMNS if column not in dataframe]
    if missing:
        raise ValueError(f"Missing contract-v2 model features: {missing}")
    unexpected = [column for column in dataframe if column not in RAW_MODEL_FEATURE_COLUMNS]
    if unexpected:
        raise ValueError(f"Unexpected contract-v2 model features: {unexpected}")
    return dataframe.loc[:, RAW_MODEL_FEATURE_COLUMNS].copy()


def prepare_model_feature_row(
    statistics: dict[str, Any], context: dict[str, Any]
) -> pd.DataFrame:
    """Build one strict raw model row from canonical statistics and context."""
    missing_statistics = [
        column for column in RAW_TRIP_FEATURE_COLUMNS if column not in statistics
    ]
    missing_context = [column for column in MODEL_CONTEXT_COLUMNS if column not in context]
    if missing_statistics or missing_context:
        raise ValueError(
            "Missing contract-v2 inputs: "
            f"statistics={missing_statistics}, context={missing_context}"
        )
    return prepare_model_feature_frame(
        pd.DataFrame([{**statistics, **context}], columns=RAW_MODEL_FEATURE_COLUMNS)
    )


def categorical_feature_contract() -> dict[str, Any]:
    """Return JSON-serializable categorical encoder metadata."""
    return {
        "elevation_profile_type": {
            "categories": list(ELEVATION_PROFILE_CATEGORIES),
            "baseline": ELEVATION_PROFILE_BASELINE,
            "dummy_columns": [
                f"elevation_profile_type_{category}"
                for category in ELEVATION_PROFILE_CATEGORIES
                if category != ELEVATION_PROFILE_BASELINE
            ],
        }
    }


def encode_categorical_features(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Encode contract categoricals with a fixed category universe.

    ``flat`` is the fixed baseline. All other dummy columns are materialized in
    the same order even when a batch contains none of a given category. Missing
    or unknown values fail instead of acquiring a batch-dependent encoding.
    """
    if "elevation_profile_type" not in dataframe.columns:
        raise ValueError("Missing categorical feature 'elevation_profile_type'")

    result = dataframe.copy()
    raw = result.pop("elevation_profile_type")
    if raw.isna().any():
        raise ValueError("Categorical feature 'elevation_profile_type' contains null values")
    normalized = raw.astype(str)
    unknown = sorted(set(normalized.unique()) - set(ELEVATION_PROFILE_CATEGORIES))
    if unknown:
        raise ValueError(
            "Categorical feature 'elevation_profile_type' contains unknown values: "
            f"{unknown}"
        )

    for category in ELEVATION_PROFILE_CATEGORIES:
        if category == ELEVATION_PROFILE_BASELINE:
            continue
        result[f"elevation_profile_type_{category}"] = (
            normalized == category
        ).astype("float64")
    return result
