#!/usr/bin/env python3
"""
Script to compute trip statistics for all shift JSON files using the API endpoint.
Processes each JSON file in the specified folder and saves results to a statistics folder.

Configuration:
    The script loads credentials from tests/test.env file using python-dotenv.
    Required environment variables:
    - TEST_LOGIN_EMAIL: Email for API authentication
    - TEST_LOGIN_PASSWORD: Password for API authentication
    - API_BASE_URL: Base URL for the API (default: http://localhost:8002)

Usage:
    python compute_shift_statistics.py [--json-folder PATH] [--stats-folder PATH]

Arguments:
    --json-folder: Path to the folder containing JSON files (default: script_dir/turni_macchina_2026/2026-TM_15f_lu-ve_TM_json)
    --stats-folder: Path to the folder where statistics will be saved (default: script_dir/statistics/{json_folder_basename})
"""

import os
import json
import requests
import time
import argparse
from pathlib import Path
from typing import List, Dict, Any
import logging
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load environment variables from test.env
def load_config():
    """Load configuration from environment variables."""
    # Try to load from test.env file (relative to project root)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent.parent  # Go up to project root
    test_env_path = project_root / "tests" / "test.env"
    
    if test_env_path.exists():
        load_dotenv(test_env_path)
        logger.info(f"Loaded environment variables from {test_env_path}")
    else:
        logger.warning(f"test.env file not found at {test_env_path}, using system environment variables")
    
    # Configuration with fallbacks
    API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8002")
    LOGIN_EMAIL = os.getenv("TEST_LOGIN_EMAIL", "test@supsi.ch")
    LOGIN_PASSWORD = os.getenv("TEST_LOGIN_PASSWORD", ">tha0-!UdLb.hZ@aP)*x")
    STATISTICS_FOLDER = "statistics"
    
    return API_BASE_URL, LOGIN_EMAIL, LOGIN_PASSWORD, STATISTICS_FOLDER

def get_auth_token(api_base_url: str, login_email: str, login_password: str) -> str:
    """Get authentication token from the API."""
    login_url = f"{api_base_url}/auth/login"
    login_data = {
        "email": login_email,
        "password": login_password
    }
    
    try:
        response = requests.post(login_url, json=login_data, timeout=30)
        response.raise_for_status()
        token = response.json()["access_token"]
        logger.info("Successfully authenticated")
        return token
    except Exception as e:
        logger.error(f"Failed to authenticate: {e}")
        raise

def compute_trip_statistics(api_base_url: str, token: str, trip_ids: List[str]) -> Dict[str, Any]:
    """Compute statistics for a list of trip IDs using the API."""
    url = f"{api_base_url}/api/v1/simulation/trip-statistics/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    data = {"trip_ids": trip_ids}
    
    try:
        response = requests.post(url, json=data, headers=headers, timeout=60)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to compute statistics for trips {trip_ids}: {e}")
        return {"error": str(e), "trip_ids": trip_ids}

def process_shift_file(file_path: Path, api_base_url: str, token: str) -> Dict[str, Any]:
    """Process a single shift JSON file and compute statistics for each trip individually."""
    logger.info(f"Processing file: {file_path.name}")
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            shift_data = json.load(f)
        
        if not shift_data:
            logger.warning(f"Empty file: {file_path.name}")
            return {"error": "Empty file", "file": file_path.name}
        
        # Extract trip IDs from the shift data (use 'id' field which contains UUIDs)
        trip_ids = []
        for trip in shift_data:
            if 'id' in trip and trip['id']:
                trip_ids.append(trip['id'])
        
        if not trip_ids:
            logger.warning(f"No trip IDs found in: {file_path.name}")
            return {"error": "No trip IDs found", "file": file_path.name}
        
        logger.info(f"Found {len(trip_ids)} trips in {file_path.name}")
        
        # Compute statistics for each trip individually
        trip_statistics = []
        successful_trips = 0
        failed_trips = 0
        
        for i, trip_id in enumerate(trip_ids, 1):
            logger.info(f"Processing trip {i}/{len(trip_ids)}: {trip_id}")
            
            try:
                # Call API for single trip
                statistics = compute_trip_statistics(api_base_url, token, [trip_id])
                
                trip_result = {
                    "trip_id": trip_id,
                    "statistics": statistics,
                    "processed_at": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                
                if "error" in statistics and statistics["error"]:
                    failed_trips += 1
                    logger.warning(f"Failed to process trip {trip_id}: {statistics['error']}")
                else:
                    successful_trips += 1
                    logger.info(f"Successfully processed trip {trip_id}")
                
                trip_statistics.append(trip_result)
                
                # Small delay between API calls
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error processing trip {trip_id}: {e}")
                trip_statistics.append({
                    "trip_id": trip_id,
                    "error": str(e),
                    "processed_at": time.strftime("%Y-%m-%d %H:%M:%S")
                })
                failed_trips += 1
        
        # Add metadata
        result = {
            "file": file_path.name,
            "shift_id": file_path.stem,
            "total_trips": len(trip_ids),
            "successful_trips": successful_trips,
            "failed_trips": failed_trips,
            "trip_ids": trip_ids,
            "trip_statistics": trip_statistics,
            "processed_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Error processing {file_path.name}: {e}")
        return {"error": str(e), "file": file_path.name}

def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Compute trip statistics for all shift JSON files using the API endpoint"
    )
    parser.add_argument(
        "--json-folder",
        type=str,
        help="Path to the folder containing JSON files (default: script_dir/turni_macchina_2026/2026-TM_15f_lu-ve_TM_json)"
    )
    parser.add_argument(
        "--stats-folder",
        type=str,
        help="Path to the folder where statistics will be saved (default: script_dir/statistics/{json_folder_basename})"
    )
    return parser.parse_args()

def main():
    """Main function to process all shift files."""
    # Load configuration from environment variables
    api_base_url, login_email, login_password, statistics_folder = load_config()
    
    # Parse command line arguments
    args = parse_arguments()
    
    # Get the directory of this script
    script_dir = Path(__file__).parent
    
    # Set folder paths based on arguments or defaults
    if args.json_folder:
        json_folder = Path(args.json_folder)
    else:
        json_folder = script_dir / "turni_macchina_2026" / "2026-TM_15f_lu-ve_TM_json"
    
    if args.stats_folder:
        stats_folder = Path(args.stats_folder)
    else:
        # Create stats folder with json folder basename as subdirectory
        json_folder_basename = json_folder.name
        stats_folder = script_dir / statistics_folder / json_folder_basename
    
    # Create statistics folder if it doesn't exist
    stats_folder.mkdir(exist_ok=True)
    
    # Check if JSON folder exists
    if not json_folder.exists():
        logger.error(f"JSON folder not found: {json_folder}")
        return
    
    # Get authentication token
    try:
        token = get_auth_token(api_base_url, login_email, login_password)
    except Exception as e:
        logger.error(f"Failed to get authentication token: {e}")
        return
    
    # Find all JSON files
    json_files = list(json_folder.glob("*.json"))
    logger.info(f"Found {len(json_files)} JSON files to process")
    
    # Process each file
    results = []
    successful = 0
    failed = 0
    
    for i, json_file in enumerate(json_files, 1):
        logger.info(f"Processing file {i}/{len(json_files)}: {json_file.name}")
        
        result = process_shift_file(json_file, api_base_url, token)
        results.append(result)
        
        if "error" in result:
            failed += 1
            logger.error(f"Failed to process {json_file.name}: {result['error']}")
        else:
            successful += 1
            logger.info(f"Successfully processed {json_file.name}")
        
        # Save individual result
        output_file = stats_folder / f"{json_file.stem}_statistics.json"
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved statistics to {output_file.name}")
        except Exception as e:
            logger.error(f"Failed to save {output_file.name}: {e}")
        
        # Small delay to avoid overwhelming the API
        time.sleep(0.2)
    
    # Save summary
    summary = {
        "total_files": len(json_files),
        "successful": successful,
        "failed": failed,
        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "results": results
    }
    
    summary_file = stats_folder / "processing_summary.json"
    try:
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved processing summary to {summary_file.name}")
    except Exception as e:
        logger.error(f"Failed to save summary: {e}")
    
    logger.info(f"Processing complete: {successful} successful, {failed} failed")

if __name__ == "__main__":
    main()
