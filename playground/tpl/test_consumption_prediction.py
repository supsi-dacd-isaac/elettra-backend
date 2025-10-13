#!/usr/bin/env python3
"""
Test script for consumption prediction

This script tests the consumption prediction pipeline using trip statistics JSON files.

Usage:
    python test_consumption_prediction.py \
        --json-file path/to/statistics.json \
        --model-name models/qrf_optimized_test/model.joblib \
        --bus-length 18 \
        --battery-capacity 450 \
        --external-temp 20 \
        [--output predictions.json] \
        [--cache-dir ./model_cache]

Example:
    python test_consumption_prediction.py \
        --json-file statistics/01_40102_statistics.json \
        --model-name models/qrf_optimized_test/model.joblib \
        --bus-length 18 \
        --battery-capacity 450 \
        --external-temp 20
"""

import sys
import json
import argparse
from pathlib import Path
import logging

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from simulation.consumption_prediction import ConsumptionPredictor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Predict energy consumption for bus trips using QRF model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument(
        "--json-file",
        type=str,
        required=True,
        help="Path to trip statistics JSON file"
    )
    parser.add_argument(
        "--model-name",
        type=str,
        required=True,
        help="Model name (e.g., 'qrf_production_crps_optimized') or full path"
    )
    parser.add_argument(
        "--bus-length",
        type=float,
        required=True,
        help="Bus length in meters (e.g., 12, 18)"
    )
    parser.add_argument(
        "--battery-capacity",
        type=float,
        required=True,
        help="Battery capacity in kWh (e.g., 350, 450)"
    )
    parser.add_argument(
        "--external-temp",
        type=float,
        required=True,
        help="External temperature in Celsius (e.g., 15, 20, 25)"
    )
    parser.add_argument(
        "--bucket-name",
        type=str,
        default="consumption-models",
        help="MinIO bucket name (default: consumption-models)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output file path for predictions (JSON). If not provided, prints to stdout"
    )
    parser.add_argument(
        "--cache-dir",
        type=str,
        default="./model_cache",
        help="Local cache directory for models (default: ./model_cache)"
    )
    parser.add_argument(
        "--quantiles",
        type=float,
        nargs="+",
        default=[0.05, 0.25, 0.5, 0.75, 0.95],
        help="Quantiles to predict (default: 0.05 0.25 0.5 0.75 0.95)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose output"
    )
    
    return parser.parse_args()


def load_json_file(file_path: str) -> dict:
    """Load trip statistics from JSON file."""
    logger.info(f"Loading trip statistics from: {file_path}")
    
    with open(file_path, 'r') as f:
        data = json.load(f)
    
    logger.info(f"✓ Loaded JSON file: {data.get('shift_id', 'unknown shift')}")
    logger.info(f"  Total trips: {data.get('total_trips', 0)}")
    logger.info(f"  Successful trips: {data.get('successful_trips', 0)}")
    
    return data


def print_summary(results: dict, verbose: bool = False, quantiles: list = None):
    """Print prediction summary to console."""
    print("\n" + "="*70)
    print("CONSUMPTION PREDICTION RESULTS")
    print("="*70)
    
    # Shift info
    print(f"\nShift ID: {results.get('shift_id', 'N/A')}")
    print(f"Input File: {results.get('file', 'N/A')}")
    print(f"Total Trips: {results.get('total_trips', 0)}")
    
    # Contextual parameters
    params = results.get('contextual_parameters', {})
    print(f"\nContextual Parameters:")
    print(f"  • Bus Length: {params.get('bus_length_m', 'N/A')} m")
    print(f"  • Battery Capacity: {params.get('battery_capacity_kwh', 'N/A')} kWh")
    print(f"  • External Temperature: {params.get('external_temp_celsius', 'N/A')} °C")
    
    # Summary statistics
    summary = results.get('summary', {})
    print(f"\n{'─'*70}")
    print("CONSUMPTION SUMMARY (Point Estimates)")
    print(f"{'─'*70}")
    print(f"Total Consumption: {summary.get('total_consumption_kwh', 0):.2f} kWh")
    print(f"Mean per Trip: {summary.get('mean_consumption_per_trip_kwh', 0):.2f} kWh")
    
    if summary.get('total_distance_km'):
        print(f"Total Distance: {summary.get('total_distance_km', 0):.2f} km")
        print(f"Consumption per km: {summary.get('consumption_per_km_kwh', 0):.3f} kWh/km")
    
    # Probabilistic predictions - show all quantiles
    print(f"\n{'─'*70}")
    print("PROBABILISTIC PREDICTIONS")
    print(f"{'─'*70}")
    
    if 'quantiles' in summary and summary['quantiles']:
        print(f"\nTotal Shift Consumption by Quantile:")
        for q_label, total_q in sorted(summary['quantiles'].items()):
            q_pct = int(q_label[1:])  # Extract percentage from 'q05', 'q25', etc.
            print(f"  Q{q_pct:2d}%: {total_q:7.2f} kWh")
    
    # Individual trip predictions
    if verbose:
        print(f"\n{'─'*70}")
        print("INDIVIDUAL TRIP PREDICTIONS")
        print(f"{'─'*70}")
        
        for i, pred in enumerate(predictions[:10], 1):  # Show first 10
            trip_id = pred.get('trip_id', f'Trip {i}')
            consumption = pred.get('prediction_kwh', 0)
            print(f"\n{i}. {trip_id}")
            print(f"   Median (Q50): {consumption:.2f} kWh")
            
            # Show all available quantiles
            if quantiles:
                print(f"   Quantiles:")
                for q in sorted(quantiles):
                    q_key = f'quantile_{q:.2f}'
                    if q_key in pred:
                        print(f"     Q{int(q*100):2d}%: {pred[q_key]:6.2f} kWh")
        
        if len(predictions) > 10:
            print(f"\n... and {len(predictions) - 10} more trips")
    
    print("\n" + "="*70)


def main():
    """Main execution function."""
    args = parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        # 1. Load JSON file
        json_data = load_json_file(args.json_file)
        
        # 2. Initialize predictor
        logger.info(f"Initializing predictor with model: {args.model_name}")
        predictor = ConsumptionPredictor(
            bucket_name=args.bucket_name,
            cache_dir=Path(args.cache_dir)
        )
        
        # 3. Load model
        predictor.load_model(args.model_name)
        
        # 4. Make predictions
        logger.info("Making predictions...")
        results = predictor.predict_from_json(
            json_data=json_data,
            bus_length_m=args.bus_length,
            battery_capacity_kwh=args.battery_capacity,
            external_temp_celsius=args.external_temp,
            quantiles=args.quantiles
        )
        
        # 5. Print summary
        print_summary(results, verbose=args.verbose, quantiles=args.quantiles)
        
        # 6. Save output if requested
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_path, 'w') as f:
                json.dump(results, f, indent=2)
            
            logger.info(f"\n✓ Predictions saved to: {output_path}")
        
        return 0
        
    except Exception as e:
        logger.error(f"\n❌ ERROR: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    sys.exit(main())

