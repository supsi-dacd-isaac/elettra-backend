#!/usr/bin/env python3
"""
Build shift JSON from one CSV or an entire folder of CSVs, using backend API to resolve
trips and depot transfers.

Two modes:
- Single file:  --csv <file.csv> [--output <file.json>]
- Batch folder: --input-dir <folder_with_csvs> [--output-dir <out_folder>]

Common options include agency, route-short-name, base-url, env for credentials, and day-of-week.

Example (batch):
  python playground/build_shift_json_from_csv.py \
    --input-dir /path/to/2026-TM_6f_Sa_TM_csv \
    --output-dir /path/to/2026-TM_6f_Sa_JSON \
    --day-of-week saturday \
    --route-short-name 1

Example (single):
  python playground/build_shift_json_from_csv.py \
    --csv /path/to/00_40101.csv \
    --output /path/to/shift_40101_reconstructed.json \
    --day-of-week saturday
"""

import argparse
import csv
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
import re
import difflib


@dataclass
class TripKey:
    start_name: str
    end_name: str
    dep_time_hm: str  # HH:MM (supports 24:xx)
    arr_time_hm: str  # HH:MM (supports 24:xx)


def read_env_file(env_path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not os.path.exists(env_path):
        return env
    with open(env_path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            env[k] = v.strip("'\"")
    return env


def api_login(base_url: str, email: str, password: str) -> Dict[str, str]:
    url = f"{base_url}/auth/login"
    resp = requests.post(url, json={"email": email, "password": password})
    resp.raise_for_status()
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def get_route_id(base_url: str, headers: Dict[str, str], agency_id: str, route_short_name: str) -> str:
    url = f"{base_url}/api/v1/gtfs/gtfs-routes/by-agency/{agency_id}"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    for r in resp.json():
        if r.get("route_short_name") == route_short_name:
            return r["id"]
    raise RuntimeError(f"Route with short_name={route_short_name} not found for agency {agency_id}")


def fetch_routes_by_agency(base_url: str, headers: Dict[str, str], agency_id: str) -> List[dict]:
    url = f"{base_url}/api/v1/gtfs/gtfs-routes/by-agency/{agency_id}"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    rows = resp.json()
    return rows or []


def fetch_trips_by_route(
    base_url: str,
    headers: Dict[str, str],
    route_id: str,
    day_of_week: str = "saturday",
    status: str = "gtfs",
):
    url = f"{base_url}/api/v1/gtfs/gtfs-trips/by-route/{route_id}"
    params = {"day_of_week": day_of_week, "status": status}
    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()


def _day_group_to_days(day_group: str) -> List[str]:
    group = day_group.strip().lower()
    if group == "mon-fri":
        return ["monday", "tuesday", "wednesday", "thursday", "friday"]
    if group == "sat":
        return ["saturday"]
    if group == "sun":
        return ["sunday"]
    # Fallback: treat unknown as mon-fri for safety
    return ["monday", "tuesday", "wednesday", "thursday", "friday"]


def fetch_trips_by_route_multi_day(
    base_url: str,
    headers: Dict[str, str],
    route_id: str,
    day_group: str,
    status: str = "gtfs",
) -> List[dict]:
    days = _day_group_to_days(day_group)
    aggregated: Dict[str, dict] = {}
    for day in days:
        trips = fetch_trips_by_route(base_url, headers, route_id, day_of_week=day, status=status)
        for t in trips or []:
            tid = t.get("id")
            if tid and tid not in aggregated:
                aggregated[tid] = t
    return list(aggregated.values())


def fetch_stops_by_trip(base_url: str, headers: Dict[str, str], trip_uuid: str):
    url = f"{base_url}/api/v1/gtfs/gtfs-stops/by-trip/{trip_uuid}"
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_stops_for_trip_cached(base_url: str, headers: Dict[str, str], trip_uuid: str, cache: Dict[str, List[dict]]):
    if trip_uuid in cache:
        return cache[trip_uuid]
    stops = fetch_stops_by_trip(base_url, headers, trip_uuid) or []
    cache[trip_uuid] = stops
    return stops


def time_hms_to_hm(t: str) -> str:
    # Expect HH:MM:SS, return HH:MM
    return t[:5]


def adjust_midnight_hm(hm: str) -> str:
    # Convert 00:xx to 24:xx to align with GTFS post-midnight times
    hm = hm.strip()
    return ("24:" + hm[3:]) if hm.startswith("00:") else hm


def normalize_hm(hm: str) -> str:
    """Normalize H:MM or HH:MM to HH:MM without changing special 24:xx values.

    Examples:
      '5:07' -> '05:07'
      '05:07' -> '05:07'
      '24:22' -> '24:22' (unchanged)
    """
    hm = hm.strip()
    # Preserve 24:xx (already normalized for post-midnight)
    if hm.startswith("24:"):
        return hm
    parts = hm.split(":", 1)
    if len(parts) != 2:
        return hm
    h, m = parts[0], parts[1]
    if len(h) == 1:
        h = "0" + h
    return f"{h}:{m}"


def hm_to_minutes(hm: str) -> Optional[int]:
    hm = hm.strip()
    if not re.match(r"^\d{1,2}:\d{2}$", hm):
        return None
    h, m = hm.split(":", 1)
    try:
        return int(h) * 60 + int(m)
    except Exception:
        return None


def minutes_diff(a_hm: str, b_hm: str) -> Optional[int]:
    a = hm_to_minutes(a_hm)
    b = hm_to_minutes(b_hm)
    if a is None or b is None:
        return None
    return abs(a - b)


def infer_route_short_name_from_filename(filename_stem: str) -> Optional[str]:
    """Infer route short name (e.g., '1', '2', '3') from a filename stem like '03_40201'.

    We expect a five-digit code somewhere in the name ending the stem, e.g. '40201'.
    Mapping rule observed in dataset:
      401xx -> route-short-name '1'
      402xx -> route-short-name '2'
      403xx -> route-short-name '3'
    So we extract the 5-digit token and take its 2nd-3rd digits.
    """
    import re

    # Try to match the trailing 5 digits (e.g., 40201) possibly after an underscore
    m = re.search(r"(?:_|)(\d{5})$", filename_stem)
    if not m:
        # Try any 5-digit token in the name
        m = re.search(r"(\d{5})", filename_stem)
    if not m:
        return None
    token = m.group(1)  # e.g., '40201'
    if len(token) != 5 or token[0] != '4':
        return None
    route_two_digits = token[1:3]  # '02'
    try:
        return str(int(route_two_digits))  # '2'
    except ValueError:
        return None


def build_trip_index(
    base_url: str,
    headers: Dict[str, str],
    trips: List[dict],
    stops_cache: Optional[Dict[str, List[dict]]] = None,
):
    index: Dict[Tuple[str, str, str, str], dict] = {}
    stop_name_to_id: Dict[str, str] = {}
    stops_cache = stops_cache if stops_cache is not None else {}
    for t in trips:
        stops = get_stops_for_trip_cached(base_url, headers, t["id"], stops_cache) or []
        if not stops:
            continue
        first = stops[0]
        last = stops[-1]
        key = (
            first["stop_name"],
            last["stop_name"],
            time_hms_to_hm(first["departure_time"]),
            time_hms_to_hm(last["arrival_time"]),
        )
        index[key] = {"trip": t, "stops": (first, last)}
        # Capture IDs for common terminal names
        if first.get("id"):
            stop_name_to_id.setdefault(first["stop_name"], first["id"])
        if last.get("id"):
            stop_name_to_id.setdefault(last["stop_name"], last["id"])
    return index, stop_name_to_id


def find_stop_id_by_name(base_url: str, headers: Dict[str, str], name: str, use_fuzzy: bool = False) -> Optional[str]:
    # Paginate through stops and try to find exact stop_name.
    # If use_fuzzy=True and exact not found, choose the most similar name above a threshold and WARN.
    # If large dataset, increase limit or add server-side filtering endpoint later

    def _normalize(n: str) -> str:
        n = (n or "").lower().strip()
        # Replace separators with spaces, drop other punctuation
        n = n.replace("/", " ").replace("-", " ")
        n = re.sub(r"[^a-z0-9 ]+", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        return n

    target = name
    target_norm = _normalize(name) if use_fuzzy else name

    limit = 1000
    offset = 0

    best: Tuple[float, Optional[str], Optional[str]] = (0.0, None, None)  # (score, id, stop_name)

    while True:
        url = f"{base_url}/api/v1/gtfs/gtfs-stops/"
        params = {"skip": offset, "limit": limit}
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        for s in rows:
            sname = s.get("stop_name") or ""
            if sname == target:
                return s.get("id")
            if use_fuzzy:
                s_norm = _normalize(sname)
                if s_norm == target_norm and sname != target:
                    # Normalized match (non-exact): warn and accept
                    print(f"[WARN] Normalized stop match: '{target}' -> '{sname}'")
                    return s.get("id")
                # Fuzzy similarity
                sim = difflib.SequenceMatcher(None, target_norm, s_norm).ratio()
                if sim > best[0]:
                    best = (sim, s.get("id"), sname)
        offset += limit

    # Accept fuzzy match if very similar
    if use_fuzzy and best[1] is not None and best[0] >= 0.88:
        print(f"[WARN] Fuzzy stop match: '{target}' -> '{best[2]}' (similarity {best[0]:.2f})")
        return best[1]

    return None


def parse_csv_segments(csv_path: str, allowed_terminal_names: Optional[List[str]] = None) -> Tuple[dict, List[dict], dict]:
    # Returns (start_depot, service_segments, end_depot) using names+times (HH:MM)
    with open(csv_path, newline="") as f:
        r = csv.DictReader(f)
        rows = list(r)

    if len(rows) < 4:
        raise RuntimeError("CSV has too few rows")

    # Start depot transfer from first two rows
    start_depot = {
        "from_name": rows[0]["stop"].strip(),
        "from_time": normalize_hm(rows[0]["time"].strip()),
        "to_name": rows[1]["stop"].strip(),
        "to_time": normalize_hm(rows[1]["time"].strip()),
    }

    # End depot transfer from last two rows
    end_depot = {
        "from_name": rows[-2]["stop"].strip(),
        "from_time": normalize_hm(rows[-2]["time"].strip()),
        "to_name": rows[-1]["stop"].strip(),
        "to_time": normalize_hm(rows[-1]["time"].strip()),
    }

    # Service segments from rows[2:-2]
    body = rows[2:-2]

    # Determine which stop names should be treated as terminals
    if allowed_terminal_names is not None:
        terminal_names = set([n.strip() for n in allowed_terminal_names])
    else:
        # Heuristic fallback: terminals are the most common adjacent-duplicate stop names
        duplicates: List[str] = []
        for prev, cur in zip(body, body[1:]):
            if prev["stop"].strip() == cur["stop"].strip():
                duplicates.append(prev["stop"].strip())
        from collections import Counter
        term_counts = Counter(duplicates)
        # Take up to two most common names, else fall back to ends
        terminal_names = set([name for name, _ in term_counts.most_common(2)])
        if len(terminal_names) < 1 and body:
            terminal_names = {body[0]["stop"].strip()}

    # Build clusters of consecutive rows with the same terminal stop name.
    # For each cluster, keep the first time (arrival) and last time (departure).
    clusters: List[dict] = []
    current: Optional[dict] = None
    for row in body:
        name = row["stop"].strip()
        hm = adjust_midnight_hm(normalize_hm(row["time"].strip()))
        if name in terminal_names:
            if current and current["name"] == name:
                current["last_time"] = hm  # later time within same terminal block
            else:
                # close previous cluster, if any
                if current is not None:
                    clusters.append(current)
                current = {"name": name, "first_time": hm, "last_time": hm}
        else:
            # non-terminal row breaks cluster
            if current is not None:
                clusters.append(current)
                current = None
    if current is not None:
        clusters.append(current)

    # Build service segments between consecutive terminal clusters
    # Use departure time (last_time) of the start cluster and arrival time (first_time) of the end cluster
    service_segments: List[dict] = []
    for a, b in zip(clusters, clusters[1:]):
        # Skip degenerate segments where terminal doesn't change or no time elapses
        if a["name"] == b["name"]:
            continue
        if a["last_time"] == b["first_time"]:
            continue
        service_segments.append(
            {
                "from_name": a["name"],
                "from_time": a["last_time"],
                "to_name": b["name"],
                "to_time": b["first_time"],
            }
        )

    return start_depot, service_segments, end_depot


def _build_service_segments_from_body(body_rows: List[dict], terminal_names: List[str]) -> List[dict]:
    # Construct service segments between terminal clusters within the provided body rows
    terminal_set = set([n.strip() for n in terminal_names])

    def is_terminal(name: str) -> bool:
        return name.strip() in terminal_set

    clusters: List[dict] = []
    current: Optional[dict] = None
    for row in body_rows:
        name = row["stop"].strip()
        hm = adjust_midnight_hm(normalize_hm(row["time"].strip()))
        if is_terminal(name):
            if current and current["name"] == name:
                current["last_time"] = hm
            else:
                if current is not None:
                    clusters.append(current)
                current = {"name": name, "first_time": hm, "last_time": hm}
        else:
            if current is not None:
                clusters.append(current)
                current = None
    if current is not None:
        clusters.append(current)

    segments: List[dict] = []
    for a, b in zip(clusters, clusters[1:]):
        if a["name"] == b["name"]:
            continue
        if a["last_time"] == b["first_time"]:
            continue
        segments.append(
            {
                "from_name": a["name"],
                "from_time": a["last_time"],
                "to_name": b["name"],
                "to_time": b["first_time"],
            }
        )
    return segments


def parse_csv_into_shifts(csv_path: str, allowed_terminal_names: Optional[List[str]] = None) -> List[Tuple[dict, List[dict], dict]]:
    # Split a CSV into multiple shifts using DEPOT boundaries, not generic terminal changes.
    # Heuristic: a depot row is a stop name containing 'rimessa' (case-insensitive).
    with open(csv_path, newline="") as f:
        r = csv.DictReader(f)
        rows = list(r)

    if len(rows) < 4:
        raise RuntimeError("CSV has too few rows")

    def is_depot(name: str) -> bool:
        return "rimessa" in (name or "").lower()

    # Identify starts (depot -> non-depot) and ends (non-depot -> depot)
    starts: List[int] = []  # index i where rows[i] depot, rows[i+1] non-depot
    ends: List[int] = []    # index i where rows[i] non-depot, rows[i+1] depot
    for i in range(0, len(rows) - 1):
        a_name = rows[i]["stop"].strip()
        b_name = rows[i + 1]["stop"].strip()
        if is_depot(a_name) and not is_depot(b_name):
            starts.append(i)
        if (not is_depot(a_name)) and is_depot(b_name):
            ends.append(i)

    shifts: List[Tuple[dict, List[dict], dict]] = []
    si = 0
    ei = 0
    while si < len(starts):
        s = starts[si]
        # Find the first end after this start
        e = None
        while ei < len(ends) and ends[ei] <= s:
            ei += 1
        if ei < len(ends):
            e = ends[ei]
        if e is None:
            break

        # Build this shift between s and e
        start_depot = {
            "from_name": rows[s]["stop"].strip(),
            "from_time": normalize_hm(rows[s]["time"].strip()),
            "to_name": rows[s + 1]["stop"].strip(),
            "to_time": normalize_hm(rows[s + 1]["time"].strip()),
        }
        end_depot = {
            "from_name": rows[e]["stop"].strip(),
            "from_time": normalize_hm(rows[e]["time"].strip()),
            "to_name": rows[e + 1]["stop"].strip(),
            "to_time": normalize_hm(rows[e + 1]["time"].strip()),
        }

        body_rows = rows[s + 2 : e]
        # Terminals for segment building: infer from body duplicates, as in parse_csv_segments
        duplicates: List[str] = []
        for prev, cur in zip(body_rows, body_rows[1:]):
            if prev["stop"].strip() == cur["stop"].strip():
                duplicates.append(prev["stop"].strip())
        from collections import Counter
        term_counts = Counter(duplicates)
        terminal_names = [name for name, _ in term_counts.most_common(2)]
        if len(terminal_names) < 1 and body_rows:
            terminal_names = [body_rows[0]["stop"].strip()]

        service_segments = _build_service_segments_from_body(body_rows, terminal_names)
        shifts.append((start_depot, service_segments, end_depot))

        # Move to the next start after this end, typically e+1 is a depot starting the next shift
        si += 1
        ei += 1

    return shifts


def find_existing_aux_trip(
    base_url: str,
    headers: Dict[str, str],
    departure_stop_id: str,
    arrival_stop_id: str,
    departure_time_hm: str,
    arrival_time_hm: str,
    route_id: str,
    status: str = "depot",
    day_of_week: Optional[str] = None,
    expected_calendar_key: Optional[str] = None,
) -> Optional[str]:
    """Find an existing trip with the same parameters. Returns trip_id if found."""
    # Fetch all trips for this route with the given status
    url = f"{base_url}/api/v1/gtfs/gtfs-trips/by-route/{route_id}"
    params = {"status": status}
    if day_of_week:
        params["day_of_week"] = day_of_week
    resp = requests.get(url, headers=headers, params=params)
    resp.raise_for_status()
    trips = resp.json()
    
    # Look for a trip with matching parameters
    departure_time_full = f"{departure_time_hm}:00"
    arrival_time_full = f"{arrival_time_hm}:00"
    
    for trip in trips:
        # If expected calendar key is provided, ensure it matches this trip's gtfs_service_id
        if expected_calendar_key and trip.get("gtfs_service_id") != expected_calendar_key:
            continue
        # Check if departure and arrival times match
        if trip.get("departure_time") != departure_time_full:
            continue
        if trip.get("arrival_time") != arrival_time_full:
            continue
        
        # Fetch stop times for this trip to check stops
        stops_url = f"{base_url}/api/v1/gtfs/gtfs-stops/by-trip/{trip['id']}"
        stops_resp = requests.get(stops_url, headers=headers)
        stops_resp.raise_for_status()
        stops = stops_resp.json()
        
        if len(stops) == 2:
            first_stop = stops[0]
            last_stop = stops[1]
            if (first_stop.get("id") == departure_stop_id and 
                last_stop.get("id") == arrival_stop_id):
                return trip["id"]
    
    return None


def delete_trip(base_url: str, headers: Dict[str, str], trip_id: str):
    """Delete a trip by its ID."""
    url = f"{base_url}/api/v1/gtfs/gtfs-trips/{trip_id}"
    resp = requests.delete(url, headers=headers)
    resp.raise_for_status()


def create_aux_trip(
    base_url: str,
    headers: Dict[str, str],
    departure_stop_id: str,
    arrival_stop_id: str,
    departure_time_hm: str,
    arrival_time_hm: str,
    route_id: str,
    status: str = "depot",
    calendar_service_key: str = "auxiliary",
    day_of_week: Optional[str] = None,
):
    # Always create a new trip (don't check for existing ones)
    url = f"{base_url}/api/v1/gtfs/aux-trip"
    body = {
        "departure_stop_id": departure_stop_id,
        "arrival_stop_id": arrival_stop_id,
        "departure_time": f"{departure_time_hm}:00",
        "arrival_time": f"{arrival_time_hm}:00",
        "route_id": route_id,
        "status": status,
        "calendar_service_key": calendar_service_key,
    }
    if day_of_week:
        body["day_of_week"] = day_of_week
    resp = requests.post(url, headers=headers, json=body)
    resp.raise_for_status()
    result = resp.json()
    print(f"[INFO] Created new trip {result.get('id')} with shape_id {result.get('shape_id')}")
    return result


def build_output_entry(base_url: str, headers: Dict[str, str], trip: dict, stops_cache: Dict[str, List[dict]]) -> dict:
    # Fetch endpoints (use cache to minimize HTTP)
    stops = get_stops_for_trip_cached(base_url, headers, trip["id"], stops_cache) or []
    start_name = stops[0]["stop_name"] if stops else None
    end_name = stops[-1]["stop_name"] if stops else None
    dep_time = stops[0]["departure_time"] if stops else None
    arr_time = stops[-1]["arrival_time"] if stops else None

    return {
        "id": trip.get("id"),
        "route_id": trip.get("route_id"),
        "service_id": trip.get("service_id"),
        "gtfs_service_id": trip.get("gtfs_service_id"),
        "trip_id": trip.get("trip_id"),
        "status": trip.get("status"),
        "trip_headsign": trip.get("trip_headsign"),
        "trip_short_name": trip.get("trip_short_name"),
        "direction_id": trip.get("direction_id"),
        "block_id": trip.get("block_id"),
        "shape_id": trip.get("shape_id"),
        "wheelchair_accessible": trip.get("wheelchair_accessible"),
        "bikes_allowed": trip.get("bikes_allowed"),
        "start_stop_name": start_name,
        "end_stop_name": end_name,
        "departure_time": dep_time,
        "arrival_time": arr_time,
    }


def process_one_csv(
    csv_path: str,
    base_url: str,
    headers: Dict[str, str],
    route_id: str,
    index_gtfs: Dict[Tuple[str, str, str, str], dict],
    index_depot: Dict[Tuple[str, str, str, str], dict],
    terminal_stop_ids: Dict[str, str],
    stops_cache: Dict[str, List[dict]],
    fuzzy_stops: bool,
    time_tolerance_min: int,
    agency_id: str,
    day_group: str,
) -> List[dict]:
    # Parse CSV into segments, use known GTFS terminal names to avoid over-grouping
    start_depot_seg, service_segments, end_depot_seg = parse_csv_segments(
        csv_path, allowed_terminal_names=list(terminal_stop_ids.keys())
    )

    # Helper to canonicalize stop names for matching using terminal names as ground truth
    def _normalize(n: str) -> str:
        n = (n or "").lower().strip()
        n = n.replace("/", " ").replace("-", " ")
        n = re.sub(r"[^a-z0-9 ]+", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        return n

    canonical_by_norm: Dict[str, str] = { _normalize(k): k for k in terminal_stop_ids.keys() }

    def canonicalize_name(name: str) -> str:
        if not fuzzy_stops:
            return name
        if name in terminal_stop_ids:
            return name
        norm = _normalize(name)
        can = canonical_by_norm.get(norm)
        if can and can != name:
            print(f"[WARN] Normalized segment stop: '{name}' -> '{can}'")
            return can
        return name

    def resolve_trip_for_segment(seg: dict, prefer_depot: bool) -> dict:
        from_name_c = canonicalize_name(seg["from_name"])
        to_name_c = canonicalize_name(seg["to_name"])
        key = (
            from_name_c,
            to_name_c,
            adjust_midnight_hm(seg["from_time"]),
            adjust_midnight_hm(seg["to_time"]),
        )
        idx = index_depot if prefer_depot else index_gtfs
        found = idx.get(key)
        if found:
            return found["trip"]
        # Endpoint tolerant match: same names, times within tolerance
        if time_tolerance_min > 0:
            for (s_name, e_name, dep_hm, arr_hm), entry in idx.items():
                if s_name != from_name_c or e_name != to_name_c:
                    continue
                d1 = minutes_diff(dep_hm, adjust_midnight_hm(seg["from_time"]))
                d2 = minutes_diff(arr_hm, adjust_midnight_hm(seg["to_time"]))
                if d1 is not None and d2 is not None and d1 <= time_tolerance_min and d2 <= time_tolerance_min:
                    return entry["trip"]
        return {}

    # Helper to create depot trip (delete old ones first, then create new)
    def ensure_depot_trip(seg: dict) -> dict:
        # Find and delete any existing depot trips with the same parameters
        dep_name = canonicalize_name(seg["from_name"]) if fuzzy_stops else seg["from_name"]
        arr_name = canonicalize_name(seg["to_name"]) if fuzzy_stops else seg["to_name"]

        def get_stop_id(name: str, role: str) -> Optional[str]:
            # Resolve by known terminal IDs first, else fall back to an exact name lookup.
            sid = terminal_stop_ids.get(name)
            if sid:
                return sid
            return find_stop_id_by_name(base_url, headers, name, use_fuzzy=fuzzy_stops)

        dep_id = get_stop_id(dep_name, 'dep')
        arr_id = get_stop_id(arr_name, 'arr')
        if not dep_id or not arr_id:
            raise RuntimeError(f"Cannot resolve stop IDs for depot segment: {dep_name} -> {arr_name}")

        # Map day_group to a specific day_of_week for auxiliary trips
        # mon-fri -> monday; sat -> saturday; sun -> sunday
        aux_day = None
        if day_group:
            g = (day_group or '').lower()
            if g == 'mon-fri':
                aux_day = 'monday'
            elif g == 'sat':
                aux_day = 'saturday'
            elif g == 'sun':
                aux_day = 'sunday'

        # Compute expected calendar key for deletion filtering when a specific day is used
        expected_calendar_key = None
        if aux_day:
            suffix = {'monday':'mon','tuesday':'tue','wednesday':'wed','thursday':'thu','friday':'fri','saturday':'sat','sunday':'sun'}[aux_day]
            expected_calendar_key = f"auxiliary_{suffix}"

        # Find existing trip with same parameters and delete it
        existing_trip_id = find_existing_aux_trip(
            base_url,
            headers,
            dep_id,
            arr_id,
            adjust_midnight_hm(seg["from_time"]),
            adjust_midnight_hm(seg["to_time"]),
            route_id,
            status="depot",
            day_of_week=aux_day,
            expected_calendar_key=expected_calendar_key,
        )
        if existing_trip_id:
            if skip_deletion:
                print(f"[INFO] Skipping deletion of existing trip {existing_trip_id} (--skip-deletion flag set)")
            else:
                print(f"[INFO] Deleting old trip {existing_trip_id}")
                try:
                    delete_trip(base_url, headers, existing_trip_id)
                    print(f"[INFO] Successfully deleted old trip {existing_trip_id}")
                except Exception as e:
                    print(f"[WARN] Failed to delete old trip {existing_trip_id}: {e}")

        # Create new trip
        created = create_aux_trip(
            base_url,
            headers,
            dep_id,
            arr_id,
            adjust_midnight_hm(seg["from_time"]),
            adjust_midnight_hm(seg["to_time"]),
            route_id,
            status="depot",
            calendar_service_key="auxiliary",
            day_of_week=aux_day,
        )
        # Update cache with new stops for created trip (fetch once)
        try:
            _ = get_stops_for_trip_cached(base_url, headers, created["id"], stops_cache)
        except Exception:
            pass
        return created

    # Build output in chronological order: start depot -> service trips -> end depot
    output: List[dict] = []

    # Start depot
    start_trip = ensure_depot_trip(start_depot_seg)
    output.append(build_output_entry(base_url, headers, start_trip, stops_cache))

    # Service trips: prefer endpoint match; otherwise, try internal stop match within a GTFS trip
    def find_trip_by_internal_stops(seg: dict) -> Optional[dict]:
        from_name = canonicalize_name(seg["from_name"]) if fuzzy_stops else seg["from_name"]
        to_name = canonicalize_name(seg["to_name"]) if fuzzy_stops else seg["to_name"]
        from_hm = adjust_midnight_hm(seg["from_time"]).strip()
        to_hm = adjust_midnight_hm(seg["to_time"]).strip()

        # Iterate through all known GTFS trips for this route/day
        for entry in index_gtfs.values():
            t = entry["trip"]
            stops = get_stops_for_trip_cached(base_url, headers, t["id"], stops_cache) or []
            from_idx = None
            to_idx = None
            for i, s in enumerate(stops):
                stop_name = s.get("stop_name")
                arr_hm = time_hms_to_hm(s.get("arrival_time", "")) if s.get("arrival_time") else None
                dep_hm = time_hms_to_hm(s.get("departure_time", "")) if s.get("departure_time") else None

                # Match departure at the origin: allow either dep or arr to equal from_hm
                names_match_from = (stop_name == from_name) or (fuzzy_stops and _normalize(stop_name) == _normalize(from_name))
                dep_ok = (dep_hm == from_hm) or (time_tolerance_min > 0 and dep_hm is not None and minutes_diff(dep_hm, from_hm) is not None and minutes_diff(dep_hm, from_hm) <= time_tolerance_min)
                arr_ok = (arr_hm == from_hm) or (time_tolerance_min > 0 and arr_hm is not None and minutes_diff(arr_hm, from_hm) is not None and minutes_diff(arr_hm, from_hm) <= time_tolerance_min)
                if from_idx is None and names_match_from and (dep_ok or arr_ok):
                    from_idx = i
                    continue
                # After we've found origin, look for destination later: allow either arr or dep to equal to_hm
                names_match_to = (stop_name == to_name) or (fuzzy_stops and _normalize(stop_name) == _normalize(to_name))
                arr_ok_to = (arr_hm == to_hm) or (time_tolerance_min > 0 and arr_hm is not None and minutes_diff(arr_hm, to_hm) is not None and minutes_diff(arr_hm, to_hm) <= time_tolerance_min)
                dep_ok_to = (dep_hm == to_hm) or (time_tolerance_min > 0 and dep_hm is not None and minutes_diff(dep_hm, to_hm) is not None and minutes_diff(dep_hm, to_hm) <= time_tolerance_min)
                if from_idx is not None and names_match_to and (arr_ok_to or dep_ok_to):
                    to_idx = i
                    break
            if from_idx is not None and to_idx is not None and to_idx > from_idx:
                return {"trip": t}
        return None

    for seg in service_segments:
        key = (
            canonicalize_name(seg["from_name"]) if fuzzy_stops else seg["from_name"],
            canonicalize_name(seg["to_name"]) if fuzzy_stops else seg["to_name"],
            adjust_midnight_hm(seg["from_time"]),
            adjust_midnight_hm(seg["to_time"]),
        )
        item = index_gtfs.get(key)
        if not item and time_tolerance_min > 0:
            # Endpoint tolerant match
            for (s_name, e_name, dep_hm, arr_hm), entry in index_gtfs.items():
                if s_name != key[0] or e_name != key[1]:
                    continue
                d1 = minutes_diff(dep_hm, key[2])
                d2 = minutes_diff(arr_hm, key[3])
                if d1 is not None and d2 is not None and d1 <= time_tolerance_min and d2 <= time_tolerance_min:
                    item = entry
                    break
        if not item:
            # Fallback: search within trip stop sequences
            item = find_trip_by_internal_stops(seg)
        # Cross-route fallback within same agency if still not found
        if not item:
            # Cache routes and built indices per other route to avoid repeated HTTP calls
            if '___other_routes_cache' not in locals():
                ___routes = []
                try:
                    ___routes = fetch_routes_by_agency(base_url, headers, agency_id)
                except Exception as e:
                    ___routes = []
                ___other_routes_cache = [r for r in ___routes if r.get('id') and r.get('id') != route_id]
                ___other_indices_cache: Dict[str, Dict[Tuple[str, str, str, str], dict]] = {}
            # Attempt match across other routes
            for ___r in ___other_routes_cache:
                ___rid = ___r.get('id')
                if not ___rid:
                    continue
                ___idx = ___other_indices_cache.get(___rid)
                if ___idx is None:
                    try:
                        ___trips = fetch_trips_by_route_multi_day(base_url, headers, ___rid, day_group=day_group, status="gtfs")
                        ___idx, _ = build_trip_index(base_url, headers, ___trips, stops_cache=stops_cache)
                        ___other_indices_cache[___rid] = ___idx
                    except Exception:
                        continue
                # Exact endpoint match on other route
                ___entry = ___idx.get(key)
                if ___entry:
                    item = ___entry
                    print(f"[INFO] Fallback matched trip on other route {___rid} for segment {key[0]} -> {key[1]} {key[2]}-{key[3]}")
                    break
                # Tolerant endpoint match on other route
                if time_tolerance_min > 0 and not item:
                    for (s_name, e_name, dep_hm, arr_hm), ___entry2 in ___idx.items():
                        if s_name != key[0] or e_name != key[1]:
                            continue
                        d1 = minutes_diff(dep_hm, key[2])
                        d2 = minutes_diff(arr_hm, key[3])
                        if d1 is not None and d2 is not None and d1 <= time_tolerance_min and d2 <= time_tolerance_min:
                            item = ___entry2
                            print(f"[INFO] Fallback tolerant match on other route {___rid} for segment {key[0]} -> {key[1]} {key[2]}-{key[3]}")
                            break
                    if item:
                        break
                # Internal stops match on other route
                if not item:
                    from_name_used = canonicalize_name(seg["from_name"]) if fuzzy_stops else seg["from_name"]
                    to_name_used = canonicalize_name(seg["to_name"]) if fuzzy_stops else seg["to_name"]
                    for ___entry3 in ___idx.values():
                        ___t = ___entry3["trip"]
                        ___stops = get_stops_for_trip_cached(base_url, headers, ___t["id"], stops_cache) or []
                        ___from_idx = None
                        ___to_idx = None
                        for i, s in enumerate(___stops):
                            stop_name = s.get("stop_name")
                            arr_hm = time_hms_to_hm(s.get("arrival_time", "")) if s.get("arrival_time") else None
                            dep_hm = time_hms_to_hm(s.get("departure_time", "")) if s.get("departure_time") else None
                            names_match_from = (stop_name == from_name_used) or (fuzzy_stops and _normalize(stop_name) == _normalize(from_name_used))
                            dep_ok = (dep_hm == key[2]) or (time_tolerance_min > 0 and dep_hm is not None and minutes_diff(dep_hm, key[2]) is not None and minutes_diff(dep_hm, key[2]) <= time_tolerance_min)
                            arr_ok = (arr_hm == key[2]) or (time_tolerance_min > 0 and arr_hm is not None and minutes_diff(arr_hm, key[2]) is not None and minutes_diff(arr_hm, key[2]) <= time_tolerance_min)
                            if ___from_idx is None and names_match_from and (dep_ok or arr_ok):
                                ___from_idx = i
                                continue
                            names_match_to = (stop_name == to_name_used) or (fuzzy_stops and _normalize(stop_name) == _normalize(to_name_used))
                            arr_ok_to = (arr_hm == key[3]) or (time_tolerance_min > 0 and arr_hm is not None and minutes_diff(arr_hm, key[3]) is not None and minutes_diff(arr_hm, key[3]) <= time_tolerance_min)
                            dep_ok_to = (dep_hm == key[3]) or (time_tolerance_min > 0 and dep_hm is not None and minutes_diff(dep_hm, key[3]) is not None and minutes_diff(dep_hm, key[3]) <= time_tolerance_min)
                            if ___from_idx is not None and names_match_to and (arr_ok_to or dep_ok_to):
                                ___to_idx = i
                                break
                        if ___from_idx is not None and ___to_idx is not None and ___to_idx > ___from_idx:
                            item = {"trip": ___t}
                            print(f"[INFO] Fallback internal-stops match on other route {___rid} for segment {key[0]} -> {key[1]} {key[2]}-{key[3]}")
                            break
                    if item:
                        break
        if not item:
            from_name_used = canonicalize_name(seg["from_name"]) if fuzzy_stops else seg["from_name"]
            to_name_used = canonicalize_name(seg["to_name"]) if fuzzy_stops else seg["to_name"]
            raise RuntimeError(
                "No GTFS trip found for segment {" +
                f"'from_name': '{from_name_used}', 'from_time': '{seg['from_time']}', " +
                f"'to_name': '{to_name_used}', 'to_time': '{seg['to_time']}'" +
                "}"
            )
        output.append(build_output_entry(base_url, headers, item["trip"], stops_cache))

    # End depot
    end_trip = ensure_depot_trip(end_depot_seg)
    output.append(build_output_entry(base_url, headers, end_trip, stops_cache))

    return output


def process_csv_multi(
    csv_path: str,
    base_url: str,
    headers: Dict[str, str],
    route_id: str,
    index_gtfs: Dict[Tuple[str, str, str, str], dict],
    index_depot: Dict[Tuple[str, str, str, str], dict],
    terminal_stop_ids: Dict[str, str],
    stops_cache: Dict[str, List[dict]],
    fuzzy_stops: bool,
    time_tolerance_min: int,
    agency_id: str,
    day_group: str,
    skip_deletion: bool = False,
) -> List[List[dict]]:
    # Parse the CSV into potentially multiple shifts and build outputs for each
    # Reuse the same internal helpers from process_one_csv

    # Local helpers copied to avoid refactor risk
    def _normalize(n: str) -> str:
        n = (n or "").lower().strip()
        n = n.replace("/", " ").replace("-", " ")
        n = re.sub(r"[^a-z0-9 ]+", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
        return n

    canonical_by_norm: Dict[str, str] = { _normalize(k): k for k in terminal_stop_ids.keys() }

    def canonicalize_name(name: str) -> str:
        if not fuzzy_stops:
            return name
        if name in terminal_stop_ids:
            return name
        norm = _normalize(name)
        can = canonical_by_norm.get(norm)
        if can and can != name:
            print(f"[WARN] Normalized segment stop: '{name}' -> '{can}'")
            return can
        return name

    def resolve_trip_for_segment(seg: dict, prefer_depot: bool) -> dict:
        from_name_c = canonicalize_name(seg["from_name"])
        to_name_c = canonicalize_name(seg["to_name"])
        key = (
            from_name_c,
            to_name_c,
            adjust_midnight_hm(seg["from_time"]),
            adjust_midnight_hm(seg["to_time"]),
        )
        idx = index_depot if prefer_depot else index_gtfs
        found = idx.get(key)
        if found:
            return found["trip"]
        if time_tolerance_min > 0:
            for (s_name, e_name, dep_hm, arr_hm), entry in idx.items():
                if s_name != from_name_c or e_name != to_name_c:
                    continue
                d1 = minutes_diff(dep_hm, adjust_midnight_hm(seg["from_time"]))
                d2 = minutes_diff(arr_hm, adjust_midnight_hm(seg["to_time"]))
                if d1 is not None and d2 is not None and d1 <= time_tolerance_min and d2 <= time_tolerance_min:
                    return entry["trip"]
        return {}

    def ensure_depot_trip(seg: dict) -> dict:
        # Find and delete any existing depot trips with the same parameters, then create new
        dep_name = canonicalize_name(seg["from_name"]) if fuzzy_stops else seg["from_name"]
        arr_name = canonicalize_name(seg["to_name"]) if fuzzy_stops else seg["to_name"]

        def get_stop_id(name: str) -> Optional[str]:
            sid = terminal_stop_ids.get(name)
            if sid:
                return sid
            return find_stop_id_by_name(base_url, headers, name, use_fuzzy=fuzzy_stops)

        dep_id = get_stop_id(dep_name)
        arr_id = get_stop_id(arr_name)
        if not dep_id or not arr_id:
            raise RuntimeError(f"Cannot resolve stop IDs for depot segment: {dep_name} -> {arr_name}")

        # Map day_group to a specific day_of_week for auxiliary trips
        # mon-fri -> monday; sat -> saturday; sun -> sunday
        aux_day = None
        if day_group:
            g = (day_group or '').lower()
            if g == 'mon-fri':
                aux_day = 'monday'
            elif g == 'sat':
                aux_day = 'saturday'
            elif g == 'sun':
                aux_day = 'sunday'

        # Compute expected calendar key for deletion filtering when a specific day is used
        expected_calendar_key = None
        if aux_day:
            suffix = {'monday':'mon','tuesday':'tue','wednesday':'wed','thursday':'thu','friday':'fri','saturday':'sat','sunday':'sun'}[aux_day]
            expected_calendar_key = f"auxiliary_{suffix}"

        # Find existing trip with same parameters and delete it
        existing_trip_id = find_existing_aux_trip(
            base_url,
            headers,
            dep_id,
            arr_id,
            adjust_midnight_hm(seg["from_time"]),
            adjust_midnight_hm(seg["to_time"]),
            route_id,
            status="depot",
            day_of_week=aux_day,
            expected_calendar_key=expected_calendar_key,
        )
        if existing_trip_id:
            if skip_deletion:
                print(f"[INFO] Skipping deletion of existing trip {existing_trip_id} (--skip-deletion flag set)")
            else:
                print(f"[INFO] Deleting old trip {existing_trip_id}")
                try:
                    delete_trip(base_url, headers, existing_trip_id)
                    print(f"[INFO] Successfully deleted old trip {existing_trip_id}")
                except Exception as e:
                    print(f"[WARN] Failed to delete old trip {existing_trip_id}: {e}")

        # Create new trip
        created = create_aux_trip(
            base_url,
            headers,
            dep_id,
            arr_id,
            adjust_midnight_hm(seg["from_time"]),
            adjust_midnight_hm(seg["to_time"]),
            route_id,
            status="depot",
            calendar_service_key="auxiliary",
            day_of_week=aux_day,
        )
        try:
            _ = get_stops_for_trip_cached(base_url, headers, created["id"], stops_cache)
        except Exception:
            pass
        return created

    def find_trip_by_internal_stops(seg: dict) -> Optional[dict]:
        from_name = canonicalize_name(seg["from_name"]) if fuzzy_stops else seg["from_name"]
        to_name = canonicalize_name(seg["to_name"]) if fuzzy_stops else seg["to_name"]
        from_hm = adjust_midnight_hm(seg["from_time"]).strip()
        to_hm = adjust_midnight_hm(seg["to_time"]).strip()

        for entry in index_gtfs.values():
            t = entry["trip"]
            stops = get_stops_for_trip_cached(base_url, headers, t["id"], stops_cache) or []
            from_idx = None
            to_idx = None
            for i, s in enumerate(stops):
                stop_name = s.get("stop_name")
                arr_hm = time_hms_to_hm(s.get("arrival_time", "")) if s.get("arrival_time") else None
                dep_hm = time_hms_to_hm(s.get("departure_time", "")) if s.get("departure_time") else None
                names_match_from = (stop_name == from_name) or (_normalize(stop_name) == _normalize(from_name) if fuzzy_stops else False)
                dep_ok = (dep_hm == from_hm) or (time_tolerance_min > 0 and dep_hm is not None and minutes_diff(dep_hm, from_hm) is not None and minutes_diff(dep_hm, from_hm) <= time_tolerance_min)
                arr_ok = (arr_hm == from_hm) or (time_tolerance_min > 0 and arr_hm is not None and minutes_diff(arr_hm, from_hm) is not None and minutes_diff(arr_hm, from_hm) <= time_tolerance_min)
                if from_idx is None and names_match_from and (dep_ok or arr_ok):
                    from_idx = i
                    continue
                names_match_to = (stop_name == to_name) or (_normalize(stop_name) == _normalize(to_name) if fuzzy_stops else False)
                arr_ok_to = (arr_hm == to_hm) or (time_tolerance_min > 0 and arr_hm is not None and minutes_diff(arr_hm, to_hm) is not None and minutes_diff(arr_hm, to_hm) <= time_tolerance_min)
                dep_ok_to = (dep_hm == to_hm) or (time_tolerance_min > 0 and dep_hm is not None and minutes_diff(dep_hm, to_hm) is not None and minutes_diff(dep_hm, to_hm) <= time_tolerance_min)
                if from_idx is not None and to_idx is None and names_match_to and (arr_ok_to or dep_ok_to):
                    to_idx = i
                    break
            if from_idx is not None and to_idx is not None and to_idx > from_idx:
                return {"trip": t}
        return None

    def build_output_for_segments(start_depot_seg: dict, service_segments: List[dict], end_depot_seg: dict) -> List[dict]:
        output: List[dict] = []
        start_trip = ensure_depot_trip(start_depot_seg)
        output.append(build_output_entry(base_url, headers, start_trip, stops_cache))
        for seg in service_segments:
            key = (
                canonicalize_name(seg["from_name"]) if fuzzy_stops else seg["from_name"],
                canonicalize_name(seg["to_name"]) if fuzzy_stops else seg["to_name"],
                adjust_midnight_hm(seg["from_time"]),
                adjust_midnight_hm(seg["to_time"]),
            )
            item = index_gtfs.get(key)
            if not item and time_tolerance_min > 0:
                for (s_name, e_name, dep_hm, arr_hm), entry in index_gtfs.items():
                    if s_name != key[0] or e_name != key[1]:
                        continue
                    d1 = minutes_diff(dep_hm, key[2])
                    d2 = minutes_diff(arr_hm, key[3])
                    if d1 is not None and d2 is not None and d1 <= time_tolerance_min and d2 <= time_tolerance_min:
                        item = entry
                        break
            if not item:
                item = find_trip_by_internal_stops(seg)
            # Cross-route fallback within same agency if still not found
            if not item:
                if '___other_routes_cache' not in locals():
                    ___routes = []
                    try:
                        ___routes = fetch_routes_by_agency(base_url, headers, agency_id)
                    except Exception:
                        ___routes = []
                    ___other_routes_cache = [r for r in ___routes if r.get('id') and r.get('id') != route_id]
                    ___other_indices_cache: Dict[str, Dict[Tuple[str, str, str, str], dict]] = {}
                for ___r in ___other_routes_cache:
                    ___rid = ___r.get('id')
                    if not ___rid:
                        continue
                    ___idx = ___other_indices_cache.get(___rid)
                    if ___idx is None:
                        try:
                            ___trips = fetch_trips_by_route_multi_day(base_url, headers, ___rid, day_group=day_group, status="gtfs")
                            ___idx, _ = build_trip_index(base_url, headers, ___trips, stops_cache=stops_cache)
                            ___other_indices_cache[___rid] = ___idx
                        except Exception:
                            continue
                    ___entry = ___idx.get(key)
                    if ___entry:
                        item = ___entry
                        print(f"[INFO] Fallback matched trip on other route {___rid} for segment {key[0]} -> {key[1]} {key[2]}-{key[3]}")
                        break
                    if time_tolerance_min > 0 and not item:
                        for (s_name, e_name, dep_hm, arr_hm), ___entry2 in ___idx.items():
                            if s_name != key[0] or e_name != key[1]:
                                continue
                            d1 = minutes_diff(dep_hm, key[2])
                            d2 = minutes_diff(arr_hm, key[3])
                            if d1 is not None and d2 is not None and d1 <= time_tolerance_min and d2 <= time_tolerance_min:
                                item = ___entry2
                                print(f"[INFO] Fallback tolerant match on other route {___rid} for segment {key[0]} -> {key[1]} {key[2]}-{key[3]}")
                                break
                        if item:
                            break
                    if not item:
                        from_name_used = canonicalize_name(seg["from_name"]) if fuzzy_stops else seg["from_name"]
                        to_name_used = canonicalize_name(seg["to_name"]) if fuzzy_stops else seg["to_name"]
                        for ___entry3 in ___idx.values():
                            ___t = ___entry3["trip"]
                            ___stops = get_stops_for_trip_cached(base_url, headers, ___t["id"], stops_cache) or []
                            ___from_idx = None
                            ___to_idx = None
                            for i, s in enumerate(___stops):
                                stop_name = s.get("stop_name")
                                arr_hm = time_hms_to_hm(s.get("arrival_time", "")) if s.get("arrival_time") else None
                                dep_hm = time_hms_to_hm(s.get("departure_time", "")) if s.get("departure_time") else None
                                names_match_from = (stop_name == from_name_used) or (fuzzy_stops and _normalize(stop_name) == _normalize(from_name_used))
                                dep_ok = (dep_hm == key[2]) or (time_tolerance_min > 0 and dep_hm is not None and minutes_diff(dep_hm, key[2]) is not None and minutes_diff(dep_hm, key[2]) <= time_tolerance_min)
                                arr_ok = (arr_hm == key[2]) or (time_tolerance_min > 0 and arr_hm is not None and minutes_diff(arr_hm, key[2]) is not None and minutes_diff(arr_hm, key[2]) <= time_tolerance_min)
                                if ___from_idx is None and names_match_from and (dep_ok or arr_ok):
                                    ___from_idx = i
                                    continue
                                names_match_to = (stop_name == to_name_used) or (fuzzy_stops and _normalize(stop_name) == _normalize(to_name_used))
                                arr_ok_to = (arr_hm == key[3]) or (time_tolerance_min > 0 and arr_hm is not None and minutes_diff(arr_hm, key[3]) is not None and minutes_diff(arr_hm, key[3]) <= time_tolerance_min)
                                dep_ok_to = (dep_hm == key[3]) or (time_tolerance_min > 0 and dep_hm is not None and minutes_diff(dep_hm, key[3]) is not None and minutes_diff(dep_hm, key[3]) <= time_tolerance_min)
                                if ___from_idx is not None and names_match_to and (arr_ok_to or dep_ok_to):
                                    ___to_idx = i
                                    break
                            if ___from_idx is not None and ___to_idx is not None and ___to_idx > ___from_idx:
                                item = {"trip": ___t}
                                print(f"[INFO] Fallback internal-stops match on other route {___rid} for segment {key[0]} -> {key[1]} {key[2]}-{key[3]}")
                                break
                        if item:
                            break
            if not item:
                from_name_used = canonicalize_name(seg["from_name"]) if fuzzy_stops else seg["from_name"]
                to_name_used = canonicalize_name(seg["to_name"]) if fuzzy_stops else seg["to_name"]
                raise RuntimeError(
                    "No GTFS trip found for segment {" +
                    f"'from_name': '{from_name_used}', 'from_time': '{seg['from_time']}', " +
                    f"'to_name': '{to_name_used}', 'to_time': '{seg['to_time']}'" +
                    "}"
                )
            output.append(build_output_entry(base_url, headers, item["trip"], stops_cache))
        end_trip = ensure_depot_trip(end_depot_seg)
        output.append(build_output_entry(base_url, headers, end_trip, stops_cache))
        return output

    shifts = parse_csv_into_shifts(csv_path, allowed_terminal_names=list(terminal_stop_ids.keys()))
    outputs: List[List[dict]] = []
    for start_depot_seg, service_segments, end_depot_seg in shifts:
        outputs.append(build_output_for_segments(start_depot_seg, service_segments, end_depot_seg))
    return outputs


def main():
    parser = argparse.ArgumentParser(description="Reconstruct shift JSON from CSV(s) and backend API")
    # Mutually exclusive input: single CSV or a folder with many CSVs
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--csv", help="Path to a single CSV file to process")
    group.add_argument("--input-dir", help="Directory containing CSV files to process")

    # Output options (single or batch)
    parser.add_argument("--output", help="Output JSON path (when using --csv)")
    # output-dir is not necessary: when --input-dir is used JSON files will be written
    # next to each CSV (same folder) replacing the .csv extension with .json
    # kept for backward compatibility but ignored if provided
    # parser.add_argument("--output-dir", help="(deprecated) output directory for JSON files")

    # Common options
    parser.add_argument("--agency-id", default="ec5a38c2-e6af-461b-a0a9-0ab70b82cf7b")
    parser.add_argument("--route-short-name", default="1")
    parser.add_argument("--base-url", default="http://localhost:8002")
    parser.add_argument("--env", default="/home/elettra/projects/elettra-backend/tests/test.env")
    parser.add_argument("--day-of-week", default="mon-fri", choices=[
        "mon-fri","sat","sun"
    ])
    # Matching options
    parser.add_argument("--fuzzy-stops", action="store_true", help="Enable fuzzy stop-name matching with warnings")
    parser.add_argument("--time-tolerance-min", type=int, default=0, help="Allow ±N minutes difference when matching times")
    parser.add_argument("--skip-deletion", action="store_true", help="Skip deletion of existing trips from database")
    args = parser.parse_args()

    # Auth
    env = read_env_file(args.env)
    email = env.get("TEST_LOGIN_EMAIL") or os.getenv("TEST_LOGIN_EMAIL")
    password = env.get("TEST_LOGIN_PASSWORD") or os.getenv("TEST_LOGIN_PASSWORD")
    if not email or not password:
        raise RuntimeError("Missing TEST_LOGIN_EMAIL/TEST_LOGIN_PASSWORD in env or tests/test.env")
    headers = api_login(args.base_url, email, password)

    # We'll build route-specific indices. For single-file mode, use the provided route-short-name.
    # For batch mode, infer from filename and cache per route-short-name.
    stops_cache: Dict[str, List[dict]] = {}
    per_route_cache: Dict[str, dict] = {}

    def get_indices_for_route(route_short_name: str):
        if route_short_name in per_route_cache:
            return per_route_cache[route_short_name]
        route_id_local = get_route_id(args.base_url, headers, args.agency_id, route_short_name)
        service_trips_local = fetch_trips_by_route_multi_day(
            args.base_url, headers, route_id_local, day_group=args.day_of_week, status="gtfs"
        )
        index_gtfs_local, terminal_stop_ids_local = build_trip_index(
            args.base_url, headers, service_trips_local, stops_cache=stops_cache
        )
        depot_trips_local = fetch_trips_by_route_multi_day(
            args.base_url, headers, route_id_local, day_group=args.day_of_week, status="depot"
        )
        index_depot_local, _ = build_trip_index(
            args.base_url, headers, depot_trips_local, stops_cache=stops_cache
        )
        per_route_cache[route_short_name] = {
            "route_id": route_id_local,
            "index_gtfs": index_gtfs_local,
            "index_depot": index_depot_local,
            "terminal_stop_ids": terminal_stop_ids_local,
        }
        return per_route_cache[route_short_name]

    # Single-file mode
    if args.csv:
        csv_path = args.csv
        if not os.path.isfile(csv_path):
            raise FileNotFoundError(f"CSV not found: {csv_path}")
        out_path = args.output
        if not out_path:
            base = os.path.splitext(os.path.basename(csv_path))[0]
            out_path = os.path.join(os.path.dirname(csv_path), f"{base}.json")
        # Determine route for this file
        route_sn = args.route_short_name
        inferred = infer_route_short_name_from_filename(os.path.splitext(os.path.basename(csv_path))[0])
        if inferred:
            route_sn = inferred

        inds = get_indices_for_route(route_sn)
        outputs = process_csv_multi(
            csv_path,
            args.base_url,
            headers,
            inds["route_id"],
            inds["index_gtfs"],
            inds["index_depot"],
            inds["terminal_stop_ids"],
            stops_cache,
            args.fuzzy_stops,
            args.time_tolerance_min,
            args.agency_id,
            args.day_of_week,
            args.skip_deletion,
        )
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        if len(outputs) <= 1:
            out_single = out_path
            data = outputs[0] if outputs else []
            with open(out_single, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            base_name = os.path.splitext(os.path.basename(out_single))[0]
            print(f"[OK] {base_name}: {len(data)} trips -> {out_single}")
        else:
            base, ext = os.path.splitext(out_path)
            for idx, data in enumerate(outputs, start=1):
                out_part = f"{base}__part{idx}{ext}"
                with open(out_part, "w") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                base_name = os.path.splitext(os.path.basename(out_part))[0]
                print(f"[OK] {base_name}: {len(data)} trips -> {out_part}")
        return

    # Batch folder mode
    input_dir = args.input_dir
    if not os.path.isdir(input_dir):
        raise NotADirectoryError(f"Input directory not found: {input_dir}")
    input_dir_clean = input_dir.rstrip('/\\')
    # Build an output directory by replacing 'csv' with 'json' in the last path component
    parent_dir, last_comp = os.path.split(input_dir_clean)
    if 'csv' in last_comp:
        new_last = last_comp.replace('csv', 'json')
    else:
        new_last = f"{last_comp}_json"
    output_dir = os.path.join(parent_dir, new_last) if parent_dir else new_last
    os.makedirs(output_dir, exist_ok=True)

    # Collect CSV files
    import glob
    csv_files = sorted(glob.glob(os.path.join(input_dir, "*.csv")))
    if not csv_files:
        raise RuntimeError(f"No CSV files found in {input_dir}")

    total_ok = 0
    total_fail = 0
    for csv_path in csv_files:
        base = os.path.splitext(os.path.basename(csv_path))[0]
        # write the JSON next to the CSV, replacing its extension
        out_path = os.path.join(output_dir, f"{base}.json")
        try:
            # Infer route short name from filename; fallback to provided arg
            route_sn = infer_route_short_name_from_filename(base) or args.route_short_name
            inds = get_indices_for_route(route_sn)
            outputs = process_csv_multi(
                csv_path,
                args.base_url,
                headers,
                inds["route_id"],
                inds["index_gtfs"],
                inds["index_depot"],
                inds["terminal_stop_ids"],
                stops_cache,
                args.fuzzy_stops,
                args.time_tolerance_min,
                args.agency_id,
                args.day_of_week,
                args.skip_deletion,
            )
            if len(outputs) <= 1:
                data = outputs[0] if outputs else []
                with open(out_path, "w") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"[OK] {base}: {len(data)} trips -> {out_path}")
            else:
                for idx, data in enumerate(outputs, start=1):
                    out_part = os.path.join(os.path.dirname(out_path), f"{base}__part{idx}.json")
                    with open(out_part, "w") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    print(f"[OK] {base}__part{idx}: {len(data)} trips -> {out_part}")
            total_ok += 1
        except Exception as e:
            print(f"[FAIL] {base}: {e}")
            total_fail += 1

    print(f"Done. Success: {total_ok}, Failed: {total_fail}, Wrote JSON files into: {output_dir}")


if __name__ == "__main__":
    main()
