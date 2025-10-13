# Automatic Bus ID Extraction from Shift Filenames

## Overview

The batch processing system now automatically extracts the bus ID from shift filenames, eliminating the need to manually map each shift to a bus.

## Filename Pattern

Shift statistics files follow this naming pattern:
```
XX_YYYXX_statistics.json
```

Where:
- `XX`: Shift sequence number (e.g., `01`, `02`, `03`)
- `YYY`: **Bus ID** (first 3 digits, e.g., `401`, `402`, `404`)
- `XX`: Additional shift identifier

## Examples

| Filename | Extracted Bus ID | Bus Model |
|----------|------------------|-----------|
| `01_40102_statistics.json` | `401` | AA_NF (18m, 514kWh) |
| `02_40201_statistics.json` | `402` | AU_NF (12m, 350kWh) |
| `03_40403_statistics.json` | `404` | AA_NF (18m, 514kWh) |
| `04_40909_statistics.json` | `409` | MB_NF (6m, 250kWh) |
| `05_41215_statistics.json` | `412` | AL_NF (10m, 300kWh) |

## How It Works

1. **Extract Bus ID**: The system parses the filename and extracts the first 3 digits after the underscore
2. **Lookup Bus Parameters**: It matches the bus ID with the configuration in `bus_models.json`
3. **Apply Parameters**: Uses the correct bus length and battery capacity for predictions

## Configuration

### Bus Models Definition

`bus_models.json` contains:
```json
{
  "bus_models": {
    "AA_NF": {
      "bus_length_m": 18,
      "battery_capacity_kwh": 514,
      "description": "18m articulated bus"
    },
    "AU_NF": {
      "bus_length_m": 12,
      "battery_capacity_kwh": 350,
      "description": "12m standard bus"
    },
    "AL_NF": {
      "bus_length_m": 10,
      "battery_capacity_kwh": 300,
      "description": "10m midi bus"
    },
    "MB_NF": {
      "bus_length_m": 6,
      "battery_capacity_kwh": 250,
      "description": "6m mini bus"
    }
  },
  "bus_assignments": {
    "401": "AA_NF",
    "402": "AU_NF",
    "403": "AA_NF",
    "404": "AA_NF",
    "405": "AA_NF",
    "406": "AA_NF",
    "407": "AA_NF",
    "408": "AU_NF",
    "409": "MB_NF",
    "410": "MB_NF",
    "412": "AL_NF",
    "415": "AL_NF",
    "416": "AL_NF",
    "417": "MB_NF",
    "418": "MB_NF"
  }
}
```

### Batch Configuration

Generate a configuration for specific buses:
```bash
# Generate config for specific bus IDs
python generate_batch_config.py \
    --bus-ids 401 402 404 409 \
    --external-temp 20 \
    --output batch_config.json

# Or generate for ALL assigned buses
python generate_batch_config.py \
    --bus-ids all \
    --external-temp 20 \
    --output batch_config_all.json
```

Generated `batch_config.json`:
```json
{
  "default": {
    "bus_length_m": 18,
    "battery_capacity_kwh": 514,
    "external_temp_celsius": 20.0
  },
  "shifts": {
    "401": {
      "bus_length_m": 18,
      "battery_capacity_kwh": 514,
      "external_temp_celsius": 20.0,
      "bus_model": "AA_NF"
    },
    "402": {
      "bus_length_m": 12,
      "battery_capacity_kwh": 350,
      "external_temp_celsius": 20.0,
      "bus_model": "AU_NF"
    }
  }
}
```

## Usage

### Batch Processing with Auto-Extraction

Simply run batch processing with the configuration - the system automatically:
1. Extracts bus ID from each filename
2. Matches it with the configuration
3. Uses the correct bus parameters

```bash
python batch_predict.py \
    --statistics-dir statistics/ \
    --config batch_config.json \
    --output-dir predictions/ \
    --model-name models/qrf_production_crps_optimized/qrf_production_crps_optimized.joblib
```

### Console Output Shows Extraction

```
Processing: 01_40102_statistics.json
  Shift ID: 01_40102
  Extracted Bus ID: 401        ← Automatically extracted!
  Bus length: 18 m
  Battery capacity: 514 kWh
  External temp: 20.0 °C
  Bus model: AA_NF
```

### Summary Includes Bus Info

The batch summary includes both the extracted bus ID and model:
```json
{
  "results": [
    {
      "shift_id": "01_40102",
      "bus_id": "401",              ← Extracted from filename
      "bus_model": "AA_NF",         ← From configuration
      "bus_length_m": 18,
      "battery_capacity_kwh": 514,
      "total_consumption_kwh": 325.35,
      "num_trips": 49
    }
  ]
}
```

## Fallback Behavior

If a bus ID cannot be extracted or matched:
1. System logs a warning
2. Falls back to default configuration parameters
3. Processing continues without interruption

## Complete Workflow Example

```bash
# 1. Setup: Create bus models file (already done)
cat bus_models.json

# 2. Generate batch configuration for your buses
python generate_batch_config.py \
    --bus-ids all \
    --external-temp 20 \
    --output batch_config_production.json

# 3. Run batch processing - bus IDs extracted automatically!
python batch_predict.py \
    --statistics-dir statistics/ \
    --config batch_config_production.json \
    --output-dir predictions_$(date +%Y%m%d) \
    --model-name models/qrf_production_crps_optimized/qrf_production_crps_optimized.joblib \
    --quantiles 0.05 0.25 0.5 0.75 0.95

# 4. Review results
cat predictions_$(date +%Y%m%d)/batch_summary.json
```

## Benefits

✅ **No manual mapping needed**: Bus ID extracted automatically from filenames
✅ **Consistent naming**: Uses fleet-standard bus IDs (401, 402, etc.)
✅ **Error reduction**: Eliminates manual shift-to-bus mapping errors
✅ **Full traceability**: Bus ID and model included in all outputs
✅ **Flexible**: Can still override parameters per shift if needed

## Advanced: Override Specific Shifts

You can still override specific shifts in the configuration:

```json
{
  "default": { "bus_length_m": 18, "battery_capacity_kwh": 514, "external_temp_celsius": 20 },
  "shifts": {
    "401": { "bus_length_m": 18, "battery_capacity_kwh": 514 },
    "01_40102": {
      "bus_length_m": 12,
      "battery_capacity_kwh": 300,
      "external_temp_celsius": 15,
      "comment": "Special case: using smaller bus on this specific shift"
    }
  }
}
```

The system first tries to match by full shift ID (`01_40102`), then by bus ID (`401`), then uses defaults.

