#!/usr/bin/env python3
"""
Simple example of using the consumption prediction module

This demonstrates the basic workflow for predicting bus consumption.
"""

import sys
import json
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from simulation.consumption_prediction import ConsumptionPredictor


def main():
    # Example 1: Load and predict from a JSON file
    print("="*70)
    print("EXAMPLE: Consumption Prediction")
    print("="*70)
    
    # Load trip statistics
    json_file = "statistics/01_40102_statistics.json"
    print(f"\n1. Loading trip statistics from: {json_file}")
    
    with open(json_file, 'r') as f:
        trip_data = json.load(f)
    
    print(f"   ✓ Loaded shift {trip_data['shift_id']} with {trip_data['total_trips']} trips")
    
    # Initialize predictor
    print(f"\n2. Initializing predictor...")
    predictor = ConsumptionPredictor(
        bucket_name="consumption-models",
        cache_dir="./model_cache"
    )
    
    # Load model (using production model)
    model_name = "qrf_production_crps_optimized"
    print(f"\n3. Loading model: {model_name}")
    predictor.load_model(model_name)
    
    # Define contextual parameters
    bus_length_m = 18
    battery_capacity_kwh = 450
    external_temp_celsius = 20
    
    # Define custom quantiles for probabilistic predictions
    quantiles = [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95]
    
    print(f"\n4. Making predictions with:")
    print(f"   • Bus length: {bus_length_m} m")
    print(f"   • Battery capacity: {battery_capacity_kwh} kWh")
    print(f"   • External temperature: {external_temp_celsius} °C")
    print(f"   • Quantiles: {quantiles}")
    
    # Make predictions
    results = predictor.predict_from_json(
        json_data=trip_data,
        bus_length_m=bus_length_m,
        battery_capacity_kwh=battery_capacity_kwh,
        external_temp_celsius=external_temp_celsius,
        quantiles=quantiles
    )
    
    # Display results
    print(f"\n{'='*70}")
    print("RESULTS")
    print(f"{'='*70}")
    
    summary = results['summary']
    print(f"\n📊 Point Estimates:")
    print(f"   Total Consumption: {summary['total_consumption_kwh']:.2f} kWh")
    print(f"   Mean per Trip: {summary['mean_consumption_per_trip_kwh']:.2f} kWh")
    print(f"   Total Distance: {summary['total_distance_km']:.2f} km")
    print(f"   Consumption per km: {summary['consumption_per_km_kwh']:.3f} kWh/km")
    
    # Show probabilistic predictions for total shift consumption
    print(f"\n📈 Probabilistic Predictions (Total Shift):")
    if 'quantiles' in summary:
        for q_label, total_q in sorted(summary['quantiles'].items()):
            q_pct = int(q_label[1:])  # Extract percentage from 'q05', 'q25', etc.
            print(f"   Q{q_pct:2d}%: {total_q:7.2f} kWh")
    
    # Show first 3 trips with all quantiles
    print(f"\n📋 First 3 Trip Predictions:")
    for i, pred in enumerate(results['predictions'][:3], 1):
        print(f"\n  {i}. Trip: {pred['trip_id'][:8]}...")
        print(f"     Quantiles:")
        for q in sorted(quantiles):
            q_key = f'quantile_{q:.2f}'
            if q_key in pred:
                print(f"       Q{int(q*100):2d}%: {pred[q_key]:6.2f} kWh")
    
    print(f"\n{'='*70}")
    print("✓ Example completed successfully!")
    print(f"{'='*70}\n")
    
    # Save results
    output_file = "example_predictions.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to: {output_file}")


if __name__ == "__main__":
    main()

