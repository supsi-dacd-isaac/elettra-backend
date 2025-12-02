#!/usr/bin/env python3
"""
Generate stringline plots (time-space diagrams) for bus shifts.

Creates visual diagrams showing:
- X-axis: Time of day
- Y-axis: Stops/locations
- Lines: Bus movements between stops

Overlays original and optimized schedules for comparison.
"""

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Patch
from matplotlib.lines import Line2D


def parse_time(time_str: str) -> datetime:
    """Parse a time string (HH:MM:SS) into a datetime object."""
    parts = time_str.split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = int(parts[2])
    # Use a fixed date for plotting
    return datetime(2026, 1, 1, hours % 24, minutes, seconds)


def get_stop_order(trips: list[dict], trips2: list[dict] = None) -> list[str]:
    """
    Determine stop order for Y-axis based on trip sequence.
    Places Lugano, Centro in the middle with other stops above/below.
    """
    all_trips = trips + (trips2 or [])
    stops = []
    for trip in all_trips:
        if trip["start_stop_name"] not in stops:
            stops.append(trip["start_stop_name"])
        if trip["end_stop_name"] not in stops:
            stops.append(trip["end_stop_name"])
    
    # Separate stops: Lugano Centro in middle, depot stops at edges, others sorted
    centro = "Lugano, Centro"
    depot_stops = [s for s in stops if "Rimessa" in s or "TPL" in s]
    other_stops = [s for s in stops if s != centro and s not in depot_stops]
    
    # Sort other stops alphabetically for consistent ordering
    other_stops.sort()
    
    # Order: depot stops, other stops (half), Lugano Centro, other stops (half), depot stops
    mid = len(other_stops) // 2
    ordered = depot_stops + other_stops[:mid] + [centro] + other_stops[mid:] + depot_stops
    
    # Remove duplicates while preserving order
    seen = set()
    result = []
    for s in ordered:
        if s not in seen:
            seen.add(s)
            result.append(s)
    
    return result


def generate_comparison_plot(
    original_trips: list[dict],
    optimized_trips: list[dict],
    output_path: Path,
    title: str = "Bus Shift Comparison"
):
    """Generate a stringline plot comparing original and optimized schedules."""
    
    if not original_trips or not optimized_trips:
        return
    
    # Get stop ordering (from both to ensure all stops are included)
    stop_order = get_stop_order(original_trips, optimized_trips)
    stop_to_y = {stop: i for i, stop in enumerate(stop_order)}
    
    # Create figure
    fig, ax = plt.subplots(figsize=(16, 9))
    
    # Color scheme
    colors = {
        # Original - muted/lighter colors with dashed lines
        "orig_depot": "#AAAAAA",
        "orig_regular": "#7FB3D5",      # Light blue
        "orig_lugano": "#F5B041",        # Orange
        # Optimized - bold colors with solid lines
        "opt_depot": "#666666",
        "opt_regular": "#1A5276",        # Dark blue
        "opt_lugano": "#C0392B",         # Dark red
    }
    
    # Plot original trips (dashed lines, behind)
    for trip in original_trips:
        start_time = parse_time(trip["departure_time"])
        end_time = parse_time(trip["arrival_time"])
        
        start_y = stop_to_y.get(trip["start_stop_name"], 0)
        end_y = stop_to_y.get(trip["end_stop_name"], 0)
        
        if trip.get("status") == "depot":
            color = colors["orig_depot"]
            linewidth = 1.5
        elif trip.get("start_stop_name") == "Lugano, Centro":
            color = colors["orig_lugano"]
            linewidth = 2.5
        else:
            color = colors["orig_regular"]
            linewidth = 2
        
        ax.plot(
            [start_time, end_time],
            [start_y, end_y],
            color=color,
            linewidth=linewidth,
            linestyle='--',
            alpha=0.7,
            zorder=1
        )
        
        # Add hollow dots at start and end
        ax.scatter([start_time], [start_y], color=color, s=30, zorder=2, 
                   facecolors='none', edgecolors=color, linewidths=1.5)
        ax.scatter([end_time], [end_y], color=color, s=30, zorder=2,
                   facecolors='none', edgecolors=color, linewidths=1.5)
    
    # Plot optimized trips (solid lines, on top)
    for trip in optimized_trips:
        start_time = parse_time(trip["departure_time"])
        end_time = parse_time(trip["arrival_time"])
        
        start_y = stop_to_y.get(trip["start_stop_name"], 0)
        end_y = stop_to_y.get(trip["end_stop_name"], 0)
        
        if trip.get("status") == "depot":
            color = colors["opt_depot"]
            linewidth = 1.5
        elif trip.get("start_stop_name") == "Lugano, Centro":
            color = colors["opt_lugano"]
            linewidth = 2.5
        else:
            color = colors["opt_regular"]
            linewidth = 2
        
        ax.plot(
            [start_time, end_time],
            [start_y, end_y],
            color=color,
            linewidth=linewidth,
            linestyle='-',
            alpha=0.9,
            solid_capstyle='round',
            zorder=3
        )
        
        # Add filled dots at start and end
        ax.scatter([start_time], [start_y], color=color, s=25, zorder=4)
        ax.scatter([end_time], [end_y], color=color, s=25, zorder=4)
    
    # Configure axes
    ax.set_yticks(range(len(stop_order)))
    ax.set_yticklabels(stop_order, fontsize=9)
    
    # Format x-axis as time
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=1))
    ax.xaxis.set_minor_locator(mdates.MinuteLocator(byminute=[15, 30, 45]))
    
    # Grid
    ax.grid(True, alpha=0.3, which='major')
    ax.grid(True, alpha=0.15, which='minor')
    
    # Labels
    ax.set_xlabel('Time', fontsize=11)
    ax.set_ylabel('Stop', fontsize=11)
    ax.set_title(title, fontsize=13, fontweight='bold')
    
    # Legend
    legend_elements = [
        Line2D([0], [0], color=colors["orig_lugano"], linewidth=2.5, linestyle='--',
               label='Original: from Lugano Centro'),
        Line2D([0], [0], color=colors["orig_regular"], linewidth=2, linestyle='--',
               label='Original: other trips'),
        Line2D([0], [0], color=colors["orig_depot"], linewidth=1.5, linestyle='--',
               label='Original: depot'),
        Line2D([0], [0], color=colors["opt_lugano"], linewidth=2.5, linestyle='-',
               label='Optimized: from Lugano Centro'),
        Line2D([0], [0], color=colors["opt_regular"], linewidth=2, linestyle='-',
               label='Optimized: other trips'),
        Line2D([0], [0], color=colors["opt_depot"], linewidth=1.5, linestyle='-',
               label='Optimized: depot'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=8, ncol=2)
    
    # Adjust layout
    plt.tight_layout()
    
    # Save
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()


def process_folder(input_folder: Path):
    """Process all optimized folders and generate comparison stringline plots."""
    
    for subfolder in sorted(input_folder.iterdir()):
        if not subfolder.is_dir():
            continue
        
        # Only process optimized folders
        if not subfolder.name.endswith("_optimized"):
            continue
        
        # Find corresponding original folder
        original_folder_name = subfolder.name.replace("_optimized", "")
        original_folder = input_folder / original_folder_name
        
        if not original_folder.exists():
            print(f"Warning: Original folder not found for {subfolder.name}")
            continue
        
        print(f"\nGenerating comparison plots for: {subfolder.name}")
        
        json_files = sorted(subfolder.glob("*.json"))
        
        for json_file in json_files:
            # Load optimized trips
            with open(json_file, "r", encoding="utf-8") as f:
                optimized_trips = json.load(f)
            
            # Load original trips
            original_file = original_folder / json_file.name
            if not original_file.exists():
                print(f"  Warning: Original file not found: {json_file.name}")
                continue
            
            with open(original_file, "r", encoding="utf-8") as f:
                original_trips = json.load(f)
            
            # Generate output filename
            output_path = subfolder / f"{json_file.stem}_stringline.png"
            
            # Create title from filename
            title = f"Bus Shift: {json_file.stem} — Original vs Optimized"
            
            generate_comparison_plot(original_trips, optimized_trips, output_path, title)
            print(f"  Created: {output_path.name}")
    
    print("\nDone!")


def main():
    parser = argparse.ArgumentParser(
        description="Generate stringline plots for bus shifts"
    )
    parser.add_argument(
        "input_folder",
        type=Path,
        help="Path to the folder containing turni macchina subfolders"
    )
    
    args = parser.parse_args()
    
    if not args.input_folder.exists():
        print(f"Error: Input folder does not exist: {args.input_folder}")
        return 1
    
    process_folder(args.input_folder)
    return 0


if __name__ == "__main__":
    exit(main())

