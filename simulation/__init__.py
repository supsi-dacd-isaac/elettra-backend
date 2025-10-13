"""
Consumption Prediction Module

This module provides tools for predicting bus energy consumption based on
trip statistics and contextual parameters using Quantile Random Forest models.
"""

from .feature_preparation import prepare_features_from_trip_stats
from .minio_utils import download_model_from_minio, get_minio_client
from .consumption_prediction import ConsumptionPredictor

__all__ = [
    "prepare_features_from_trip_stats",
    "download_model_from_minio",
    "get_minio_client",
    "ConsumptionPredictor",
]

