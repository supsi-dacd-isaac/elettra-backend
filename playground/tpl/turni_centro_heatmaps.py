import argparse
import json
import math
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]  # Go up 2 levels: tpl -> playground -> project_root


def parse_gtfs_hms_to_seconds(time_str: Optional[str]) -> Optional[int]:
    if not time_str:
        return None
    parts = time_str.split(":")
    if len(parts) != 3:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2])
        if minutes < 0 or minutes > 59 or seconds < 0 or seconds > 59:
            return None
        return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        return None


def seconds_to_hms(total_seconds: int) -> str:
    hours = total_seconds // 3600
    rem = total_seconds % 3600
    minutes = rem // 60
    seconds = rem % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def minute_bucket(seconds: Optional[int]) -> Optional[int]:
    if seconds is None:
        return None
    return seconds // 60


def natural_sort_key(value: Optional[str]):
    s = value or ""
    tokens = re.findall(r"\d+|\D+", s)
    key = []
    for tok in tokens:
        if tok.isdigit():
            key.append((0, int(tok)))
        else:
            key.append((1, tok.casefold()))
    return key


def extract_turno_id(filename: str) -> str:
    """Extract turno ID from filename, removing part suffixes."""
    # Remove .json extension
    name = filename.replace('.json', '')
    
    # Remove part suffixes like __part1, __part2
    if '__part' in name:
        name = name.split('__part')[0]
    
    # Extract the turno ID (e.g., 00_40101 -> 40101)
    if '_' in name:
        parts = name.split('_')
        if len(parts) >= 2:
            return parts[1]  # Return the second part (40101)
    
    return name


def build_dwell_rows_for_turno(records: List[Dict], target_stop: str) -> List[Dict]:
    events: List[Tuple[str, int]] = []  # (kind, seconds)

    for rec in records:
        # Process all trips regardless of status
        dep_time = rec.get("departure_time")
        arr_time = rec.get("arrival_time")

        if (rec.get("start_stop_name") or "") == target_stop and dep_time:
            dep_s = parse_gtfs_hms_to_seconds(dep_time)
            if dep_s is not None:
                events.append(("dep", dep_s))

        if (rec.get("end_stop_name") or "") == target_stop and arr_time:
            arr_s = parse_gtfs_hms_to_seconds(arr_time)
            if arr_s is not None:
                events.append(("arr", arr_s))

    if not events:
        return []

    events.sort(key=lambda x: x[1])

    rows: List[Dict] = []
    current_start: Optional[int] = None

    for kind, ts in events:
        if kind == "arr":
            if current_start is None:
                current_start = ts
            else:
                current_start = min(current_start, ts)
        else:  # dep
            if current_start is not None:
                rows.append(
                    {
                        "arrival_time": seconds_to_hms(current_start),
                        "departure_time": seconds_to_hms(ts),
                    }
                )
                current_start = None

    return rows


def build_combined_dataframe(all_turno_data: Dict[str, List[Dict]]) -> pd.DataFrame:
    """Build a single DataFrame with all turni macchina."""
    if not all_turno_data:
        return pd.DataFrame()
    
    # Get all turno IDs and determine time bounds
    turno_ids = sorted(all_turno_data.keys(), key=natural_sort_key)
    min_minute = math.inf
    max_minute = -math.inf
    
    # First pass: determine time bounds
    for turno_id, dwell_rows in all_turno_data.items():
        if not dwell_rows:
            continue
        for rec in dwell_rows:
            arr_s = parse_gtfs_hms_to_seconds(rec['arrival_time'])
            dep_s = parse_gtfs_hms_to_seconds(rec['departure_time'])
            
            start_min = minute_bucket(arr_s) if arr_s is not None else minute_bucket(dep_s)
            end_min = minute_bucket(dep_s) if dep_s is not None else minute_bucket(arr_s)
            
            if start_min is None and end_min is None:
                continue
            if start_min is None:
                start_min = end_min
            if end_min is None:
                end_min = start_min
            if end_min < start_min:
                start_min, end_min = end_min, start_min
            
            min_minute = min(min_minute, start_min)
            max_minute = max(max_minute, end_min)
    
    if min_minute == math.inf:
        return pd.DataFrame()
    
    # Create DataFrame with turni as columns
    num_minutes = max_minute + 1
    df = pd.DataFrame(0, index=pd.RangeIndex(0, num_minutes, name='minute'), columns=turno_ids, dtype='int32')
    
    # Second pass: fill the DataFrame
    for turno_id, dwell_rows in all_turno_data.items():
        if not dwell_rows:
            continue
        for rec in dwell_rows:
            arr_s = parse_gtfs_hms_to_seconds(rec['arrival_time'])
            dep_s = parse_gtfs_hms_to_seconds(rec['departure_time'])
            
            start_min = minute_bucket(arr_s) if arr_s is not None else minute_bucket(dep_s)
            end_min = minute_bucket(dep_s) if dep_s is not None else minute_bucket(arr_s)
            
            if start_min is None and end_min is None:
                continue
            if start_min is None:
                start_min = end_min
            if end_min is None:
                end_min = start_min
            if end_min < start_min:
                start_min, end_min = end_min, start_min
            
            # Set to 1 for occupied minutes
            df.loc[start_min:end_min, turno_id] = 1
    
    return df


def plot_combined_heatmap(df: pd.DataFrame, title: str, output_png: Path, save_pdf: bool = True) -> Optional[Path]:
    if df.empty:
        return None

    import matplotlib.pyplot as plt  # noqa: E402

    num_minutes = df.shape[0]
    num_turni = df.shape[1]
    height = max(10.0, min(16.0, num_minutes / 80.0))  # Adjust height for time
    width = max(12.0, min(24.0, num_turni * 0.4))  # Adjust width for number of turni

    # Create two subplots: left heatmap, right totals line; share Y axis (minutes)
    fig, (ax_hm, ax_line) = plt.subplots(
        ncols=2,
        figsize=(width + 4, height),
        gridspec_kw={'width_ratios': [max(1, num_turni / 5), 1]},
        sharey=True,
    )
    
    # Adjust subplot spacing to make room for labels
    plt.subplots_adjust(left=0.15, right=0.97, top=0.95, bottom=0.08)

    # Use custom colormap: white for 0, specific blue for 1
    from matplotlib.colors import ListedColormap
    colors = ['white', '#1f77b4']  # Same blue as matplotlib default
    cmap = ListedColormap(colors)

    # Plot without extent so that pixel rows align exactly with minute indices (0..num_minutes-1)
    im = ax_hm.imshow(df.values, aspect='auto', origin='upper', cmap=cmap, interpolation='nearest')
    
    ax_hm.set_title(title)
    ax_hm.set_xlabel('Turni Macchina')
    ax_hm.set_ylabel('Tempo')

    # X ticks: show all turni
    turni = list(df.columns)
    ax_hm.set_xticks(list(range(len(turni))))
    ax_hm.set_xticklabels(turni, rotation=90, fontsize=8)

    # Y ticks: every 1 hour with HH:MM format (can exceed 24 hours)
    max_minute = int(df.index.max())
    hour_ticks = list(range(0, max_minute + 1, 60))
    hour_labels = [f"{m // 60:02d}:{m % 60:02d}" for m in hour_ticks]

    ax_hm.set_yticks(hour_ticks)
    ax_hm.set_yticklabels(hour_labels)
    # Ensure last row (minute == max_minute) is fully visible
    ax_hm.set_ylim(max_minute + 0.5, -0.5)

    # Right subplot: total buses per minute (sum across turni)
    totals = df.sum(axis=1)
    ax_line.plot(totals.values, df.index.values, color='#1f77b4', linewidth=1.2)
    ax_line.set_xlabel('Totale Bus')
    ax_line.grid(True, axis='x', linestyle=':', alpha=0.5)
    # Align y ticks with left plot; hide duplicate labels on right axis only
    ax_line.set_yticks(hour_ticks)
    ax_line.tick_params(axis='y', labelleft=False)
    # Match y-limits to left axis (origin at top)
    ax_line.set_ylim(ax_hm.get_ylim())

    fig.tight_layout()

    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, dpi=200)
    if save_pdf:
        output_pdf = output_png.with_suffix(".pdf")
        fig.savefig(output_pdf)
    plt.close(fig)
    return output_png


def process_all_files(json_dir: Path, target_stop: str, out_dir: Path, save_pdf: bool = True) -> Optional[Path]:
    """Process all JSON files and create a single combined heatmap."""
    
    # Group files by turno ID (combining part1, part2, etc.)
    turno_groups: Dict[str, List[Path]] = {}
    
    for json_file in sorted(json_dir.glob("*.json")):
        turno_id = extract_turno_id(json_file.name)
        if turno_id not in turno_groups:
            turno_groups[turno_id] = []
        turno_groups[turno_id].append(json_file)
    
    print(f"Found {len(turno_groups)} unique turni macchina")
    
    # Process each turno group
    all_turno_data: Dict[str, List[Dict]] = {}
    
    for turno_id, files in turno_groups.items():
        print(f"Processing turno {turno_id} ({len(files)} files)")
        
        all_dwell_rows = []
        for json_file in files:
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    dwell_rows = build_dwell_rows_for_turno(data, target_stop)
                    all_dwell_rows.extend(dwell_rows)
            except Exception as e:
                print(f"Error processing {json_file}: {e}")
                continue
        
        if all_dwell_rows:
            all_turno_data[turno_id] = all_dwell_rows
            print(f"  -> {len(all_dwell_rows)} dwell periods")
        else:
            print(f"  -> No data")
    
    if not all_turno_data:
        print("No data found for any turno")
        return None
    
    # Build combined DataFrame
    df = build_combined_dataframe(all_turno_data)
    print(f"Combined DataFrame shape: {df.shape}")

    # Save CSV with one column per turno and a 'total_buses' column
    try:
        csv_path = out_dir / "turni_macchina_combined_minutes.csv"
        out_dir.mkdir(parents=True, exist_ok=True)
        df_with_total = df.copy()
        df_with_total["total_buses"] = df_with_total.sum(axis=1)
        df_with_total.to_csv(csv_path, index_label="minute")
        print(f"Saved CSV: {csv_path}")
    except Exception as e:
        print(f"Failed to save CSV: {e}")
    
    # Create heatmap
    title = f"{target_stop} — Tutti i Turni Macchina"
    png_path = out_dir / "turni_macchina_combined_heatmap.png"
    return plot_combined_heatmap(df, title, png_path, save_pdf=save_pdf)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate combined Lugano, Centro heatmap from all turno JSON files")
    parser.add_argument(
        "--dir",
        type=str,
        default=str(
            PROJECT_ROOT
            / "playground"
            / "tpl"
            / "turni_macchina_2026"
            / "2026-TM_15f_lu-ve_TM_json"
        ),
        help="Directory containing turno JSON files",
    )
    parser.add_argument("--stop", type=str, default="Lugano, Centro", help="Target stop name")
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(PROJECT_ROOT / "playground" / "tpl" / "turni_centro_heatmaps"),
        help="Output directory for heatmap",
    )
    parser.add_argument("--no-pdf", action="store_true", help="Do not save PDF alongside PNG")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    json_dir = Path(args.dir)
    out_dir = Path(args.output_dir)
    save_pdf = not args.no_pdf

    if not json_dir.exists():
        print(f"Directory not found: {json_dir}")
        return

    result = process_all_files(json_dir, args.stop, out_dir, save_pdf=save_pdf)
    if result:
        print(f"Saved combined heatmap: {result}")
    else:
        print("No data found to create heatmap")


if __name__ == "__main__":
    main()