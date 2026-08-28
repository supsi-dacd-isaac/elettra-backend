import math

import pandas as pd
import pytest

from app.utils import trip_statistics as backend_features
from elettra_core import (
    FEATURE_CONTRACT_VERSION,
    add_distance_columns,
    combine_elevation_profiles,
    combine_trip_schedules,
    compute_global_trip_statistics_combined,
    categorical_feature_contract,
    extract_route_difficulty_metrics_from_elevation,
    extract_stop_to_stop_statistics_for_schedule,
    encode_categorical_features,
)


def _short_segment_schedule() -> pd.DataFrame:
    # Consecutive coordinates are about 111 m apart: all are deliberately below
    # the former, unjustified 200 m cutoff.
    return pd.DataFrame(
        [
            {"stop_id": "A", "stop_lat": 47.0, "stop_lon": 8.000, "arrival_time": "08:00:00", "departure_time": "08:01:00"},
            {"stop_id": "B", "stop_lat": 47.0, "stop_lon": 8.001, "arrival_time": "08:04:00", "departure_time": "08:05:00"},
            {"stop_id": "C", "stop_lat": 47.0, "stop_lon": 8.002, "arrival_time": "08:09:00", "departure_time": "08:10:00"},
            {"stop_id": "D", "stop_lat": 47.0, "stop_lon": 8.003, "arrival_time": "08:14:00", "departure_time": "08:15:00"},
        ]
    )


def _elevation_profile() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "latitude": [47.0] * 4,
            "longitude": [8.000, 8.001, 8.002, 8.003],
            "altitude_m": [500.0, 500.0, 515.0, 515.0],
            "cumulative_distance_m": [0.0, 100.0, 250.0, 400.0],
        }
    )


def test_distance_contract_keeps_gtfs_chainage_and_derives_3d_distance():
    profile = add_distance_columns(_elevation_profile())

    assert profile["cumulative_distance_m"].tolist() == [0.0, 100.0, 250.0, 400.0]
    assert profile["cumulative_horizontal_distance_m"].tolist() == [0.0, 100.0, 250.0, 400.0]
    expected_3d = 100.0 + math.hypot(150.0, 15.0) + 150.0
    assert profile["cumulative_distance_3d_m"].iloc[-1] == pytest.approx(expected_3d)


def test_explicit_and_legacy_horizontal_chainage_must_agree():
    profile = _elevation_profile()
    profile["cumulative_horizontal_distance_m"] = profile["cumulative_distance_m"]
    assert add_distance_columns(profile)["cumulative_distance_3d_m"].iloc[-1] > 0

    profile.loc[2, "cumulative_horizontal_distance_m"] += 0.01
    with pytest.raises(ValueError, match="same horizontal chainage"):
        add_distance_columns(profile)


def test_short_stop_pairs_are_features_not_stitch_boundaries():
    stats = extract_stop_to_stop_statistics_for_schedule(
        _short_segment_schedule(), _elevation_profile()
    )

    assert stats["num_segments"] == 3
    assert stats["min_segment_distance_m"] == pytest.approx(100.0)
    assert stats["mean_segment_horizontal_distance_m"] == pytest.approx(400 / 3)


def test_golden_feature_contract_and_backend_adapter_parity():
    schedule = _short_segment_schedule()
    profile = _elevation_profile()

    global_stats = compute_global_trip_statistics_combined(schedule, profile)
    segment_stats = extract_stop_to_stop_statistics_for_schedule(schedule, profile)
    difficulty_stats = extract_route_difficulty_metrics_from_elevation(profile)

    expected_3d = 100.0 + math.hypot(150.0, 15.0) + 150.0
    assert FEATURE_CONTRACT_VERSION == "2.0.0"
    assert global_stats["total_horizontal_distance_m"] == 400.0
    assert global_stats["total_distance_m"] == pytest.approx(expected_3d)
    assert global_stats["total_duration_minutes"] == 15.0
    assert global_stats["total_dwell_time_minutes"] == 4.0
    assert global_stats["start_time_minutes"] == 480.0
    assert global_stats["end_time_minutes"] == 495.0
    assert segment_stats["num_segments"] == 3
    assert difficulty_stats["pct_uphill_segments"] == pytest.approx(100 / 3)

    # The backend compatibility module must expose the exact canonical callables,
    # not a copied implementation that can drift.
    assert backend_features.compute_global_trip_statistics_combined is compute_global_trip_statistics_combined
    assert backend_features.extract_stop_to_stop_statistics_for_schedule is extract_stop_to_stop_statistics_for_schedule
    assert backend_features.extract_route_difficulty_metrics_from_elevation is extract_route_difficulty_metrics_from_elevation


def test_real_trip_boundaries_are_still_excluded():
    first = _short_segment_schedule().iloc[:2].copy()
    second = _short_segment_schedule().iloc[2:].copy()
    first["trip_index"] = 0
    second["trip_index"] = 1
    schedule = pd.concat([first, second], ignore_index=True)

    stats = extract_stop_to_stop_statistics_for_schedule(schedule, _elevation_profile())

    assert stats["num_segments"] == 2


def test_sequence_wall_clock_is_partitioned_into_driving_dwell_and_layover():
    first = pd.DataFrame(
        [
            {"stop_id": "A", "arrival_time": "08:00:00", "departure_time": "08:01:00"},
            {"stop_id": "B", "arrival_time": "08:05:00", "departure_time": "08:05:00"},
        ]
    )
    second = pd.DataFrame(
        [
            {"stop_id": "C", "arrival_time": "08:10:00", "departure_time": "08:10:00"},
            {"stop_id": "D", "arrival_time": "08:14:00", "departure_time": "08:15:00"},
        ]
    )
    from elettra_core import combine_trip_schedules

    stats = compute_global_trip_statistics_combined(
        combine_trip_schedules([first, second]), _elevation_profile()
    )

    assert stats["total_duration_minutes"] == 15.0
    assert stats["total_dwell_time_minutes"] == 7.0  # 2 endpoint min + 5 min layover
    assert stats["driving_time_minutes"] == 8.0


def _two_stop_group(start: str, end: str, prefix: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "stop_id": f"{prefix}1", "stop_lat": 47.0, "stop_lon": 8.0,
                "arrival_time": start, "departure_time": start,
            },
            {
                "stop_id": f"{prefix}2", "stop_lat": 47.0, "stop_lon": 8.001,
                "arrival_time": end, "departure_time": end,
            },
        ]
    )


def test_overlapping_trip_groups_do_not_wrap_into_next_day_layover():
    schedule = combine_trip_schedules(
        [
            _two_stop_group("23:40:00", "23:57:00", "a"),
            _two_stop_group("23:50:00", "24:10:00", "b"),
        ]
    )
    profile = combine_elevation_profiles([_elevation_profile(), _elevation_profile()])
    stats = compute_global_trip_statistics_combined(schedule, profile)

    # 17 + 20 minutes of internal duration, with zero layover for the nominal
    # seven-minute overlap. The former dur_sec gap produced 23h53 here.
    assert stats["total_duration_minutes"] == 37.0
    assert stats["total_dwell_time_minutes"] == 0.0
    assert stats["driving_time_minutes"] == 37.0
    assert stats["total_dwell_time_minutes"] <= stats["total_duration_minutes"]
    assert stats["driving_time_minutes"] + stats["total_dwell_time_minutes"] == pytest.approx(
        stats["total_duration_minutes"]
    )


def test_gtfs_hours_above_24_preserve_real_midnight_layover():
    schedule = combine_trip_schedules(
        [
            _two_stop_group("23:40:00", "23:57:00", "a"),
            _two_stop_group("24:05:00", "24:20:00", "b"),
        ]
    )
    profile = combine_elevation_profiles([_elevation_profile(), _elevation_profile()])
    stats = compute_global_trip_statistics_combined(schedule, profile)

    assert stats["total_duration_minutes"] == 40.0  # 17 + 8 layover + 15
    assert stats["total_dwell_time_minutes"] == 8.0
    assert stats["driving_time_minutes"] == 32.0
    assert stats["driving_time_minutes"] + stats["total_dwell_time_minutes"] == pytest.approx(
        stats["total_duration_minutes"]
    )


def _submetric_grade_profile(final_altitude: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start_stop_id": ["A", "A", "A"],
            "end_stop_id": ["B", "B", "B"],
            "altitude_m": [0.0, 0.1, final_altitude],
            "cumulative_distance_m": [0.0, 0.287, 1.287],
        }
    )


def _grade_schedule() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"stop_id": "A", "arrival_time": "08:00:00", "departure_time": "08:00:00"},
            {"stop_id": "B", "arrival_time": "08:01:00", "departure_time": "08:01:00"},
        ]
    )


def test_submetric_run_contributes_physics_but_not_local_grade_metrics():
    profile = _submetric_grade_profile(final_altitude=0.1)
    global_stats = compute_global_trip_statistics_combined(_grade_schedule(), profile)
    segment_stats = extract_stop_to_stop_statistics_for_schedule(_grade_schedule(), profile)
    difficulty = extract_route_difficulty_metrics_from_elevation(profile)

    assert global_stats["total_ascent_m"] == pytest.approx(0.1)
    assert global_stats["total_distance_m"] == pytest.approx(
        math.hypot(0.287, 0.1) + 1.0
    )
    assert global_stats["mean_gradient"] == 0.0
    assert segment_stats["max_segment_gradient"] == 0.0
    # Segment mean is deliberately net rise over the full physical run.
    assert segment_stats["mean_segment_gradient"] == pytest.approx(0.1 / 1.287)
    assert difficulty["pct_flat_segments"] == 100.0
    assert difficulty["ratio_gradient_0_3"] == 1.0


def test_one_metre_run_is_included_in_local_grade_metrics():
    profile = _submetric_grade_profile(final_altitude=0.2)
    global_stats = compute_global_trip_statistics_combined(_grade_schedule(), profile)
    segment_stats = extract_stop_to_stop_statistics_for_schedule(_grade_schedule(), profile)
    difficulty = extract_route_difficulty_metrics_from_elevation(profile)

    assert global_stats["total_ascent_m"] == pytest.approx(0.2)
    assert global_stats["mean_gradient"] == pytest.approx(0.1)
    assert segment_stats["max_segment_gradient"] == pytest.approx(0.1)
    assert difficulty["pct_uphill_segments"] == 100.0
    assert difficulty["ratio_gradient_6_plus"] == 1.0


def test_non_monotonic_chainage_is_rejected():
    profile = _elevation_profile()
    profile["cumulative_distance_m"] = [0.0, 100.0, 90.0, 200.0]
    with pytest.raises(ValueError, match="non-decreasing"):
        add_distance_columns(profile)


def test_runtime_rejects_a_model_from_another_feature_contract(caplog):
    from simulation.consumption_prediction import validate_model_feature_contract

    validate_model_feature_contract(
        {
            "feature_contract_version": FEATURE_CONTRACT_VERSION,
            "categorical_feature_contract": categorical_feature_contract(),
        }
    )
    with pytest.raises(ValueError, match="incompatible"):
        validate_model_feature_contract({"feature_contract_version": "1.0.0"})
    with pytest.raises(ValueError, match="no categorical"):
        validate_model_feature_contract(
            {"feature_contract_version": FEATURE_CONTRACT_VERSION}
        )
    with pytest.raises(ValueError, match="categorical feature contract"):
        validate_model_feature_contract(
            {
                "feature_contract_version": FEATURE_CONTRACT_VERSION,
                "categorical_feature_contract": {},
            }
        )

    validate_model_feature_contract({"selected_features": ["total_distance_m"]})
    assert "legacy model" in caplog.text


def test_contract_metadata_is_not_in_the_runtime_model_frame():
    from simulation.feature_preparation import prepare_features_from_trip_stats
    from elettra_core import prepare_model_feature_row

    schedule = _short_segment_schedule()
    profile = _elevation_profile()
    stats = {
        **compute_global_trip_statistics_combined(schedule, profile),
        **extract_stop_to_stop_statistics_for_schedule(schedule, profile),
        **extract_route_difficulty_metrics_from_elevation(profile),
    }
    frame = prepare_features_from_trip_stats(
        [{"trip_id": "trip-1", "statistics": {"statistics": stats}}],
        bus_length_m=12.0,
        battery_capacity_kwh=350.0,
        external_temp_celsius=10.0,
    )

    assert "feature_contract_version" not in stats
    assert "feature_contract_version" not in frame.columns
    assert frame.loc[0, "total_distance_m"] == pytest.approx(stats["total_distance_m"])
    assert frame.loc[0, "total_horizontal_distance_m"] == 400.0
    assert frame.loc[0, "mean_segment_horizontal_distance_m"] == pytest.approx(400 / 3)
    assert "elevation_profile_type" not in frame.columns
    assert frame.loc[0, "elevation_profile_type_ascent_only"] == 1.0
    expected = encode_categorical_features(
        prepare_model_feature_row(
            stats,
            {
                "bus_length_m": 12.0,
                "bus_battery_kwh": 350.0,
                "avg_temp_outside_celsius": 10.0,
            },
        )
    )
    pd.testing.assert_frame_equal(frame.drop(columns="trip_id"), expected)


def test_runtime_feature_preparation_fails_if_a_canonical_statistic_is_missing():
    from simulation.feature_preparation import prepare_features_from_trip_stats

    schedule = _short_segment_schedule()
    profile = _elevation_profile()
    stats = {
        **compute_global_trip_statistics_combined(schedule, profile),
        **extract_stop_to_stop_statistics_for_schedule(schedule, profile),
        **extract_route_difficulty_metrics_from_elevation(profile),
    }
    del stats["mean_segment_horizontal_distance_m"]
    with pytest.raises(ValueError, match="mean_segment_horizontal_distance_m"):
        prepare_features_from_trip_stats(
            [{"trip_id": "trip-1", "statistics": {"statistics": stats}}],
            bus_length_m=12.0,
            battery_capacity_kwh=350.0,
            external_temp_celsius=10.0,
        )


def test_categorical_encoder_has_batch_independent_columns_and_rejects_unknowns():
    encoded = encode_categorical_features(
        pd.DataFrame(
            {
                "elevation_profile_type": ["flat", "mixed"],
                "value": [1.0, 2.0],
            }
        )
    )

    assert encoded.columns.tolist() == [
        "value",
        "elevation_profile_type_ascent_only",
        "elevation_profile_type_descent_only",
        "elevation_profile_type_mixed",
    ]
    assert encoded.loc[0, "elevation_profile_type_mixed"] == 0.0
    assert encoded.loc[1, "elevation_profile_type_mixed"] == 1.0
    assert encoded["elevation_profile_type_ascent_only"].sum() == 0.0

    with pytest.raises(ValueError, match="unknown values"):
        encode_categorical_features(
            pd.DataFrame({"elevation_profile_type": ["mountainous"]})
        )


def _segmented_profile(altitude_offset: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start_stop_id": ["A", "A", "B", "B"],
            "end_stop_id": ["B", "B", "C", "C"],
            "altitude_m": [
                altitude_offset,
                altitude_offset,
                altitude_offset,
                altitude_offset,
            ],
            "cumulative_distance_m": [0.0, 100.0, 100.0, 250.0],
        }
    )


def _three_stop_schedule(start_hour: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"stop_id": "A", "arrival_time": f"{start_hour:02d}:00:00", "departure_time": f"{start_hour:02d}:00:00"},
            {"stop_id": "B", "arrival_time": f"{start_hour:02d}:05:00", "departure_time": f"{start_hour:02d}:05:00"},
            {"stop_id": "C", "arrival_time": f"{start_hour:02d}:10:00", "departure_time": f"{start_hour:02d}:10:00"},
        ]
    )


def test_repeated_shape_profiles_do_not_span_disjoint_segment_occurrences():
    schedule = combine_trip_schedules(
        [_three_stop_schedule(8), _three_stop_schedule(9)]
    )
    profile = combine_elevation_profiles(
        [_segmented_profile(), _segmented_profile()]
    )

    stats = extract_stop_to_stop_statistics_for_schedule(schedule, profile)

    assert stats["num_segments"] == 4
    assert stats["min_segment_distance_m"] == 100.0
    assert stats["max_segment_distance_m"] == 150.0
    assert stats["mean_segment_distance_m"] == 125.0


def test_profile_boundary_altitude_jump_is_not_ascent_descent_or_flat_step():
    first = pd.DataFrame(
        {"altitude_m": [0.0, 10.0], "cumulative_distance_m": [0.0, 100.0]}
    )
    second = pd.DataFrame(
        {"altitude_m": [100.0, 90.0], "cumulative_distance_m": [0.0, 100.0]}
    )
    profile = combine_elevation_profiles([first, second])
    schedule = combine_trip_schedules(
        [
            pd.DataFrame([{"arrival_time": "08:00:00", "departure_time": "08:00:00"}]),
            pd.DataFrame([{"arrival_time": "09:00:00", "departure_time": "09:00:00"}]),
        ]
    )

    global_stats = compute_global_trip_statistics_combined(schedule, profile)
    difficulty = extract_route_difficulty_metrics_from_elevation(profile)

    expected_distance = 2 * math.hypot(100.0, 10.0)
    assert global_stats["total_distance_m"] == pytest.approx(expected_distance)
    assert global_stats["total_ascent_m"] == 10.0
    assert global_stats["total_descent_m"] == 10.0
    assert global_stats["mean_gradient"] == 0.0
    assert global_stats["net_elevation_change_m"] == 0.0
    assert difficulty["significant_elevation_changes"] == 2
    assert difficulty["pct_uphill_segments"] == 50.0
    assert difficulty["pct_downhill_segments"] == 50.0
    assert difficulty["pct_flat_segments"] == 0.0
    assert difficulty["roughness_index"] == pytest.approx(50.0 / expected_distance)


def _same_stop_schedule() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"stop_id": "A", "arrival_time": "08:00:00", "departure_time": "08:00:00"},
            {"stop_id": "A", "arrival_time": "08:02:00", "departure_time": "08:02:00"},
        ]
    )


def test_same_stop_id_can_delimit_a_real_loop():
    profile = pd.DataFrame(
        {
            "start_stop_id": ["A", "A"],
            "end_stop_id": ["A", "A"],
            "altitude_m": [500.0, 500.0],
            "cumulative_distance_m": [0.0, 500.0],
        }
    )

    global_stats = compute_global_trip_statistics_combined(_same_stop_schedule(), profile)
    segment_stats = extract_stop_to_stop_statistics_for_schedule(_same_stop_schedule(), profile)

    assert segment_stats["num_segments"] == 1
    assert segment_stats["mean_segment_distance_m"] == 500.0
    assert global_stats["total_duration_minutes"] == 2.0
    assert global_stats["total_dwell_time_minutes"] == 0.0
    assert global_stats["driving_time_minutes"] == 2.0


def test_zero_distance_same_stop_gap_is_dwell_not_a_road_segment():
    profile = pd.DataFrame(
        {
            "start_stop_id": ["A", "A"],
            "end_stop_id": ["A", "A"],
            "altitude_m": [500.0, 500.0],
            "cumulative_distance_m": [0.0, 0.0],
        }
    )

    schedule = pd.DataFrame(
        [
            {"stop_id": "A", "arrival_time": "07:59:00", "departure_time": "08:00:00"},
            {"stop_id": "A", "arrival_time": "08:02:00", "departure_time": "08:03:00"},
        ]
    )
    global_stats = compute_global_trip_statistics_combined(schedule, profile)
    segment_stats = extract_stop_to_stop_statistics_for_schedule(schedule, profile)

    assert global_stats["total_duration_minutes"] == 4.0
    # 1 minute at each stop plus the 2-minute stationary gap: no overlap and
    # no double counting.
    assert global_stats["total_dwell_time_minutes"] == 4.0
    assert global_stats["driving_time_minutes"] == 0.0
    assert segment_stats == {}
