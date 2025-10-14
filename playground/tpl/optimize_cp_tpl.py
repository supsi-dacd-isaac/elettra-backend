import cvxpy as cp
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
import glob
import os
import json


def get_trip_consumption(consumption_dir: str, shift_name: str, trip_id: str, quantile: str = "mean"):
    """
    Load consumption data for a specific trip from the consumption directory.
    
    Args:
        consumption_dir: Directory containing consumption prediction files
        shift_name: Name of the shift (e.g., "00_40101__part1")
        trip_id: ID of the trip to get consumption for
        quantile: Which quantile to return ("mean", "median", "0.05", "0.25", "0.50", "0.75", "0.95")
    
    Returns:
        Consumption value in kWh, or None if not found
    """
    # Construct the filename for the consumption file
    consumption_file = os.path.join(consumption_dir, f"{shift_name}_predictions.json")
    
    with open(consumption_file, 'r') as f:
        consumption_data = json.load(f)
    
    # Find the trip in the predictions
    for prediction in consumption_data.get("predictions", []):
        if prediction.get("trip_id") == trip_id:
            # Return the requested quantile
            if quantile == "mean":
                return prediction.get("prediction_kwh")
            elif quantile == "median":
                return prediction.get("prediction_median_kwh")
            elif quantile in ["0.05", "0.25", "0.50", "0.75", "0.95"]:
                return prediction.get(f"quantile_{quantile}")
            else:
                raise ValueError(f"Invalid quantile: {quantile}")
    else:
        raise ValueError(f"Trip {trip_id} not found in {consumption_file}")


def identify_lugano_centro_files(shift_dir: str):
    shift_files = glob.glob(os.path.join(shift_dir, "*.json"))
    lugano_centro_files = []
    for shift_file in shift_files:
        with open(shift_file, 'r') as f:
            shift_data = json.load(f)
            if isinstance(shift_data, list):
                if any(trip.get("end_stop_name") == "Lugano, Centro" for trip in shift_data if isinstance(trip, dict)):
                    lugano_centro_files.append(shift_file)
    return lugano_centro_files

def identify_stations(shift_files: list):
    unique_stations = set()
    for shift_file in shift_files:
        with open(shift_file, 'r') as f:
            shift_data = json.load(f)
            if isinstance(shift_data, list):
                for trip in shift_data:
                    if isinstance(trip, dict):
                        unique_stations.add(trip.get("start_stop_name"))
                        unique_stations.add(trip.get("end_stop_name"))
    filtered_stations = {station for station in unique_stations if "Rimessa" not in station}
    return list(filtered_stations)


def time_to_minutes(time_str: str) -> int:
    """
    Convert time string (HH:MM:SS) to minutes from midnight.
    Handles GTFS times that can exceed 24 hours (e.g., 25:30:00 = 1530 minutes).
    
    Args:
        time_str: Time string in format "HH:MM:SS"
    
    Returns:
        int: Minutes from midnight
    """
    if not time_str:
        return None
    
    try:
        hours, minutes, seconds = map(int, time_str.split(':'))
        total_minutes = hours * 60 + minutes
        return total_minutes
    except (ValueError, AttributeError):
        return None


def identify_first_and_last_departure(shift_files: list):
    """
    Find the global earliest and latest times (considering both arrivals and departures)
    across all shift files. Converts times to minutes from midnight (can exceed 1440 for GTFS).
    
    Returns:
        tuple: (first_time_minutes, last_time_minutes) as integers
    """
    first_time = None
    last_time = None
    
    for shift_file in shift_files:
        with open(shift_file, 'r') as f:
            shift_data = json.load(f)
            if isinstance(shift_data, list):
                for trip in shift_data:
                    if isinstance(trip, dict):
                        arrival_time_str = trip.get("arrival_time")
                        departure_time_str = trip.get("departure_time")
                        
                        if arrival_time_str:
                            arrival_minutes = time_to_minutes(arrival_time_str)
                            if arrival_minutes is not None:
                                if first_time is None or arrival_minutes < first_time:
                                    first_time = arrival_minutes
                                if last_time is None or arrival_minutes > last_time:
                                    last_time = arrival_minutes
                        
                        if departure_time_str:
                            departure_minutes = time_to_minutes(departure_time_str)
                            if departure_minutes is not None:
                                if first_time is None or departure_minutes < first_time:
                                    first_time = departure_minutes
                                if last_time is None or departure_minutes > last_time:
                                    last_time = departure_minutes
    
    return first_time, last_time


def load_bus_config(config_file: str) -> dict:
    """
    Load bus configuration from batch config file.
    
    Args:
        config_file: Path to the batch config JSON file
    
    Returns:
        dict: Bus configuration data
    """
    with open(config_file, 'r') as f:
        config_data = json.load(f)
    return config_data


def get_bus_battery_capacity_and_max_charging_power(shift_name: str, bus_config: dict) -> tuple[float, float]:
    """
    Get battery capacity and max charging power for a bus based on shift name.
    
    Args:
        shift_name: Shift name like '30_40405__part2'
        bus_config: Bus configuration data
    
    Returns:
        tuple: (battery_capacity_kwh, max_charging_power_kw)
    """
    # Extract bus type (first 3 digits after underscore)
    # e.g., '30_40405__part2' -> '404'
    parts = shift_name.split('_')
    if len(parts) >= 2:
        bus_type = parts[1][:3]  # First 3 digits of the second part
        if bus_type in bus_config.get("routes", {}):
            route_config = bus_config["routes"][bus_type]
            return (
                route_config["battery_capacity_kwh"],
                route_config["max_charging_power_kw"]
            )
    
    # Fallback to default
    default_config = bus_config.get("default", {})
    return (
        default_config.get("battery_capacity_kwh", 514),
        default_config.get("max_charging_power_kw", 514)
    )


def optimize_cp_lugano_centro(
    shift_dir: str,
    consumption_dir: str,
    cost_cps: dict,
    min_soc: float,
    max_soc: float,
    min_session_duration: int = 0,
    session_penalty_weight: float = 0.0,
    early_charging_weight: float = 0.0,
    quantile_consumption: str = "mean",
):
    # Load bus configuration
    bus_config = load_bus_config("playground/tpl/batch_config_all_shifts.json")
    
    # identify the data files that have Lugano Centro as end station at least once
    shift_files = identify_lugano_centro_files(shift_dir)
    first_arrival_time, last_departure_time = identify_first_and_last_departure(shift_files)
    stations = identify_stations(shift_files)
    
    print(f"\nStations ({len(stations)} total):")
    for station in sorted(stations):
        print(f"  - {station}")

    num_steps = last_departure_time - first_arrival_time + 1
    cp_state = cp.Variable((num_steps, len(stations), len(shift_files)), "charging point state", boolean=True)
    charging_power = cp.Variable((num_steps, len(stations), len(shift_files)), "charging power", nonneg=True)
    soc = cp.Variable((num_steps+1, len(shift_files)), "SOC", nonneg=True)
    # Connection indicator per bus (1 if charging at any station)
    connect = cp.Variable((num_steps, len(shift_files)), name="connect", boolean=True)
    # Start of charging session per bus
    start_session = cp.Variable((num_steps, len(shift_files)), name="start_session", boolean=True)
    time_step_hours = 1.0 / 60.0
    constraints = []
    # Installation variables per station (slot-based) and capacity coupling
    install_cp_vars_by_station = {}
    install_cost_vectors_by_station = {}
    for s_idx, station_name in enumerate(stations):
        costs_for_station = cost_cps.get(station_name, [])
        num_slots = len(costs_for_station)
        install_cost_vectors_by_station[s_idx] = np.array(costs_for_station, dtype=float) if num_slots > 0 else np.array([], dtype=float)
        if num_slots > 0:
            install_cp_vars_by_station[s_idx] = cp.Variable(num_slots, boolean=True, name=f"install_cp_{s_idx}")
            # Coupling: concurrent chargers cannot exceed installed slots
            for t in range(num_steps):
                constraints.append(cp.sum(cp_state[t, s_idx, :]) <= cp.sum(install_cp_vars_by_station[s_idx]))
        else:
            install_cp_vars_by_station[s_idx] = None
            # No installation allowed -> enforce zero concurrent chargers
            for t in range(num_steps):
                constraints.append(cp.sum(cp_state[t, s_idx, :]) <= 0)

    # Build discharging power profiles (kW) for plotting: constant during driving for each trip
    discharge_power_plot_kw = np.zeros((num_steps, len(shift_files)))
    # Track presence per bus and allowable start mask for min-session-duration
    presence_any_by_bus = np.zeros((num_steps, len(shift_files)))
    # Track battery capacity per bus for SOC percent plotting
    battery_capacity_by_bus = np.zeros(len(shift_files))

    for shift_file in shift_files:
        # Extract shift name from filename (e.g., "00_40101__part1.json" -> "00_40101__part1")
        filename = os.path.basename(shift_file)
        shift_name = filename.replace('.json', '')
        bus_idx = shift_files.index(shift_file)
        
        print(f"Processing shift: {shift_name}")
        
        # create discharge events and presence at stations
        with open(shift_file, 'r') as f:
            discharge_events = np.zeros(num_steps)
            presence_at_stations = np.zeros((num_steps, len(stations)))
            bus_events = []
            shift_data = json.load(f)
            if isinstance(shift_data, list):
                for trip in shift_data:
                    if isinstance(trip, dict):
                        end_stop_name = trip["end_stop_name"]
                        arrival_time = time_to_minutes(trip["arrival_time"])
                        departure_time = time_to_minutes(trip["departure_time"])
                        consumption = get_trip_consumption(consumption_dir, shift_name, trip["id"], quantile=quantile_consumption)
                        # Maintain event-style discharge for optimization constraints
                        discharge_events[arrival_time - first_arrival_time] = consumption
                        bus_events.append({
                            "end_stop_name": end_stop_name,
                            "arrival_time": arrival_time,
                            "departure_time": departure_time,
                            "consumption": consumption
                        })

                for i in range(len(bus_events)-1):
                    start_time = bus_events[i]["arrival_time"] - first_arrival_time
                    end_time = bus_events[i+1]["departure_time"] - first_arrival_time
                    presence_at_stations[start_time:end_time, stations.index(bus_events[i]["end_stop_name"])] = 1
                # Save any-station presence for this bus
                presence_any_by_bus[:, bus_idx] = presence_at_stations.max(axis=1)

                # Build constant discharging power across driving intervals for plotting
                for ev in bus_events:
                    dep_idx = max(0, ev["departure_time"] - first_arrival_time)
                    arr_idx = min(num_steps, ev["arrival_time"] - first_arrival_time)
                    duration_min = arr_idx - dep_idx
                    if duration_min is None or duration_min <= 0:
                        # fallback: treat as instantaneous at arrival (no smoothing possible)
                        continue
                    discharge_kw = (ev["consumption"] * 60.0) / duration_min
                    discharge_power_plot_kw[dep_idx:arr_idx, bus_idx] += discharge_kw

                # Initialize SOC data structure
                battery_capacity, max_charging_power = get_bus_battery_capacity_and_max_charging_power(shift_name, bus_config)
                print(f"  - Battery capacity: {battery_capacity} kWh")
                print(f"  - Max charging power: {max_charging_power} kW")
                battery_capacity_by_bus[bus_idx] = float(battery_capacity)

                # SOC limits
                constraints.append(soc[0, bus_idx] == battery_capacity * max_soc)  # Initial SOC = full battery
                constraints.append(soc[:, bus_idx] >= battery_capacity * min_soc)
                constraints.append(soc[:, bus_idx] <= battery_capacity * max_soc)

                # Presence constraints
                constraints.append(cp_state[:, :, bus_idx] <= presence_at_stations)

                for t in range(num_steps):
                    # SOC update for optimization uses event-based discharge to keep the solved optimum consistent
                    constraints.append(
                        soc[t+1, bus_idx]
                        == soc[t, bus_idx]
                        - discharge_events[t]
                        + charging_power[t, :, bus_idx].sum() * time_step_hours
                    )
                    constraints.append(charging_power[t, :, bus_idx] <= cp_state[t, :, bus_idx] * max_charging_power)

    # (Removed) Peak-per-station constraints based on installed_chargers; replaced by install_cp_vars_by_station above

    # Link connect variable to cp_state (connect if charging at any station)
    for b_idx in range(len(shift_files)):
        constraints.append(connect[:, b_idx] == cp.sum(cp_state[:, :, b_idx], axis=1))

    # Optional: at most one station connected per bus and time (should be implied by presence)
    # for b_idx in range(len(shift_files)):
    #     constraints.append(cp.sum(cp_state[:, :, b_idx], axis=1) <= 1)

    # Charging session start logic and minimum session duration
    if min_session_duration and min_session_duration > 0:
        # Build allowable start mask where a full min_session_duration is within presence window
        allowed_start_mask = np.zeros((num_steps, len(shift_files)))
        for b_idx in range(len(shift_files)):
            presence_any = presence_any_by_bus[:, b_idx].astype(int)
            if num_steps >= min_session_duration:
                window_sum = np.convolve(presence_any, np.ones(min_session_duration, dtype=int), mode='valid')
                allowed = (window_sum == min_session_duration).astype(int)
                allowed_start_mask[:allowed.shape[0], b_idx] = allowed
            # Last indices cannot start due to insufficient remaining time
            # Ensure we never start while not present
            allowed_start_mask[:, b_idx] = np.minimum(allowed_start_mask[:, b_idx], presence_any)

        # Enforce mask
        constraints.append(start_session <= allowed_start_mask)

        # Start detection and min duration
        for b_idx in range(len(shift_files)):
            # t=0 start bounds
            constraints.append(start_session[0, b_idx] >= connect[0, b_idx])
            constraints.append(start_session[0, b_idx] <= connect[0, b_idx])
            for t in range(1, num_steps):
                constraints.append(start_session[t, b_idx] >= connect[t, b_idx] - connect[t-1, b_idx])
                constraints.append(start_session[t, b_idx] <= connect[t, b_idx])
                constraints.append(start_session[t, b_idx] <= 1 - connect[t-1, b_idx])
            # Min session duration constraints
            for t in range(0, max(0, num_steps - min_session_duration + 1)):
                constraints.append(cp.sum(connect[t:t+min_session_duration, b_idx]) >= min_session_duration * start_session[t, b_idx])
    else:
        # Define starts even without min duration to allow session penalization
        for b_idx in range(len(shift_files)):
            constraints.append(start_session[0, b_idx] >= connect[0, b_idx])
            constraints.append(start_session[0, b_idx] <= connect[0, b_idx])
            for t in range(1, num_steps):
                constraints.append(start_session[t, b_idx] >= connect[t, b_idx] - connect[t-1, b_idx])
                constraints.append(start_session[t, b_idx] <= connect[t, b_idx])
                constraints.append(start_session[t, b_idx] <= 1 - connect[t-1, b_idx])

    # Objective: minimize installation cost (primary), sessions, and push charging earlier
    installation_cost_term = 0
    for s_idx in range(len(stations)):
        var = install_cp_vars_by_station[s_idx]
        costs = install_cost_vectors_by_station[s_idx]
        if var is not None and costs.size > 0:
            installation_cost_term += cp.sum(cp.multiply(var, costs))
    objective_terms = [installation_cost_term]

    if session_penalty_weight and session_penalty_weight > 0:
        objective_terms.append(session_penalty_weight * cp.sum(start_session))

    if early_charging_weight and early_charging_weight > 0:
        # Weight later connection more heavily to prioritize earlier charging
        time_weights = np.arange(num_steps, dtype=float)
        time_weights = time_weights / max(1.0, float(num_steps - 1))
        objective_terms.append(early_charging_weight * cp.sum(cp.multiply(time_weights.reshape((-1, 1)), connect)))

    objective = cp.Minimize(cp.sum(objective_terms))

    problem = cp.Problem(objective, constraints)

    # Force GUROBI for solving
    print("Solving with GUROBI...")
    problem.solve(solver=cp.GUROBI, verbose=True)
    print(f"Solved with GUROBI. Status: {problem.status}. Objective value: {problem.value}")

    # Report installed chargers per station and total installation cost
    installed_by_station = {}
    total_installation_cost = 0.0
    for s_idx, station_name in enumerate(stations):
        var = install_cp_vars_by_station[s_idx]
        costs = install_cost_vectors_by_station[s_idx]
        if var is not None and var.value is not None:
            installed_int = int(np.rint(np.sum(var.value)))
            installed_by_station[station_name] = installed_int
            # Sum cost using rounded installs to avoid tiny numerical noise
            if costs.size > 0:
                # Sort by slot order; assume first slots are used first
                total_installation_cost += float(np.sum(costs[:installed_int]))
        else:
            installed_by_station[station_name] = 0
    for station_name, installed_int in installed_by_station.items():
        print(f"  - {station_name}: {installed_int}")

    # Breakdown objective components for transparency
    installed_sum = sum(installed_by_station.values())
    print(f"Objective components:")
    print(f"  - Installed chargers (sum): {installed_sum}")
    print(f"  - Installation cost (primary term): {total_installation_cost:.4f}")
    sessions_sum = None
    early_penalty_val = None
    if start_session.value is not None:
        sessions_sum = float(np.sum(np.rint(start_session.value)))
        print(f"  - Session starts (count): {int(sessions_sum)}")
        if session_penalty_weight and session_penalty_weight > 0:
            print(f"  - Session term (weighted): {session_penalty_weight * sessions_sum:.4f}")
    if early_charging_weight and early_charging_weight > 0:
        time_weights = np.arange(num_steps, dtype=float)
        time_weights = time_weights / max(1.0, float(num_steps - 1))
        if connect.value is not None:
            early_penalty_val = float(np.sum(time_weights.reshape((-1, 1)) * connect.value))
            print(f"  - Early charging term (unscaled): {early_penalty_val:.4f}")
            print(f"  - Early charging term (weighted): {early_charging_weight * early_penalty_val:.4f}")

    # Also compute observed peaks as a consistency check (from cp_state)
    if cp_state.value is not None:
        print("Observed peak concurrent chargers per station (from schedule):")
        for s_idx, station_name in enumerate(stations):
            per_time = cp_state.value[:, s_idx, :].sum(axis=1)
            peak = int(np.max(per_time)) if per_time.size else 0
            print(f"  - {station_name}: {peak}")

    # Persist results: create output directory with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join("playground", "tpl", "results", f"optimization_{timestamp}")
    os.makedirs(results_dir, exist_ok=True)

    # Save installed chargers summary
    with open(os.path.join(results_dir, "installed_chargers_by_station.json"), "w") as f:
        json.dump(installed_by_station, f, indent=2, ensure_ascii=False)

    # Save installation details per station (installed count, cost, peak, avg utilization)
    station_rows = []
    for s_idx, station_name in enumerate(stations):
        installed = int(installed_by_station.get(station_name, 0))
        costs_vec = install_cost_vectors_by_station.get(s_idx, np.array([], dtype=float))
        install_cost = float(np.sum(costs_vec[:installed])) if installed > 0 and costs_vec.size > 0 else 0.0
        if cp_state.value is not None:
            per_time = cp_state.value[:, s_idx, :].sum(axis=1)
            peak = int(np.max(per_time)) if per_time.size else 0
            avg_utilization = float((np.mean(per_time) / installed)) if installed > 0 else 0.0
        else:
            peak = 0
            avg_utilization = 0.0
        station_rows.append({
            "station": station_name,
            "installed": installed,
            "installation_cost": install_cost,
            "peak_concurrency": peak,
            "avg_utilization_per_cp": avg_utilization,
        })
    if station_rows:
        df_install = pd.DataFrame(station_rows)
        df_install.sort_values(by=["installed", "installation_cost"], ascending=[False, True], inplace=True)
        df_install.to_csv(os.path.join(results_dir, "installation_by_station.csv"), index=False)
        with open(os.path.join(results_dir, "installation_by_station.json"), "w") as f:
            json.dump(station_rows, f, indent=2, ensure_ascii=False)

    # Build time indices
    time_minutes_power = np.arange(first_arrival_time, last_departure_time + 1)  # length = num_steps
    time_minutes_soc = np.arange(first_arrival_time, last_departure_time + 2)    # length = num_steps + 1
    time_index_power = pd.to_datetime(time_minutes_power, unit="m", origin=pd.Timestamp("2026-01-01"))
    time_index_soc = pd.to_datetime(time_minutes_soc, unit="m", origin=pd.Timestamp("2026-01-01"))

    # Map bus index -> shift name for output naming
    bus_index_to_shift = {}
    for file_idx, shift_file in enumerate(shift_files):
        bus_index_to_shift[file_idx] = os.path.basename(shift_file).replace(".json", "")

    # Persist run parameters
    run_params = {
        "min_soc": min_soc,
        "max_soc": max_soc,
        "min_session_duration": min_session_duration,
        "session_penalty_weight": session_penalty_weight,
        "early_charging_weight": early_charging_weight,
        "installed_sum": installed_sum,
        "sessions_sum": int(sessions_sum) if sessions_sum is not None else None,
        "early_penalty_unscaled": early_penalty_val,
        "installation_cost": total_installation_cost,
    }
    with open(os.path.join(results_dir, "run_parameters.json"), "w") as f:
        json.dump(run_params, f, indent=2)

    # Export per-shift SOC and power (with smoothed SOC and discharging power for plotting)
    if soc.value is not None and charging_power.value is not None:
        # Total power by bus (sum across stations)
        total_power_by_bus = charging_power.value.sum(axis=1)  # shape (num_steps, num_buses)
        for b_idx in range(len(shift_files)):
            shift_name = bus_index_to_shift[b_idx]
            # Build smoothed SOC by integrating power (charge and discharge)
            smoothed_soc = np.zeros(num_steps + 1)
            smoothed_soc[0] = float(soc.value[0, b_idx])
            for t in range(num_steps):
                chg_kw = float(total_power_by_bus[t, b_idx])
                dis_kw = float(discharge_power_plot_kw[t, b_idx])
                smoothed_soc[t+1] = smoothed_soc[t] - dis_kw * time_step_hours + chg_kw * time_step_hours
            soc_series = pd.Series(smoothed_soc, index=time_index_soc, name="soc_kwh")
            soc_csv_path = os.path.join(results_dir, f"soc_{shift_name}.csv")
            soc_series.to_csv(soc_csv_path, header=True)

            # Power series (kW)
            power_series = pd.Series(total_power_by_bus[:, b_idx], index=time_index_power, name="power_kw")
            power_csv_path = os.path.join(results_dir, f"power_{shift_name}.csv")
            power_series.to_csv(power_csv_path, header=True)

            # Discharging power series (kW) for plotting (shown as negative in plots)
            discharge_series = pd.Series(discharge_power_plot_kw[:, b_idx], index=time_index_power, name="discharge_power_kw")
            discharge_csv_path = os.path.join(results_dir, f"discharge_power_{shift_name}.csv")
            discharge_series.to_csv(discharge_csv_path, header=True)

            # Plot SOC and power for the shift
            fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
            ax_p = axes[0]
            ax_s = axes[1]
            ax_p.plot(power_series.index, power_series.values, label="Charging Power [kW]", color="tab:green")
            ax_p.plot(discharge_series.index, -discharge_series.values, label="Discharging Power [kW]", color="tab:red")
            ax_p.set_ylabel("Power [kW]")
            ax_p.legend(loc="upper right")
            ax_p.grid(True, linestyle=":", alpha=0.5)

            # Annotate charge session starts with station name
            if cp_state.value is not None:
                connected = (cp_state.value[:, :, b_idx].sum(axis=1) > 0.5).astype(int)
                starts = np.where((connected[1:] == 1) & (connected[:-1] == 0))[0] + 1
                for t_idx in starts:
                    ts = time_index_power[t_idx]
                    ax_p.axvline(ts, color="tab:green", alpha=0.3, linestyle="--")
                    # Determine station at charge start
                    active_stations = np.where(cp_state.value[t_idx, :, b_idx] > 0.5)[0]
                    if active_stations.size > 0:
                        station_label = stations[int(active_stations[0])]
                    else:
                        station_label = stations[int(np.argmax(cp_state.value[t_idx, :, b_idx]))]
                    ax_p.text(ts, ax_p.get_ylim()[1]*0.9, station_label, rotation=90, color="tab:green", fontsize=8, va="top")

            ax_s.plot(soc_series.index, soc_series.values, color="tab:orange", label="SOC [kWh]")
            ax_s.set_ylabel("SOC [kWh]")
            # Secondary axis for SOC percent
            ax_s2 = ax_s.twinx()
            if battery_capacity_by_bus[b_idx] > 0:
                soc_percent = (soc_series.values / battery_capacity_by_bus[b_idx]) * 100.0
                ax_s2.plot(soc_series.index, soc_percent, color="tab:purple", linestyle="--", label="SOC [%]")
                ax_s2.set_ylabel("SOC [%]")
            # Combine legends
            lines1, labels1 = ax_s.get_legend_handles_labels()
            lines2, labels2 = ax_s2.get_legend_handles_labels()
            ax_s.legend(lines1 + lines2, labels1 + labels2, loc="upper right")
            ax_s.grid(True, linestyle=":", alpha=0.5)
            fig.suptitle(f"Shift {shift_name}: Power and SOC")
            fig.align_labels()
            fig.autofmt_xdate()
            png_path = os.path.join(results_dir, f"power_soc_{shift_name}.png")
            plt.savefig(png_path, bbox_inches="tight")
            plt.close(fig)

            # Build session table for this shift
            sessions_rows = []
            if 'connect' in locals() and connect.value is not None and 'start_session' in locals() and start_session.value is not None:
                conn = (connect.value[:, b_idx] > 0.5).astype(int)
                # Identify session starts
                starts_idx = np.where((conn[1:] == 1) & (conn[:-1] == 0))[0] + 1
                if conn[0] == 1:
                    starts_idx = np.r_[0, starts_idx]
                # Identify session ends
                ends_idx = np.where((conn[1:] == 0) & (conn[:-1] == 1))[0]
                if conn[-1] == 1:
                    ends_idx = np.r_[ends_idx, len(conn)-1]
                for s_i, e_i in zip(starts_idx, ends_idx):
                    duration_min = int(e_i - s_i + 1)
                    # Station name at start
                    if cp_state.value is not None:
                        active_stations = np.where(cp_state.value[s_i, :, b_idx] > 0.5)[0]
                        station_label = stations[int(active_stations[0])] if active_stations.size > 0 else None
                    else:
                        station_label = None
                    # Energy charged in kWh during session
                    energy_kwh = float(np.sum(charging_power.value[s_i:e_i+1, :, b_idx]) * time_step_hours)
                    sessions_rows.append({
                        "shift": shift_name,
                        "start_time": str(time_index_power[s_i]),
                        "end_time": str(time_index_power[e_i]),
                        "duration_min": duration_min,
                        "station": station_label,
                        "energy_kwh": energy_kwh,
                    })
            if sessions_rows:
                df_sessions = pd.DataFrame(sessions_rows)
                df_sessions.to_csv(os.path.join(results_dir, f"sessions_{shift_name}.csv"), index=False)

        # Combined figure: all shifts with dual y-axes (power and SOC) in subplots
        num_buses = len(shift_files)
        ncols = 3
        nrows = int(np.ceil(num_buses / ncols))
        fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 6, nrows * 4), sharex=True)
        if nrows == 1 and ncols == 1:
            axes = np.array([[axes]])
        elif nrows == 1:
            axes = np.array([axes])
        for b_idx in range(num_buses):
            r = b_idx // ncols
            c = b_idx % ncols
            ax = axes[r, c]
            shift_name = bus_index_to_shift[b_idx]
            power_series = pd.Series(total_power_by_bus[:, b_idx], index=time_index_power)
            discharge_series = pd.Series(discharge_power_plot_kw[:, b_idx], index=time_index_power)
            # Smoothed SOC re-compute for this subplot
            smoothed_soc = np.zeros(num_steps + 1)
            smoothed_soc[0] = float(soc.value[0, b_idx])
            for t in range(num_steps):
                smoothed_soc[t+1] = smoothed_soc[t] - float(discharge_series.iloc[t]) * time_step_hours + float(power_series.iloc[t]) * time_step_hours
            soc_series = pd.Series(smoothed_soc, index=time_index_soc)

            # Primary axis: power
            ax.plot(power_series.index, power_series.values, color="tab:green", label="Charge [kW]")
            ax.plot(discharge_series.index, -discharge_series.values, color="tab:red", label="Discharge [kW]")
            ax.set_title(shift_name)
            ax.grid(True, linestyle=":", alpha=0.35)
            # Secondary axis: SOC
            ax2 = ax.twinx()
            if battery_capacity_by_bus[b_idx] > 0:
                soc_percent = (soc_series.values / battery_capacity_by_bus[b_idx]) * 100.0
                ax2.plot(soc_series.index, soc_percent, color="tab:orange", label="SOC [%]")
                ax2.set_ylabel("SOC [%]")
            # Combine legends
            lines, labels = ax.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax.legend(lines + lines2, labels + labels2, loc="upper right", fontsize=8)

        # Hide any empty subplots
        total_plots = nrows * ncols
        for k in range(num_buses, total_plots):
            r = k // ncols
            c = k % ncols
            fig.delaxes(axes[r, c])
        fig.suptitle("All Shifts: Power and SOC")
        fig.autofmt_xdate()
        combined_path = os.path.join(results_dir, "all_shifts_power_soc.png")
        plt.savefig(combined_path, bbox_inches="tight")
        plt.close(fig)

    # Export per-station per-CP power time series
    if charging_power.value is not None and cp_state.value is not None:
        for s_idx, station_name in enumerate(stations):
            num_cps = int(installed_by_station.get(station_name, 0))
            if num_cps <= 0:
                continue
            # Initialize CP-level power matrix: (num_steps, num_cps)
            cp_power = np.zeros((time_index_power.shape[0], num_cps), dtype=float)

            # For each time step, assign active buses to CP slots deterministically by bus index
            for t in range(time_index_power.shape[0]):
                active_buses = [b for b in range(len(shift_files)) if cp_state.value[t, s_idx, b] > 0.5]
                active_buses.sort()
                # Ensure we do not exceed available CPs (shouldn't due to constraint)
                for j, b_idx in enumerate(active_buses[:num_cps]):
                    cp_power[t, j] = charging_power.value[t, s_idx, b_idx]

            # Save CSV
            df_cp = pd.DataFrame(cp_power, index=time_index_power, columns=[f"cp_{i+1}_kw" for i in range(num_cps)])
            station_safe = station_name.replace(",", "").replace(" ", "_").replace("/", "_")
            csv_path = os.path.join(results_dir, f"station_{station_safe}_cp_power.csv")
            df_cp.to_csv(csv_path)

            # Plot CP-level power
            fig, ax = plt.subplots(1, 1, figsize=(14, 5))
            for i in range(num_cps):
                ax.plot(df_cp.index, df_cp.iloc[:, i], label=f"CP {i+1}")
            ax.set_title(f"{station_name}: Power per Charging Point")
            ax.set_ylabel("kW")
            ax.grid(True, linestyle=":", alpha=0.5)
            ax.legend(ncol=min(4, num_cps), loc="upper right")
            fig.autofmt_xdate()
            png_path = os.path.join(results_dir, f"station_{station_safe}_cp_power.png")
            plt.savefig(png_path, bbox_inches="tight")
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
        df_station_agg = pd.DataFrame([
            {"station": k, **v} for k, v in station_sessions.items()
        ]).sort_values(by=["sessions", "total_duration_min"], ascending=[False, False])
        df_station_agg.to_csv(os.path.join(results_dir, "sessions_by_station.csv"), index=False)

    print(f"Results saved under: {results_dir}")

    return installed_by_station
    


def main():
	shift_dir = "playground/tpl/turni_macchina_2026/2026-TM_15f_lu-ve_TM_json"
	consumption_dir = "playground/tpl/predctions"
	cost_cps = {'Brè, Paese':[1],
	     'Canobbio, Ganna':[1],
	     'Comano, Studio TV':[1],
	     'Lugano, Centro':[1.5, 0.3, 0.3, 0.3],
	     'Lugano, Cornaredo':[1],
	     'Lugano, Pista Ghiaccio':[1],
	     'Pazzallo, P+R Fornaci':[1],
	     'Piano Stampa, Capolinea':[1],
	     'Pregassona, Piazza di Giro':[1]}

	min_soc = 0.4  # Minimum state of charge (40%)
	max_soc = 0.9  # Maximum state of charge (90%)
	min_session_duration = 3  # minutes
	session_penalty_weight = 0.01  # penalize number of charging sessions
	early_charging_weight = 0.005   # penalize late charging
	quantile_consumption = "mean"

	optimize_cp_lugano_centro(
		shift_dir,
		consumption_dir,
		cost_cps,
		min_soc,
		max_soc,
		min_session_duration=min_session_duration,
		session_penalty_weight=session_penalty_weight,
		early_charging_weight=early_charging_weight,
		quantile_consumption=quantile_consumption,
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())