import os
import json
import argparse
from datetime import datetime
from typing import List, Tuple, Dict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from pyomo.environ import (
    ConcreteModel,
    Set,
    Param,
    Var,
    Binary,
    NonNegativeReals,
    Reals,
    Constraint,
    Objective,
    minimize,
    value,
    SolverFactory,
)


def time_to_minutes(time_str: str) -> int | None:
    if not time_str:
        return None
    try:
        h, m, s = map(int, time_str.split(":"))
        return h * 60 + m
    except Exception:
        return None


def load_bus_config(config_file: str) -> dict:
    with open(config_file, "r") as f:
        return json.load(f)


def identify_lugano_centro_files(shift_dir: str) -> List[str]:
    res = []
    for name in os.listdir(shift_dir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(shift_dir, name)
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, list) and any(isinstance(t, dict) and t.get("end_stop_name") == "Lugano, Centro" for t in data):
            res.append(path)
    return res


def identify_stations(shift_files: List[str]) -> List[str]:
    stations = set()
    for path in shift_files:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, list):
            continue
        for trip in data:
            if not isinstance(trip, dict):
                continue
            s = trip.get("start_stop_name")
            e = trip.get("end_stop_name")
            if s and "Rimessa" not in s:
                stations.add(s)
            if e and "Rimessa" not in e:
                stations.add(e)
    return sorted(stations)


def compute_time_bounds(shift_files: List[str]) -> Tuple[int, int]:
    first_t = None
    last_t = None
    for path in shift_files:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, list):
            continue
        for trip in data:
            if not isinstance(trip, dict):
                continue
            a = time_to_minutes(trip.get("arrival_time"))
            d = time_to_minutes(trip.get("departure_time"))
            if a is not None:
                first_t = a if first_t is None else min(first_t, a)
                last_t = a if last_t is None else max(last_t, a)
            if d is not None:
                first_t = d if first_t is None else min(first_t, d)
                last_t = d if last_t is None else max(last_t, d)
    if first_t is None or last_t is None:
        raise RuntimeError("No valid times found")
    return first_t, last_t


def load_consumption_map(consumption_dir: str, shift_name: str) -> Dict[str, dict]:
    path = os.path.join(consumption_dir, f"{shift_name}_predictions.json")
    with open(path, "r") as f:
        data = json.load(f)
    out: Dict[str, dict] = {}
    for pred in data.get("predictions", []):
        tid = pred.get("trip_id")
        if tid is not None:
            out[str(tid)] = pred
    return out


def get_consumption_from_map(cmap: Dict[str, dict], trip_id: str, quantile: str = "mean") -> float:
    p = cmap.get(str(trip_id))
    if p is None:
        raise ValueError(f"Trip {trip_id} not found in predictions")
    if quantile == "mean":
        return float(p.get("prediction_kwh"))
    if quantile == "median":
        return float(p.get("prediction_median_kwh"))
    if quantile in ["0.05", "0.25", "0.50", "0.75", "0.95"]:
        return float(p.get(f"quantile_{quantile}"))
    raise ValueError(f"Invalid quantile: {quantile}")


def print_shifts_by_route(shift_files: List[str]):
    """Print the list of shifts selected, divided by route."""
    route_shifts = {}
    
    for path in shift_files:
        filename = os.path.basename(path)
        shift_name = filename.replace('.json', '')
        
        # Extract route from shift name (first 3 numbers after the _)
        parts = shift_name.split('_')
        if len(parts) >= 2:
            route = parts[1][:3]  # First 3 characters after the underscore
        else:
            route = "unknown"
        
        if route not in route_shifts:
            route_shifts[route] = []
        route_shifts[route].append(shift_name)
    
    print(f"\nSelected shifts by route ({len(shift_files)} total):")
    for route in sorted(route_shifts.keys()):
        shifts = sorted(route_shifts[route])
        print(f"  Route {route}: {len(shifts)} shifts")
        for shift in shifts:
            print(f"    - {shift}")
    print()


def optimize_cp_lugano_centro_fast_highs(
    shift_dir: str,
    consumption_dir: str,
    cost_cps: dict,
    min_soc: float,
    max_soc: float,
    min_session_duration: int = 0,
    session_penalty_weight: float = 0.0,
    early_charging_weight: float = 0.0,
    quantile_consumption: str = "mean",
    lock_entire_dwell: bool = False,
    day_of_week: str = "mon-fri",
):
    # Inputs and preprocessing
    bus_config = load_bus_config("playground/tpl/batch_config_all_shifts.json")
    shift_files = identify_lugano_centro_files(shift_dir)
    
    # Print shifts by route
    print_shifts_by_route(shift_files)
    
    stations = identify_stations(shift_files)
    station_to_idx = {s: i for i, s in enumerate(stations)}
    first_t, last_t = compute_time_bounds(shift_files)

    print(f"\nStations ({len(stations)} total):")
    for s in stations:
        print(f"  - {s}")

    num_steps = last_t - first_t + 1
    num_buses = len(shift_files)
    dt = 1.0 / 60.0

    # Per-bus data
    presence_mask = np.zeros((num_steps, num_buses), dtype=int)
    station_at_minute = -np.ones((num_steps, num_buses), dtype=int)
    discharge_events = np.zeros((num_steps, num_buses), dtype=float)
    battery_capacity = np.zeros(num_buses, dtype=float)
    max_power = np.zeros(num_buses, dtype=float)
    shift_names = []
    drive_events_by_bus = [[] for _ in range(num_buses)]

    # Track dwell segments per bus for optional CP locking during entire dwell
    dwell_segments_by_bus: list[list[tuple[int, int]]] = [list() for _ in range(len(shift_files))]

    for b_idx, path in enumerate(shift_files):
        filename = os.path.basename(path)
        shift_name = filename.replace('.json', '')
        shift_names.append(shift_name)

        parts = shift_name.split('_')
        route_cfg = None
        if len(parts) >= 2:
            bus_type = parts[1][:3]
            route_cfg = bus_config.get("routes", {}).get(bus_type)
        cap = (route_cfg or {}).get("battery_capacity_kwh", bus_config.get("default", {}).get("battery_capacity_kwh", 514))
        pmax = (route_cfg or {}).get("max_charging_power_kw", bus_config.get("default", {}).get("max_charging_power_kw", 350))
        battery_capacity[b_idx] = float(cap)
        max_power[b_idx] = float(pmax)

        with open(path, 'r') as f:
            data = json.load(f)
        cmap = load_consumption_map(consumption_dir, shift_name)
        if not isinstance(data, list):
            continue
        trips = []
        for trip in data:
            if not isinstance(trip, dict):
                continue
            arr = time_to_minutes(trip.get("arrival_time"))
            dep = time_to_minutes(trip.get("departure_time"))
            if arr is None or dep is None:
                continue
            end_station = trip.get("end_stop_name")
            energy = get_consumption_from_map(cmap, trip.get("id"), quantile=quantile_consumption)
            trips.append({
                "arrival": arr,
                "departure": dep,
                "end_station": end_station,
                "energy": float(energy),
            })
        if not trips:
            continue
        trips.sort(key=lambda r: (r["arrival"], r["departure"]))

        for t in trips:
            t_idx = t["arrival"] - first_t
            if 0 <= t_idx < num_steps:
                discharge_events[t_idx, b_idx] += t["energy"]
            drive_events_by_bus[b_idx].append({
                "departure": t["departure"],
                "arrival": t["arrival"],
                "energy": t["energy"],
            })
        for i in range(len(trips) - 1):
            arr_i = trips[i]["arrival"]
            dep_next = trips[i + 1]["departure"]
            st = trips[i]["end_station"]
            if st in station_to_idx and dep_next > arr_i:
                s_idx = station_to_idx[st]
                start = max(0, arr_i - first_t)
                end = max(0, dep_next - first_t)
                presence_mask[start:end, b_idx] = 1
                station_at_minute[start:end, b_idx] = s_idx
                dwell_segments_by_bus[b_idx].append((start, end))  # [start, end)

    # Build Pyomo model
    m = ConcreteModel()

    # Index sets
    m.T = Set(initialize=range(num_steps))
    m.T1 = Set(initialize=range(num_steps + 1))  # for SOC
    m.B = Set(initialize=range(num_buses))
    m.S = Set(initialize=range(len(stations)))

    # Installation slots per station (ragged): store valid (s,k) pairs
    station_slot_costs = []  # list of np.array per station
    install_index: list[tuple[int, int]] = []
    for s_idx, s_name in enumerate(stations):
        costs = cost_cps.get(s_name, [])
        arr = np.array(costs, dtype=float)
        station_slot_costs.append(arr)
        for k in range(len(costs)):
            install_index.append((s_idx, k))

    m.InstallIndex = Set(dimen=2, initialize=install_index)
    # Costs as Param over InstallIndex
    def init_costs(mdl, s, k):
        return float(station_slot_costs[s][k])

    m.install_cost = Param(m.InstallIndex, initialize=init_costs, mutable=False)
    m.install = Var(m.InstallIndex, domain=Binary)

    # Decision variables
    m.connect = Var(m.T, m.B, domain=Binary)
    m.power = Var(m.T, m.B, domain=NonNegativeReals)
    m.soc = Var(m.T1, m.B, domain=Reals)
    m.start_session = Var(m.T, m.B, domain=Binary)

    # Presence constraints: connect <= presence_mask
    def presence_rule(mdl, t, b):
        return mdl.connect[t, b] <= int(presence_mask[t, b])

    m.presence_con = Constraint(m.T, m.B, rule=presence_rule)

    # Station capacity constraints per minute
    # Precompute buses_here per (t, s)
    buses_here: dict[tuple[int, int], list[int]] = {}
    for t in range(num_steps):
        for s_idx in range(len(stations)):
            buses_here[(t, s_idx)] = [b for b in range(num_buses) if station_at_minute[t, b] == s_idx]

    def station_capacity_rule(mdl, t, s):
        # capacity = sum of installed slots at station s
        cap = 0
        # If station has slots
        for (ss, kk) in mdl.InstallIndex:
            if ss == s:
                cap = cap + mdl.install[ss, kk]
        # sum of connects for buses present at s at time t
        if buses_here.get((t, s)):
            return sum(mdl.connect[t, b] for b in buses_here[(t, s)]) <= cap
        else:
            # No buses; vacuous: 0 <= cap
            return Constraint.Skip

    m.station_capacity = Constraint(m.T, m.S, rule=station_capacity_rule)

    # Installation slot ordering: install[s,k] <= install[s,k-1]
    def slot_order_rule(mdl, s, k):
        if k == 0:
            return Constraint.Skip
        if (s, k) in mdl.InstallIndex and (s, k - 1) in mdl.InstallIndex:
            return mdl.install[s, k] <= mdl.install[s, k - 1]
        return Constraint.Skip

    m.slot_order = Constraint(m.InstallIndex, rule=lambda mdl, s, k: slot_order_rule(mdl, s, k))

    # Power bound and SOC dynamics
    def p_bound_rule(mdl, t, b):
        return mdl.power[t, b] <= float(max_power[b]) * mdl.connect[t, b]

    m.p_bound = Constraint(m.T, m.B, rule=p_bound_rule)

    def soc_init_rule(mdl, b):
        return mdl.soc[0, b] == float(battery_capacity[b]) * float(max_soc)

    m.soc_init = Constraint(m.B, rule=soc_init_rule)

    def soc_min_rule(mdl, t, b):
        return mdl.soc[t, b] >= float(battery_capacity[b]) * float(min_soc)

    def soc_max_rule(mdl, t, b):
        return mdl.soc[t, b] <= float(battery_capacity[b]) * float(max_soc)

    m.soc_min = Constraint(m.T1, m.B, rule=soc_min_rule)
    m.soc_max = Constraint(m.T1, m.B, rule=soc_max_rule)

    def soc_dyn_rule(mdl, t, b):
        return mdl.soc[t + 1, b] == mdl.soc[t, b] - float(discharge_events[t, b]) + float(dt) * mdl.power[t, b]

    m.soc_dyn = Constraint(m.T, m.B, rule=soc_dyn_rule)

    # Session starts and min-session-duration
    # Allowed start mask
    allowed_start_mask = None
    if min_session_duration and min_session_duration > 0:
        allowed_start_mask = np.zeros((num_steps, num_buses))
        for b in range(num_buses):
            pres = presence_mask[:, b].astype(int)
            if num_steps >= min_session_duration:
                window = np.convolve(pres, np.ones(min_session_duration, dtype=int), mode='valid')
                allowed = (window == min_session_duration).astype(int)
                allowed_start_mask[:allowed.shape[0], b] = allowed
            allowed_start_mask[:, b] = np.minimum(allowed_start_mask[:, b], pres)

        def start_mask_rule(mdl, t, b):
            return mdl.start_session[t, b] <= int(allowed_start_mask[t, b])

        m.start_mask = Constraint(m.T, m.B, rule=start_mask_rule)

    def start0_rule(mdl, b):
        return mdl.start_session[0, b] == mdl.connect[0, b]

    m.start0 = Constraint(m.B, rule=start0_rule)

    def start_lb_rule(mdl, t, b):
        if t == 0:
            return Constraint.Skip
        return mdl.start_session[t, b] >= mdl.connect[t, b] - mdl.connect[t - 1, b]

    def start_ub1_rule(mdl, t, b):
        if t == 0:
            return Constraint.Skip
        return mdl.start_session[t, b] <= mdl.connect[t, b]

    def start_ub2_rule(mdl, t, b):
        if t == 0:
            return Constraint.Skip
        return mdl.start_session[t, b] <= 1 - mdl.connect[t - 1, b]

    m.start_lb = Constraint(m.T, m.B, rule=start_lb_rule)
    m.start_ub1 = Constraint(m.T, m.B, rule=start_ub1_rule)
    m.start_ub2 = Constraint(m.T, m.B, rule=start_ub2_rule)

    if min_session_duration and min_session_duration > 0:
        def min_sess_rule(mdl, t, b):
            if t > num_steps - min_session_duration:
                return Constraint.Skip
            return sum(mdl.connect[tau, b] for tau in range(t, t + min_session_duration)) >= min_session_duration * mdl.start_session[t, b]

        m.min_sess = Constraint(m.T, m.B, rule=min_sess_rule)

    # Optional: lock a CP for the full dwell if a bus is connected at any time during that dwell.
    # Enforce connect to be constant across each dwell segment.
    if lock_entire_dwell:
        # Build an index of (b, t) pairs where t is within a dwell and has a predecessor also in the same dwell
        lock_pairs: list[tuple[int, int]] = []
        for b in range(num_buses):
            for (start, end) in dwell_segments_by_bus[b]:
                for t in range(start + 1, end):
                    lock_pairs.append((b, t))

        m.LockIndex = Set(dimen=2, initialize=lock_pairs)

        def lock_rule(mdl, b, t):
            return mdl.connect[t, b] == mdl.connect[t - 1, b]

        m.lock_dwell = Constraint(m.LockIndex, rule=lock_rule)

    # Objective
    time_weights = np.arange(num_steps, dtype=float)
    time_weights = time_weights / max(1.0, float(num_steps - 1))

    def obj_rule(mdl):
        install_cost_term = sum(mdl.install[s, k] * m.install_cost[s, k] for (s, k) in mdl.InstallIndex)
        session_term = 0.0
        if session_penalty_weight and session_penalty_weight > 0:
            session_term = float(session_penalty_weight) * sum(mdl.start_session[t, b] for t in mdl.T for b in mdl.B)
        early_term = 0.0
        if early_charging_weight and early_charging_weight > 0:
            early_term = float(early_charging_weight) * sum(float(time_weights[t]) * sum(mdl.connect[t, b] for b in mdl.B) for t in mdl.T)
        return install_cost_term + session_term + early_term

    m.obj = Objective(rule=obj_rule, sense=minimize)

    # Solve with HiGHS
    solver = SolverFactory("highs")
    # Tight tolerances similar to Gurobi settings
    # Note: option names follow HiGHS parameters. These may vary by version.
    solver.options["mip_rel_gap"] = 0.0
    solver.options["mip_abs_gap"] = 0.0
    solver.options["mip_feasibility_tolerance"] = 1e-9
    solver.options["primal_feasibility_tolerance"] = 1e-9
    solver.options["dual_feasibility_tolerance"] = 1e-9

    results = solver.solve(m, tee=True)
    print(f"Solved. Solver termination: {results.solver.termination_condition}")
    try:
        print(f"Objective value: {value(m.obj):.9f}")
    except Exception:
        pass

    # Extract results
    installed_by_station: Dict[str, int] = {}
    total_installation_cost = 0.0
    for s_idx, s_name in enumerate(stations):
        # count slots installed
        costs = station_slot_costs[s_idx]
        nslots = len(costs)
        count = 0
        for k in range(nslots):
            if (s_idx, k) in m.InstallIndex:
                v = m.install[s_idx, k].value
                if v is not None and v >= 0.5:
                    count += 1
        installed_by_station[s_name] = int(count)
        if nslots > 0 and count > 0:
            total_installation_cost += float(np.sum(costs[:count]))

    print("Installed chargers per station:")
    for s_name, n in installed_by_station.items():
        print(f"  - {s_name}: {n}")

    # Build arrays for exports
    connect_val = np.zeros((num_steps, num_buses))
    power_val = np.zeros((num_steps, num_buses))
    soc_val = np.zeros((num_steps + 1, num_buses))
    for t in range(num_steps):
        for b in range(num_buses):
            connect_val[t, b] = float(m.connect[t, b].value or 0.0)
            power_val[t, b] = float(m.power[t, b].value or 0.0)
    for t in range(num_steps + 1):
        for b in range(num_buses):
            soc_val[t, b] = float(m.soc[t, b].value or 0.0)

    # Compute objective component values (print sub-objectives)
    installed_sum = int(sum(installed_by_station.values()))

    sessions_sum = 0.0
    for t in range(num_steps):
        for b in range(num_buses):
            sessions_sum += float(m.start_session[t, b].value or 0.0)

    time_weights = np.arange(num_steps, dtype=float)
    time_weights = time_weights / max(1.0, float(num_steps - 1))
    early_unscaled = 0.0
    for t in range(num_steps):
        early_unscaled += float(time_weights[t]) * sum(float(m.connect[t, b].value or 0.0) for b in range(num_buses))
    session_term_val = float(session_penalty_weight) * float(sessions_sum) if session_penalty_weight and session_penalty_weight > 0 else 0.0
    early_term_val = float(early_charging_weight) * float(early_unscaled) if early_charging_weight and early_charging_weight > 0 else 0.0

    print("Objective components:")
    print(f"  - Installed chargers (sum): {installed_sum}")
    print(f"  - Installation cost (primary term): {total_installation_cost:.6f}")
    if session_penalty_weight and session_penalty_weight > 0:
        print(f"  - Session starts (count): {int(round(sessions_sum))}")
        print(f"  - Session term (weighted): {session_term_val:.6f}")
    if early_charging_weight and early_charging_weight > 0:
        print(f"  - Early charging term (unscaled): {early_unscaled:.6f}")
        print(f"  - Early charging term (weighted): {early_term_val:.6f}")

    # Persist results (use a different prefix to avoid confusion)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join("playground", "tpl", "results", f"fast_highs_pyomo_optimization_{timestamp}")
    os.makedirs(results_dir, exist_ok=True)

    # Log input parameters for analysis
    input_parameters = {
        "day_of_week": day_of_week,
        "shift_dir": shift_dir,
        "consumption_dir": consumption_dir,
        "min_soc": float(min_soc),
        "max_soc": float(max_soc),
        "min_session_duration": int(min_session_duration),
        "session_penalty_weight": float(session_penalty_weight),
        "early_charging_weight": float(early_charging_weight),
        "quantile_consumption": quantile_consumption,
        "lock_entire_dwell": lock_entire_dwell,
        "cost_cps": cost_cps,
        "timestamp": timestamp,
        "num_buses": int(num_buses),
        "num_stations": int(len(stations)),
        "time_range_minutes": [int(first_t), int(last_t)],
        "stations": stations,
    }
    with open(os.path.join(results_dir, "input_parameters.json"), "w") as f:
        json.dump(input_parameters, f, indent=2, ensure_ascii=False)

    with open(os.path.join(results_dir, "installed_chargers_by_station.json"), "w") as f:
        json.dump(installed_by_station, f, indent=2, ensure_ascii=False)

    # Station summary (peak and avg util)
    station_rows = []
    for s_idx, s_name in enumerate(stations):
        installed = int(installed_by_station.get(s_name, 0))
        costs_vec = station_slot_costs[s_idx]
        install_cost = float(np.sum(costs_vec[:installed])) if installed > 0 and costs_vec.size > 0 else 0.0
        per_time = []
        for t in range(num_steps):
            buses_here_t = [b for b in range(num_buses) if station_at_minute[t, b] == s_idx]
            if buses_here_t:
                per_time.append(float(np.sum([connect_val[t, b] for b in buses_here_t])))
            else:
                per_time.append(0.0)
        peak = int(max(per_time)) if per_time else 0
        avg_util = float(np.mean(per_time) / installed) if installed > 0 else 0.0
        station_rows.append({
            "station": s_name,
            "installed": installed,
            "installation_cost": install_cost,
            "peak_concurrency": peak,
            "avg_utilization_per_cp": avg_util,
        })
    if station_rows:
        df_install = pd.DataFrame(station_rows)
        df_install.sort_values(by=["installed", "installation_cost"], ascending=[False, True], inplace=True)
        df_install.to_csv(os.path.join(results_dir, "installation_by_station.csv"), index=False)
        with open(os.path.join(results_dir, "installation_by_station.json"), "w") as f:
            json.dump(station_rows, f, indent=2, ensure_ascii=False)

    # Build indices and discharge power for plotting
    time_minutes_power = np.arange(first_t, last_t + 1)
    time_minutes_soc = np.arange(first_t, last_t + 2)
    idx_power = pd.to_datetime(time_minutes_power, unit="m", origin=pd.Timestamp("2026-01-01"))
    idx_soc = pd.to_datetime(time_minutes_soc, unit="m", origin=pd.Timestamp("2026-01-01"))

    discharge_power_plot_kw = np.zeros((num_steps, num_buses))
    for b in range(num_buses):
        for ev in drive_events_by_bus[b]:
            dep_idx = max(0, ev["departure"] - first_t)
            arr_idx = min(num_steps, ev["arrival"] - first_t)
            dur = arr_idx - dep_idx
            if dur and dur > 0:
                kw = (float(ev["energy"]) * 60.0) / float(dur)
                discharge_power_plot_kw[dep_idx:arr_idx, b] += kw

    # Export per-bus power and SOC
    for b in range(num_buses):
        shift_name = shift_names[b]
        pd.Series(power_val[:, b], index=idx_power, name="power_kw").to_csv(os.path.join(results_dir, f"power_{shift_name}.csv"))
        # Smoothed SOC
        sm_soc = np.zeros(num_steps + 1)
        sm_soc[0] = float(soc_val[0, b])
        for t in range(num_steps):
            sm_soc[t + 1] = sm_soc[t] - discharge_power_plot_kw[t, b] * dt + power_val[t, b] * dt
        pd.Series(sm_soc, index=idx_soc, name="soc_kwh").to_csv(os.path.join(results_dir, f"soc_{shift_name}.csv"))

        # Plot power and SOC
        fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
        ax_p, ax_s = axes
        ax_p.plot(idx_power, power_val[:, b], label="Charging Power [kW]", color="tab:green")
        ax_p.plot(idx_power, -discharge_power_plot_kw[:, b], label="Discharging Power [kW]", color="tab:red")
        ax_p.set_ylabel("Power [kW]")
        ax_p.legend(loc="upper right")
        ax_p.grid(True, linestyle=":", alpha=0.5)
        # Annotate charge session starts with station name
        conn = (connect_val[:, b] > 0.5).astype(int)
        starts = np.where((conn[1:] == 1) & (conn[:-1] == 0))[0] + 1
        if conn[0] == 1:
            starts = np.r_[0, starts]
        for t_idx in starts:
            ts = idx_power[t_idx]
            ax_p.axvline(ts, color="tab:green", alpha=0.3, linestyle="--")
            s_idx = int(station_at_minute[t_idx, b])
            if 0 <= s_idx < len(stations):
                station_label = stations[s_idx]
                ax_p.text(ts, ax_p.get_ylim()[1] * 0.9, station_label, rotation=90, color="tab:green", fontsize=8, va="top")
        ax_s.plot(idx_soc, sm_soc, color="tab:orange", label="SOC [kWh]")
        ax_s.set_ylabel("SOC [kWh]")
        ax_s2 = ax_s.twinx()
        if battery_capacity[b] > 0:
            soc_percent = (np.array(sm_soc) / battery_capacity[b]) * 100.0
            ax_s2.plot(idx_soc, soc_percent, color="tab:purple", linestyle="--", label="SOC [%]")
            ax_s2.set_ylabel("SOC [%]")
        lines1, labels1 = ax_s.get_legend_handles_labels()
        lines2, labels2 = ax_s2.get_legend_handles_labels()
        ax_s.legend(lines1 + lines2, labels1 + labels2, loc="upper right")
        ax_s.grid(True, linestyle=":", alpha=0.5)
        fig.suptitle(f"Shift {shift_name}: Power and SOC")
        fig.align_labels()
        fig.autofmt_xdate()
        plt.savefig(os.path.join(results_dir, f"power_soc_{shift_name}.png"), bbox_inches="tight")
        plt.close(fig)

        # Sessions
        sessions_rows = []
        conn = (connect_val[:, b] > 0.5).astype(int)
        starts = np.where((conn[1:] == 1) & (conn[:-1] == 0))[0] + 1
        if conn[0] == 1:
            starts = np.r_[0, starts]
        ends = np.where((conn[1:] == 0) & (conn[:-1] == 1))[0]
        if conn[-1] == 1:
            ends = np.r_[ends, len(conn) - 1]
        for s_i, e_i in zip(starts, ends):
            dur = int(e_i - s_i + 1)
            s_idx = station_at_minute[s_i, b]
            station_label = stations[int(s_idx)] if s_idx >= 0 else None
            energy_kwh = float(np.sum(power_val[s_i:e_i + 1, b]) * dt)
            sessions_rows.append({
                "shift": shift_name,
                "start_time": str(idx_power[s_i]),
                "end_time": str(idx_power[e_i]),
                "duration_min": dur,
                "station": station_label,
                "energy_kwh": energy_kwh,
            })
        if sessions_rows:
            pd.DataFrame(sessions_rows).to_csv(os.path.join(results_dir, f"sessions_{shift_name}.csv"), index=False)

    # Combined grid of all shifts
    ncols = 3
    nrows = int(np.ceil(num_buses / ncols)) if num_buses > 0 else 1
    fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 6, nrows * 4), sharex=True)
    if nrows == 1 and ncols == 1:
        axes = np.array([[axes]])
    elif nrows == 1:
        axes = np.array([axes])
    for b in range(num_buses):
        r = b // ncols
        c = b % ncols
        ax = axes[r, c]
        power_series = pd.Series(power_val[:, b], index=idx_power)
        dis_series = pd.Series(discharge_power_plot_kw[:, b], index=idx_power)
        sm_soc = np.zeros(num_steps + 1)
        sm_soc[0] = float(soc_val[0, b])
        for t in range(num_steps):
            sm_soc[t + 1] = sm_soc[t] - float(dis_series.iloc[t]) * dt + float(power_series.iloc[t]) * dt
        soc_series = pd.Series(sm_soc, index=idx_soc)
        ax.plot(power_series.index, power_series.values, color="tab:green", label="Charge [kW]")
        ax.plot(dis_series.index, -dis_series.values, color="tab:red", label="Discharge [kW]")
        ax.set_title(shift_names[b])
        ax.grid(True, linestyle=":", alpha=0.35)
        ax2 = ax.twinx()
        if battery_capacity[b] > 0:
            soc_percent = (soc_series.values / battery_capacity[b]) * 100.0
            ax2.plot(soc_series.index, soc_percent, color="tab:orange", label="SOC [%]")
            ax2.set_ylabel("SOC [%]")
        lines, labels = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines + lines2, labels + labels2, loc="upper right", fontsize=8)
    total_plots = nrows * ncols
    for k in range(num_buses, total_plots):
        r = k // ncols
        c = k % ncols
        fig.delaxes(axes[r, c])
    fig.suptitle("All Shifts: Power and SOC")
    fig.autofmt_xdate()
    plt.savefig(os.path.join(results_dir, "all_shifts_power_soc.png"), bbox_inches="tight")
    plt.close(fig)

    # Per-station per-CP power (assign buses to CP slots deterministically)
    for s_idx, s_name in enumerate(stations):
        num_cps = int(installed_by_station.get(s_name, 0))
        if num_cps <= 0:
            continue
        cp_power = np.zeros((num_steps, num_cps), dtype=float)
        for t in range(num_steps):
            active_buses = [b for b in range(num_buses) if station_at_minute[t, b] == s_idx and connect_val[t, b] > 0.5]
            active_buses.sort()
            for j, b in enumerate(active_buses[:num_cps]):
                cp_power[t, j] = power_val[t, b]
        df_cp = pd.DataFrame(cp_power, index=idx_power, columns=[f"cp_{i+1}_kw" for i in range(num_cps)])
        safe = s_name.replace(",", "").replace(" ", "_").replace("/", "_")
        df_cp.to_csv(os.path.join(results_dir, f"station_{safe}_cp_power.csv"))
        fig, ax = plt.subplots(1, 1, figsize=(14, 5))
        for i in range(num_cps):
            ax.plot(df_cp.index, df_cp.iloc[:, i], label=f"CP {i+1}")
        ax.set_title(f"{s_name}: Power per Charging Point")
        ax.set_ylabel("kW")
        ax.grid(True, linestyle=":", alpha=0.5)
        ax.legend(ncol=min(4, num_cps), loc="upper right")
        plt.savefig(os.path.join(results_dir, f"station_{safe}_cp_power.png"), bbox_inches="tight")
        plt.close(fig)

    # Aggregate sessions by station
    station_sessions = {}
    for f_name in os.listdir(results_dir):
        if f_name.startswith("sessions_") and f_name.endswith(".csv"):
            df_s = pd.read_csv(os.path.join(results_dir, f_name))
            for _, row in df_s.iterrows():
                station = row.get("station")
                if pd.isna(station):
                    continue
                station_sessions.setdefault(station, {"sessions": 0, "total_duration_min": 0, "total_energy_kwh": 0.0})
                station_sessions[station]["sessions"] += 1
                station_sessions[station]["total_duration_min"] += int(row.get("duration_min", 0))
                station_sessions[station]["total_energy_kwh"] += float(row.get("energy_kwh", 0.0))
    if station_sessions:
        pd.DataFrame([{ "station": k, **v } for k, v in station_sessions.items()]) \
            .sort_values(by=["sessions", "total_duration_min"], ascending=[False, False]) \
            .to_csv(os.path.join(results_dir, "sessions_by_station.csv"), index=False)

    # Run parameters
    run_params = {
        "min_soc": float(min_soc),
        "max_soc": float(max_soc),
        "min_session_duration": int(min_session_duration),
        "session_penalty_weight": float(session_penalty_weight),
        "early_charging_weight": float(early_charging_weight),
        "installed_sum": int(installed_sum),
        "installation_cost": float(total_installation_cost),
        "sessions_sum": int(round(sessions_sum)),
        "early_penalty_unscaled": float(early_unscaled),
    }
    with open(os.path.join(results_dir, "run_parameters.json"), "w") as f:
        json.dump(run_params, f, indent=2)

    print(f"Fast-HiGHS (Pyomo) results saved under: {results_dir}")
    return installed_by_station


def main():
    parser = argparse.ArgumentParser(description='Optimize charging point placement for Lugano Centro')
    
    # Day of week parameter
    parser.add_argument('--day-of-week', 
                       choices=['mon-fri', 'sat', 'sun'], 
                       default='mon-fri',
                       help='Day of week for simulation (default: mon-fri)')
    
    # SOC parameters
    parser.add_argument('--min-soc', 
                       type=float, 
                       default=0.4,
                       help='Minimum state of charge (default: 0.4)')
    
    parser.add_argument('--max-soc', 
                       type=float, 
                       default=0.9,
                       help='Maximum state of charge (default: 0.9)')
    
    # Session parameters
    parser.add_argument('--min-session-duration', 
                       type=int, 
                       default=2,
                       help='Minimum charging session duration in minutes (default: 2)')
    
    parser.add_argument('--session-penalty-weight', 
                       type=float, 
                       default=0.01,
                       help='Weight for session penalty in objective (default: 0.01)')
    
    parser.add_argument('--early-charging-weight', 
                       type=float, 
                       default=0.0,
                       help='Weight for early charging penalty in objective (default: 0.0)')
    
    # Consumption prediction parameters
    parser.add_argument('--quantile-consumption', 
                       choices=['mean', 'median', '0.05', '0.25', '0.50', '0.75', '0.95'], 
                       default='0.95',
                       help='Quantile for consumption prediction (default: 0.95)')
    
    # Feature flags
    parser.add_argument('--lock-entire-dwell', 
                       action='store_true', 
                       default=True,
                       help='Lock charging point for entire dwell period (default: True)')
    
    parser.add_argument('--no-lock-entire-dwell', 
                       dest='lock_entire_dwell', 
                       action='store_false',
                       help='Disable locking charging point for entire dwell period')
    
    args = parser.parse_args()
    
    # Directory mapping
    dirs = {
        'mon-fri': '2026-TM_15f_lu-ve_TM_json', 
        'sat': '2026-TM_6f_Sa_TM_json', 
        'sun': '2026-TM_7+_Do_TM_json'
    }
    
    shift_dir = f"playground/tpl/turni_macchina_2026/{dirs[args.day_of_week]}"
    consumption_dir = f"playground/tpl/predctions/{dirs[args.day_of_week]}"
    
    # Cost configuration for charging points
    cost_cps = {
        'Brè, Paese': [10],
        'Canobbio, Ganna': [1],
        'Comano, Studio TV': [1],
        'Lugano, Centro': [1, 0.3, 0.3, 0.3],
        'Lugano, Cornaredo': [1],
        'Lugano, Pista Ghiaccio': [1],
        'Pazzallo, P+R Fornaci': [1],
        'Piano Stampa, Capolinea': [1],
        'Pregassona, Piazza di Giro': [10],
    }
    
    print(f"Running optimization with parameters:")
    print(f"  Day of week: {args.day_of_week}")
    print(f"  Min SOC: {args.min_soc}")
    print(f"  Max SOC: {args.max_soc}")
    print(f"  Min session duration: {args.min_session_duration} minutes")
    print(f"  Session penalty weight: {args.session_penalty_weight}")
    print(f"  Early charging weight: {args.early_charging_weight}")
    print(f"  Quantile consumption: {args.quantile_consumption}")
    print(f"  Lock entire dwell: {args.lock_entire_dwell}")
    print(f"  Shift directory: {shift_dir}")
    print(f"  Consumption directory: {consumption_dir}")
    print()

    optimize_cp_lugano_centro_fast_highs(
        shift_dir=shift_dir,
        consumption_dir=consumption_dir,
        cost_cps=cost_cps,
        min_soc=args.min_soc,
        max_soc=args.max_soc,
        min_session_duration=args.min_session_duration,
        session_penalty_weight=args.session_penalty_weight,
        early_charging_weight=args.early_charging_weight,
        quantile_consumption=args.quantile_consumption,
        lock_entire_dwell=args.lock_entire_dwell,
        day_of_week=args.day_of_week,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
