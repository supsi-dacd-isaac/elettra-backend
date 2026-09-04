"""Canonical trip feature extraction used by training and inference.

Distance has two deliberately distinct meanings:

``cumulative_distance_m`` / ``cumulative_horizontal_distance_m``
    Planimetric distance along the GTFS shape.  The legacy column is preserved
    because it is part of the elevation-profile storage contract.

``cumulative_distance_3d_m``
    Distance travelled along the road surface, derived from horizontal distance
    and altitude.  Physical distance and speed features use this column.

Road grade remains rise over *horizontal* run.  Reinterpreting the legacy
column as a 3-D distance would silently invalidate existing profile manifests
and make GTFS distance release-dependent.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
import re
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd


FEATURE_CONTRACT_VERSION = "3.0.0"
_EARTH_RADIUS_M = 6_371_000.0
_DISTANCE_TOLERANCE_M = 1e-6
MIN_GRADE_RUN_M = 1.0

GRADE_DISTANCE_SHARE_COLUMNS = (
    "grade_distance_share_lt_neg5",
    "grade_distance_share_neg5_neg3",
    "grade_distance_share_neg3_neg1",
    "grade_distance_share_neg1_pos1",
    "grade_distance_share_pos1_pos3",
    "grade_distance_share_pos3_pos5",
    "grade_distance_share_ge_pos5",
)
ROAD_DISTANCE_SHARE_COLUMNS = (
    "road_distance_share_local",
    "road_distance_share_distributor",
    "road_distance_share_trunk_city",
    "road_distance_share_unknown",
)
SCHEDULED_SPEED_DISTANCE_SHARE_COLUMNS = (
    "scheduled_speed_distance_share_lt_10",
    "scheduled_speed_distance_share_10_20",
    "scheduled_speed_distance_share_20_30",
    "scheduled_speed_distance_share_ge_30",
)
_GRADE_SHARE_BOUNDARIES = np.asarray([-0.05, -0.03, -0.01, 0.01, 0.03, 0.05])
_SPEED_SHARE_BOUNDARIES_KMH = np.asarray([10.0, 20.0, 30.0])


def parse_gtfs_hms_to_seconds(value: str) -> int:
    """Parse a GTFS ``HH:MM:SS`` value, including hours beyond 24."""
    try:
        hours, minutes, seconds = map(int, str(value).split(":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid GTFS time {value!r}; expected HH:MM:SS") from exc
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"Invalid GTFS time {value!r}; expected HH:MM:SS")
    return hours * 3600 + minutes * 60 + seconds


def dur_sec(departure_hms: str, arrival_hms: str) -> int:
    """Return elapsed seconds, treating a lower arrival hour as next-day time."""
    departure = parse_gtfs_hms_to_seconds(departure_hms)
    arrival = parse_gtfs_hms_to_seconds(arrival_hms)
    if arrival < departure:
        arrival += 24 * 3600
    return max(arrival - departure, 0)


def haversine_distance(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Return great-circle distance in metres."""
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(
        radians, (float(lat1), float(lon1), float(lat2), float(lon2))
    )
    delta_lat = lat2_rad - lat1_rad
    delta_lon = lon2_rad - lon1_rad
    haversine = (
        sin(delta_lat / 2) ** 2
        + cos(lat1_rad) * cos(lat2_rad) * sin(delta_lon / 2) ** 2
    )
    return 2 * asin(min(1.0, sqrt(max(0.0, haversine)))) * _EARTH_RADIUS_M


def _finite_numeric_values(dataframe: pd.DataFrame, column: str) -> np.ndarray:
    if column not in dataframe.columns:
        raise ValueError(f"Elevation profile is missing required column {column!r}")
    values = pd.to_numeric(dataframe[column], errors="coerce").to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(f"Elevation profile contains non-finite {column!r} values")
    return values


def classify_road_width_proxy(value: Any) -> str:
    """Map swissTLM ``road_objektart`` width labels to an audit proxy.

    These labels are descriptive inputs only: they are not HBEFA traffic
    situations and never depend on measured or predicted energy.  Width 3/4 m
    maps to ``local``, 6 m to ``distributor`` and at least 8 m to
    ``trunk_city``.  Missing, malformed and other values remain ``unknown``.
    """
    if value is None or isinstance(value, (bool, np.bool_)):
        return "unknown"
    width: float | None = None
    if isinstance(value, (int, float, np.integer, np.floating)):
        numeric = float(value)
        if np.isfinite(numeric):
            width = numeric
    else:
        text = str(value).strip().lower().replace(",", ".")
        if text and text not in {"nan", "none", "null"}:
            matches = re.findall(
                r"(?<![0-9.])([0-9]+(?:\.[0-9]+)?)\s*m(?:\b|_)", text
            )
            if len(matches) == 1:
                width = float(matches[0])
    if width in {3.0, 4.0}:
        return "local"
    if width == 6.0:
        return "distributor"
    if width is not None and width >= 8.0:
        return "trunk_city"
    return "unknown"


def _distance_shares(
    values: np.ndarray,
    distances: np.ndarray,
    *,
    boundaries: np.ndarray,
    columns: Sequence[str],
) -> dict[str, float]:
    """Return exhaustive distance shares with strict finite-value gates."""
    values = np.asarray(values, dtype=float)
    distances = np.asarray(distances, dtype=float)
    if values.shape != distances.shape:
        raise ValueError("Distance-share values and weights have different shapes")
    if not np.isfinite(values).all() or not np.isfinite(distances).all():
        raise ValueError("Distance-share inputs contain non-finite values")
    if (distances < 0).any():
        raise ValueError("Distance-share weights must be non-negative")
    total = float(distances.sum())
    if total <= _DISTANCE_TOLERANCE_M:
        return {column: 0.0 for column in columns}
    buckets = np.digitize(values, boundaries)
    shares = np.bincount(
        buckets, weights=distances, minlength=len(columns)
    ).astype(float) / total
    if len(shares) != len(columns) or not np.isclose(
        shares.sum(), 1.0, rtol=0.0, atol=1e-12
    ):
        raise ValueError("Distance shares are not exhaustive")
    return {column: float(value) for column, value in zip(columns, shares)}


def extract_route_exposure_features(elevation_df: pd.DataFrame) -> dict[str, float]:
    """Extract signed-grade and road-width-proxy distance shares.

    Grade is rise over horizontal run.  Positive sub-metre steps, for which a
    local grade would be unstable, are assigned to the neutral ``[-1,+1)``
    band.  Each road segment inherits ``road_objektart`` from its starting
    profile sample, matching the external-validation audit convention.
    """
    profile, horizontal, _ = _profile_distance_arrays(elevation_df)
    horizontal_steps = np.maximum(np.diff(horizontal), 0.0)
    altitude_steps = np.diff(profile["altitude_m"].to_numpy(dtype=float))
    positive = horizontal_steps > _DISTANCE_TOLERANCE_M
    total = float(horizontal_steps[positive].sum())
    if total <= _DISTANCE_TOLERANCE_M:
        output = {column: 0.0 for column in GRADE_DISTANCE_SHARE_COLUMNS}
        output.update({column: 0.0 for column in ROAD_DISTANCE_SHARE_COLUMNS})
        return output

    grades = np.zeros_like(horizontal_steps)
    eligible = horizontal_steps >= MIN_GRADE_RUN_M
    grades[eligible] = altitude_steps[eligible] / horizontal_steps[eligible]
    output = _distance_shares(
        grades[positive],
        horizontal_steps[positive],
        boundaries=_GRADE_SHARE_BOUNDARIES,
        columns=GRADE_DISTANCE_SHARE_COLUMNS,
    )

    if "road_objektart" in profile:
        policies = profile["road_objektart"].iloc[:-1].map(
            classify_road_width_proxy
        ).to_numpy(dtype=object)
    else:
        policies = np.full(len(horizontal_steps), "unknown", dtype=object)
    for policy, column in zip(
        ("local", "distributor", "trunk_city", "unknown"),
        ROAD_DISTANCE_SHARE_COLUMNS,
    ):
        output[column] = float(horizontal_steps[positive & (policies == policy)].sum() / total)
    road_sum = sum(output[column] for column in ROAD_DISTANCE_SHARE_COLUMNS)
    if not np.isclose(road_sum, 1.0, rtol=0.0, atol=1e-12):
        raise ValueError("Road-width proxy shares are not exhaustive")
    return output


def _horizontal_distance_from_coordinates(dataframe: pd.DataFrame) -> np.ndarray:
    latitudes = _finite_numeric_values(dataframe, "latitude")
    longitudes = _finite_numeric_values(dataframe, "longitude")
    cumulative = np.zeros(len(dataframe), dtype=float)
    for index in range(1, len(dataframe)):
        cumulative[index] = cumulative[index - 1] + haversine_distance(
            latitudes[index - 1],
            longitudes[index - 1],
            latitudes[index],
            longitudes[index],
        )
    return cumulative


def add_distance_columns(elevation_df: pd.DataFrame) -> pd.DataFrame:
    """Validate a profile and add explicit horizontal and 3-D distances.

    The legacy ``cumulative_distance_m`` column is treated as horizontal.  If it
    is absent, horizontal distance is calculated from latitude/longitude and the
    legacy column is created.  Duplicate horizontal samples add no 3-D distance:
    a pure vertical jump at an identical coordinate is profile noise or a
    stitched boundary, not travel by a road vehicle.
    """
    if elevation_df is None or len(elevation_df) == 0:
        raise ValueError("Elevation profile must contain at least one row")

    result = elevation_df.copy()
    altitudes = _finite_numeric_values(result, "altitude_m")

    if "cumulative_horizontal_distance_m" in result.columns:
        horizontal = _finite_numeric_values(result, "cumulative_horizontal_distance_m")
        if "cumulative_distance_m" in result.columns:
            legacy_horizontal = _finite_numeric_values(result, "cumulative_distance_m")
            if not np.allclose(
                horizontal,
                legacy_horizontal,
                rtol=0.0,
                atol=_DISTANCE_TOLERANCE_M,
            ):
                raise ValueError(
                    "cumulative_horizontal_distance_m and cumulative_distance_m "
                    "must describe the same horizontal chainage"
                )
    elif "cumulative_distance_m" in result.columns:
        horizontal = _finite_numeric_values(result, "cumulative_distance_m")
    else:
        horizontal = _horizontal_distance_from_coordinates(result)
    if "cumulative_distance_m" not in result.columns:
        result["cumulative_distance_m"] = horizontal

    horizontal_steps = np.diff(horizontal)
    if (horizontal_steps < -_DISTANCE_TOLERANCE_M).any():
        raise ValueError("Horizontal cumulative distance must be non-decreasing")
    horizontal_steps = np.maximum(horizontal_steps, 0.0)

    altitude_steps = np.diff(altitudes)
    travelled_steps = np.where(
        horizontal_steps > _DISTANCE_TOLERANCE_M,
        np.hypot(horizontal_steps, altitude_steps),
        0.0,
    )
    distance_3d = np.concatenate(([0.0], np.cumsum(travelled_steps)))

    result["cumulative_horizontal_distance_m"] = horizontal
    result["cumulative_distance_3d_m"] = distance_3d
    return result


def combine_trip_schedules(schedules: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate schedules and mark trip boundaries deterministically."""
    parts: list[pd.DataFrame] = []
    for trip_index, schedule in enumerate(schedules):
        if schedule is None or len(schedule) == 0:
            continue
        part = schedule.copy()
        part["trip_index"] = trip_index
        parts.append(part)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def combine_elevation_profiles(profiles: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate profiles while keeping horizontal distance continuous.

    A profile boundary contributes no artificial distance.  The returned frame
    is normalized once more so its 3-D cumulative distance is derived from the
    final, continuous horizontal axis.
    """
    parts: list[pd.DataFrame] = []
    horizontal_offset = 0.0
    for profile_index, profile in enumerate(profiles):
        if profile is None or len(profile) == 0:
            continue
        part = add_distance_columns(profile)
        horizontal = part["cumulative_horizontal_distance_m"].to_numpy(dtype=float)
        horizontal = horizontal - horizontal[0] + horizontal_offset
        part["cumulative_distance_m"] = horizontal
        part["cumulative_horizontal_distance_m"] = horizontal
        part["profile_index"] = profile_index
        horizontal_offset = float(horizontal[-1])
        parts.append(part)
    if not parts:
        return pd.DataFrame()
    return add_distance_columns(pd.concat(parts, ignore_index=True))


def _profile_distance_arrays(elevation_df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    profile = add_distance_columns(elevation_df)
    horizontal = profile["cumulative_horizontal_distance_m"].to_numpy(dtype=float)
    travelled = profile["cumulative_distance_3d_m"].to_numpy(dtype=float)
    return profile, horizontal, travelled


def _trip_groups(trip_schedule: pd.DataFrame) -> list[pd.DataFrame]:
    if "trip_index" not in trip_schedule.columns:
        return [trip_schedule]
    return [group for _, group in trip_schedule.groupby("trip_index", sort=True)]


def _ordered_layover_seconds(previous_departure: str, next_arrival: str) -> int:
    """Return a layover on the shared GTFS service-day axis.

    GTFS expresses after-midnight service with hours >= 24. Consequently a
    negative difference between two concatenated groups is an overlap (or
    nominal timetable inconsistency), not an implicit next-day layover.
    """
    previous = parse_gtfs_hms_to_seconds(previous_departure)
    following = parse_gtfs_hms_to_seconds(next_arrival)
    return max(following - previous, 0)


def compute_global_trip_statistics_combined(
    trip_schedule: pd.DataFrame,
    elevation_df: pd.DataFrame,
) -> dict:
    """Compute canonical global features for one trip or a trip sequence."""
    if trip_schedule is None or len(trip_schedule) == 0:
        return {}

    profile, horizontal, travelled = _profile_distance_arrays(elevation_df)
    first_stop = trip_schedule.iloc[0]
    last_stop = trip_schedule.iloc[-1]
    groups = _trip_groups(trip_schedule)

    stats: dict[str, object] = {
        "start_time_minutes": parse_gtfs_hms_to_seconds(first_stop["arrival_time"]) / 60,
        "end_time_minutes": parse_gtfs_hms_to_seconds(last_stop["departure_time"]) / 60,
        "total_number_of_stops": len(trip_schedule),
    }

    # Sequence duration is additive across trip groups. Positive gaps on the
    # GTFS service-day axis are layovers; overlapping groups contribute no
    # layover. Never apply dur_sec's single-trip midnight wrap to a group gap,
    # because a 23:57 -> 23:50 overlap would become a spurious 23h53 layover.
    group_duration_seconds = sum(
        dur_sec(group.iloc[0]["arrival_time"], group.iloc[-1]["departure_time"])
        for group in groups
    )
    layover_seconds = sum(
        _ordered_layover_seconds(
            groups[index].iloc[-1]["departure_time"],
            groups[index + 1].iloc[0]["arrival_time"],
        )
        for index in range(len(groups) - 1)
    )
    total_seconds = group_duration_seconds + layover_seconds
    dwell_seconds = sum(
        dur_sec(stop["arrival_time"], stop["departure_time"])
        for _, stop in trip_schedule.iterrows()
    ) + layover_seconds + _stationary_same_stop_seconds(trip_schedule, profile)

    if dwell_seconds > total_seconds:
        raise ValueError(
            "Dwell time exceeds additive sequence duration; schedule intervals "
            "do not form a valid driving/dwell partition"
        )
    stats["total_duration_minutes"] = total_seconds / 60
    stats["total_dwell_time_minutes"] = dwell_seconds / 60
    stats["driving_time_minutes"] = (total_seconds - dwell_seconds) / 60

    horizontal_distance = float(horizontal[-1] - horizontal[0])
    travelled_distance = float(travelled[-1] - travelled[0])
    stats["total_horizontal_distance_m"] = horizontal_distance
    stats["total_distance_m"] = travelled_distance

    duration_hours = max(float(stats["total_duration_minutes"]), 0.0) / 60
    driving_hours = max(float(stats["driving_time_minutes"]), 0.0) / 60
    stats["average_speed_kmh"] = travelled_distance / 1000 / duration_hours if duration_hours else 0.0
    stats["driving_average_speed_kmh"] = travelled_distance / 1000 / driving_hours if driving_hours else 0.0
    stats["scheduled_stop_density_per_km"] = (
        len(trip_schedule) / (horizontal_distance / 1000)
        if horizontal_distance > _DISTANCE_TOLERANCE_M
        else 0.0
    )
    stats["scheduled_dwell_time_fraction"] = (
        dwell_seconds / total_seconds if total_seconds > 0 else 0.0
    )

    altitudes = profile["altitude_m"].to_numpy(dtype=float)
    altitude_steps = np.diff(altitudes)
    horizontal_steps = np.diff(horizontal)
    moving = horizontal_steps > _DISTANCE_TOLERANCE_M
    travelled_altitude_steps = altitude_steps[moving]
    travelled_horizontal_steps = horizontal_steps[moving]
    grade_steps = horizontal_steps >= MIN_GRADE_RUN_M
    with np.errstate(divide="ignore", invalid="ignore"):
        gradients = np.divide(
            altitude_steps[grade_steps],
            horizontal_steps[grade_steps],
            out=np.zeros_like(altitude_steps[grade_steps]),
            where=horizontal_steps[grade_steps] >= MIN_GRADE_RUN_M,
        )

    total_ascent = float(travelled_altitude_steps[travelled_altitude_steps > 0].sum())
    total_descent = float(-travelled_altitude_steps[travelled_altitude_steps < 0].sum())
    stats.update(
        {
            "elevation_range_m": float(np.ptp(altitudes)),
            "mean_elevation_m": float(np.mean(altitudes)),
            "min_elevation_m": float(np.min(altitudes)),
            "max_elevation_m": float(np.max(altitudes)),
            "total_ascent_m": total_ascent,
            "total_descent_m": total_descent,
            "mean_gradient": float(np.mean(gradients)) if len(gradients) else 0.0,
            "net_elevation_change_m": float(np.sum(travelled_altitude_steps)),
        }
    )

    if total_descent < 1.0:
        stats["ascent_descent_ratio"] = None
        stats["elevation_profile_type"] = "flat" if total_ascent < 1.0 else "ascent_only"
    elif total_ascent < 1.0:
        stats["ascent_descent_ratio"] = 0.0
        stats["elevation_profile_type"] = "descent_only"
    else:
        stats["ascent_descent_ratio"] = total_ascent / total_descent
        stats["elevation_profile_type"] = "mixed"
    return stats


def _closest_profile_index(
    profile: pd.DataFrame,
    latitude: float,
    longitude: float,
) -> int:
    coordinate_distance = (
        (profile["latitude"].to_numpy(dtype=float) - float(latitude)) ** 2
        + (profile["longitude"].to_numpy(dtype=float) - float(longitude)) ** 2
    )
    return int(np.argmin(coordinate_distance))


def _segment_profile(
    stop1: pd.Series,
    stop2: pd.Series,
    profile: pd.DataFrame,
    cursor: int,
) -> tuple[pd.DataFrame, int]:
    if "profile_index" in profile.columns and "trip_index" in stop1.index:
        profile = profile.loc[profile["profile_index"] == stop1["trip_index"]]
        if profile.empty:
            return pd.DataFrame(), cursor
    profile = profile.reset_index(drop=True)

    stop1_id = stop1.get("stop_id", "")
    stop2_id = stop2.get("stop_id", "")
    if {"start_stop_id", "end_stop_id"}.issubset(profile.columns):
        mask = (
            (profile["start_stop_id"] == stop1_id)
            & (profile["end_stop_id"] == stop2_id)
        ).to_numpy(dtype=bool)
        positions = np.flatnonzero(mask)
        positions = positions[positions >= cursor]
        if not len(positions):
            return pd.DataFrame(), cursor
        # A repeated segment can occur more than once in a loop. Select only
        # the next contiguous occurrence instead of spanning all occurrences.
        split_at = np.flatnonzero(np.diff(positions) > 1)
        end_offset = int(split_at[0] + 1) if len(split_at) else len(positions)
        block = positions[:end_offset]
        return profile.iloc[int(block[0]) : int(block[-1]) + 1], int(block[-1]) + 1

    required = {"latitude", "longitude"}
    if not required.issubset(profile.columns):
        return pd.DataFrame(), cursor
    coordinates = (stop1.get("stop_lat"), stop1.get("stop_lon"), stop2.get("stop_lat"), stop2.get("stop_lon"))
    if any(value is None or pd.isna(value) for value in coordinates):
        return pd.DataFrame(), cursor
    candidates = profile.iloc[cursor:]
    if len(candidates) < 2:
        return pd.DataFrame(), cursor
    start_index = cursor + _closest_profile_index(candidates, coordinates[0], coordinates[1])
    end_candidates = profile.iloc[start_index + 1 :]
    if end_candidates.empty:
        return pd.DataFrame(), cursor
    end_index = start_index + 1 + _closest_profile_index(
        end_candidates, coordinates[2], coordinates[3]
    )
    return profile.iloc[start_index : end_index + 1], end_index


def _segment_elevation_statistics(
    stop1: pd.Series,
    stop2: pd.Series,
    profile: pd.DataFrame,
    cursor: int,
) -> tuple[dict, int]:
    segment, next_cursor = _segment_profile(stop1, stop2, profile, cursor)
    if segment.empty:
        return {}, cursor

    horizontal = segment["cumulative_horizontal_distance_m"].to_numpy(dtype=float)
    travelled = segment["cumulative_distance_3d_m"].to_numpy(dtype=float)
    altitudes = segment["altitude_m"].to_numpy(dtype=float)
    horizontal_steps = np.diff(horizontal)
    altitude_steps = np.diff(altitudes)
    moving = horizontal_steps > _DISTANCE_TOLERANCE_M
    travelled_altitude_steps = altitude_steps[moving]
    travelled_horizontal_steps = horizontal_steps[moving]
    grade_steps = horizontal_steps >= MIN_GRADE_RUN_M
    with np.errstate(divide="ignore", invalid="ignore"):
        gradients = np.divide(
            altitude_steps[grade_steps],
            horizontal_steps[grade_steps],
            out=np.zeros_like(altitude_steps[grade_steps]),
            where=horizontal_steps[grade_steps] >= MIN_GRADE_RUN_M,
        )

    horizontal_distance = float(horizontal[-1] - horizontal[0])
    travelled_distance = float(travelled[-1] - travelled[0])
    elevation_delta = float(np.sum(travelled_altitude_steps))
    return {
        "start_elevation_m": float(altitudes[0]),
        "end_elevation_m": float(altitudes[-1]),
        "segment_horizontal_distance_m": horizontal_distance,
        "segment_distance_m": travelled_distance,
        "ascent_m": float(np.clip(travelled_altitude_steps, 0.0, None).sum()) if len(travelled_altitude_steps) else max(elevation_delta, 0.0),
        "descent_m": float((-np.clip(travelled_altitude_steps, None, 0.0)).sum()) if len(travelled_altitude_steps) else max(-elevation_delta, 0.0),
        "mean_gradient": elevation_delta / horizontal_distance if horizontal_distance > _DISTANCE_TOLERANCE_M else 0.0,
        "max_gradient": float(np.max(np.abs(gradients))) if len(gradients) else 0.0,
    }, next_cursor


def _stationary_same_stop_seconds(
    trip_schedule: pd.DataFrame,
    profile: pd.DataFrame,
) -> int:
    """Classify zero-distance A→A intervals as dwell rather than driving."""
    cursors: dict[object, int] = {}
    stationary_seconds = 0
    for index in range(len(trip_schedule) - 1):
        current_stop = trip_schedule.iloc[index]
        next_stop = trip_schedule.iloc[index + 1]
        if (
            "trip_index" in trip_schedule.columns
            and current_stop["trip_index"] != next_stop["trip_index"]
        ):
            continue
        trip_key = current_stop.get("trip_index", 0)
        segment, cursors[trip_key] = _segment_elevation_statistics(
            current_stop,
            next_stop,
            profile,
            cursors.get(trip_key, 0),
        )
        if (
            current_stop.get("stop_id") == next_stop.get("stop_id")
            and segment
            and segment["segment_distance_m"] <= _DISTANCE_TOLERANCE_M
        ):
            stationary_seconds += dur_sec(
                current_stop["departure_time"], next_stop["arrival_time"]
            )
    return stationary_seconds


def extract_stop_to_stop_statistics_for_schedule(
    trip_schedule: pd.DataFrame,
    elevation_df: pd.DataFrame,
) -> dict:
    """Compute segment features without discarding legitimate short segments."""
    if trip_schedule is None or len(trip_schedule) < 2:
        return {}
    profile = add_distance_columns(elevation_df)
    segments: list[dict] = []
    cursors: dict[object, int] = {}

    for index in range(len(trip_schedule) - 1):
        current_stop = trip_schedule.iloc[index]
        next_stop = trip_schedule.iloc[index + 1]
        if (
            "trip_index" in trip_schedule.columns
            and current_stop["trip_index"] != next_stop["trip_index"]
        ):
            continue
        trip_key = current_stop.get("trip_index", 0)
        elevation, cursors[trip_key] = _segment_elevation_statistics(
            current_stop,
            next_stop,
            profile,
            cursors.get(trip_key, 0),
        )
        if not elevation:
            raise ValueError(
                "Missing elevation data for segment "
                f"{current_stop.get('stop_id')} -> {next_stop.get('stop_id')}"
            )
        if (
            current_stop.get("stop_id") == next_stop.get("stop_id")
            and elevation["segment_distance_m"] <= _DISTANCE_TOLERANCE_M
        ):
            continue
        duration_seconds = dur_sec(current_stop["departure_time"], next_stop["arrival_time"])
        speed = elevation["segment_distance_m"] / 1000 / (duration_seconds / 3600) if duration_seconds else 0.0
        segments.append(
            {
                **elevation,
                "segment_duration_minutes": duration_seconds / 60,
                "segment_speed_kmh": speed,
                "dwell_time_at_end_minutes": dur_sec(
                    next_stop["arrival_time"], next_stop["departure_time"]
                ) / 60,
            }
        )

    if not segments:
        return {}

    def values(key: str) -> list[float]:
        return [float(segment[key]) for segment in segments]

    distances = values("segment_distance_m")
    horizontal_distances = values("segment_horizontal_distance_m")
    durations = values("segment_duration_minutes")
    speeds = values("segment_speed_kmh")
    ascents = values("ascent_m")
    descents = values("descent_m")
    gradients = values("mean_gradient")
    max_gradients = values("max_gradient")
    dwell_times = values("dwell_time_at_end_minutes")
    statistics = {
        "num_segments": len(segments),
        "mean_segment_distance_m": float(np.mean(distances)),
        "median_segment_distance_m": float(np.median(distances)),
        "min_segment_distance_m": float(np.min(distances)),
        "max_segment_distance_m": float(np.max(distances)),
        "std_segment_distance_m": float(np.std(distances)),
        "mean_segment_horizontal_distance_m": float(np.mean(horizontal_distances)),
        "mean_segment_duration_minutes": float(np.mean(durations)),
        "median_segment_duration_minutes": float(np.median(durations)),
        "min_segment_duration_minutes": float(np.min(durations)),
        "max_segment_duration_minutes": float(np.max(durations)),
        "mean_segment_speed_kmh": float(np.mean(speeds)),
        "median_segment_speed_kmh": float(np.median(speeds)),
        "min_segment_speed_kmh": float(np.min(speeds)),
        "max_segment_speed_kmh": float(np.max(speeds)),
        "mean_segment_ascent_m": float(np.mean(ascents)),
        "median_segment_ascent_m": float(np.median(ascents)),
        "max_segment_ascent_m": float(np.max(ascents)),
        "mean_segment_descent_m": float(np.mean(descents)),
        "median_segment_descent_m": float(np.median(descents)),
        "max_segment_descent_m": float(np.max(descents)),
        "mean_segment_gradient": float(np.mean(gradients)),
        "median_segment_gradient": float(np.median(gradients)),
        "std_segment_gradient": float(np.std(gradients)),
        "max_segment_gradient": float(np.max(max_gradients)),
        "mean_dwell_time_minutes": float(np.mean(dwell_times)),
        "median_dwell_time_minutes": float(np.median(dwell_times)),
        "num_steep_segments_5pct_threshold": sum(abs(value) > 0.05 for value in max_gradients),
        "num_steep_segments_10pct_threshold": sum(abs(value) > 0.10 for value in max_gradients),
        "variance_segment_gradients": float(np.var(gradients)),
    }
    statistics.update(
        _distance_shares(
            np.asarray(speeds, dtype=float),
            np.asarray(horizontal_distances, dtype=float),
            boundaries=_SPEED_SHARE_BOUNDARIES_KMH,
            columns=SCHEDULED_SPEED_DISTANCE_SHARE_COLUMNS,
        )
    )
    return statistics


def extract_route_difficulty_metrics_from_elevation(elevation_df: pd.DataFrame) -> dict:
    """Compute route difficulty using 3-D length and horizontal road grade."""
    profile, horizontal, travelled = _profile_distance_arrays(elevation_df)
    altitudes = profile["altitude_m"].to_numpy(dtype=float)
    altitude_steps = np.diff(altitudes)
    horizontal_steps = np.diff(horizontal)
    moving = horizontal_steps > _DISTANCE_TOLERANCE_M
    travelled_altitude_steps = altitude_steps[moving]
    grade_steps = horizontal_steps >= MIN_GRADE_RUN_M
    grade_altitude_steps = altitude_steps[grade_steps]
    grade_horizontal_steps = horizontal_steps[grade_steps]
    with np.errstate(divide="ignore", invalid="ignore"):
        gradients = np.divide(
            grade_altitude_steps,
            grade_horizontal_steps,
            out=np.zeros_like(grade_altitude_steps),
            where=grade_horizontal_steps >= MIN_GRADE_RUN_M,
        )

    total_distance = float(travelled[-1] - travelled[0])
    grade_altitudes = np.concatenate((altitudes[:1], altitudes[1:][grade_steps]))
    if "profile_index" in profile.columns:
        # Pool within-profile variance. A vertical offset between two stitched
        # profiles is not road roughness because the bus did not travel across
        # that boundary.
        variance_numerator = 0.0
        variance_denominator = 0
        for _, group in profile.groupby("profile_index", sort=True):
            group_horizontal = group["cumulative_horizontal_distance_m"].to_numpy(dtype=float)
            group_altitudes = group["altitude_m"].to_numpy(dtype=float)
            group_grade_steps = np.diff(group_horizontal) >= MIN_GRADE_RUN_M
            group_altitudes = np.concatenate((group_altitudes[:1], group_altitudes[1:][group_grade_steps]))
            if len(group_altitudes) > 1:
                variance_numerator += float(np.var(group_altitudes, ddof=1)) * (len(group_altitudes) - 1)
                variance_denominator += len(group_altitudes) - 1
        altitude_variance = variance_numerator / variance_denominator if variance_denominator else 0.0
    else:
        altitude_variance = float(pd.Series(grade_altitudes).var()) if len(grade_altitudes) > 1 else 0.0
    roughness = (
        altitude_variance / total_distance
        if total_distance > 0
        else 0.0
    )
    total_segments = len(gradients)
    pct_uphill = float(np.mean(gradients > 0.01) * 100) if total_segments else 0.0
    pct_downhill = float(np.mean(gradients < -0.01) * 100) if total_segments else 0.0
    pct_flat = 100.0 - pct_uphill - pct_downhill if total_segments else 0.0
    total_distance_km = total_distance / 1000
    significant_changes = int(np.sum(np.abs(travelled_altitude_steps) > 1.0))
    frequency = significant_changes / total_distance_km if total_distance_km else 0.0

    ratio_negative = float(np.mean(gradients < 0)) if total_segments else 0.0
    ratio_0_3 = float(np.mean((gradients >= 0) & (gradients < 0.03))) if total_segments else 0.0
    ratio_3_6 = float(np.mean((gradients >= 0.03) & (gradients < 0.06))) if total_segments else 0.0
    ratio_6_plus = float(np.mean(gradients >= 0.06)) if total_segments else 0.0
    complexity = (
        min(roughness * 1000, 1.0) * 0.3
        + (pct_uphill / 100) * 0.3
        + ratio_6_plus * 0.3
        + min(frequency / 10, 1.0) * 0.1
    )
    result = {
        "roughness_index": roughness,
        "pct_uphill_segments": pct_uphill,
        "pct_downhill_segments": pct_downhill,
        "pct_flat_segments": pct_flat,
        "ratio_gradient_negative": ratio_negative,
        "ratio_gradient_0_3": ratio_0_3,
        "ratio_gradient_3_6": ratio_3_6,
        "ratio_gradient_6_plus": ratio_6_plus,
        "significant_elevation_changes": significant_changes,
        "elevation_change_frequency_per_km": float(frequency),
        "route_complexity_score": float(complexity),
    }
    result.update(extract_route_exposure_features(profile))
    return result
