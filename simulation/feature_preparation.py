"""
Feature preparation for consumption prediction

Extracts features from trip statistics JSON and prepares them for the QRF model.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)


def prepare_features_from_trip_stats(
    trip_statistics: List[Dict[str, Any]],
    bus_length_m: float,
    battery_capacity_kwh: float,
    external_temp_celsius: float,
    additional_params: Optional[Dict[str, Any]] = None
) -> pd.DataFrame:
    """
    Prepare features DataFrame from trip statistics for consumption prediction.
    
    Args:
        trip_statistics: List of trip statistics dictionaries (from JSON)
        bus_length_m: Bus length in meters (e.g., 12, 18)
        battery_capacity_kwh: Battery capacity in kWh (e.g., 350, 450)
        external_temp_celsius: External temperature in Celsius
        additional_params: Optional additional parameters to include as features
        
    Returns:
        DataFrame with features ready for model prediction
    """
    rows = []
    
    for trip_stat in trip_statistics:
        trip_id = trip_stat.get('trip_id')
        stats = trip_stat.get('statistics', {}).get('statistics', {})
        
        if not stats:
            logger.warning(f"No statistics found for trip {trip_id}, skipping")
            continue
        
        # Extract all available features
        row = {
            # Trip identifier
            'trip_id': trip_id,
            
            # Basic trip features
            'total_distance_m': stats.get('total_distance_m', 0.0),
            'total_duration_minutes': stats.get('total_duration_minutes', 0.0),
            'total_number_of_stops': stats.get('total_number_of_stops', 0),
            'average_speed_kmh': stats.get('average_speed_kmh', 0.0),
            'driving_average_speed_kmh': stats.get('driving_average_speed_kmh', 0.0),
            'driving_time_minutes': stats.get('driving_time_minutes', 0.0),
            
            # Dwell time
            'total_dwell_time_minutes': stats.get('total_dwell_time_minutes', 0.0),
            'mean_dwell_time_minutes': stats.get('mean_dwell_time_minutes', 0.0),
            'median_dwell_time_minutes': stats.get('median_dwell_time_minutes', 0.0),
            
            # Timing features
            'start_time_minutes': stats.get('start_time_minutes', 0.0),
            'end_time_minutes': stats.get('end_time_minutes', 0.0),
            
            # Elevation features
            'elevation_range_m': stats.get('elevation_range_m', 0.0),
            'mean_elevation_m': stats.get('mean_elevation_m', 0.0),
            'min_elevation_m': stats.get('min_elevation_m', 0.0),
            'max_elevation_m': stats.get('max_elevation_m', 0.0),
            'total_ascent_m': stats.get('total_ascent_m', 0.0),
            'total_descent_m': stats.get('total_descent_m', 0.0),
            'net_elevation_change_m': stats.get('net_elevation_change_m', 0.0),
            'mean_gradient': stats.get('mean_gradient', 0.0),
            'ascent_descent_ratio': stats.get('ascent_descent_ratio', 0.0),
            
            # Segment features - distance
            'num_segments': stats.get('num_segments', 0),
            'mean_segment_distance_m': stats.get('mean_segment_distance_m', 0.0),
            'median_segment_distance_m': stats.get('median_segment_distance_m', 0.0),
            'min_segment_distance_m': stats.get('min_segment_distance_m', 0.0),
            'max_segment_distance_m': stats.get('max_segment_distance_m', 0.0),
            'std_segment_distance_m': stats.get('std_segment_distance_m', 0.0),
            
            # Segment features - duration
            'mean_segment_duration_minutes': stats.get('mean_segment_duration_minutes', 0.0),
            'median_segment_duration_minutes': stats.get('median_segment_duration_minutes', 0.0),
            'min_segment_duration_minutes': stats.get('min_segment_duration_minutes', 0.0),
            'max_segment_duration_minutes': stats.get('max_segment_duration_minutes', 0.0),
            
            # Segment features - speed
            'mean_segment_speed_kmh': stats.get('mean_segment_speed_kmh', 0.0),
            'median_segment_speed_kmh': stats.get('median_segment_speed_kmh', 0.0),
            'min_segment_speed_kmh': stats.get('min_segment_speed_kmh', 0.0),
            'max_segment_speed_kmh': stats.get('max_segment_speed_kmh', 0.0),
            
            # Segment features - elevation
            'mean_segment_ascent_m': stats.get('mean_segment_ascent_m', 0.0),
            'median_segment_ascent_m': stats.get('median_segment_ascent_m', 0.0),
            'max_segment_ascent_m': stats.get('max_segment_ascent_m', 0.0),
            'mean_segment_descent_m': stats.get('mean_segment_descent_m', 0.0),
            'median_segment_descent_m': stats.get('median_segment_descent_m', 0.0),
            'max_segment_descent_m': stats.get('max_segment_descent_m', 0.0),
            
            # Segment features - gradient
            'mean_segment_gradient': stats.get('mean_segment_gradient', 0.0),
            'median_segment_gradient': stats.get('median_segment_gradient', 0.0),
            'std_segment_gradient': stats.get('std_segment_gradient', 0.0),
            'max_segment_gradient': stats.get('max_segment_gradient', 0.0),
            'variance_segment_gradients': stats.get('variance_segment_gradients', 0.0),
            
            # Route complexity features
            'route_complexity_score': stats.get('route_complexity_score', 0.0),
            'roughness_index': stats.get('roughness_index', 0.0),
            'num_steep_segments_5pct_threshold': stats.get('num_steep_segments_5pct_threshold', 0),
            'num_steep_segments_10pct_threshold': stats.get('num_steep_segments_10pct_threshold', 0),
            'significant_elevation_changes': stats.get('significant_elevation_changes', 0),
            'elevation_change_frequency_per_km': stats.get('elevation_change_frequency_per_km', 0.0),
            
            # Gradient distribution
            'pct_uphill_segments': stats.get('pct_uphill_segments', 0.0),
            'pct_downhill_segments': stats.get('pct_downhill_segments', 0.0),
            'pct_flat_segments': stats.get('pct_flat_segments', 0.0),
            'ratio_gradient_negative': stats.get('ratio_gradient_negative', 0.0),
            'ratio_gradient_0_3': stats.get('ratio_gradient_0_3', 0.0),
            'ratio_gradient_3_6': stats.get('ratio_gradient_3_6', 0.0),
            'ratio_gradient_6_plus': stats.get('ratio_gradient_6_plus', 0.0),
            
            # Contextual parameters
            'bus_length_m': bus_length_m,
            'battery_capacity_kwh': battery_capacity_kwh,
            'external_temp_celsius': external_temp_celsius,
            'avg_temp_outside_celsius': external_temp_celsius,  # Alias for compatibility
        }
        
        # Add any additional parameters
        if additional_params:
            row.update(additional_params)
        
        rows.append(row)
    
    df = pd.DataFrame(rows)
    
    logger.info(f"Prepared features for {len(df)} trips with {len(df.columns)} columns")
    
    return df


def validate_features(df: pd.DataFrame, required_features: List[str]) -> pd.DataFrame:
    """
    Validate and align features with model requirements.
    
    Args:
        df: DataFrame with features
        required_features: List of required feature names in correct order (MANDATORY)
        
    Returns:
        DataFrame with features aligned to model requirements
        
    Raises:
        ValueError: If required_features is None or empty
    """
    if not required_features:
        raise ValueError("Required features list cannot be empty or None")
    
    # Check for missing features - FAIL IMMEDIATELY
    missing_features = set(required_features) - set(df.columns)
    if missing_features:
        raise ValueError(f"Missing required features: {missing_features}. Cannot proceed without all required features.")
    
    # Select and reorder features
    # Keep trip_id if present for reference
    if 'trip_id' in df.columns:
        feature_cols = ['trip_id'] + [f for f in required_features if f != 'trip_id']
        available_cols = [c for c in feature_cols if c in df.columns]
        df = df[available_cols]
    else:
        df = df[required_features]
    
    logger.info(f"Validated features: {len(df.columns)} columns, {len(df)} rows")
    
    return df

