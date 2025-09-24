#!/usr/bin/env python3
"""
Stringline (time–distance) diagram generator for bus operations.

This script:
- Loads credentials from tests/test.env
- Logs into the backend to obtain a JWT token
- Retrieves the current user id via /auth/me
- Fetches shifts via GET /api/v1/user/shifts/?user_id=...
- For each shift, fetches stops (with times and coords) for each trip in order
- Renders a time–stops stringline diagram: X=time, Y=stops equally spaced

Usage:
  python playground/stringline_diagram.py \
    --base-url http://localhost:8002 \
    --limit 5 \
    --output playground/stringline.png

Requirements:
  - requests
  - python-dotenv
  - matplotlib

Notes:
  - The script assumes services are running locally on port 8002 unless overridden.
  - Times may exceed 24:00:00 (GTFS extended). These are parsed accordingly.
"""

from __future__ import annotations

import argparse
import os
import sys
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from dotenv import load_dotenv
import matplotlib.pyplot as plt


def load_env(env_file: str) -> None:
    if os.path.exists(env_file):
        load_dotenv(env_file, override=True)


def env_or_raise(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate stringline diagram from API shifts")
    ap.add_argument("--base-url", default=os.getenv("ELETTRA_BASE_URL", "http://localhost:8002"), help="Backend base URL (default: http://localhost:8002)")
    ap.add_argument("--env-file", default="tests/test.env", help="Path to env file with TEST_LOGIN_* (default: tests/test.env)")
    ap.add_argument("--limit", type=int, default=5, help="Max number of shifts to include (default: 5)")
    ap.add_argument("--output", default="playground/stringline.png", help="Output PNG path")
    ap.add_argument("--user-id", default=None, help="Optional explicit user UUID; if not set, uses /auth/me")
    ap.add_argument("--reverse-y", action="store_true", help="Reverse stop order top-to-bottom")
    return ap.parse_args()


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def login(base_url: str, email: str, password: str) -> str:
    url = join_url(base_url, "/auth/login")
    r = requests.post(url, json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    js = r.json()
    tok = js.get("access_token")
    if not tok:
        raise RuntimeError("Login response missing access_token")
    return tok


def get_me(base_url: str, token: str) -> Dict[str, Any]:
    url = join_url(base_url, "/auth/me")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()


def list_shifts(base_url: str, token: str, user_id: Optional[str], limit: int) -> List[Dict[str, Any]]:
    params = {"skip": 0, "limit": max(limit, 1)}
    if user_id:
        params["user_id"] = user_id
    url = join_url(base_url, "/api/v1/user/shifts/")
    r = requests.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def fetch_stops_by_trip(base_url: str, token: str, trip_id: str) -> List[Dict[str, Any]]:
    url = join_url(base_url, f"/api/v1/gtfs/gtfs-stops/by-trip/{trip_id}")
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    js = r.json()
    return js if isinstance(js, list) else []


def parse_gtfs_time_to_minutes(time_str: Optional[str]) -> Optional[int]:
    if not time_str:
        return None
    # Accept HH:MM or HH:MM:SS, where HH can be >= 24
    parts = time_str.split(":")
    try:
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        seconds = int(parts[2]) if len(parts) > 2 else 0
    except Exception:
        return None
    total_minutes = hours * 60 + minutes + (1 if seconds >= 30 else 0)
    return total_minutes


def build_time_for_trip(stops: List[Dict[str, Any]]) -> List[Tuple[str, int]]:
    """Return list of (stop_id, time_min) for a single trip.
    Uses departure_time when available, else arrival_time. Filters Nones.
    """
    series: List[Tuple[str, int]] = []
    for s in stops:
        stop_pk = str(s.get("id"))
        t = s.get("departure_time") or s.get("arrival_time")
        tm = parse_gtfs_time_to_minutes(t)
        if stop_pk and tm is not None:
            series.append((stop_pk, tm))
    return series


def choose_stop_order(stop_info: Dict[str, Dict[str, Any]], reverse: bool = False) -> List[str]:
    """Choose a global stop order for Y-axis using the dominant spatial axis.
    Sort by longitude if its spread is larger than latitude; otherwise by latitude.
    """
    lats = [v.get("stop_lat") for v in stop_info.values() if v.get("stop_lat") is not None]
    lons = [v.get("stop_lon") for v in stop_info.values() if v.get("stop_lon") is not None]
    use_lon = False
    if lats and lons:
        lat_span = (max(lats) - min(lats)) if lats else 0.0
        lon_span = (max(lons) - min(lons)) if lons else 0.0
        use_lon = lon_span >= lat_span
    # Fallback: stable by name if coords missing
    def sort_key(item: Tuple[str, Dict[str, Any]]):
        sid, v = item
        if use_lon and v.get("stop_lon") is not None:
            return (float(v.get("stop_lon")), float(v.get("stop_lat") or 0.0))
        if v.get("stop_lat") is not None:
            return (float(v.get("stop_lat")), float(v.get("stop_lon") or 0.0))
        return (0.0, v.get("stop_name") or sid)

    ordered = [sid for sid, _ in sorted(stop_info.items(), key=sort_key, reverse=reverse)]
    return ordered


def format_time_label(minutes: int) -> str:
    h = minutes // 60
    m = minutes % 60
    if h < 24:
        return f"{h:02d}:{m:02d}"
    return f"D+{h // 24} {h % 24:02d}:{m:02d}"


def plot_time_vs_stops(
    shifts: List[Dict[str, Any]],
    series_by_shift: Dict[str, List[List[Tuple[str, int]]]],
    stop_info: Dict[str, Dict[str, Any]],
    stop_order: List[str],
    output_path: str,
) -> None:
    plt.figure(figsize=(16, 9))
    ax = plt.gca()
    colors = plt.get_cmap("tab10")

    # Map stop id to y index
    stop_to_y = {sid: idx for idx, sid in enumerate(stop_order)}

    all_times: List[int] = []

    for idx, sh in enumerate(shifts):
        color = colors(idx % 10)
        name = sh.get("name") or sh.get("id", "shift")
        trips = series_by_shift.get(str(sh.get("id")), [])
        for trip_idx, trip_series in enumerate(trips):
            # Convert to aligned series
            xs: List[int] = []
            ys: List[int] = []
            for stop_pk, tmin in trip_series:
                if stop_pk in stop_to_y:
                    xs.append(tmin)
                    ys.append(stop_to_y[stop_pk])
            if len(xs) >= 2:
                all_times.extend(xs)
                ax.plot(xs, ys, color=color, linewidth=2.0, alpha=0.85, label=name if trip_idx == 0 else None)

    # Axes formatting
    if all_times:
        tmin = min(all_times)
        tmax = max(all_times)
        # Expand range a bit
        tmin = (tmin // 60) * 60
        tmax = ((tmax + 59) // 60) * 60
        ax.set_xlim(tmin, tmax)
        # Hourly ticks
        hours = list(range(tmin, tmax + 1, 60))
        ax.set_xticks(hours)
        ax.set_xticklabels([format_time_label(h) for h in hours], rotation=0)

    # Y ticks and labels
    ax.set_yticks(list(range(len(stop_order))))
    ax.set_yticklabels([stop_info[sid].get("stop_name") or sid for sid in stop_order])

    # Gridlines: horizontal for stops, vertical hourly
    ax.grid(axis="x", which="major", linestyle=":", alpha=0.6)
    for y in range(len(stop_order)):
        ax.axhline(y=y, color="#cccccc", linestyle=":", linewidth=0.8, alpha=0.7)

    ax.set_xlabel("Time")
    ax.set_ylabel("Stops")
    ax.set_title("Stringline Diagram (Time vs Stops)")
    ax.legend(loc="upper right", fontsize=8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def main() -> int:
    args = parse_args()
    # Ensure PYTHONPATH includes project root if running from elsewhere
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, project_root)

    load_env(args.env_file)
    email = os.getenv("TEST_LOGIN_EMAIL")
    password = os.getenv("TEST_LOGIN_PASSWORD")
    if not email or not password:
        print("ERROR: TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD not set (see tests/test.env)", file=sys.stderr)
        return 2

    base_url = args.base_url
    try:
        token = login(base_url, email, password)
        user_id = args.user_id
        if not user_id:
            me = get_me(base_url, token)
            user_id = me.get("id")
            if not user_id:
                raise RuntimeError("/auth/me did not return an id")

        shifts = list_shifts(base_url, token, user_id, args.limit)
        if not shifts:
            print("No shifts found for user.")
            return 0

        # For each shift, fetch per-trip time series and collect stop info
        series_by_shift: Dict[str, List[List[Tuple[str, int]]]] = {}
        stop_info: Dict[str, Dict[str, Any]] = {}
        for sh in shifts[: args.limit]:
            sid = str(sh.get("id"))
            struct: List[Dict[str, Any]] = sh.get("structure", [])
            trips_series: List[List[Tuple[str, int]]] = []
            for item in sorted(struct, key=lambda x: x.get("sequence_number", 0)):
                trip_id = item.get("trip_id")
                if not trip_id:
                    continue
                try:
                    stops = fetch_stops_by_trip(base_url, token, trip_id)
                    # collect stop info
                    for s in stops:
                        sid_pk = str(s.get("id"))
                        if sid_pk and sid_pk not in stop_info:
                            stop_info[sid_pk] = {
                                "stop_name": s.get("stop_name"),
                                "stop_lat": s.get("stop_lat"),
                                "stop_lon": s.get("stop_lon"),
                            }
                    series = build_time_for_trip(stops)
                    if len(series) >= 2:
                        trips_series.append(series)
                except Exception as e:
                    print(f"WARN: failed to fetch/process trip {trip_id}: {e}")
                    continue
            series_by_shift[sid] = trips_series

        # Choose global stop order
        stop_order = choose_stop_order(stop_info, reverse=args.reverse_y)

        os.makedirs(os.path.dirname(args.output), exist_ok=True)
        plot_time_vs_stops(shifts[: args.limit], series_by_shift, stop_info, stop_order, args.output)
        print(f"Saved diagram to {args.output}")
        return 0
    except requests.HTTPError as he:
        print(f"HTTP error: {he} | body={getattr(he.response, 'text', '')}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


