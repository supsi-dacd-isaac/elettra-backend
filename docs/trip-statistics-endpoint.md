# Trip Statistics Endpoint

## Overview

A new endpoint has been added to compute comprehensive trip statistics for GTFS trips. This endpoint extracts features from trip schedules and elevation profiles, which can be used for energy consumption modeling and trip analysis.

## Endpoint Details

**URL**: `POST /api/v1/simulation/trip-statistics/`

**Authentication**: Required (Bearer token)

**Request Body**:
```json
{
  "trip_ids": ["uuid1", "uuid2", ...]
}
```

**Response**:
```json
[
  {
    "trip_id": "uuid1",
    "statistics": {
      "total_duration_minutes": 11.0,
      "total_number_of_stops": 10,
      "total_distance_m": 3187.65,
      "average_speed_kmh": 17.39,
      "driving_time_minutes": 10.5,
      "total_dwell_time_minutes": 0.5,
      "elevation_range_m": 150.0,
      "mean_elevation_m": 500.0,
      "min_elevation_m": 450.0,
      "max_elevation_m": 600.0,
      "total_ascent_m": 100.0,
      "total_descent_m": 50.0,
      "mean_gradient": 0.025,
      "net_elevation_change_m": 50.0,
      "ascent_descent_ratio": 2.0,
      "elevation_profile_type": "mixed"
    },
    "error": null
  }
]
```

## Computed Statistics

### Basic Trip Metrics
- **total_duration_minutes**: Total trip duration from first stop arrival to last stop departure
- **total_number_of_stops**: Number of stops in the trip
- **total_distance_m**: Total distance traveled in meters (from elevation profile or haversine calculation)
- **average_speed_kmh**: Average speed in km/h
- **driving_time_minutes**: Time spent driving (excluding dwell time)
- **total_dwell_time_minutes**: Total time spent at stops

### Elevation Metrics (when elevation data available)
- **elevation_range_m**: Difference between max and min elevation
- **mean_elevation_m**: Average elevation along the route
- **min_elevation_m**: Minimum elevation
- **max_elevation_m**: Maximum elevation
- **total_ascent_m**: Total elevation gain
- **total_descent_m**: Total elevation loss
- **mean_gradient**: Average gradient (elevation change / distance)
- **net_elevation_change_m**: Net elevation change (end - start)
- **ascent_descent_ratio**: Ratio of total ascent to total descent
- **elevation_profile_type**: Classification of elevation profile:
  - `flat`: minimal elevation changes (< 1m)
  - `ascent_only`: only uphill
  - `descent_only`: only downhill
  - `mixed`: both uphill and downhill sections

## Implementation Details

### Data Sources

1. **Trip Schedule**: Fetched from the database (gtfs_stops_times joined with gtfs_stops)
   - Stop coordinates (latitude, longitude)
   - Arrival and departure times at each stop
   - Stop sequence

2. **Elevation Profile**: Fetched from MinIO storage
   - Parquet file containing elevation data along the route
   - Includes latitude, longitude, altitude, and cumulative distance

### Processing Logic

The endpoint follows the feature extraction logic from `playground/feature_extraction.py`:

1. For each trip_id:
   - Fetch stop times and coordinates from the database
   - Fetch elevation profile from MinIO (if available)
   - Calculate cumulative distance if not present in elevation data
   - Compute statistics using the `compute_global_trip_statistics_combined` function

2. Statistics computation:
   - Time calculations use GTFS time format (HH:MM:SS), handling midnight crossover
   - Distance calculations use elevation profile data when available, otherwise fall back to haversine distance
   - Elevation metrics are computed only when elevation data is available
   - Handles edge cases (flat routes, ascent-only, descent-only)

### Error Handling

- Each trip is processed independently
- If a trip has no stops, an error is returned for that trip: `"No stops found for trip"`
- If elevation data is not available, elevation metrics default to 0.0
- Exceptions during processing are caught and returned as errors in the response

## Testing

Comprehensive tests are included in `tests/test_trip_statistics.py`:

1. **test_trip_statistics_single_trip**: Tests computation for a single trip
2. **test_trip_statistics_multiple_trips**: Tests batch processing of multiple trips
3. **test_trip_statistics_invalid_trip**: Tests handling of non-existent trips
4. **test_trip_statistics_unauthorized**: Tests authentication requirement
5. **test_trip_statistics_empty_list**: Tests handling of empty trip list

All tests pass successfully (5/5 tests passed).

### Test Results

From the test report:
```
Test: trip_statistics_single_trip
Status: PASS
Details: Duration: 11.00 min, Distance: 3187.65 m, Stops: 10, Speed: 17.39 km/h
```

## Files Modified

1. **app/routers/simulation.py**: Added trip statistics endpoint and helper functions
2. **app/schemas/requests.py**: Added `TripStatisticsRequest` schema
3. **app/schemas/responses.py**: Added `TripStatisticsResponse` schema
4. **tests/test_trip_statistics.py**: Added comprehensive test suite

## Usage Example

```python
import requests

# Authentication
response = requests.post(
    "http://localhost:8002/auth/login",
    json={"email": "user@example.com", "password": "password"}
)
token = response.json()["access_token"]

# Get trip statistics
response = requests.post(
    "http://localhost:8002/api/v1/simulation/trip-statistics/",
    headers={"Authorization": f"Bearer {token}"},
    json={"trip_ids": ["5adc5823-61b8-4f7f-a953-13e93fb1f7fa"]}
)

statistics = response.json()
print(statistics)
```

## Future Enhancements

Possible enhancements for future versions:

1. Add stop-to-stop segment statistics (per-segment metrics)
2. Add route difficulty metrics (roughness, complexity scores)
3. Add support for combined trip sequences (multiple trips as a single route)
4. Add caching for frequently requested trips
5. Add pagination for large batch requests
6. Add filtering/selection of specific statistics to reduce response size

## References

- Feature extraction logic: `playground/feature_extraction.py`
- Elevation profile endpoint: `GET /api/v1/gtfs/elevation-profile/by-trip/{trip_id}`
- GTFS data models: `app/models.py`

