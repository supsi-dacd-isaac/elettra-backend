"""
MinIO utilities for downloading and caching models
"""

import os
import io
import json
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import logging

from minio import Minio

logger = logging.getLogger(__name__)


def get_minio_client() -> Minio:
    """
    Create and return a MinIO client using environment configuration.
    
    Returns:
        Minio: Configured MinIO client
    """
    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9002")
    access_key = os.getenv("AWS_ACCESS_KEY_ID", "minio_user")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "minio_password")
    secure = os.getenv("MINIO_SECURE", "false").lower() in ("1", "true", "yes", "on")
    
    logger.info(f"Connecting to MinIO at {endpoint} (secure={secure})")
    
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure
    )


def download_model_from_minio(
    bucket_name: str,
    model_path: str,
    local_cache_dir: Optional[Path] = None
) -> Tuple[bytes, Optional[Dict[str, Any]]]:
    """
    Download a model file from MinIO. Optionally cache locally.
    
    Args:
        bucket_name: MinIO bucket name (e.g., "consumption-models")
        model_path: Path to model in bucket (e.g., "models/qrf_optimized_test/model.joblib")
        local_cache_dir: Optional local directory to cache the model
        
    Returns:
        Tuple of (model_bytes, metadata_dict)
        
    Raises:
        Exception: If model cannot be downloaded
    """
    client = get_minio_client()
    
    # Check cache first
    if local_cache_dir:
        local_cache_dir = Path(local_cache_dir)
        local_cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Create a safe filename from the model path
        cache_filename = model_path.replace("/", "_")
        cached_model_path = local_cache_dir / cache_filename
        
        if cached_model_path.exists():
            logger.info(f"Loading model from cache: {cached_model_path}")
            model_bytes = cached_model_path.read_bytes()
            
            # Check for cached metadata
            metadata_path = cached_model_path.with_suffix('.json')
            metadata = None
            if metadata_path.exists():
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
            
            return model_bytes, metadata
    
    # Download from MinIO
    logger.info(f"Downloading model from MinIO: {bucket_name}/{model_path}")
    
    try:
        response = client.get_object(bucket_name, model_path)
        try:
            model_bytes = response.read()
        finally:
            response.close()
            response.release_conn()
    except Exception as e:
        raise Exception(f"Failed to download model from MinIO: {bucket_name}/{model_path}: {str(e)}")
    
    # Try to download metadata (optional)
    metadata = None
    metadata_path = model_path.rsplit('.', 1)[0] + '_metadata.json'
    try:
        response = client.get_object(bucket_name, metadata_path)
        try:
            metadata_bytes = response.read()
            metadata = json.loads(metadata_bytes.decode('utf-8'))
            logger.info(f"Loaded model metadata from MinIO")
        finally:
            response.close()
            response.release_conn()
    except Exception:
        logger.warning(f"No metadata found for model at {metadata_path}")
    
    # Cache locally if requested
    if local_cache_dir:
        logger.info(f"Caching model locally: {cached_model_path}")
        cached_model_path.write_bytes(model_bytes)
        
        if metadata:
            metadata_cache_path = cached_model_path.with_suffix('.json')
            with open(metadata_cache_path, 'w') as f:
                json.dump(metadata, f, indent=2)
    
    return model_bytes, metadata


def build_model_path(model_name: str) -> str:
    """
    Build the full model path from a model name.
    
    Args:
        model_name: Model name (e.g., "qrf_production_crps_optimized")
        
    Returns:
        Full model path (e.g., "models/qrf_production_crps_optimized/qrf_production_crps_optimized.joblib")
    """
    # If already a full path, return as is
    if "/" in model_name and model_name.endswith(".joblib"):
        return model_name
    
    # Build standard path structure
    return f"models/{model_name}/{model_name}.joblib"


def list_models_in_minio(bucket_name: str, prefix: str = "") -> list:
    """
    List available models in MinIO bucket.
    
    Args:
        bucket_name: MinIO bucket name
        prefix: Optional prefix to filter models
        
    Returns:
        List of object names
    """
    client = get_minio_client()
    
    try:
        objects = client.list_objects(bucket_name, prefix=prefix, recursive=True)
        return [obj.object_name for obj in objects]
    except Exception as e:
        logger.error(f"Failed to list objects in {bucket_name}/{prefix}: {str(e)}")
        return []

