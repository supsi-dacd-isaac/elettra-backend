#!/usr/bin/env python3
"""
Optimize bus missions (turni macchina) by reducing dwell time at Lugano Centro.

This script:
- Reads JSON files containing bus shift data
- For trips starting at "Lugano, Centro" (non-depot), shifts departure time
  to match the previous trip's arrival time (eliminating dwell time)
- Keeps travel duration the same, so arrival time shifts accordingly
- Trips to/from depot and trips starting at other stops are unchanged
- Saves optimized files to a new folder with "_optimized" suffix
"""

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def parse_time(time_str: str) -> timedelta:
    """Parse a time string (HH:MM:SS) into a timedelta from midnight."""
    parts = time_str.split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = int(parts[2])
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def format_time(td: timedelta) -> str:
    """Format a timedelta as HH:MM:SS string."""
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def optimize_shift(trips: list[dict]) -> tuple[list[dict], dict]:
    """
    Optimize a single bus shift by reducing dwell time at Lugano Centro.
    
    Args:
        trips: List of trip dictionaries for one bus shift
        
    Returns:
        Tuple of (optimized trips list, statistics dict)
    """
    optimized = []
    stats = {
        "total_trips": len(trips),
        "depot_trips": 0,
        "modified_trips": 0,
        "unchanged_trips": 0,
        "total_time_saved_seconds": 0,
    }
    
    for i, trip in enumerate(trips):
        new_trip = trip.copy()
        
        # Skip depot trips
        if trip.get("status") == "depot":
            stats["depot_trips"] += 1
            optimized.append(new_trip)
            continue
        
        # Only modify trips starting at "Lugano, Centro"
        if trip.get("start_stop_name") != "Lugano, Centro":
            stats["unchanged_trips"] += 1
            optimized.append(new_trip)
            continue
        
        # Need a previous trip to determine new departure time
        if i == 0:
            stats["unchanged_trips"] += 1
            optimized.append(new_trip)
            continue
        
        prev_trip = optimized[i - 1]  # Use the (possibly modified) previous trip
        prev_arrival_time = parse_time(prev_trip["arrival_time"])
        
        current_departure = parse_time(trip["departure_time"])
        current_arrival = parse_time(trip["arrival_time"])
        
        # Calculate travel duration (this stays the same)
        travel_duration = current_arrival - current_departure
        
        # New departure = previous trip's arrival
        new_departure = prev_arrival_time
        new_arrival = new_departure + travel_duration
        
        # Calculate time saved
        time_saved = current_departure - new_departure
        
        if time_saved.total_seconds() > 0:
            new_trip["departure_time"] = format_time(new_departure)
            new_trip["arrival_time"] = format_time(new_arrival)
            stats["modified_trips"] += 1
            stats["total_time_saved_seconds"] += int(time_saved.total_seconds())
        else:
            stats["unchanged_trips"] += 1
        
        optimized.append(new_trip)
    
    return optimized, stats


def process_folder(input_folder: Path, dry_run: bool = False) -> dict:
    """
    Process all JSON files in a folder and its subfolders.
    
    Args:
        input_folder: Path to the input folder
        dry_run: If True, don't write files, just report what would be done
        
    Returns:
        Statistics dictionary
    """
    overall_stats = {
        "folders_processed": 0,
        "files_processed": 0,
        "total_trips": 0,
        "depot_trips": 0,
        "modified_trips": 0,
        "unchanged_trips": 0,
        "total_time_saved_seconds": 0,
    }
    
    # Find all subfolders with JSON files
    for subfolder in sorted(input_folder.iterdir()):
        if not subfolder.is_dir():
            continue
        
        # Skip already optimized folders
        if subfolder.name.endswith("_optimized"):
            continue
        
        # Only process folders ending with _json
        if not subfolder.name.endswith("_json"):
            continue
        
        # Create output folder
        output_folder = input_folder / f"{subfolder.name}_optimized"
        
        if not dry_run:
            output_folder.mkdir(exist_ok=True)
        
        print(f"\nProcessing: {subfolder.name}")
        print(f"  Output: {output_folder.name}")
        
        json_files = sorted(subfolder.glob("*.json"))
        folder_stats = {
            "files": 0,
            "modified_trips": 0,
            "time_saved_seconds": 0,
        }
        
        for json_file in json_files:
            with open(json_file, "r", encoding="utf-8") as f:
                trips = json.load(f)
            
            optimized_trips, file_stats = optimize_shift(trips)
            
            # Update statistics
            folder_stats["files"] += 1
            folder_stats["modified_trips"] += file_stats["modified_trips"]
            folder_stats["time_saved_seconds"] += file_stats["total_time_saved_seconds"]
            
            overall_stats["files_processed"] += 1
            overall_stats["total_trips"] += file_stats["total_trips"]
            overall_stats["depot_trips"] += file_stats["depot_trips"]
            overall_stats["modified_trips"] += file_stats["modified_trips"]
            overall_stats["unchanged_trips"] += file_stats["unchanged_trips"]
            overall_stats["total_time_saved_seconds"] += file_stats["total_time_saved_seconds"]
            
            # Write output file
            if not dry_run:
                output_file = output_folder / json_file.name
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(optimized_trips, f, indent=2, ensure_ascii=False)
        
        overall_stats["folders_processed"] += 1
        
        time_saved_min = folder_stats["time_saved_seconds"] / 60
        print(f"  Files: {folder_stats['files']}, "
              f"Modified trips: {folder_stats['modified_trips']}, "
              f"Time saved: {time_saved_min:.1f} min")
    
    return overall_stats


def main():
    parser = argparse.ArgumentParser(
        description="Optimize bus missions by reducing dwell time at Lugano Centro"
    )
    parser.add_argument(
        "input_folder",
        type=Path,
        help="Path to the folder containing turni macchina subfolders"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write files, just show what would be done"
    )
    
    args = parser.parse_args()
    
    if not args.input_folder.exists():
        print(f"Error: Input folder does not exist: {args.input_folder}")
        return 1
    
    print(f"{'DRY RUN - ' if args.dry_run else ''}Optimizing turni macchina")
    print(f"Input folder: {args.input_folder}")
    print("=" * 60)
    
    stats = process_folder(args.input_folder, dry_run=args.dry_run)
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Folders processed: {stats['folders_processed']}")
    print(f"Files processed: {stats['files_processed']}")
    print(f"Total trips: {stats['total_trips']}")
    print(f"  - Depot trips (unchanged): {stats['depot_trips']}")
    print(f"  - Modified trips: {stats['modified_trips']}")
    print(f"  - Other unchanged trips: {stats['unchanged_trips']}")
    
    time_saved_min = stats["total_time_saved_seconds"] / 60
    time_saved_hours = time_saved_min / 60
    print(f"Total dwell time saved: {time_saved_min:.1f} minutes ({time_saved_hours:.2f} hours)")
    
    if args.dry_run:
        print("\nThis was a dry run. No files were written.")
    
    return 0


if __name__ == "__main__":
    exit(main())




