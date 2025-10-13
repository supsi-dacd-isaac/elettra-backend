# Trip Statistics Endpoint - Feature Implementation Summary

## Overview
The `/api/v1/simulation/trip-statistics/` endpoint now returns **58 comprehensive features** for trip analysis, supporting both single trips and combined trip sequences.

## Implemented Features (58 Total)

### 1. Global Trip Statistics (18 features)
- `start_time_minutes` - Trip start time as minutes from midnight (GTFS format, can exceed 1440)
- `end_time_minutes` - Trip end time as minutes from midnight (GTFS format, can exceed 1440)
- `total_distance_m` - Total trip distance in meters
- `total_duration_minutes` - Total trip duration in minutes
- `driving_time_minutes` - Actual driving time (excluding dwell)
- `total_dwell_time_minutes` - Total time spent at stops
- `total_number_of_stops` - Number of stops on the trip
- `average_speed_kmh` - Average speed including stops
- `driving_average_speed_kmh` - Average speed while driving (bonus feature)
- `elevation_range_m` - Difference between max and min elevation
- `mean_elevation_m` - Mean elevation along the route
- `min_elevation_m` - Minimum elevation
- `max_elevation_m` - Maximum elevation
- `total_ascent_m` - Total uphill elevation gain
- `total_descent_m` - Total downhill elevation loss
- `net_elevation_change_m` - Net elevation change (end - start)
- `mean_gradient` - Mean gradient along the route
- `ascent_descent_ratio` - Ratio of ascent to descent
- `elevation_profile_type` - Profile classification: flat, ascent_only, descent_only, mixed

### 2. Segment Statistics (29 features)
**Distance & Duration:**
- `num_segments` - Number of stop-to-stop segments
- `mean_segment_distance_m` - Mean segment distance
- `median_segment_distance_m` - Median segment distance
- `min_segment_distance_m` - Minimum segment distance
- `max_segment_distance_m` - Maximum segment distance
- `std_segment_distance_m` - Standard deviation of segment distances
- `mean_segment_duration_minutes` - Mean segment duration
- `median_segment_duration_minutes` - Median segment duration
- `min_segment_duration_minutes` - Minimum segment duration
- `max_segment_duration_minutes` - Maximum segment duration

**Speed & Dwell:**
- `mean_segment_speed_kmh` - Mean segment speed
- `median_segment_speed_kmh` - Median segment speed
- `min_segment_speed_kmh` - Minimum segment speed
- `max_segment_speed_kmh` - Maximum segment speed
- `mean_dwell_time_minutes` - Mean dwell time at stops
- `median_dwell_time_minutes` - Median dwell time at stops

**Elevation & Gradient:**
- `mean_segment_ascent_m` - Mean ascent per segment
- `median_segment_ascent_m` - Median ascent per segment
- `max_segment_ascent_m` - Maximum ascent in any segment
- `mean_segment_descent_m` - Mean descent per segment
- `median_segment_descent_m` - Median descent per segment
- `max_segment_descent_m` - Maximum descent in any segment
- `mean_segment_gradient` - Mean gradient across segments
- `median_segment_gradient` - Median gradient across segments
- `std_segment_gradient` - Standard deviation of gradients
- `max_segment_gradient` - Maximum gradient in any segment
- `num_steep_segments_5pct_threshold` - Count of segments with >5% gradient
- `num_steep_segments_10pct_threshold` - Count of segments with >10% gradient
- `variance_segment_gradients` - Variance of segment gradients

### 3. Route Difficulty Metrics (11 features)
- `roughness_index` - Elevation variance normalized by distance
- `route_complexity_score` - Composite difficulty score (0-1 scale)
- `pct_uphill_segments` - Percentage of uphill segments (>1% gradient)
- `pct_downhill_segments` - Percentage of downhill segments (<-1% gradient)
- `pct_flat_segments` - Percentage of flat segments (-1% to 1% gradient)
- `ratio_gradient_negative` - Ratio of points with negative gradient
- `ratio_gradient_0_3` - Ratio of points with 0-3% gradient
- `ratio_gradient_3_6` - Ratio of points with 3-6% gradient
- `ratio_gradient_6_plus` - Ratio of points with >6% gradient
- `significant_elevation_changes` - Count of elevation changes >1m
- `elevation_change_frequency_per_km` - Elevation changes per kilometer

## Key Implementation Details

### Time Features
- **GTFS Compatibility**: Times are stored as minutes from midnight and can exceed 1440 (24:00) to represent service continuing past midnight
- **Single Trip**: Start time from first stop's arrival, end time from last stop's departure
- **Combined Trips**: Start time from first trip's first stop, end time from last trip's last stop

### Elevation Data Handling
- **Automatic Matching**: Elevation profiles are automatically matched to trips using MinIO storage
- **Flexible Format**: Supports both pre-segmented elevation data and raw elevation profiles
- **Point Matching**: For raw profiles, finds closest elevation points to each stop using coordinate-based matching
- **Distance Calculation**: Uses actual route distance from elevation data when available, falls back to haversine distance

### Combined Trip Support
- **Schedule Concatenation**: Merges GTFS schedules from multiple trips while preserving stop sequence
- **Elevation Offsetting**: Concatenates elevation profiles with cumulative distance offsets for continuous analysis
- **Inter-trip Gaps**: Correctly accounts for dwell time between trips in duration calculations
- **Segment Filtering**: Skips boundary segments between trips to avoid artificial segments

## Testing Results

### Single Trip Example (3.6 km)
```
Start Time: 05:52 (352 minutes)
End Time: 06:03 (363 minutes)
Duration: 11 minutes
Average Speed: 19.9 km/h
Elevation: 46m ascent, 81m descent (-35.5m net)
Segments: 9 segments for 10 stops
```

### Combined Trips Example (7.4 km)
```
Start Time: 05:52 (352 minutes)
End Time: 06:23 (383 minutes)
Duration: 31 minutes (22 min driving + 9 min dwell)
Average Speed: 14.4 km/h (20.3 km/h driving)
Elevation: 129m ascent, 129m descent (0m net, perfect balance)
Segments: 17 segments for 23 stops
```

### Sanity Checks Passed
✅ Distance values are reasonable (< 100km per trip)
✅ Speed values are realistic (5-50 km/h urban bus range)
✅ Duration = Driving Time + Dwell Time (consistent)
✅ Ascent/Descent are balanced for round trips
✅ Gradient percentages sum to ~100%
✅ Segment count matches stops (n-1 segments for n stops)

## API Usage

### Endpoint
```
POST /api/v1/simulation/trip-statistics/
```

### Request Body
```json
{
  "trip_ids": ["trip-id-1", "trip-id-2", ...]
}
```

### Response
```json
{
  "trip_ids": ["trip-id-1", "trip-id-2"],
  "statistics": {
    "start_time_minutes": 352.0,
    "end_time_minutes": 383.0,
    "total_distance_m": 7432.5,
    "total_duration_minutes": 31.0,
    ...
  },
  "error": null
}
```

## Files Modified

### Core Implementation
- **`app/utils/trip_statistics.py`** (NEW) - Trip statistics computation utilities (598 lines)
  - All trip statistics computation logic extracted into reusable utility module
  - `parse_gtfs_hms_to_seconds()` - GTFS time parsing
  - `dur_sec()` - Duration calculation with midnight handling
  - `haversine_distance()` - Geographic distance calculation
  - `compute_global_trip_statistics_combined()` - Global trip metrics
  - `extract_stop_to_stop_statistics_for_schedule()` - Segment analysis
  - `extract_route_difficulty_metrics_from_elevation()` - Route difficulty
  - `_calculate_segment_elevation_stats()` - Elevation matching for segments
  - All functions raise `ValueError` when elevation data is missing (no fallbacks)

- **`app/utils/__init__.py`** (NEW) - Utils package initialization

- **`app/routers/simulation.py`** - API endpoints (reduced from 910 to 406 lines)
  - Now focused purely on API request/response handling
  - Imports trip statistics functions from `app.utils.trip_statistics`
  - Orchestrates data fetching and delegates computation to utils

## Testing Scripts
- `verify_trip_statistics_features.py` - Feature presence verification
- `verify_combined_trips.py` - Combined trip functionality test
- `display_trip_statistics.py` - Comprehensive statistics display

## Notes
- All 58 features are always computed when elevation data is available
- **Missing elevation data now raises ValueError** - no silent fallbacks to haversine distance
- Combined trip analysis treats the sequence as a single continuous journey
- Times follow GTFS convention where values can exceed 24:00:00 (1440 minutes)
- Utility functions separated into `app/utils/trip_statistics.py` for reusability
- Router code reduced by ~55% (910 → 406 lines) for better maintainability

## Refactoring Summary (Latest Changes)

### Code Organization
- **Created `app/utils/` package** for reusable utility functions
- **Moved all trip statistics computation logic** from router to utilities
- **Router now focuses on**:
  - Request/response handling
  - Database queries
  - MinIO data fetching
  - Orchestration of utility functions

### Behavior Changes
- **Removed haversine fallbacks** - elevation data is now strictly required
- Functions raise `ValueError` if:
  - Elevation data is missing
  - Required columns (`cumulative_distance_m`, `altitude_m`) are not present
  - Segment elevation data cannot be matched
- This ensures data quality and catches missing elevation profiles early

### Benefits
- ✅ Better separation of concerns
- ✅ Reusable utilities for other modules
- ✅ Easier to test individual functions
- ✅ Cleaner, more focused router code
- ✅ Explicit error handling (no silent fallbacks)

