"""
Consumption Prediction using Quantile Random Forest

Main prediction engine for estimating bus energy consumption.
"""

import io
import json
import sys
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Callable, Union
import logging

from .minio_utils import download_model_from_minio, build_model_path
from .feature_preparation import prepare_features_from_trip_stats, validate_features
from .greybox_models import (
    CombinedGreyboxQRF,
    MechanicalGreyBox,
    GreyBoxParams,
    compute_aux_energy,
    GREYBOX_PRED_FEATURE,
)
from .greybox_sensitivity import compute_battery_sensitivity_from_metadata

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
        self.is_greybox = False
        
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
        # Register custom classes/functions under __main__ to support models pickled from training script
        try:
            sys.modules['__main__'].CombinedGreyboxQRF = CombinedGreyboxQRF
            sys.modules['__main__'].MechanicalGreyBox = MechanicalGreyBox
            sys.modules['__main__'].GreyBoxParams = GreyBoxParams
            sys.modules['__main__'].compute_aux_energy = compute_aux_energy
        except Exception:
            # Non-fatal; proceed to load (will fail if required)
            pass
        self.model = joblib.load(io.BytesIO(model_bytes))
        self.metadata = metadata
        # Determine model type
        try:
            self.is_greybox = bool(metadata and metadata.get('model_type') == 'CombinedGreyboxQRF')
        except Exception:
            self.is_greybox = False
        
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
        
        # Greybox models require additional columns and a superset of features
        if self.is_greybox:
            # Provide alias expected by greybox from battery capacity parameter
            if 'bus_battery_kwh' not in df.columns:
                df['bus_battery_kwh'] = float(battery_capacity_kwh)
            
            # Ensure required features list is known
            if not self.required_features:
                raise ValueError("Required features not set. Model must be loaded with metadata first.")
            
            # Validate presence of QRF selected features (do not drop extras).
            # Greybox-specific helper features are generated internally and can be skipped here.
            missing_features = set(self.required_features) - set(df.columns)
            auto_generated = {GREYBOX_PRED_FEATURE}
            missing_non_auto = missing_features - auto_generated
            if missing_non_auto:
                raise ValueError(f"Missing required features for model: {missing_non_auto}")
            
            # Validate presence of greybox required columns
            gb_required = {'bus_length_m', 'bus_battery_kwh', 'total_distance_m', 'driving_average_speed_kmh', 'total_ascent_m', 'total_descent_m', 'driving_time_minutes', 'total_duration_minutes'}
            missing_gb = gb_required - set(df.columns)
            if missing_gb:
                raise ValueError(f"Missing required greybox features: {missing_gb}")
            
            return df
        else:
            # Validate and align features - MANDATORY (QRF-only models expect exact columns)
            if not self.required_features:
                raise ValueError("Required features not set. Model must be loaded with metadata first.")
            
            df = validate_features(df, self.required_features)
            return df
    
    def predict(
        self,
        features: pd.DataFrame,
        quantiles: Optional[List[float]] = None,
        aux_energy_fn: Optional[Callable[[pd.DataFrame], Union[np.ndarray, pd.Series]]] = None,
        override_mass: Optional[np.ndarray] = None,
    ) -> pd.DataFrame:
        """
        Make consumption predictions with uncertainty quantification.
        
        Args:
            features: DataFrame with features (including trip_id if available)
            quantiles: List of quantiles to predict (default: [0.05, 0.25, 0.5, 0.75, 0.95])
            aux_energy_fn: Optional function(X_df) -> np.ndarray|pd.Series of aux energy per row (kWh)
            
        Returns:
            DataFrame with predictions and prediction intervals.
            For greybox models, also includes:
            - drivetrain_kwh: Mechanical/drivetrain energy consumption
            - auxiliary_kwh: Auxiliary (HVAC, etc.) energy consumption
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
        if self.is_greybox:
            y_pred_median = self.model.predict(X, aux_energy_fn=aux_energy_fn, override_mass=override_mass)
        else:
            y_pred_median = self.model.predict(X)
        
        # Mean prediction from QRF
        logger.info("Computing mean prediction")
        if self.is_greybox:
            y_pred_mean = self.model.predict(X, quantiles="mean", aux_energy_fn=aux_energy_fn, override_mass=override_mass)
        else:
            y_pred_mean = self.model.predict(X, quantiles="mean")
        
        # Quantile predictions
        logger.info(f"Computing quantiles: {quantiles}")
        if self.is_greybox:
            y_pred_quantiles = self.model.predict(X, quantiles=quantiles, aux_energy_fn=aux_energy_fn, override_mass=override_mass)
        else:
            y_pred_quantiles = self.model.predict(X, quantiles=quantiles)
        
        # Create results DataFrame
        results = pd.DataFrame({
            'prediction_kwh': y_pred_mean,  # True mean from QRF model
            'prediction_median_kwh': y_pred_median  # Median for reference
        })
        
        # Add trip IDs if available
        if trip_ids is not None:
            results.insert(0, 'trip_id', trip_ids)
        
        # Add drivetrain and auxiliary consumption breakdown for greybox models
        if self.is_greybox:
            # Get auxiliary consumption first (deterministic, based on temp and duration)
            if aux_energy_fn is not None:
                aux_out = aux_energy_fn(X)
                if isinstance(aux_out, pd.Series):
                    auxiliary_kwh = aux_out.to_numpy(dtype=float)
                else:
                    auxiliary_kwh = np.asarray(aux_out, dtype=float).reshape(-1)
            else:
                auxiliary_kwh = self.model._aux_energy(X)
            results['auxiliary_kwh'] = auxiliary_kwh
            
            # Drivetrain = total - auxiliary = gb_pred + res_pred
            # This includes mechanical (greybox) + QRF residual correction
            # Mean drivetrain
            drivetrain_kwh = y_pred_mean - auxiliary_kwh
            results['drivetrain_kwh'] = drivetrain_kwh
            
            # Median drivetrain
            drivetrain_median_kwh = y_pred_median - auxiliary_kwh
            results['drivetrain_median_kwh'] = drivetrain_median_kwh
            
            logger.info(f"  Drivetrain total: {drivetrain_kwh.sum():.2f} kWh")
            logger.info(f"  Auxiliary total: {auxiliary_kwh.sum():.2f} kWh")
        
        # Add quantile predictions
        for i, q in enumerate(quantiles):
            results[f'quantile_{q:.2f}'] = y_pred_quantiles[:, i]
        
        # Add drivetrain quantiles for greybox models
        # Since auxiliary is deterministic: drivetrain_qXX = total_qXX - auxiliary
        if self.is_greybox:
            for i, q in enumerate(quantiles):
                results[f'drivetrain_quantile_{q:.2f}'] = y_pred_quantiles[:, i] - auxiliary_kwh
        
        logger.info(f"✓ Generated predictions for {len(results)} trips")
        
        return results
    
    def predict_from_json(
        self,
        json_data: Dict[str, Any],
        bus_length_m: float,
        battery_capacity_kwh: float,
        external_temp_celsius: float,
        quantiles: Optional[List[float]] = None,
        aux_energy_fn: Optional[Callable[[pd.DataFrame], Union[np.ndarray, pd.Series]]] = None,
        override_mass: Optional[np.ndarray] = None,
        battery_pack_density_override: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        End-to-end prediction from trip statistics JSON.
        
        Args:
            json_data: Trip statistics JSON data
            bus_length_m: Bus length in meters
            battery_capacity_kwh: Battery capacity in kWh
            external_temp_celsius: External temperature in Celsius
            quantiles: Optional list of quantiles to predict
            aux_energy_fn: Optional function(X_df) -> np.ndarray|pd.Series of aux energy per row (kWh)
            
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
        predictions = self.predict(features, quantiles=quantiles, aux_energy_fn=aux_energy_fn, override_mass=override_mass)

        # If this is a greybox model and we have parameters, compute and attach
        # battery-size sensitivities per trip. The sensitivity expresses how
        # much the mechanical energy changes (kWh) per 1 kWh change in
        # battery capacity, under the greybox model.
        greybox_params = None
        if self.is_greybox and isinstance(self.metadata, dict):
            greybox_params = self.metadata.get("greybox_params")
        if self.is_greybox and greybox_params:
            try:
                sens = compute_battery_sensitivity_from_metadata(
                    features, greybox_params,
                    battery_pack_density_override=battery_pack_density_override,
                )
                predictions["mass_sensitivity_kwh_per_kwh_batt"] = sens
            except Exception as exc:
                logger.warning(f"Failed to compute greybox battery sensitivity: {exc}")
        
        # Compile results
        summary_data = {
            'total_consumption_kwh': float(predictions['prediction_kwh'].sum()),
            'mean_consumption_per_trip_kwh': float(predictions['prediction_kwh'].mean()),
            'total_distance_km': float(features['total_distance_m'].sum() / 1000) if 'total_distance_m' in features.columns else None,
            'consumption_per_km_kwh': float(predictions['prediction_kwh'].sum() / (features['total_distance_m'].sum() / 1000)) if 'total_distance_m' in features.columns and features['total_distance_m'].sum() > 0 else None,
        }
        
        # Add drivetrain and auxiliary breakdown if available (greybox models)
        total_km = summary_data.get('total_distance_km')
        has_distance = isinstance(total_km, (int, float)) and total_km and total_km > 0
        
        if 'drivetrain_kwh' in predictions.columns:
            total_drivetrain = float(predictions['drivetrain_kwh'].sum())
            summary_data['total_drivetrain_kwh'] = total_drivetrain
            summary_data['mean_drivetrain_per_trip_kwh'] = float(predictions['drivetrain_kwh'].mean())
            if has_distance:
                summary_data['drivetrain_per_km_kwh'] = float(total_drivetrain / total_km)
        
        if 'auxiliary_kwh' in predictions.columns:
            total_auxiliary = float(predictions['auxiliary_kwh'].sum())
            summary_data['total_auxiliary_kwh'] = total_auxiliary
            summary_data['mean_auxiliary_per_trip_kwh'] = float(predictions['auxiliary_kwh'].mean())
            if has_distance:
                summary_data['auxiliary_per_km_kwh'] = float(total_auxiliary / total_km)
        
        results = {
            'shift_id': json_data.get('shift_id'),
            'file': json_data.get('file'),
            'total_trips': len(trip_statistics),
            'contextual_parameters': {
                'bus_length_m': bus_length_m,
                'battery_capacity_kwh': battery_capacity_kwh,
                'external_temp_celsius': external_temp_celsius
            },
            # Expose a small, focused subset of model metadata that is
            # relevant for downstream optimization.
            'greybox_params': greybox_params if greybox_params else None,
            'predictions': predictions.to_dict(orient='records'),
            'summary': summary_data
        }
        
        # Add quantile summary
        if quantiles:
            results['summary']['quantiles'] = {}
            # If distance is available, also compute per-km consumption for each quantile
            total_km = results['summary'].get('total_distance_km', None)
            has_distance = isinstance(total_km, (int, float)) and total_km and total_km > 0
            if has_distance:
                results['summary']['consumption_per_km_kwh_quantiles'] = {}
            
            # Check if drivetrain quantiles are available (greybox models)
            has_drivetrain_quantiles = f'drivetrain_quantile_{quantiles[0]:.2f}' in predictions.columns
            if has_drivetrain_quantiles:
                results['summary']['drivetrain_quantiles'] = {}
                if has_distance:
                    results['summary']['drivetrain_per_km_kwh_quantiles'] = {}
            
            for q in quantiles:
                q_key = f'quantile_{q:.2f}'
                if q_key in predictions.columns:
                    total_q_kwh = float(predictions[q_key].sum())
                    q_label = f'q{int(q*100):02d}'
                    results['summary']['quantiles'][q_label] = total_q_kwh
                    if has_distance:
                        results['summary']['consumption_per_km_kwh_quantiles'][q_label] = float(total_q_kwh / total_km)
                
                # Add drivetrain quantile totals
                dt_q_key = f'drivetrain_quantile_{q:.2f}'
                if dt_q_key in predictions.columns:
                    total_dt_q_kwh = float(predictions[dt_q_key].sum())
                    q_label = f'q{int(q*100):02d}'
                    results['summary']['drivetrain_quantiles'][q_label] = total_dt_q_kwh
                    if has_distance:
                        results['summary']['drivetrain_per_km_kwh_quantiles'][q_label] = float(total_dt_q_kwh / total_km)
        
        logger.info(f"✓ Total predicted consumption: {results['summary']['total_consumption_kwh']:.2f} kWh")
        
        return results

