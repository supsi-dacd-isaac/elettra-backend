#!/usr/bin/env python3
"""
Generate batch configuration file from bus models and shift assignments

This script reads:
1. Bus model specifications (bus_models.json)
2. Optional shift-to-bus mapping

And generates a batch configuration file for consumption predictions.
"""

import json
import argparse
from pathlib import Path


def load_bus_models(models_file: str = "bus_models.json") -> dict:
    """Load bus model specifications"""
    with open(models_file, 'r') as f:
        return json.load(f)


def generate_config_from_bus_ids(
    bus_models_data: dict,
    bus_ids: list,
    external_temp: float = 20.0,
    default_model: str = "AA_NF"
) -> dict:
    """Generate config where shifts are identified by bus ID (e.g., '401', '402')
    
    Args:
        bus_models_data: Dict containing bus_models and bus_assignments
        bus_ids: List of bus IDs to include (or 'all' for all assigned buses)
        external_temp: External temperature in Celsius
        default_model: Default bus model if not specified
    """
    bus_models = bus_models_data['bus_models']
    bus_assignments = bus_models_data.get('bus_assignments', {})
    
    # Get default model specs
    default_model_spec = bus_models.get(default_model, list(bus_models.values())[0])
    
    config = {
        "default": {
            "bus_length_m": default_model_spec['bus_length_m'],
            "battery_capacity_kwh": default_model_spec['battery_capacity_kwh'],
            "external_temp_celsius": external_temp
        },
        "shifts": {}
    }
    
    # Determine which bus IDs to include
    if bus_ids == ['all']:
        bus_ids = list(bus_assignments.keys())
    
    # Generate shift entries
    for bus_id in bus_ids:
        if bus_id not in bus_assignments:
            print(f"Warning: Bus ID {bus_id} not found in assignments, using default model")
            model = default_model
        else:
            model = bus_assignments[bus_id]
        
        if model not in bus_models:
            print(f"Warning: Model {model} not found, using default")
            model_spec = default_model_spec
        else:
            model_spec = bus_models[model]
        
        # Add shift configuration
        # Support both bus ID format (e.g., "401") and shift ID format (e.g., "01_40401")
        config["shifts"][bus_id] = {
            "bus_length_m": model_spec['bus_length_m'],
            "battery_capacity_kwh": model_spec['battery_capacity_kwh'],
            "external_temp_celsius": external_temp,
            "bus_model": model
        }
    
    return config


def generate_config_from_shift_mapping(
    bus_models_data: dict,
    shift_mapping_file: str,
    external_temp: float = 20.0
) -> dict:
    """Generate config from a CSV mapping file: shift_id,bus_id
    
    Example CSV content:
        shift_id,bus_id
        01_40102,401
        02_40103,402
        03_40104,403
    """
    import csv
    
    bus_models = bus_models_data['bus_models']
    bus_assignments = bus_models_data.get('bus_assignments', {})
    
    # Read shift mapping
    shift_to_bus = {}
    with open(shift_mapping_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            shift_to_bus[row['shift_id']] = row['bus_id']
    
    # Get default model
    default_model = list(bus_models.values())[0]
    
    config = {
        "default": {
            "bus_length_m": default_model['bus_length_m'],
            "battery_capacity_kwh": default_model['battery_capacity_kwh'],
            "external_temp_celsius": external_temp
        },
        "shifts": {}
    }
    
    # Generate shift entries
    for shift_id, bus_id in shift_to_bus.items():
        model_name = bus_assignments.get(bus_id, list(bus_models.keys())[0])
        model_spec = bus_models[model_name]
        
        config["shifts"][shift_id] = {
            "bus_length_m": model_spec['bus_length_m'],
            "battery_capacity_kwh": model_spec['battery_capacity_kwh'],
            "external_temp_celsius": external_temp,
            "bus_model": model_name,
            "bus_id": bus_id
        }
    
    return config


def main():
    parser = argparse.ArgumentParser(
        description="Generate batch configuration file from bus models",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:

  # Generate config for specific bus IDs
  python generate_batch_config.py \\
      --bus-ids 401 402 403 \\
      --output batch_config.json

  # Generate config for all assigned buses
  python generate_batch_config.py \\
      --bus-ids all \\
      --output batch_config_all.json

  # Generate from shift mapping CSV
  python generate_batch_config.py \\
      --shift-mapping shift_to_bus.csv \\
      --output batch_config.json

  # Custom temperature
  python generate_batch_config.py \\
      --bus-ids 401 402 403 \\
      --external-temp 15 \\
      --output batch_config_winter.json
        """
    )
    
    parser.add_argument(
        '--bus-models',
        type=str,
        default='bus_models.json',
        help='Path to bus models JSON file (default: bus_models.json)'
    )
    
    parser.add_argument(
        '--bus-ids',
        type=str,
        nargs='+',
        help='List of bus IDs to include, or "all" for all assigned buses'
    )
    
    parser.add_argument(
        '--shift-mapping',
        type=str,
        help='CSV file mapping shift_id to bus_id (alternative to --bus-ids)'
    )
    
    parser.add_argument(
        '--external-temp',
        type=float,
        default=20.0,
        help='External temperature in Celsius (default: 20.0)'
    )
    
    parser.add_argument(
        '--default-model',
        type=str,
        default='AA_NF',
        help='Default bus model to use (default: AA_NF)'
    )
    
    parser.add_argument(
        '--output',
        type=str,
        required=True,
        help='Output JSON configuration file'
    )
    
    args = parser.parse_args()
    
    # Validate inputs
    if not args.bus_ids and not args.shift_mapping:
        parser.error("Must specify either --bus-ids or --shift-mapping")
    
    if args.bus_ids and args.shift_mapping:
        parser.error("Cannot specify both --bus-ids and --shift-mapping")
    
    # Load bus models
    print(f"Loading bus models from: {args.bus_models}")
    bus_models_data = load_bus_models(args.bus_models)
    
    print(f"Found {len(bus_models_data['bus_models'])} bus models:")
    for model, spec in bus_models_data['bus_models'].items():
        print(f"  {model}: {spec['bus_length_m']}m, {spec['battery_capacity_kwh']}kWh")
    
    # Generate configuration
    if args.shift_mapping:
        print(f"\nGenerating config from shift mapping: {args.shift_mapping}")
        config = generate_config_from_shift_mapping(
            bus_models_data,
            args.shift_mapping,
            args.external_temp
        )
    else:
        print(f"\nGenerating config for bus IDs: {args.bus_ids}")
        config = generate_config_from_bus_ids(
            bus_models_data,
            args.bus_ids,
            args.external_temp,
            args.default_model
        )
    
    # Save configuration
    with open(args.output, 'w') as f:
        json.dump(config, f, indent=2)
    
    print(f"\n✓ Configuration saved to: {args.output}")
    print(f"  Default model: {config['default']}")
    print(f"  Shift-specific configs: {len(config['shifts'])}")


if __name__ == "__main__":
    main()

