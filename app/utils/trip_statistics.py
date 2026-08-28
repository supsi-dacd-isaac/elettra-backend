"""Compatibility imports for the canonical :mod:`elettra_core` contract.

New code should import from ``elettra_core`` directly. This module remains so
existing backend callers do not acquire a second implementation.
"""

from elettra_core.features import (
    FEATURE_CONTRACT_VERSION,
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

__all__ = [
    "FEATURE_CONTRACT_VERSION",
    "add_distance_columns",
    "combine_elevation_profiles",
    "combine_trip_schedules",
    "compute_global_trip_statistics_combined",
    "dur_sec",
    "extract_route_difficulty_metrics_from_elevation",
    "extract_stop_to_stop_statistics_for_schedule",
    "haversine_distance",
    "parse_gtfs_hms_to_seconds",
]
