"""
Trip statistics computation utilities.

This module contains functions for computing comprehensive trip statistics
including global metrics, segment-level analysis, and route difficulty metrics.
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# =============================================================================
# Time and Distance Utilities
# =============================================================================

def parse_gtfs_hms_to_seconds(t: str) -> int:
    """
    Parse GTFS time string (HH:MM:SS) to seconds from midnight.
    
    Handles GTFS times that can exceed 24:00:00 (e.g., 25:30:00 for 1:30 AM next day).
    
    Args:
        t: Time string in HH:MM:SS format
        
    Returns:
        Seconds from midnight (can exceed 86400 for times past midnight)
    """
    h, m, s = map(int, t.split(':'))
    return h * 3600 + m * 60 + s


def dur_sec(dep_hms: str, arr_hms: str) -> int:
    """
    Calculate duration in seconds between departure and arrival times.
    
    Handles midnight crossing by adding 24 hours when arrival < departure.
    
    Args:
        dep_hms: Departure time in HH:MM:SS format
        arr_hms: Arrival time in HH:MM:SS format
        
    Returns:
        Duration in seconds (always >= 0)
    """
    dep = parse_gtfs_hms_to_seconds(dep_hms)
    arr = parse_gtfs_hms_to_seconds(arr_hms)
    if arr < dep:  # crossed midnight
        arr += 24 * 3600
    return max(arr - dep, 0)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great circle distance between two points on Earth.
    
    Args:
        lat1: Latitude of first point in decimal degrees
        lon1: Longitude of first point in decimal degrees
        lat2: Latitude of second point in decimal degrees
        lon2: Longitude of second point in decimal degrees
        
    Returns:
        Distance in meters
    """
    from math import radians, cos, sin, asin, sqrt
    
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    r = 6371  # Radius of earth in kilometers
    return c * r * 1000  # Convert to meters


# =============================================================================
# Segment-Level Calculations
# =============================================================================

def _calculate_segment_duration(stop1, stop2):
    """Calculate duration between two stops in seconds."""
    return dur_sec(stop1['departure_time'], stop2['arrival_time'])


def _calculate_dwell_time(stop):
    """Calculate dwell time at a stop in minutes."""
    dwell_seconds = dur_sec(stop['arrival_time'], stop['departure_time'])
    return dwell_seconds / 60  # Convert to minutes


def _calculate_segment_elevation_stats(stop1, stop2, elevation_df):
    """
    Calculate elevation statistics for a segment between two stops using actual elevation data.
    
    This function requires elevation data to be present. If elevation data is not available
    or cannot be matched, it returns an empty dict, which will cause the calling code to fail.
    
    Args:
        stop1: First stop data (dict-like with stop_id, stop_lat, stop_lon)
        stop2: Second stop data (dict-like with stop_id, stop_lat, stop_lon)
        elevation_df: DataFrame with elevation profile data
        
    Returns:
        Dictionary with elevation statistics or empty dict if data unavailable
    """
    if elevation_df is None or len(elevation_df) == 0:
        return {}
    
    # Get stop IDs and coordinates from GTFS data
    stop1_id = stop1.get('stop_id', '')
    stop2_id = stop2.get('stop_id', '')

    # Skip identical stops
    if stop1_id == stop2_id:
        return {}

    # Check if elevation data has pre-segmented data with start_stop_id and end_stop_id
    if 'start_stop_id' in elevation_df.columns and 'end_stop_id' in elevation_df.columns:
        segment_mask = (
            (elevation_df['start_stop_id'] == stop1_id) &
            (elevation_df['end_stop_id'] == stop2_id)
        )
        
        if segment_mask.any():
            segment_elevation = elevation_df[segment_mask]
        else:
            return {}
    else:
        # Work with raw elevation profile - find closest points to stops
        # Need latitude, longitude, and altitude_m columns
        if 'latitude' not in elevation_df.columns or 'longitude' not in elevation_df.columns or 'altitude_m' not in elevation_df.columns:
            return {}
        
        # Get stop coordinates
        stop1_lat = stop1.get('stop_lat')
        stop1_lon = stop1.get('stop_lon')
        stop2_lat = stop2.get('stop_lat')
        stop2_lon = stop2.get('stop_lon')
        
        if stop1_lat is None or stop1_lon is None or stop2_lat is None or stop2_lon is None:
            return {}
        
        # Find closest elevation points to each stop
        def find_closest_index(df, target_lat, target_lon):
            """Find index of closest point in elevation profile to target coordinates."""
            distances = np.sqrt(
                (df['latitude'] - target_lat)**2 + 
                (df['longitude'] - target_lon)**2
            )
            return distances.argmin()
        
        idx1 = find_closest_index(elevation_df, stop1_lat, stop1_lon)
        idx2 = find_closest_index(elevation_df, stop2_lat, stop2_lon)
        
        # Ensure idx1 < idx2 (forward direction)
        if idx1 >= idx2:
            return {}
        
        # Extract segment from elevation profile
        segment_elevation = elevation_df.iloc[idx1:idx2+1].copy()
    
    if len(segment_elevation) == 0:
        return {}
    
    # Calculate actual route distance from cumulative distance
    if 'cumulative_distance_m' in segment_elevation.columns:
        start_distance = segment_elevation['cumulative_distance_m'].iloc[0]
        end_distance = segment_elevation['cumulative_distance_m'].iloc[-1]
        segment_distance = end_distance - start_distance
    else:
        # Calculate distance from coordinates if cumulative_distance not available
        segment_distance = 0
        if 'latitude' in segment_elevation.columns and 'longitude' in segment_elevation.columns:
            for i in range(len(segment_elevation) - 1):
                lat1 = segment_elevation.iloc[i]['latitude']
                lon1 = segment_elevation.iloc[i]['longitude']
                lat2 = segment_elevation.iloc[i+1]['latitude']
                lon2 = segment_elevation.iloc[i+1]['longitude']
                segment_distance += haversine_distance(lat1, lon1, lat2, lon2)
    
    # Calculate elevation statistics
    start_elevation = segment_elevation['altitude_m'].iloc[0]
    end_elevation = segment_elevation['altitude_m'].iloc[-1]
    elevation_diff = end_elevation - start_elevation
    
    # Calculate cumulative ascent/descent
    if len(segment_elevation) > 1:
        diffs = segment_elevation['altitude_m'].diff().dropna()
        ascent = float(diffs.clip(lower=0).sum())
        descent = float((-diffs).clip(lower=0).sum())
    else:
        ascent = max(elevation_diff, 0)
        descent = max(-elevation_diff, 0)
    
    # Calculate gradients
    mean_gradient = elevation_diff / segment_distance if segment_distance > 0 else 0
    
    # Calculate max gradient
    if len(segment_elevation) > 1:
        elevation_diffs = segment_elevation['altitude_m'].diff().dropna()
        if 'cumulative_distance_m' in segment_elevation.columns:
            distance_diffs = segment_elevation['cumulative_distance_m'].diff().dropna()
        else:
            # Calculate point-to-point distances
            distance_diffs = []
            for i in range(len(segment_elevation) - 1):
                lat1 = segment_elevation.iloc[i]['latitude']
                lon1 = segment_elevation.iloc[i]['longitude']
                lat2 = segment_elevation.iloc[i+1]['latitude']
                lon2 = segment_elevation.iloc[i+1]['longitude']
                distance_diffs.append(haversine_distance(lat1, lon1, lat2, lon2))
            distance_diffs = pd.Series(distance_diffs)
        
        with np.errstate(divide='ignore', invalid='ignore'):
            gradients = np.where(distance_diffs != 0, elevation_diffs / distance_diffs, 0)
        max_gradient = np.abs(gradients).max() if len(gradients) > 0 else 0
    else:
        max_gradient = abs(mean_gradient)
    
    return {
        'start_elevation_m': float(start_elevation),
        'end_elevation_m': float(end_elevation),
        'segment_distance_m': float(segment_distance),
        'ascent_m': float(ascent),
        'descent_m': float(descent),
        'mean_gradient': float(mean_gradient),
        'max_gradient': float(max_gradient)
    }


# =============================================================================
# Global Trip Statistics
# =============================================================================

def compute_global_trip_statistics_combined(trip_schedule: pd.DataFrame, elevation_df: pd.DataFrame) -> dict:
    """
    Compute global statistics for a trip or sequence of trips.
    
    Uses schedule for timing and elevation for distance/elevation metrics.
    Requires elevation data to be present - will not fall back to haversine distance.
    
    Args:
        trip_schedule: DataFrame with GTFS schedule data (stops, times)
        elevation_df: DataFrame with elevation profile data
        
    Returns:
        Dictionary with global trip statistics
        
    Raises:
        ValueError: If elevation data is missing or invalid
    """
    stats = {}
    try:
        if trip_schedule is None or len(trip_schedule) == 0:
            return stats
        
        # Extract start and end times (minutes from midnight)
        first_stop_overall = trip_schedule.iloc[0]
        last_stop_overall = trip_schedule.iloc[-1]
        
        # Start time: arrival at first stop
        start_seconds = parse_gtfs_hms_to_seconds(first_stop_overall['arrival_time'])
        stats['start_time_minutes'] = start_seconds / 60
        
        # End time: departure from last stop
        end_seconds = parse_gtfs_hms_to_seconds(last_stop_overall['departure_time'])
        stats['end_time_minutes'] = end_seconds / 60
            
        # Combined duration = sum(per-trip durations) + sum(inter-trip gaps)
        if 'trip_index' in trip_schedule.columns:
            grouped = [g for _, g in trip_schedule.groupby('trip_index', sort=True)]
            # Sum per-trip durations
            total_trip_seconds = 0
            for g in grouped:
                first_stop_trip = g.iloc[0]
                last_stop_trip = g.iloc[-1]
                total_trip_seconds += dur_sec(first_stop_trip['departure_time'], last_stop_trip['arrival_time'])
            # Sum inter-trip gaps (arrival of t -> departure of t+1)
            inter_trip_gap_seconds_for_duration = 0
            for i in range(len(grouped) - 1):
                last_arrival = grouped[i].iloc[-1]['arrival_time']
                next_departure = grouped[i + 1].iloc[0]['departure_time']
                inter_trip_gap_seconds_for_duration += dur_sec(last_arrival, next_departure)
            total_seconds = total_trip_seconds + inter_trip_gap_seconds_for_duration
        else:
            total_seconds = dur_sec(first_stop_overall['departure_time'], last_stop_overall['arrival_time'])
        stats['total_duration_minutes'] = total_seconds / 60
        stats['total_number_of_stops'] = len(trip_schedule)

        dwell_times = []
        per_stop_dwell_seconds = 0
        for _, stop in trip_schedule.iterrows():
            per_stop_dwell_seconds += dur_sec(stop['arrival_time'], stop['departure_time'])

        # Include inter-trip gaps as dwell (arrival of trip t -> departure of trip t+1)
        inter_trip_gap_seconds = 0
        if 'trip_index' in trip_schedule.columns:
            grouped = [g for _, g in trip_schedule.groupby('trip_index', sort=True)]
            for i in range(len(grouped) - 1):
                last_arrival = grouped[i].iloc[-1]['arrival_time']
                next_departure = grouped[i + 1].iloc[0]['departure_time']
                inter_trip_gap_seconds += dur_sec(last_arrival, next_departure)

        total_dwell_seconds = per_stop_dwell_seconds + inter_trip_gap_seconds
        stats['total_dwell_time_minutes'] = total_dwell_seconds / 60
        stats['driving_time_minutes'] = max(stats['total_duration_minutes'] - stats['total_dwell_time_minutes'], 0)

        # REQUIRE elevation data - no fallback to haversine
        if elevation_df is None or len(elevation_df) == 0 or 'cumulative_distance_m' not in elevation_df.columns:
            raise ValueError("Elevation data with cumulative_distance_m is required for trip statistics")
        
        stats['total_distance_m'] = float(elevation_df['cumulative_distance_m'].max())

        dur_h = max(stats['total_duration_minutes'], 0.0) / 60.0
        stats['average_speed_kmh'] = (stats['total_distance_m'] / 1000.0) / dur_h if dur_h > 0 else 0.0
        drive_h = max(stats.get('driving_time_minutes', 0.0), 0.0) / 60.0
        stats['driving_average_speed_kmh'] = (stats['total_distance_m'] / 1000.0) / drive_h if drive_h > 0 else 0.0

        # Elevation metrics from elevation_df
        if 'altitude_m' not in elevation_df.columns:
            raise ValueError("Elevation data must contain altitude_m column")
            
        altitudes = elevation_df['altitude_m'].values
        stats['elevation_range_m'] = float(np.max(altitudes) - np.min(altitudes))
        stats['mean_elevation_m'] = float(np.mean(altitudes))
        stats['min_elevation_m'] = float(np.min(altitudes))
        stats['max_elevation_m'] = float(np.max(altitudes))

        elevation_changes = np.diff(altitudes)
        distances = elevation_df['cumulative_distance_m'].values
        distance_changes = np.diff(distances)
        # Suppress divide by zero warnings (handled by np.where)
        with np.errstate(divide='ignore', invalid='ignore'):
            gradients = np.where(distance_changes != 0, elevation_changes / distance_changes, 0)

        stats['total_ascent_m'] = float(np.sum(elevation_changes[elevation_changes > 0]))
        stats['total_descent_m'] = float(np.abs(np.sum(elevation_changes[elevation_changes < 0])))
        stats['mean_gradient'] = float(np.mean(gradients))
        stats['net_elevation_change_m'] = float(altitudes[-1] - altitudes[0])

        # Profile type classification
        min_elevation_threshold = 1.0
        if stats['total_descent_m'] < min_elevation_threshold:
            if stats['total_ascent_m'] < min_elevation_threshold:
                stats['ascent_descent_ratio'] = None
                stats['elevation_profile_type'] = 'flat'
            else:
                stats['ascent_descent_ratio'] = None
                stats['elevation_profile_type'] = 'ascent_only'
        else:
            if stats['total_ascent_m'] < min_elevation_threshold:
                stats['ascent_descent_ratio'] = 0.0
                stats['elevation_profile_type'] = 'descent_only'
            else:
                stats['ascent_descent_ratio'] = stats['total_ascent_m'] / stats['total_descent_m']
                stats['elevation_profile_type'] = 'mixed'

        return stats
    except Exception as e:
        logger.error(f"Error computing combined global trip stats: {e}")
        raise


# =============================================================================
# Segment Statistics
# =============================================================================

def extract_stop_to_stop_statistics_for_schedule(trip_schedule: pd.DataFrame, elevation_df: pd.DataFrame) -> dict:
    """
    Compute segment statistics across a GTFS schedule.
    
    Requires elevation data to be present - will not fall back to haversine distance.
    
    Args:
        trip_schedule: DataFrame with GTFS schedule data
        elevation_df: DataFrame with elevation profile data
        
    Returns:
        Dictionary with segment statistics
        
    Raises:
        ValueError: If elevation data is missing for any segment
    """
    stats = {}
    try:
        if trip_schedule is None or len(trip_schedule) < 2:
            return stats
        
        if elevation_df is None or len(elevation_df) == 0:
            raise ValueError("Elevation data is required for segment statistics")
            
        segment_stats = []
        for i in range(len(trip_schedule) - 1):
            current_stop = trip_schedule.iloc[i]
            next_stop = trip_schedule.iloc[i + 1]
            # Skip cross-boundary segments between different trips when concatenated
            try:
                if 'trip_index' in trip_schedule.columns and current_stop['trip_index'] != next_stop['trip_index']:
                    continue
            except Exception:
                pass
            # Skip stitched boundaries: identical stop repeated at trip junctions,
            # or virtually the same location within a small threshold
            try:
                if current_stop['stop_id'] == next_stop['stop_id']:
                    continue
                approx_gap_m = haversine_distance(
                    float(current_stop['stop_lat']), float(current_stop['stop_lon']),
                    float(next_stop['stop_lat']), float(next_stop['stop_lon'])
                )
                if approx_gap_m < 200.0:
                    continue
            except Exception:
                pass
            
            segment_duration = _calculate_segment_duration(current_stop, next_stop)
            
            # Get elevation data for this segment - REQUIRED, no fallback
            segment_elevation_stats = _calculate_segment_elevation_stats(
                current_stop, next_stop, elevation_df
            )
            
            if not segment_elevation_stats or 'segment_distance_m' not in segment_elevation_stats:
                raise ValueError(f"Missing elevation data for segment {current_stop['stop_id']} -> {next_stop['stop_id']}")
            
            segment_distance = segment_elevation_stats['segment_distance_m']
            
            km = segment_distance / 1000.0
            h = segment_duration / 3600.0
            segment_speed_kmh = km / h if h > 0 else 0.0
            
            segment_data = {
                'segment_distance_m': segment_distance,
                'segment_duration_minutes': segment_duration / 60,
                'segment_speed_kmh': segment_speed_kmh,
                'start_stop_id': current_stop['stop_id'],
                'end_stop_id': next_stop['stop_id'],
                'start_elevation_m': segment_elevation_stats.get('start_elevation_m', 0),
                'end_elevation_m': segment_elevation_stats.get('end_elevation_m', 0),
                'segment_ascent_m': segment_elevation_stats.get('ascent_m', 0),
                'segment_descent_m': segment_elevation_stats.get('descent_m', 0),
                'segment_mean_gradient': segment_elevation_stats.get('mean_gradient', 0),
                'segment_max_gradient': segment_elevation_stats.get('max_gradient', 0),
                'dwell_time_at_end_minutes': _calculate_dwell_time(next_stop)
            }
            
            segment_stats.append(segment_data)
        
        if segment_stats:
            distances = [s['segment_distance_m'] for s in segment_stats]
            durations = [s['segment_duration_minutes'] for s in segment_stats]
            speeds = [s['segment_speed_kmh'] for s in segment_stats]
            ascents = [s['segment_ascent_m'] for s in segment_stats]
            descents = [s['segment_descent_m'] for s in segment_stats]
            gradients = [s['segment_mean_gradient'] for s in segment_stats]
            max_gradients = [s['segment_max_gradient'] for s in segment_stats]
            dwell_times = [s['dwell_time_at_end_minutes'] for s in segment_stats]
            
            # Statistical aggregations
            stats.update({
                'num_segments': len(segment_stats),
                'mean_segment_distance_m': float(np.mean(distances)),
                'median_segment_distance_m': float(np.median(distances)),
                'min_segment_distance_m': float(np.min(distances)),
                'max_segment_distance_m': float(np.max(distances)),
                'std_segment_distance_m': float(np.std(distances)),
                'mean_segment_duration_minutes': float(np.mean(durations)),
                'median_segment_duration_minutes': float(np.median(durations)),
                'min_segment_duration_minutes': float(np.min(durations)),
                'max_segment_duration_minutes': float(np.max(durations)),
                'mean_segment_speed_kmh': float(np.mean(speeds)),
                'median_segment_speed_kmh': float(np.median(speeds)),
                'min_segment_speed_kmh': float(np.min(speeds)),
                'max_segment_speed_kmh': float(np.max(speeds)),
                'mean_segment_ascent_m': float(np.mean(ascents)),
                'median_segment_ascent_m': float(np.median(ascents)),
                'max_segment_ascent_m': float(np.max(ascents)),
                'mean_segment_descent_m': float(np.mean(descents)),
                'median_segment_descent_m': float(np.median(descents)),
                'max_segment_descent_m': float(np.max(descents)),
                'mean_segment_gradient': float(np.mean(gradients)),
                'median_segment_gradient': float(np.median(gradients)),
                'std_segment_gradient': float(np.std(gradients)),
                'max_segment_gradient': float(np.max(max_gradients)),
                'mean_dwell_time_minutes': float(np.mean(dwell_times)),
                'median_dwell_time_minutes': float(np.median(dwell_times)),
                'num_steep_segments_5pct_threshold': len([g for g in max_gradients if abs(g) > 0.05]),
                'num_steep_segments_10pct_threshold': len([g for g in max_gradients if abs(g) > 0.10]),
                'variance_segment_gradients': float(np.var(gradients))
            })
        return stats
    except Exception as e:
        logger.error(f"Error extracting segment statistics: {e}")
        raise


# =============================================================================
# Route Difficulty Metrics
# =============================================================================

def extract_route_difficulty_metrics_from_elevation(elevation_df: pd.DataFrame) -> dict:
    """
    Compute route difficulty metrics from elevation profile.
    
    Args:
        elevation_df: DataFrame with elevation profile data
        
    Returns:
        Dictionary with route difficulty metrics
        
    Raises:
        ValueError: If elevation data is missing or invalid
    """
    stats = {}
    try:
        if elevation_df is None or len(elevation_df) == 0:
            raise ValueError("Elevation data is required for route difficulty metrics")
        
        if 'cumulative_distance_m' not in elevation_df.columns or 'altitude_m' not in elevation_df.columns:
            raise ValueError("Elevation data must contain cumulative_distance_m and altitude_m columns")
            
        total_distance = elevation_df['cumulative_distance_m'].max()
        if total_distance > 0:
            elevation_variance = elevation_df['altitude_m'].var()
            roughness_index = elevation_variance / total_distance
        else:
            roughness_index = 0

        if len(elevation_df) > 1:
            elevation_diffs = elevation_df['altitude_m'].diff().dropna()
            distance_diffs = elevation_df['cumulative_distance_m'].diff().dropna()
            with np.errstate(divide='ignore', invalid='ignore'):
                gradients = np.where(distance_diffs != 0, elevation_diffs / distance_diffs, 0)
            uphill_segments = (gradients > 0.01).sum()
            downhill_segments = (gradients < -0.01).sum()
            flat_segments = ((gradients >= -0.01) & (gradients <= 0.01)).sum()
            total_segments = len(gradients)
            if total_segments > 0:
                pct_uphill = uphill_segments / total_segments * 100
                pct_downhill = downhill_segments / total_segments * 100
                pct_flat = flat_segments / total_segments * 100
            else:
                pct_uphill = pct_downhill = pct_flat = 0
        else:
            pct_uphill = pct_downhill = pct_flat = 0

        if len(elevation_df) > 1:
            elevation_diffs = elevation_df['altitude_m'].diff().dropna()
            distance_diffs = elevation_df['cumulative_distance_m'].diff().dropna()
            with np.errstate(divide='ignore', invalid='ignore'):
                gradients = np.where(distance_diffs != 0, elevation_diffs / distance_diffs, 0)
            ratio_gradient_negative = (gradients < 0).sum() / len(gradients) if len(gradients) > 0 else 0
            ratio_gradient_0_3 = ((gradients >= 0) & (gradients < 0.03)).sum() / len(gradients) if len(gradients) > 0 else 0
            ratio_gradient_3_6 = ((gradients >= 0.03) & (gradients < 0.06)).sum() / len(gradients) if len(gradients) > 0 else 0
            ratio_gradient_6_plus = (gradients >= 0.06).sum() / len(gradients) if len(gradients) > 0 else 0
        else:
            ratio_gradient_negative = ratio_gradient_0_3 = ratio_gradient_3_6 = ratio_gradient_6_plus = 0

        total_ascent = elevation_df['altitude_m'].diff().clip(lower=0).sum()
        total_distance_km = (total_distance / 1000) if total_distance else 0
        if len(elevation_df) > 1 and total_distance_km > 0:
            elevation_changes = elevation_df['altitude_m'].diff().abs()
            significant_changes = (elevation_changes > 1.0).sum()
            change_frequency_per_km = significant_changes / total_distance_km
        else:
            significant_changes = 0
            change_frequency_per_km = 0

        ratio_uphill = (pct_uphill / 100.0) if isinstance(pct_uphill, (int, float)) else 0
        normalized_roughness = min((roughness_index * 1000), 1.0) if isinstance(roughness_index, (int, float)) else 0
        normalized_frequency = min((change_frequency_per_km / 10.0), 1.0) if isinstance(change_frequency_per_km, (int, float)) else 0
        complexity_score = (
            normalized_roughness * 0.3 +
            ratio_uphill * 0.3 +
            ratio_gradient_6_plus * 0.3 +
            normalized_frequency * 0.1
        )
        stats.update({
            'roughness_index': float(roughness_index),
            'pct_uphill_segments': float(pct_uphill),
            'pct_downhill_segments': float(pct_downhill),
            'pct_flat_segments': float(pct_flat),
            'ratio_gradient_negative': float(ratio_gradient_negative),
            'ratio_gradient_0_3': float(ratio_gradient_0_3),
            'ratio_gradient_3_6': float(ratio_gradient_3_6),
            'ratio_gradient_6_plus': float(ratio_gradient_6_plus),
            'significant_elevation_changes': int(significant_changes),
            'elevation_change_frequency_per_km': float(change_frequency_per_km),
            'route_complexity_score': float(complexity_score)
        })
        return stats
    except Exception as e:
        logger.error(f"Error extracting route difficulty metrics: {e}")
        raise

