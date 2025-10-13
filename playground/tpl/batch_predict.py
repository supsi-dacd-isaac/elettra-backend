#!/usr/bin/env python3
"""
Batch processing script for consumption predictions

This script processes multiple shift statistics files using a configuration file
that specifies the bus length, battery capacity, and external temperature for each shift.
"""

import sys
import json
import argparse
import pandas as pd
from pathlib import Path
import logging

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from simulation.consumption_prediction import ConsumptionPredictor

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config(config_file: str) -> dict:
    """Load configuration file with shift parameters
    
    Config file should be a JSON with format:
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
    
    Or use default values for all shifts by omitting the "shifts" section.
    """
    with open(config_file, 'r') as f:
        config = json.load(f)
    
    if 'default' not in config:
        raise ValueError("Config file must have a 'default' section with default parameters")
    
    return config


def print_detailed_statistics(results_summary: list):
    """Print detailed statistics for all shifts"""
    print(f"\n{'='*80}")
    print("📊 DETAILED STATISTICS BY SHIFT")
    print(f"{'='*80}")
    
    # Group by bus model
    bus_models = {}
    for result in results_summary:
        model = result.get('bus_model', 'Unknown')
        if model not in bus_models:
            bus_models[model] = []
        bus_models[model].append(result)
    
    for model, shifts in bus_models.items():
        print(f"\n🚌 BUS MODEL: {model}")
        print(f"{'─'*60}")
        
        # Calculate model-level statistics
        total_consumption = sum(s['total_consumption_kwh'] for s in shifts)
        total_trips = sum(s['num_trips'] for s in shifts)
        total_distance = sum(s.get('distance_statistics', {}).get('total_km', 0) for s in shifts)
        
        print(f"📈 MODEL TOTALS:")
        print(f"  Total Consumption: {total_consumption:.2f} kWh")
        print(f"  Total Trips: {total_trips}")
        print(f"  Total Distance: {total_distance:.2f} km")
        print(f"  Avg Consumption per Trip: {total_consumption/total_trips:.2f} kWh")
        if total_distance > 0:
            print(f"  Avg Consumption per km: {total_consumption/total_distance:.3f} kWh/km")
        
        # Individual shift statistics
        print(f"\n📋 INDIVIDUAL SHIFTS:")
        for shift in sorted(shifts, key=lambda x: x['total_consumption_kwh'], reverse=True):
            print(f"\n  🔸 {shift['shift_id']} (Bus {shift.get('bus_id', 'N/A')}):")
            print(f"     Consumption: {shift['total_consumption_kwh']:.2f} kWh")
            print(f"     Trips: {shift['num_trips']}")
            
            # Consumption statistics
            if 'consumption_statistics' in shift:
                stats = shift['consumption_statistics']
                print(f"     Per Trip Stats:")
                print(f"       Mean: {stats['mean']:.2f} kWh")
                print(f"       Median: {stats['median']:.2f} kWh")
                print(f"       Std: {stats['std']:.2f} kWh")
                print(f"       Min: {stats['min']:.2f} kWh")
                print(f"       Max: {stats['max']:.2f} kWh")
                print(f"       Q25: {stats['q25']:.2f} kWh")
                print(f"       Q75: {stats['q75']:.2f} kWh")
            
            # Distance statistics
            if 'distance_statistics' in shift and shift['distance_statistics']:
                dist_stats = shift['distance_statistics']
                print(f"     Distance Stats:")
                print(f"       Total: {dist_stats['total_km']:.2f} km")
                print(f"       Avg per trip: {dist_stats['avg_per_trip_km']:.2f} km")
                print(f"       Consumption per km: {dist_stats['consumption_per_km_kwh']:.3f} kWh/km")
            
            # Quantile statistics
            if 'quantile_statistics' in shift and shift['quantile_statistics']:
                quant_stats = shift['quantile_statistics']
                print(f"     Quantile Totals:")
                for q_label, q_value in sorted(quant_stats.items()):
                    q_pct = int(q_label[1:])  # Extract percentage from 'q05', 'q25', etc.
                    print(f"       Q{q_pct:2d}%: {q_value:.2f} kWh")


def get_shift_params(config: dict, shift_id: str, bus_id: str = None) -> dict:
    """Get parameters for a specific shift, falling back to defaults

    Args:
        config: Configuration dictionary
        shift_id: Full shift ID (e.g., "01_40401")
        bus_id: Extracted bus ID (e.g., "404"), optional

    Returns:
        Dict with bus parameters including global temperature
    """
    default_params = config['default']
    
    # Add global temperature
    if 'global' in config and 'external_temp_celsius' in config['global']:
        default_params['external_temp_celsius'] = config['global']['external_temp_celsius']

    # Try to find params by shift_id first
    if 'shifts' in config and shift_id in config['shifts']:
        shift_params = {**default_params, **config['shifts'][shift_id]}
        return shift_params

    # Try to find params by bus_id if available
    if bus_id and 'shifts' in config and bus_id in config['shifts']:
        shift_params = {**default_params, **config['shifts'][bus_id]}
        return shift_params

    return default_params


def batch_predict(
    statistics_dir: str,
    config_file: str,
    output_dir: str,
    model_name: str,
    quantiles: list = None,
    pattern: str = "*_statistics.json"
):
    """Process multiple shift files in batch
    
    Args:
        statistics_dir: Directory containing trip statistics JSON files
        config_file: Path to configuration JSON file
        output_dir: Directory to save prediction results
        model_name: Name/path of the model in MinIO
        quantiles: List of quantiles to predict (optional)
        pattern: Glob pattern to match statistics files
    """
    stats_dir = Path(statistics_dir)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Load configuration
    logger.info(f"Loading configuration from: {config_file}")
    config = load_config(config_file)
    
    # Find all statistics files
    stats_files = sorted(stats_dir.glob(pattern))
    if not stats_files:
        logger.error(f"No statistics files found in {statistics_dir} matching pattern {pattern}")
        return
    
    logger.info(f"Found {len(stats_files)} statistics files to process")
    
    # Initialize predictor
    logger.info("Initializing predictor...")
    predictor = ConsumptionPredictor(
        bucket_name="consumption-models",
        cache_dir="./model_cache"
    )
    
    # Load model
    logger.info(f"Loading model: {model_name}")
    predictor.load_model(model_name)
    
    # Process each file
    results_summary = []
    successful = 0
    failed = 0
    
    for stats_file in stats_files:
        try:
            logger.info(f"\nProcessing: {stats_file.name}")
            
            # Load trip statistics
            with open(stats_file, 'r') as f:
                trip_data = json.load(f)
            
            shift_id = trip_data.get('shift_id', stats_file.stem.replace('_statistics', ''))
            logger.info(f"  Shift ID: {shift_id}")
            
            # Extract bus ID from shift_id (e.g., "01_40401" -> "404")
            # Pattern: XX_YYYXX where YYY is the bus ID
            bus_id = None
            if '_' in shift_id:
                parts = shift_id.split('_')
                if len(parts) >= 2 and len(parts[1]) >= 3:
                    bus_id = parts[1][:3]  # First 3 digits after underscore
                    logger.info(f"  Extracted Bus ID: {bus_id}")
            
            # Get parameters for this shift (using bus_id if available)
            params = get_shift_params(config, shift_id, bus_id)
            logger.info(f"  Bus length: {params['bus_length_m']} m")
            logger.info(f"  Battery capacity: {params['battery_capacity_kwh']} kWh")
            logger.info(f"  External temp: {params['external_temp_celsius']} °C")
            if 'bus_model' in params:
                logger.info(f"  Bus model: {params['bus_model']}")
            
            # Make predictions
            results = predictor.predict_from_json(
                json_data=trip_data,
                bus_length_m=params['bus_length_m'],
                battery_capacity_kwh=params['battery_capacity_kwh'],
                external_temp_celsius=params['external_temp_celsius'],
                quantiles=quantiles
            )
            
            # Save results
            output_file = out_dir / f"{shift_id}_predictions.json"
            with open(output_file, 'w') as f:
                json.dump(results, f, indent=2)
            
            logger.info(f"  ✓ Predictions saved to: {output_file.name}")
            logger.info(f"  Total consumption: {results['summary']['total_consumption_kwh']:.2f} kWh")
            
            # Track summary with detailed statistics
            num_trips = len(results['predictions'])
            summary = results['summary']
            
            # Calculate detailed statistics
            predictions_df = pd.DataFrame(results['predictions'])
            
            # Basic statistics
            consumption_stats = {
                'mean': predictions_df['prediction_kwh'].mean(),
                'median': predictions_df['prediction_median_kwh'].mean(),
                'std': predictions_df['prediction_kwh'].std(),
                'min': predictions_df['prediction_kwh'].min(),
                'max': predictions_df['prediction_kwh'].max(),
                'q25': predictions_df['prediction_kwh'].quantile(0.25),
                'q75': predictions_df['prediction_kwh'].quantile(0.75)
            }
            
            # Distance statistics (if available)
            distance_stats = {}
            if 'total_distance_km' in summary and summary['total_distance_km'] > 0:
                avg_distance_per_trip = summary['total_distance_km'] / num_trips
                distance_stats = {
                    'total_km': summary['total_distance_km'],
                    'avg_per_trip_km': avg_distance_per_trip,
                    'consumption_per_km_kwh': summary.get('consumption_per_km_kwh', 0)
                }
            
            # Quantile statistics (if available)
            quantile_stats = {}
            if 'quantiles' in summary and summary['quantiles']:
                quantile_stats = summary['quantiles']
            
            summary_entry = {
                'shift_id': shift_id,
                'bus_id': bus_id,
                'bus_model': params.get('bus_model', 'Unknown'),
                'bus_length_m': params['bus_length_m'],
                'battery_capacity_kwh': params['battery_capacity_kwh'],
                'external_temp_celsius': params['external_temp_celsius'],
                'num_trips': num_trips,
                'total_consumption_kwh': summary['total_consumption_kwh'],
                'consumption_statistics': consumption_stats,
                'distance_statistics': distance_stats,
                'quantile_statistics': quantile_stats,
                'output_file': str(output_file.name)
            }

            results_summary.append(summary_entry)
            
            successful += 1
            
        except Exception as e:
            logger.error(f"  ✗ Failed to process {stats_file.name}: {e}")
            failed += 1
            continue
    
    # Save summary
    summary_file = out_dir / "batch_summary.json"
    with open(summary_file, 'w') as f:
        json.dump({
            'processed_files': len(stats_files),
            'successful': successful,
            'failed': failed,
            'model_name': model_name,
            'results': results_summary
        }, f, indent=2)
    
    # Print detailed statistics summary
    print_detailed_statistics(results_summary)
    
    logger.info(f"\n{'='*70}")
    logger.info("BATCH PROCESSING COMPLETE")
    logger.info(f"{'='*70}")
    logger.info(f"Total files: {len(stats_files)}")
    logger.info(f"Successful: {successful}")
    logger.info(f"Failed: {failed}")
    logger.info(f"Summary saved to: {summary_file}")
    logger.info(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Batch process consumption predictions for multiple shifts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
  # Process all shifts with default parameters
  python batch_predict.py \\
      --statistics-dir statistics/ \\
      --config batch_config.json \\
      --output-dir predictions/ \\
      --model-name qrf_production_crps_optimized

  # With custom quantiles
  python batch_predict.py \\
      --statistics-dir statistics/ \\
      --config batch_config.json \\
      --output-dir predictions/ \\
      --model-name qrf_production_crps_optimized \\
      --quantiles 0.05 0.25 0.5 0.75 0.95

  # Custom file pattern
  python batch_predict.py \\
      --statistics-dir statistics/ \\
      --config batch_config.json \\
      --output-dir predictions/ \\
      --model-name qrf_production_crps_optimized \\
      --pattern "01_*.json"
        """
    )
    
    parser.add_argument(
        '--statistics-dir',
        type=str,
        required=True,
        help='Directory containing trip statistics JSON files'
    )
    
    parser.add_argument(
        '--config',
        type=str,
        required=True,
        help='Configuration JSON file with shift parameters'
    )
    
    parser.add_argument(
        '--output-dir',
        type=str,
        required=True,
        help='Directory to save prediction results'
    )
    
    parser.add_argument(
        '--model-name',
        type=str,
        required=True,
        help='Model name (e.g., qrf_production_crps_optimized) or full path'
    )
    
    parser.add_argument(
        '--quantiles',
        type=float,
        nargs='+',
        default=[0.05, 0.25, 0.5, 0.75, 0.95],
        help='Quantiles to predict (default: 0.05 0.25 0.5 0.75 0.95)'
    )
    
    parser.add_argument(
        '--pattern',
        type=str,
        default='*_statistics.json',
        help='Glob pattern to match statistics files (default: *_statistics.json)'
    )
    
    args = parser.parse_args()
    
    # Run batch processing
    batch_predict(
        statistics_dir=args.statistics_dir,
        config_file=args.config,
        output_dir=args.output_dir,
        model_name=args.model_name,
        quantiles=args.quantiles,
        pattern=args.pattern
    )


if __name__ == "__main__":
    main()

