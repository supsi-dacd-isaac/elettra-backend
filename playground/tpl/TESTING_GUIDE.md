# Testing Guide for Consumption Prediction

## Prerequisites

1. **Ensure Docker services are running:**
   ```bash
   cd /home/elettra/projects/elettra-backend
   docker-compose up -d
   ```

2. **Install dependencies (if not using Docker):**
   ```bash
   pip install -r requirements.txt
   ```

3. **Upload model to MinIO:**
   ```bash
   # Set up MinIO CLI
   mc alias set minio http://localhost:9002 minio_user minio_password
   
   # Create directories
   mc mb minio/consumption-models/models/qrf_optimized_test --ignore-existing
   
   # Upload your model
   mc cp /path/to/your/model.joblib minio/consumption-models/models/qrf_optimized_test/model.joblib
   
   # Optional: Upload metadata
   mc cp /path/to/your/model_metadata.json minio/consumption-models/models/qrf_optimized_test/model_metadata.json
   ```

## Test 1: Simple Example

Run the simple example script:

```bash
cd playground/tpl
python example_usage.py
```

Expected output:
```
======================================================================
EXAMPLE: Consumption Prediction
======================================================================

1. Loading trip statistics from: statistics/01_40102_statistics.json
   ✓ Loaded shift 01_40102 with 49 trips

2. Initializing predictor...

3. Loading model: models/qrf_optimized_test/model.joblib

4. Making predictions with:
   • Bus length: 18 m
   • Battery capacity: 450 kWh
   • External temperature: 20 °C

======================================================================
RESULTS
======================================================================

Total Consumption: XXXX.XX kWh
Mean per Trip: XX.XX kWh
Total Distance: XXX.XX km
Consumption per km: X.XXX kWh/km
...
```

## Test 2: Full CLI Test

Run the full test script with verbose output:

```bash
cd playground/tpl

python test_consumption_prediction.py \
    --json-file statistics/01_40102_statistics.json \
    --model-name models/qrf_optimized_test/model.joblib \
    --bus-length 18 \
    --battery-capacity 450 \
    --external-temp 20 \
    --verbose
```

## Test 3: Save Output to File

```bash
mkdir -p predictions

python test_consumption_prediction.py \
    --json-file statistics/01_40102_statistics.json \
    --model-name models/qrf_optimized_test/model.joblib \
    --bus-length 18 \
    --battery-capacity 450 \
    --external-temp 20 \
    --output predictions/01_40102_predictions.json \
    --cache-dir ./model_cache
```

Then check the output:
```bash
cat predictions/01_40102_predictions.json | jq '.summary'
```

## Test 4: Different Parameters

Test with different bus configurations:

```bash
# Small bus, cold weather
python test_consumption_prediction.py \
    --json-file statistics/01_40102_statistics.json \
    --model-name models/qrf_optimized_test/model.joblib \
    --bus-length 12 \
    --battery-capacity 350 \
    --external-temp 5

# Large bus, hot weather
python test_consumption_prediction.py \
    --json-file statistics/01_40102_statistics.json \
    --model-name models/qrf_optimized_test/model.joblib \
    --bus-length 18 \
    --battery-capacity 450 \
    --external-temp 30
```

## Test 5: Batch Processing

Process all statistics files:

```bash
cd playground/tpl

# Process all files
for json_file in statistics/*_statistics.json; do
    echo "Processing: $json_file"
    python test_consumption_prediction.py \
        --json-file "$json_file" \
        --model-name models/qrf_optimized_test/model.joblib \
        --bus-length 18 \
        --battery-capacity 450 \
        --external-temp 20 \
        --output "predictions/$(basename ${json_file/_statistics/_predictions})"
done
```

## Test 6: Custom Quantiles

Test with different quantiles:

```bash
python test_consumption_prediction.py \
    --json-file statistics/01_40102_statistics.json \
    --model-name models/qrf_optimized_test/model.joblib \
    --bus-length 18 \
    --battery-capacity 450 \
    --external-temp 20 \
    --quantiles 0.1 0.5 0.9
```

## Troubleshooting

### Error: "Model file not found"

1. Check MinIO is running:
   ```bash
   docker ps | grep minio
   ```

2. Verify bucket exists:
   ```bash
   mc ls minio/consumption-models/
   ```

3. Check model file:
   ```bash
   mc ls minio/consumption-models/models/qrf_optimized_test/
   ```

### Error: "Connection refused"

1. Check MINIO_ENDPOINT:
   ```bash
   echo $MINIO_ENDPOINT
   ```

2. If testing outside Docker:
   ```bash
   export MINIO_ENDPOINT=localhost:9002
   ```

3. If inside Docker:
   ```bash
   export MINIO_ENDPOINT=minio:9000
   ```

### Error: "Missing required features"

This means the model expects features that aren't in the trip statistics. Check:

1. Model metadata:
   ```bash
   mc cat minio/consumption-models/models/qrf_optimized_test/model_metadata.json
   ```

2. The feature preparation module will log which features are missing

### Warning: "No metadata found"

This is OK. The system will try to use all available features. To add metadata:

```bash
cat > model_metadata.json << EOF
{
  "selected_features": ["total_distance_m", "total_ascent_m", ...]
}
EOF

mc cp model_metadata.json minio/consumption-models/models/qrf_optimized_test/model_metadata.json
```

## Validation

After running predictions, validate results:

1. **Check output file exists:**
   ```bash
   ls -lh predictions/01_40102_predictions.json
   ```

2. **Inspect summary:**
   ```bash
   cat predictions/01_40102_predictions.json | jq '.summary'
   ```

3. **Check prediction reasonableness:**
   - Total consumption should be positive
   - Consumption per km typically 0.5-2.0 kWh/km for electric buses
   - Prediction intervals should be reasonable (not too wide)

4. **Verify all trips processed:**
   ```bash
   cat predictions/01_40102_predictions.json | jq '.total_trips'
   cat predictions/01_40102_predictions.json | jq '.predictions | length'
   ```

## Performance Benchmarks

Expected performance (approximate):

- Model download (first time): 2-5 seconds
- Model loading from cache: <1 second
- Feature extraction (50 trips): <1 second
- Prediction (50 trips): <1 second
- **Total (cached model): ~2 seconds**

## Next Steps

1. **Validate predictions** against historical consumption data
2. **Tune model** if predictions are off
3. **Add more features** if needed
4. **Integrate into API** for production use
5. **Set up monitoring** for prediction quality

