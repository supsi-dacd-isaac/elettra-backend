# Consumption Prediction Module

This module provides tools for predicting bus energy consumption based on trip statistics and contextual parameters using Quantile Random Forest (QRF) models.

## Architecture

The module consists of three main components:

### 1. MinIO Utilities (`minio_utils.py`)
- Download models from MinIO storage
- Cache models locally for faster access
- List available models

### 2. Feature Preparation (`feature_preparation.py`)
- Extract features from trip statistics JSON
- Prepare features for model input
- Validate and align features with model requirements

### 3. Consumption Prediction (`consumption_prediction.py`)
- Load QRF models
- Make predictions with uncertainty quantification
- Generate prediction intervals

## Usage

### Basic Usage

```python
from simulation.consumption_prediction import ConsumptionPredictor
import json

# Load trip statistics
with open('statistics/01_40102_statistics.json', 'r') as f:
    trip_data = json.load(f)

# Initialize predictor
predictor = ConsumptionPredictor(
    bucket_name="consumption-models",
    cache_dir="./model_cache"
)

# Load model from MinIO
predictor.load_model("models/qrf_optimized_test/model.joblib")

# Make predictions
results = predictor.predict_from_json(
    json_data=trip_data,
    bus_length_m=18,
    battery_capacity_kwh=450,
    external_temp_celsius=20
)

print(f"Total consumption: {results['summary']['total_consumption_kwh']:.2f} kWh")
```

### Using the Test Script

```bash
cd playground/tpl

# Basic prediction
python test_consumption_prediction.py \
    --json-file statistics/01_40102_statistics.json \
    --model-name models/qrf_optimized_test/model.joblib \
    --bus-length 18 \
    --battery-capacity 450 \
    --external-temp 20

# Save output to file
python test_consumption_prediction.py \
    --json-file statistics/01_40102_statistics.json \
    --model-name models/qrf_optimized_test/model.joblib \
    --bus-length 18 \
    --battery-capacity 450 \
    --external-temp 20 \
    --output predictions/01_40102_predictions.json \
    --verbose

# Different quantiles
python test_consumption_prediction.py \
    --json-file statistics/01_40102_statistics.json \
    --model-name models/qrf_optimized_test/model.joblib \
    --bus-length 12 \
    --battery-capacity 350 \
    --external-temp 15 \
    --quantiles 0.1 0.5 0.9
```

## Features Extracted

The module extracts over 60 features from trip statistics, including:

### Basic Trip Features
- `total_distance_m`: Total distance in meters
- `total_duration_minutes`: Total duration
- `total_number_of_stops`: Number of stops
- `average_speed_kmh`: Average speed
- `driving_time_minutes`: Driving time (excluding dwell)

### Elevation Features
- `total_ascent_m` / `total_descent_m`: Total elevation changes
- `elevation_range_m`: Elevation range
- `mean_gradient`: Average gradient
- `ascent_descent_ratio`: Ratio of ascent to descent

### Segment Features
- Mean, median, min, max, std for:
  - Segment distances
  - Segment durations
  - Segment speeds
  - Segment ascents/descents
  - Segment gradients

### Route Complexity
- `route_complexity_score`: Overall complexity metric
- `roughness_index`: Terrain roughness
- `num_steep_segments_*`: Count of steep segments
- `elevation_change_frequency_per_km`: Frequency of elevation changes

### Gradient Distribution
- `pct_uphill_segments` / `pct_downhill_segments` / `pct_flat_segments`
- `ratio_gradient_0_3` / `ratio_gradient_3_6` / `ratio_gradient_6_plus`

### Contextual Parameters
- `bus_length_m`: Bus length
- `battery_capacity_kwh`: Battery capacity
- `external_temp_celsius`: External temperature

## Output Format

The prediction output includes:

```json
{
  "shift_id": "01_40102",
  "file": "01_40102.json",
  "total_trips": 49,
  "contextual_parameters": {
    "bus_length_m": 18,
    "battery_capacity_kwh": 450,
    "external_temp_celsius": 20
  },
  "predictions": [
    {
      "trip_id": "4793ace5-0193-4e4f-a464-43321e978b9b",
      "prediction_kwh": 25.3,
      "quantile_0.05": 20.1,
      "quantile_0.25": 23.5,
      "quantile_0.50": 25.3,
      "quantile_0.75": 27.2,
      "quantile_0.95": 30.8,
      "pi_90_lower_kwh": 20.1,
      "pi_90_upper_kwh": 30.8,
      "pi_90_width_kwh": 10.7,
      "pi_50_lower_kwh": 23.5,
      "pi_50_upper_kwh": 27.2,
      "pi_50_width_kwh": 3.7
    }
  ],
  "summary": {
    "total_consumption_kwh": 1250.5,
    "mean_consumption_per_trip_kwh": 25.5,
    "total_distance_km": 180.2,
    "consumption_per_km_kwh": 6.94,
    "total_consumption_90pi_lower_kwh": 985.3,
    "total_consumption_90pi_upper_kwh": 1515.7
  }
}
```

## Model Storage

Models are stored in MinIO:
- **Bucket**: `consumption-models`
- **Path**: `models/{model_name}/model.joblib`
- **Metadata**: `models/{model_name}/model_metadata.json` (optional)

### Model Metadata Format

```json
{
  "selected_features": ["total_distance_m", "total_ascent_m", ...],
  "evaluation_metrics": {
    "r2": 0.95,
    "rmse": 2.34,
    "mae": 1.87
  },
  "model_type": "QuantileRandomForestRegressor",
  "training_date": "2025-10-13",
  "hyperparameters": {
    "n_estimators": 100,
    "max_depth": 20
  }
}
```

## Environment Configuration

The module uses the following environment variables (configured in docker-compose):

- `MINIO_ENDPOINT`: MinIO endpoint (default: `minio:9000` in docker, `localhost:9002` for testing)
- `AWS_ACCESS_KEY_ID`: MinIO access key (default: `minio_user`)
- `AWS_SECRET_ACCESS_KEY`: MinIO secret key (default: `minio_password`)
- `MINIO_SECURE`: Use HTTPS (default: `false`)

## Testing

### Unit Tests

```bash
# Run feature preparation tests
pytest tests/simulation/test_feature_preparation.py

# Run prediction tests
pytest tests/simulation/test_consumption_prediction.py
```

### Integration Test

```bash
# Test with real model from MinIO
python playground/tpl/test_consumption_prediction.py \
    --json-file playground/tpl/statistics/01_40102_statistics.json \
    --model-name models/qrf_optimized_test/model.joblib \
    --bus-length 18 \
    --battery-capacity 450 \
    --external-temp 20 \
    --verbose
```

## Dependencies

- `quantile-forest>=1.3.3`: Quantile Random Forest implementation
- `joblib>=1.3.2`: Model serialization
- `scikit-learn>=1.3.2`: Base ML utilities
- `pandas>=2.1.4`: Data manipulation
- `numpy>=1.24.0`: Numerical computing
- `minio>=7.2.7`: MinIO client

## Notes

- Models are cached locally in `cache_dir` to avoid repeated downloads
- The module handles missing features by filling with zeros (logged as warnings)
- Prediction intervals provide uncertainty estimates (90% and 50% by default)
- All consumption predictions are in kWh

