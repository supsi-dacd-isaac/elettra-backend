# Batch Processing for Consumption Predictions

This guide explains how to process multiple shift files in batch mode.

## Overview

The `batch_predict.py` script allows you to:
- Process multiple trip statistics files at once
- Configure different bus parameters for each shift
- Automatically save all predictions
- Generate a summary report

## Configuration File

Create a JSON configuration file that specifies the bus parameters for each shift:

```json
{
  "default": {
    "bus_length_m": 18,
    "battery_capacity_kwh": 450,
    "external_temp_celsius": 20
  },
  "shifts": {
    "01_40102": {
      "bus_length_m": 18,
      "battery_capacity_kwh": 450,
      "external_temp_celsius": 20
    },
    "02_40103": {
      "bus_length_m": 12,
      "battery_capacity_kwh": 300,
      "external_temp_celsius": 15
    }
  }
}
```

### Configuration Structure

- **`default`** (required): Default parameters used for all shifts not explicitly listed
  - `bus_length_m`: Bus length in meters
  - `battery_capacity_kwh`: Battery capacity in kWh
  - `external_temp_celsius`: External temperature in Celsius

- **`shifts`** (optional): Shift-specific parameters that override defaults
  - Keys should match the shift IDs in your statistics files
  - Each shift can override any of the default parameters

## Usage

### Basic Usage

Process all statistics files in a directory:

```bash
python batch_predict.py \
    --statistics-dir statistics/ \
    --config batch_config.json \
    --output-dir predictions/ \
    --model-name models/qrf_production_crps_optimized/qrf_production_crps_optimized.joblib
```

### With Custom Quantiles

Specify which quantiles to predict:

```bash
python batch_predict.py \
    --statistics-dir statistics/ \
    --config batch_config.json \
    --output-dir predictions/ \
    --model-name models/qrf_production_crps_optimized/qrf_production_crps_optimized.joblib \
    --quantiles 0.05 0.1 0.25 0.5 0.75 0.9 0.95
```

### Custom File Pattern

Process only specific files:

```bash
python batch_predict.py \
    --statistics-dir statistics/ \
    --config batch_config.json \
    --output-dir predictions/ \
    --model-name models/qrf_production_crps_optimized/qrf_production_crps_optimized.joblib \
    --pattern "01_*.json"
```

## Command-Line Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--statistics-dir` | Yes | Directory containing trip statistics JSON files |
| `--config` | Yes | Configuration JSON file with shift parameters |
| `--output-dir` | Yes | Directory to save prediction results |
| `--model-name` | Yes | Model path in MinIO |
| `--quantiles` | No | List of quantiles to predict (default: 0.05 0.25 0.5 0.75 0.95) |
| `--pattern` | No | Glob pattern to match statistics files (default: *_statistics.json) |

## Output

The script creates:

1. **Individual prediction files**: One JSON file per shift in the output directory
   - Format: `{shift_id}_predictions.json`
   - Contains full predictions for all trips plus summary

2. **Batch summary**: `batch_summary.json` in the output directory
   - Overall statistics for the batch run
   - List of all processed shifts with key metrics

### Example Output Structure

```
predictions/
├── 01_40102_predictions.json
├── 02_40103_predictions.json
├── 03_40104_predictions.json
└── batch_summary.json
```

### Batch Summary Format

```json
{
  "processed_files": 3,
  "successful": 3,
  "failed": 0,
  "model_name": "models/qrf_production_crps_optimized/qrf_production_crps_optimized.joblib",
  "results": [
    {
      "shift_id": "01_40102",
      "total_consumption_kwh": 325.35,
      "num_trips": 49,
      "total_distance_km": 188.15,
      "consumption_per_km_kwh": 1.729,
      "bus_length_m": 18,
      "battery_capacity_kwh": 450,
      "external_temp_celsius": 20,
      "output_file": "01_40102_predictions.json"
    }
  ]
}
```

## Example Workflow

1. **Prepare your configuration file**:
   ```bash
   cp batch_config_example.json my_config.json
   # Edit my_config.json with your shift parameters
   ```

2. **Run batch processing**:
   ```bash
   python batch_predict.py \
       --statistics-dir statistics/ \
       --config my_config.json \
       --output-dir predictions_$(date +%Y%m%d) \
       --model-name models/qrf_production_crps_optimized/qrf_production_crps_optimized.joblib
   ```

3. **Review the summary**:
   ```bash
   cat predictions_$(date +%Y%m%d)/batch_summary.json
   ```

## Tips

- **Default values**: If most of your shifts use the same parameters, set them as defaults and only override specific shifts
- **Incremental processing**: Use the `--pattern` argument to process specific subsets of files
- **Output organization**: Use dated output directories to keep track of different runs
- **Configuration management**: Keep different config files for different seasons, bus types, etc.

## Error Handling

- If a shift file fails to process, the script continues with the remaining files
- Failed files are logged and counted in the summary
- Check the console output or logs for specific error messages

