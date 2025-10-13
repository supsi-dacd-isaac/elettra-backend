"""
Consumption Prediction using Quantile Random Forest

Main prediction engine for estimating bus energy consumption.
"""

import io
import json
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
import logging

from .minio_utils import download_model_from_minio, build_model_path
from .feature_preparation import prepare_features_from_trip_stats, validate_features

logger = logging.getLogger(__name__)


class ConsumptionPredictor:
    """
    Energy consumption predictor using Quantile Random Forest model.
    """
    
    def __init__(
        self,
        model_name: Optional[str] = None,
        bucket_name: str = "consumption-models",
        cache_dir: Optional[Path] = None
    ):
        """
        Initialize the consumption predictor.
        
        Args:
            model_name: Model name (e.g., "qrf_production_crps_optimized") or full path
                       If None, model must be loaded explicitly
            bucket_name: MinIO bucket name
            cache_dir: Optional local cache directory for models
        """
        self.model = None
        self.metadata = None
        self.bucket_name = bucket_name
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.required_features = None
        
        if model_name:
            self.load_model(model_name)
    
    def load_model(self, model_name: str) -> None:
        """
        Load model from MinIO.
        
        Args:
            model_name: Model name (e.g., "qrf_production_crps_optimized") or full path
        """
        # Build full model path from name
        model_path = build_model_path(model_name)
        logger.info(f"Loading model: {model_name} -> {model_path}")
        
        model_bytes, metadata = download_model_from_minio(
            bucket_name=self.bucket_name,
            model_path=model_path,
            local_cache_dir=self.cache_dir
        )
        
        # Load model from bytes
        self.model = joblib.load(io.BytesIO(model_bytes))
        self.metadata = metadata
        
        # Extract required features from metadata - MANDATORY
        if not metadata:
            raise ValueError("Model metadata is required but not found. Cannot determine required features.")
        
        if 'selected_features' not in metadata:
            raise ValueError("Model metadata missing 'selected_features'. Cannot determine required features.")
        
        self.required_features = metadata['selected_features']
        logger.info(f"Model requires {len(self.required_features)} features")
        
        logger.info(f"✓ Model loaded successfully")
        
        # Log model info if available
        if metadata:
            if 'evaluation_metrics' in metadata:
                metrics = metadata['evaluation_metrics']
                logger.info(f"  Model R² Score: {metrics.get('r2', 'N/A'):.4f}")
                logger.info(f"  Model RMSE: {metrics.get('rmse', 'N/A'):.4f}")
    
    def load_model_from_file(self, file_path: str, metadata_path: Optional[str] = None) -> None:
        """
        Load model from local file (for testing).
        
        Args:
            file_path: Path to local model file
            metadata_path: Optional path to metadata JSON file
        """
        logger.info(f"Loading model from local file: {file_path}")
        
        self.model = joblib.load(file_path)
        
        if metadata_path:
            with open(metadata_path, 'r') as f:
                self.metadata = json.load(f)
            
            if 'selected_features' in self.metadata:
                self.required_features = self.metadata['selected_features']
        
        logger.info(f"✓ Model loaded from file")
    
    def prepare_features(
        self,
        trip_statistics: List[Dict[str, Any]],
        bus_length_m: float,
        battery_capacity_kwh: float,
        external_temp_celsius: float,
        additional_params: Optional[Dict[str, Any]] = None
    ) -> pd.DataFrame:
        """
        Prepare features from trip statistics.
        
        Args:
            trip_statistics: List of trip statistics dictionaries
            bus_length_m: Bus length in meters
            battery_capacity_kwh: Battery capacity in kWh
            external_temp_celsius: External temperature in Celsius
            additional_params: Optional additional parameters
            
        Returns:
            DataFrame with prepared features
        """
        df = prepare_features_from_trip_stats(
            trip_statistics=trip_statistics,
            bus_length_m=bus_length_m,
            battery_capacity_kwh=battery_capacity_kwh,
            external_temp_celsius=external_temp_celsius,
            additional_params=additional_params
        )
        
        # Validate and align features - MANDATORY
        if not self.required_features:
            raise ValueError("Required features not set. Model must be loaded with metadata first.")
        
        df = validate_features(df, self.required_features)
        
        return df
    
    def predict(
        self,
        features: pd.DataFrame,
        quantiles: Optional[List[float]] = None
    ) -> pd.DataFrame:
        """
        Make consumption predictions with uncertainty quantification.
        
        Args:
            features: DataFrame with features (including trip_id if available)
            quantiles: List of quantiles to predict (default: [0.05, 0.25, 0.5, 0.75, 0.95])
            
        Returns:
            DataFrame with predictions and prediction intervals
        """
        if self.model is None:
            raise ValueError("Model not loaded. Call load_model() first.")
        
        if quantiles is None:
            quantiles = [0.05, 0.25, 0.5, 0.75, 0.95]
        
        logger.info(f"Making predictions for {len(features)} trips")
        
        # Separate trip_id if present
        trip_ids = None
        if 'trip_id' in features.columns:
            trip_ids = features['trip_id'].values
            X = features.drop(columns=['trip_id'])
        else:
            X = features
        
        # Point prediction (median from QRF)
        logger.info(f"Predicting with features shape: {X.shape}")
        y_pred_median = self.model.predict(X)
        
        # Mean prediction from QRF
        logger.info("Computing mean prediction")
        y_pred_mean = self.model.predict(X, quantiles="mean")
        
        # Quantile predictions
        logger.info(f"Computing quantiles: {quantiles}")
        y_pred_quantiles = self.model.predict(X, quantiles=quantiles)
        
        # Create results DataFrame
        results = pd.DataFrame({
            'prediction_kwh': y_pred_mean,  # True mean from QRF model
            'prediction_median_kwh': y_pred_median  # Median for reference
        })
        
        # Add trip IDs if available
        if trip_ids is not None:
            results.insert(0, 'trip_id', trip_ids)
        
        # Add quantile predictions
        for i, q in enumerate(quantiles):
            results[f'quantile_{q:.2f}'] = y_pred_quantiles[:, i]
        
        logger.info(f"✓ Generated predictions for {len(results)} trips")
        
        return results
    
    def predict_from_json(
        self,
        json_data: Dict[str, Any],
        bus_length_m: float,
        battery_capacity_kwh: float,
        external_temp_celsius: float,
        quantiles: Optional[List[float]] = None
    ) -> Dict[str, Any]:
        """
        End-to-end prediction from trip statistics JSON.
        
        Args:
            json_data: Trip statistics JSON data
            bus_length_m: Bus length in meters
            battery_capacity_kwh: Battery capacity in kWh
            external_temp_celsius: External temperature in Celsius
            quantiles: Optional list of quantiles to predict
            
        Returns:
            Dictionary with predictions and metadata
        """
        # Extract trip statistics
        trip_statistics = json_data.get('trip_statistics', [])
        
        if not trip_statistics:
            raise ValueError("No trip statistics found in JSON data")
        
        logger.info(f"Processing {len(trip_statistics)} trips from JSON")
        
        # Prepare features
        features = self.prepare_features(
            trip_statistics=trip_statistics,
            bus_length_m=bus_length_m,
            battery_capacity_kwh=battery_capacity_kwh,
            external_temp_celsius=external_temp_celsius
        )
        
        # Make predictions
        predictions = self.predict(features, quantiles=quantiles)
        
        # Compile results
        results = {
            'shift_id': json_data.get('shift_id'),
            'file': json_data.get('file'),
            'total_trips': len(trip_statistics),
            'contextual_parameters': {
                'bus_length_m': bus_length_m,
                'battery_capacity_kwh': battery_capacity_kwh,
                'external_temp_celsius': external_temp_celsius
            },
            'predictions': predictions.to_dict(orient='records'),
            'summary': {
                'total_consumption_kwh': float(predictions['prediction_kwh'].sum()),
                'mean_consumption_per_trip_kwh': float(predictions['prediction_kwh'].mean()),
                'total_distance_km': float(features['total_distance_m'].sum() / 1000) if 'total_distance_m' in features.columns else None,
                'consumption_per_km_kwh': float(predictions['prediction_kwh'].sum() / (features['total_distance_m'].sum() / 1000)) if 'total_distance_m' in features.columns and features['total_distance_m'].sum() > 0 else None,
            }
        }
        
        # Add quantile summary
        if quantiles:
            results['summary']['quantiles'] = {}
            for q in quantiles:
                q_key = f'quantile_{q:.2f}'
                if q_key in predictions.columns:
                    results['summary']['quantiles'][f'q{int(q*100):02d}'] = float(predictions[q_key].sum())
        
        logger.info(f"✓ Total predicted consumption: {results['summary']['total_consumption_kwh']:.2f} kWh")
        
        return results

