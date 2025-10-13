# Trip Statistics Computation Script

This script processes all shift JSON files in the `turni_macchina_2026/2026-TM_15f_lu-ve_TM_json/` folder and computes trip statistics using the API endpoint.

## Prerequisites

1. The Elettra backend API must be running on `http://localhost:8002`
2. The test user must exist in the database (`test@tplsa.ch` / `Elettra123!`)
3. Python 3.7+ with the `requests` library

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### 1. Test API Connection (Recommended)

First, test that the API is accessible:

```bash
python test_api_connection.py
```

This will verify:
- API is running on `http://localhost:8002`
- Authentication works with test credentials
- Trip statistics endpoint is accessible

### 2. Run the Main Script

```bash
python compute_shift_statistics.py
```

## What it does

1. **Authentication**: Logs in to the API using the test credentials
2. **File Processing**: Processes each JSON file in the `turni_macchina_2026/2026-TM_15f_lu-ve_TM_json/` folder
3. **Trip Extraction**: Extracts all `trip_id` values from each shift file
4. **Statistics Computation**: Calls the `/api/v1/simulation/trip-statistics/` endpoint for each shift
5. **Results Storage**: Saves individual results to `statistics/{filename}_statistics.json`
6. **Summary**: Creates a `processing_summary.json` with overall results

## Output Structure

### Individual Statistics Files
Each shift file generates a statistics file with:
- `file`: Original filename
- `shift_id`: Shift identifier (filename without extension)
- `total_trips`: Number of trips in the shift
- `trip_ids`: List of trip IDs processed
- `statistics`: API response with computed statistics
- `processed_at`: Timestamp of processing

### Processing Summary
The `processing_summary.json` contains:
- `total_files`: Total number of files processed
- `successful`: Number of successfully processed files
- `failed`: Number of failed files
- `processed_at`: Overall processing timestamp
- `results`: Array of all individual results

## Error Handling

- Authentication failures are logged and stop execution
- Individual file processing errors are logged but don't stop the overall process
- Failed files are included in the summary with error details
- API timeouts and connection errors are handled gracefully

## Configuration

You can modify the following variables in the script:
- `API_BASE_URL`: Backend API URL (default: `http://localhost:8002`)
- `LOGIN_EMAIL`: Authentication email (default: `test@tplsa.ch`)
- `LOGIN_PASSWORD`: Authentication password (default: `Elettra123!`)
- `STATISTICS_FOLDER`: Output folder name (default: `statistics`)

## Notes

- The script includes a 0.5-second delay between API calls to avoid overwhelming the server
- All JSON files are processed regardless of their naming pattern
- Empty files or files without trip IDs are handled gracefully
- The script creates the statistics folder if it doesn't exist
