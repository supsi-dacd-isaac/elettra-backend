"""
Charging-point and battery-sizing MILP optimizer (Pyomo).

Refactored from playground/tpl/optimization/optimize_pyomo_gb_sensitivity.py
to accept structured data arrays instead of reading files from disk.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from pyomo.environ import (
    Binary,
    ConcreteModel,
    Constraint,
    NonNegativeIntegers,
    NonNegativeReals,
    Objective,
    Param,
    Reals,
    Set,
    SolverFactory,
    Var,
    minimize,
    value,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input / output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BusData:
    """Per-bus data fed into the optimizer."""
    shift_id: str
    shift_name: str
    battery_capacity_kwh: float
    battery_offset_kwh: float  # config_capacity - prediction_reference_capacity
    max_charging_power_kw: float
    pack_size_kwh: float
    min_packs: int
    max_packs: int
    reference_packs: int  # pack count used in the prediction run
    trips: List[TripData] = field(default_factory=list)


@dataclass
class TripData:
    """Per-trip consumption data."""
    trip_id: str
    departure_minute: int
    arrival_minute: int
    end_station_idx: int  # index into stations list; -1 if not a candidate
    base_energy_kwh: float
    sensitivity: float  # dE/dE_batt


@dataclass
class StationData:
    """Per-station charging configuration."""
    stop_id: str
    stop_name: str
    slot_costs_chf: List[float]  # marginal cost per slot (for charging_only / joint)
    num_fixed_slots: Optional[int]  # fixed slots (battery_only mode)
    max_total_power_kw: float
    max_power_per_slot_kw: Optional[float]


@dataclass
class OptimizationConfig:
    """Solver and constraint configuration."""
    mode: str  # battery_only, charging_only, joint
    min_soc: float = 0.4
    max_soc: float = 0.9
    state_of_health: float = 1.0

    # Battery cost (joint mode)
    battery_cost_per_kwh: float = 0.0
    max_battery_penalty_per_kwh: float = 1e6
    battery_sizing_mode: str = "per_bus"

    # Session constraints
    min_session_duration_minutes: int = 0
    session_connection_minutes: int = 0
    lock_entire_dwell: bool = True
    cp_slack_minutes: int = 0

    # Penalty weights
    session_penalty_weight: float = 0.01
    early_charging_weight: float = 0.0
    soc_increase_weight: float = 1e4  # for battery_only mode

    # Depot charging
    depot_dwell_minutes_after: int = 0

    # Solver
    solver_name: str = "highs"
    max_solver_time_seconds: Optional[int] = None
    mip_rel_gap: Optional[float] = None
    mip_abs_gap: Optional[float] = None
    feasibility_tol: Optional[float] = None
    optimality_tol: Optional[float] = None


@dataclass
class OptimizationResult:
    """Structured output from the optimizer."""
    solver_status: str
    objective_value: float
    solve_time_seconds: float
    electrification_feasible: bool
    electrification_summary: Dict[str, object]

    installed_chargers: Dict[str, dict]  # stop_id -> {stop_name, num_slots, cost_chf}
    total_installation_cost_chf: float

    battery_results: Dict[str, dict]  # shift_id -> {base_kwh, optimized_packs, optimized_kwh, excess_packs}
    total_battery_cost_chf: float
    total_infeasibility_penalty_chf: float

    per_bus_summary: List[dict]
    station_utilization: Dict[str, dict]


# ---------------------------------------------------------------------------
# Helper: build numpy arrays from structured data
# ---------------------------------------------------------------------------

def _prepare_arrays(
    buses: List[BusData],
    stations: List[StationData],
    config: OptimizationConfig,
) -> dict:
    """Build presence masks, discharge arrays, and dwell segments from bus/trip data."""
    station_name_to_idx = {s.stop_id: i for i, s in enumerate(stations)}

    all_times: List[int] = []
    for bus in buses:
        for trip in bus.trips:
            all_times.extend([trip.departure_minute, trip.arrival_minute])
    if not all_times:
        raise ValueError("No trips found across all buses")
    first_t = min(all_times)
    last_t = max(all_times)

    depot_after = config.depot_dwell_minutes_after
    if depot_after > 0:
        last_t = last_t + depot_after

    num_steps = last_t - first_t + 1
    num_buses = len(buses)

    presence_mask = np.zeros((num_steps, num_buses), dtype=int)
    station_at_minute = -np.ones((num_steps, num_buses), dtype=int)
    discharge_base = np.zeros((num_steps, num_buses), dtype=float)
    discharge_sens = np.zeros((num_steps, num_buses), dtype=float)
    dwell_segments: List[List[Tuple[int, int]]] = [[] for _ in range(num_buses)]

    for b_idx, bus in enumerate(buses):
        sorted_trips = sorted(bus.trips, key=lambda t: (t.arrival_minute, t.departure_minute))
        for trip in sorted_trips:
            t_idx = trip.arrival_minute - first_t
            if 0 <= t_idx < num_steps:
                discharge_base[t_idx, b_idx] += trip.base_energy_kwh
                discharge_sens[t_idx, b_idx] += trip.sensitivity

        # Inter-trip dwell segments
        for i in range(len(sorted_trips) - 1):
            arr_i = sorted_trips[i].arrival_minute
            dep_next = sorted_trips[i + 1].departure_minute
            st_idx = sorted_trips[i].end_station_idx
            if st_idx >= 0 and dep_next > arr_i:
                start = max(0, arr_i - first_t)
                end = max(0, dep_next - first_t)
                presence_mask[start:end, b_idx] = 1
                station_at_minute[start:end, b_idx] = st_idx
                dwell_segments[b_idx].append((start, end))

        # Post-shift depot dwell: park at last trip's end station
        if depot_after > 0 and sorted_trips:
            last_trip = sorted_trips[-1]
            st_idx = last_trip.end_station_idx
            if st_idx >= 0:
                start = max(0, last_trip.arrival_minute - first_t)
                end = min(num_steps, last_trip.arrival_minute + depot_after - first_t)
                if end > start:
                    presence_mask[start:end, b_idx] = 1
                    station_at_minute[start:end, b_idx] = st_idx
                    dwell_segments[b_idx].append((start, end))

    return {
        "first_t": first_t,
        "num_steps": num_steps,
        "num_buses": num_buses,
        "presence_mask": presence_mask,
        "station_at_minute": station_at_minute,
        "discharge_base": discharge_base,
        "discharge_sens": discharge_sens,
        "dwell_segments": dwell_segments,
    }


def _estimate_excess_pack_upper_bounds(
    buses: List[BusData],
    config: OptimizationConfig,
) -> np.ndarray:
    """Estimate a conservative upper bound for excess-pack slack variables."""
    usable_soc_window = max(1e-6, float(config.max_soc) - float(config.min_soc))
    bounds: list[int] = []

    for bus in buses:
        total_base_discharge_kwh = float(sum(max(0.0, trip.base_energy_kwh) for trip in bus.trips))
        positive_sensitivity = float(sum(max(0.0, trip.sensitivity) for trip in bus.trips))
        max_physical_extra_cap_kwh = max(0, bus.max_packs - bus.reference_packs) * bus.pack_size_kwh
        worst_case_discharge_kwh = total_base_discharge_kwh + positive_sensitivity * max_physical_extra_cap_kwh
        usable_kwh_per_pack = max(1e-6, bus.pack_size_kwh * usable_soc_window)
        required_total_packs = int(np.ceil(worst_case_discharge_kwh / usable_kwh_per_pack))
        bounds.append(max(1, required_total_packs - bus.max_packs + 5))

    return np.asarray(bounds, dtype=int)


# ---------------------------------------------------------------------------
# Main solver
# ---------------------------------------------------------------------------

def solve_optimization(
    buses: List[BusData],
    stations: List[StationData],
    config: OptimizationConfig,
) -> OptimizationResult:
    """Build and solve the charging-point / battery-sizing MILP."""
    t_start = time.time()

    arrays = _prepare_arrays(buses, stations, config)
    num_steps = arrays["num_steps"]
    num_buses = arrays["num_buses"]
    presence_mask = arrays["presence_mask"]
    station_at_minute = arrays["station_at_minute"]
    discharge_base = arrays["discharge_base"]
    discharge_sens = arrays["discharge_sens"]
    dwell_segments = arrays["dwell_segments"]
    dt = 1.0 / 60.0

    # Per-bus scalars
    battery_capacity = np.array([b.battery_capacity_kwh * config.state_of_health for b in buses])
    max_power = np.array([b.max_charging_power_kw for b in buses])
    pack_size = np.array([b.pack_size_kwh for b in buses])
    min_packs = np.array([b.min_packs for b in buses])
    max_packs = np.array([b.max_packs for b in buses])
    ref_packs = np.array([b.reference_packs for b in buses])
    battery_offset = np.array([b.battery_offset_kwh for b in buses])
    excess_pack_big_m = _estimate_excess_pack_upper_bounds(buses, config)

    # Station slot costs
    station_slot_costs: List[List[float]] = []
    for s in stations:
        if config.mode == "battery_only" and s.num_fixed_slots is not None:
            station_slot_costs.append([0.0] * s.num_fixed_slots)
        else:
            station_slot_costs.append(list(s.slot_costs_chf or []))

    power_limits_by_station = {i: s.max_total_power_kw for i, s in enumerate(stations)}

    # -----------------------------------------------------------------------
    # Build Pyomo model
    # -----------------------------------------------------------------------
    m = ConcreteModel()
    m.T = Set(initialize=range(num_steps))
    m.T1 = Set(initialize=range(num_steps + 1))
    m.B = Set(initialize=range(num_buses))
    m.S = Set(initialize=range(len(stations)))

    # Installation slots
    install_index: List[Tuple[int, int]] = []
    for s_idx, costs in enumerate(station_slot_costs):
        for k in range(len(costs)):
            install_index.append((s_idx, k))

    m.InstallIndex = Set(dimen=2, initialize=install_index)

    def init_costs(mdl, s, k):
        return float(station_slot_costs[s][k])

    m.install_cost = Param(m.InstallIndex, initialize=init_costs, mutable=False)
    m.install = Var(m.InstallIndex, domain=Binary)

    # Fix install vars in battery_only mode
    if config.mode == "battery_only":
        for (s, k) in install_index:
            m.install[s, k].fix(1)

    # Core decision variables
    m.connect = Var(m.T, m.B, domain=Binary)
    m.power = Var(m.T, m.B, domain=NonNegativeReals)
    m.soc = Var(m.T1, m.B, domain=Reals)
    m.start_session = Var(m.T, m.B, domain=Binary)
    # Curtailment: energy dissipated (e.g. mechanical braking) when
    # regeneration would push SOC above max_soc.
    m.curtail = Var(m.T, m.B, domain=NonNegativeReals)

    # Battery sizing variables (integer pack counts)
    m.n_packs = Var(m.B, domain=NonNegativeIntegers)
    m.n_excess_packs = Var(m.B, domain=NonNegativeIntegers)
    if config.mode != "charging_only":
        m.use_excess_packs = Var(m.B, domain=Binary)

    # Bounds and fixing for battery variables
    for b in m.B:
        m.n_packs[b].setlb(int(min_packs[b]))
        m.n_packs[b].setub(int(max_packs[b]))
        if config.mode == "charging_only":
            m.n_packs[b].fix(int(ref_packs[b]))
            # n_excess_packs stays free as a soft feasibility slack

    if config.mode != "charging_only":
        def excess_pack_activation_rule(mdl, b):
            return mdl.n_excess_packs[b] <= int(excess_pack_big_m[b]) * mdl.use_excess_packs[b]

        def max_out_physical_packs_before_excess_rule(mdl, b):
            pack_span = int(max_packs[b] - min_packs[b])
            return mdl.n_packs[b] >= int(max_packs[b]) - pack_span * (1 - mdl.use_excess_packs[b])

        m.excess_pack_activation = Constraint(m.B, rule=excess_pack_activation_rule)
        m.max_out_physical_packs_before_excess = Constraint(
            m.B, rule=max_out_physical_packs_before_excess_rule
        )

    # Per-route equality constraints for battery sizing
    if config.battery_sizing_mode == "per_route" and config.mode != "charging_only":
        route_groups: Dict[str, List[int]] = {}
        for b_idx, bus in enumerate(buses):
            parts = bus.shift_name.split("_")
            route_key = parts[1][:3] if len(parts) >= 2 else "unknown"
            route_groups.setdefault(route_key, []).append(b_idx)

        route_pairs: List[Tuple[int, int]] = []
        for group in route_groups.values():
            for i in range(1, len(group)):
                route_pairs.append((group[0], group[i]))

        if route_pairs:
            m.RoutePairIndex = Set(dimen=2, initialize=route_pairs)

            def route_eq_rule(mdl, b1, b2):
                return mdl.n_packs[b1] == mdl.n_packs[b2]

            m.route_eq = Constraint(m.RoutePairIndex, rule=route_eq_rule)

    # -----------------------------------------------------------------------
    # Precompute buses_here lookup
    # -----------------------------------------------------------------------
    buses_here: Dict[Tuple[int, int], List[int]] = {}
    for t in range(num_steps):
        for s_idx in range(len(stations)):
            bl = [b for b in range(num_buses) if station_at_minute[t, b] == s_idx]
            if bl:
                buses_here[(t, s_idx)] = bl

    # -----------------------------------------------------------------------
    # Constraints
    # -----------------------------------------------------------------------

    # Presence
    def presence_rule(mdl, t, b):
        return mdl.connect[t, b] <= int(presence_mask[t, b])
    m.presence_con = Constraint(m.T, m.B, rule=presence_rule)

    # Station capacity
    def station_capacity_rule(mdl, t, s):
        cap = sum(mdl.install[ss, kk] for (ss, kk) in mdl.InstallIndex if ss == s)
        bl = buses_here.get((t, s))
        if not bl:
            return Constraint.Skip
        return sum(mdl.connect[t, b] for b in bl) <= cap
    m.station_capacity = Constraint(m.T, m.S, rule=station_capacity_rule)

    # Station power limit
    def station_power_limit_rule(mdl, t, s):
        limit = power_limits_by_station.get(s)
        if limit is None:
            return Constraint.Skip
        bl = buses_here.get((t, s))
        if not bl:
            return Constraint.Skip
        return sum(mdl.power[t, b] for b in bl) <= limit
    m.station_power_limit = Constraint(m.T, m.S, rule=station_power_limit_rule)

    # Per-slot assignment
    assign_index: List[Tuple[int, int, int, int]] = []
    for t in range(num_steps):
        for s_idx in range(len(stations)):
            bl = buses_here.get((t, s_idx))
            nslots = len(station_slot_costs[s_idx])
            if nslots <= 0 or not bl:
                continue
            for b in bl:
                for k in range(nslots):
                    assign_index.append((t, b, s_idx, k))
    m.AssignIndex = Set(dimen=4, initialize=assign_index)
    m.assign = Var(m.AssignIndex, domain=Binary)

    # Slot capacity
    use_index: List[Tuple[int, int, int]] = []
    for s_idx, costs in enumerate(station_slot_costs):
        for k in range(len(costs)):
            for t in range(num_steps):
                use_index.append((t, s_idx, k))
    m.UseIndex = Set(dimen=3, initialize=use_index)

    def slot_cap_rule(mdl, t, s, k):
        bl = buses_here.get((t, s))
        if not bl:
            return Constraint.Skip
        terms = [mdl.assign[t, b, s, k] for b in bl if (t, b, s, k) in mdl.AssignIndex]
        if not terms:
            return Constraint.Skip
        return sum(terms) <= mdl.install[s, k]
    m.slot_cap = Constraint(m.UseIndex, rule=slot_cap_rule)

    # Bus-to-slot assignment
    def bus_assign_rule(mdl, t, b):
        s = int(station_at_minute[t, b])
        if s < 0:
            return Constraint.Skip
        nslots = len(station_slot_costs[s])
        if nslots <= 0:
            return Constraint.Skip
        terms = [mdl.assign[t, b, s, k] for k in range(nslots) if (t, b, s, k) in mdl.AssignIndex]
        if not terms:
            return mdl.connect[t, b] == 0
        return sum(terms) == mdl.connect[t, b]
    m.bus_assign = Constraint(m.T, m.B, rule=bus_assign_rule)

    # No slot switching
    def no_switch_rule(mdl, t, b, s, k):
        if t == 0:
            return Constraint.Skip
        s_prev = int(station_at_minute[t - 1, b])
        if s_prev != s or s < 0:
            return Constraint.Skip
        if (t - 1, b, s, k) not in mdl.AssignIndex:
            return Constraint.Skip
        return mdl.assign[t, b, s, k] <= mdl.assign[t - 1, b, s, k] + mdl.start_session[t, b]
    m.no_switch = Constraint(m.AssignIndex, rule=no_switch_rule)

    # Slot installation ordering
    def slot_order_rule(mdl, s, k):
        if k == 0:
            return Constraint.Skip
        if (s, k) in mdl.InstallIndex and (s, k - 1) in mdl.InstallIndex:
            return mdl.install[s, k] <= mdl.install[s, k - 1]
        return Constraint.Skip
    m.slot_order = Constraint(m.InstallIndex, rule=lambda mdl, s, k: slot_order_rule(mdl, s, k))

    # Cooldown
    if config.cp_slack_minutes > 0:
        cooldown_index: List[Tuple[int, int, int, int, int]] = []
        assign_set = set(assign_index)
        for t in range(num_steps - 1):
            for s_idx in range(len(stations)):
                bl = buses_here.get((t, s_idx))
                nslots = len(station_slot_costs[s_idx])
                if nslots <= 0 or not bl:
                    continue
                for b in bl:
                    for k in range(nslots):
                        if (t, b, s_idx, k) in assign_set:
                            for d in range(1, config.cp_slack_minutes + 1):
                                if t + d < num_steps:
                                    cooldown_index.append((t, s_idx, k, b, d))

        if cooldown_index:
            m.CooldownIndex = Set(dimen=5, initialize=cooldown_index)

            def cooldown_rule(mdl, t, s, k, b, delta):
                if (t, b, s, k) not in mdl.AssignIndex:
                    return Constraint.Skip
                if t + 1 >= num_steps or t + delta >= num_steps:
                    return Constraint.Skip
                future_buses = buses_here.get((t + delta, s), [])
                rhs_terms = [mdl.assign[t + delta, bb, s, k] for bb in future_buses if (t + delta, bb, s, k) in mdl.AssignIndex]
                rhs_sum = 0 if not rhs_terms else sum(rhs_terms)
                return mdl.assign[t, b, s, k] - mdl.connect[t + 1, b] <= 1 - rhs_sum

            m.cooldown = Constraint(m.CooldownIndex, rule=lambda mdl, t, s, k, b, d: cooldown_rule(mdl, t, s, k, b, d))

    # -----------------------------------------------------------------------
    # Power bound (bus-level and per-slot)
    # -----------------------------------------------------------------------
    def p_bound_rule(mdl, t, b):
        return mdl.power[t, b] <= float(max_power[b]) * mdl.connect[t, b]
    m.p_bound = Constraint(m.T, m.B, rule=p_bound_rule)

    slot_power_by_station = {
        i: s.max_power_per_slot_kw
        for i, s in enumerate(stations)
        if s.max_power_per_slot_kw is not None
    }

    if slot_power_by_station:
        def slot_power_rule(mdl, t, b):
            s = int(station_at_minute[t, b])
            if s < 0 or s not in slot_power_by_station:
                return Constraint.Skip
            return mdl.power[t, b] <= slot_power_by_station[s] * mdl.connect[t, b]
        m.slot_power = Constraint(m.T, m.B, rule=slot_power_rule)

    # -----------------------------------------------------------------------
    # SOC dynamics with integer pack variables
    # -----------------------------------------------------------------------
    # effective_cap[b] = (n_packs[b] + n_excess_packs[b]) * pack_size[b]
    #
    # Weight sensitivity is applied ONLY to n_packs (physical packs within
    # min/max bounds). n_excess_packs are a feasibility overflow: they add
    # pure capacity without the weight-sensitivity correction, because the
    # linear approximation is only valid near the reference battery size.
    # The result reports how many total packs are needed vs. what can be
    # physically installed.

    def soc_init_rule(mdl, b):
        ps = float(pack_size[b])
        return mdl.soc[0, b] == (mdl.n_packs[b] + mdl.n_excess_packs[b]) * ps * float(config.max_soc)
    m.soc_init = Constraint(m.B, rule=soc_init_rule)

    def soc_min_rule(mdl, t, b):
        ps = float(pack_size[b])
        return mdl.soc[t, b] >= (mdl.n_packs[b] + mdl.n_excess_packs[b]) * ps * float(config.min_soc)

    def soc_max_rule(mdl, t, b):
        ps = float(pack_size[b])
        return mdl.soc[t, b] <= (mdl.n_packs[b] + mdl.n_excess_packs[b]) * ps * float(config.max_soc)

    m.soc_min = Constraint(m.T1, m.B, rule=soc_min_rule)
    m.soc_max = Constraint(m.T1, m.B, rule=soc_max_rule)

    def soc_dyn_rule(mdl, t, b):
        base_e = float(discharge_base[t, b])
        sens_e = float(discharge_sens[t, b])
        ps = float(pack_size[b])
        rp = float(ref_packs[b])
        extra_cap_physical = (mdl.n_packs[b] - rp) * ps
        return (
            mdl.soc[t + 1, b]
            == mdl.soc[t, b]
            - (base_e + sens_e * extra_cap_physical)
            - mdl.curtail[t, b]
            + float(dt) * mdl.power[t, b]
        )
    m.soc_dyn = Constraint(m.T, m.B, rule=soc_dyn_rule)

    # -----------------------------------------------------------------------
    # Session start detection
    # -----------------------------------------------------------------------
    min_sess_dur = config.min_session_duration_minutes
    if min_sess_dur > 0:
        allowed_start_mask = np.zeros((num_steps, num_buses))
        for b in range(num_buses):
            pres = presence_mask[:, b].astype(int)
            if num_steps >= min_sess_dur:
                window = np.convolve(pres, np.ones(min_sess_dur, dtype=int), mode="valid")
                allowed = (window == min_sess_dur).astype(int)
                allowed_start_mask[: allowed.shape[0], b] = allowed
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

    if min_sess_dur > 0:
        def min_sess_rule(mdl, t, b):
            if t > num_steps - min_sess_dur:
                return Constraint.Skip
            return sum(mdl.connect[tau, b] for tau in range(t, t + min_sess_dur)) >= min_sess_dur * mdl.start_session[t, b]
        m.min_sess = Constraint(m.T, m.B, rule=min_sess_rule)

    # Connection buffer
    connection_buffer = config.session_connection_minutes
    if connection_buffer > 0:
        conn_idx: List[Tuple[int, int, int]] = []
        for b in range(num_buses):
            for start_t in range(num_steps):
                for enforce_t in range(start_t, min(num_steps, start_t + connection_buffer)):
                    conn_idx.append((start_t, enforce_t, b))
        if conn_idx:
            m.ConnectionBufferIndex = Set(dimen=3, initialize=conn_idx)

            def connection_buffer_rule(mdl, start_t, enforce_t, b):
                return mdl.power[enforce_t, b] <= float(max_power[b]) * (1 - mdl.start_session[start_t, b])
            m.connection_buffer = Constraint(m.ConnectionBufferIndex, rule=connection_buffer_rule)

    # Lock entire dwell
    if config.lock_entire_dwell:
        lock_pairs: List[Tuple[int, int]] = []
        for b in range(num_buses):
            for (start, end) in dwell_segments[b]:
                for t in range(start + 1, end):
                    lock_pairs.append((b, t))
        if lock_pairs:
            m.LockIndex = Set(dimen=2, initialize=lock_pairs)

            def lock_rule(mdl, b, t):
                return mdl.connect[t, b] == mdl.connect[t - 1, b]
            m.lock_dwell = Constraint(m.LockIndex, rule=lock_rule)

    # -----------------------------------------------------------------------
    # Objective
    # -----------------------------------------------------------------------
    time_weights = np.arange(num_steps, dtype=float)
    time_weights /= max(1.0, float(num_steps - 1))

    def obj_rule(mdl):
        obj = 0.0

        # Installation cost (charging_only and joint)
        if config.mode != "battery_only":
            obj += sum(mdl.install[s, k] * m.install_cost[s, k] for (s, k) in mdl.InstallIndex)

        # Battery cost terms
        penalty_per_kwh = float(config.max_battery_penalty_per_kwh)
        if config.mode == "joint":
            cost_per_kwh = float(config.battery_cost_per_kwh)
            for b in mdl.B:
                ps = float(pack_size[b])
                mp = float(min_packs[b])
                obj += cost_per_kwh * (mdl.n_packs[b] - mp) * ps
                obj += penalty_per_kwh * mdl.n_excess_packs[b] * ps
        elif config.mode == "battery_only":
            w = float(config.soc_increase_weight)
            for b in mdl.B:
                ps = float(pack_size[b])
                mp = float(min_packs[b])
                if w > 0:
                    obj += w * (mdl.n_packs[b] - mp) * ps
                # Excess packs are an infeasibility slack, not a normal sizing choice.
                obj += penalty_per_kwh * mdl.n_excess_packs[b] * ps
        elif config.mode == "charging_only":
            for b in mdl.B:
                ps = float(pack_size[b])
                obj += penalty_per_kwh * mdl.n_excess_packs[b] * ps

        # Session penalty
        if config.session_penalty_weight > 0:
            obj += float(config.session_penalty_weight) * sum(
                mdl.start_session[t, b] for t in mdl.T for b in mdl.B
            )

        # Early charging penalty
        if config.early_charging_weight > 0:
            obj += float(config.early_charging_weight) * sum(
                float(time_weights[t]) * sum(mdl.connect[t, b] for b in mdl.B)
                for t in mdl.T
            )

        return obj

    m.obj = Objective(rule=obj_rule, sense=minimize)

    # -----------------------------------------------------------------------
    # Solve
    # -----------------------------------------------------------------------
    solver_key = config.solver_name.lower()
    if solver_key not in {"highs", "gurobi"}:
        raise ValueError(f"Unsupported solver '{config.solver_name}'")
    solver = SolverFactory(solver_key)

    rel_gap = 0.0 if config.mip_rel_gap is None else max(0.0, config.mip_rel_gap)
    abs_gap = 0.0 if config.mip_abs_gap is None else max(0.0, config.mip_abs_gap)
    feas_tol = 1e-6 if config.feasibility_tol is None else max(1e-12, config.feasibility_tol)
    opt_tol = 1e-6 if config.optimality_tol is None else max(1e-12, config.optimality_tol)

    if solver_key == "highs":
        solver.options["mip_rel_gap"] = rel_gap
        solver.options["mip_abs_gap"] = abs_gap
        solver.options["mip_feasibility_tolerance"] = feas_tol
        solver.options["primal_feasibility_tolerance"] = feas_tol
        solver.options["dual_feasibility_tolerance"] = opt_tol
        if config.max_solver_time_seconds is not None:
            solver.options["time_limit"] = float(config.max_solver_time_seconds)
    elif solver_key == "gurobi":
        solver.options["MIPGap"] = rel_gap
        solver.options["MIPGapAbs"] = abs_gap
        solver.options["FeasibilityTol"] = feas_tol
        solver.options["OptimalityTol"] = opt_tol
        if config.max_solver_time_seconds is not None:
            solver.options["TimeLimit"] = float(config.max_solver_time_seconds)

    n_vars = sum(1 for _ in m.component_data_objects(Var, active=True))
    n_cons = sum(1 for _ in m.component_data_objects(Constraint, active=True))
    logger.info(
        "Solving with %s: %d buses, %d steps, %d stations, %d vars, %d constraints",
        solver_key, num_buses, num_steps, len(stations), n_vars, n_cons,
    )

    for b in range(num_buses):
        total_discharge = float(discharge_base[:, b].sum())
        total_sens = float(discharge_sens[:, b].sum())
        n_dwell = sum(1 for t in range(num_steps) if station_at_minute[t, b] >= 0)
        logger.info(
            "  Bus %d (%s): pack_size=%.1f, ref_packs=%d, min=%d, max=%d, "
            "discharge=%.1f kWh, sens_total=%.4f, dwell_mins=%d, n_trips=%d",
            b, buses[b].shift_name, pack_size[b], ref_packs[b],
            min_packs[b], max_packs[b], total_discharge, total_sens, n_dwell,
            len(buses[b].trips),
        )
    try:
        lp_path = "/tmp/optim_debug.lp"
        m.write(lp_path, io_options={"symbolic_solver_labels": True})
        logger.info("Wrote LP model to %s (%d bytes)", lp_path, os.path.getsize(lp_path))
    except Exception as write_exc:
        logger.warning("Could not write LP file: %s", write_exc)

    try:
        results = solver.solve(m, tee=True)
    except Exception as exc:
        solve_time = time.time() - t_start
        logger.error("Solver raised exception after %.1fs: %s", solve_time, exc)
        return OptimizationResult(
            solver_status=f"error: {exc}",
            objective_value=-1.0,
            solve_time_seconds=round(solve_time, 2),
            electrification_feasible=False,
            electrification_summary={
                "status": "solver_error",
                "message": str(exc),
                "infeasible_buses": [],
            },
            installed_chargers={},
            total_installation_cost_chf=0.0,
            battery_results={},
            total_battery_cost_chf=0.0,
            total_infeasibility_penalty_chf=0.0,
            per_bus_summary=[],
            station_utilization={},
        )
    solve_time = time.time() - t_start
    term_cond = str(results.solver.termination_condition)
    logger.info("Solver finished: %s (%.1fs)", term_cond, solve_time)

    try:
        obj_val = float(value(m.obj))
    except Exception:
        obj_val = -1.0

    # -----------------------------------------------------------------------
    # Extract results
    # -----------------------------------------------------------------------

    # Installed chargers
    installed_chargers: Dict[str, dict] = {}
    total_install_cost = 0.0
    for s_idx, station in enumerate(stations):
        costs = station_slot_costs[s_idx]
        count = 0
        for k in range(len(costs)):
            if (s_idx, k) in m.InstallIndex:
                v = m.install[s_idx, k].value
                if v is not None and v >= 0.5:
                    count += 1
        cost = float(np.sum(costs[:count])) if count > 0 else 0.0
        installed_chargers[station.stop_id] = {
            "stop_name": station.stop_name,
            "num_slots": count,
            "cost_chf": cost,
        }
        total_install_cost += cost

    # Battery results
    battery_results: Dict[str, dict] = {}
    total_battery_cost = 0.0
    total_infeasibility_penalty = 0.0
    infeasible_buses: List[dict] = []
    for b_idx, bus in enumerate(buses):
        np_val = int(round(m.n_packs[b_idx].value or min_packs[b_idx]))
        ne_val = int(round(m.n_excess_packs[b_idx].value or 0))
        total_required_packs = np_val + ne_val
        base_kwh = float(min_packs[b_idx]) * float(pack_size[b_idx])
        optimized_kwh = float(total_required_packs) * float(pack_size[b_idx])
        physical_max_kwh = float(max_packs[b_idx]) * float(pack_size[b_idx])
        physical_feasible = ne_val == 0

        if config.mode == "joint":
            extra_packs = np_val - int(min_packs[b_idx])
            total_battery_cost += extra_packs * float(pack_size[b_idx]) * config.battery_cost_per_kwh
            total_infeasibility_penalty += ne_val * float(pack_size[b_idx]) * config.max_battery_penalty_per_kwh
        elif config.mode in {"battery_only", "charging_only"}:
            total_infeasibility_penalty += ne_val * float(pack_size[b_idx]) * config.max_battery_penalty_per_kwh

        battery_results[bus.shift_id] = {
            "shift_name": bus.shift_name,
            "base_packs": int(min_packs[b_idx]),
            "optimized_packs": np_val,
            "excess_packs": ne_val,
            "required_total_packs": total_required_packs,
            "max_physical_packs": int(max_packs[b_idx]),
            "base_kwh": base_kwh,
            "optimized_kwh": optimized_kwh,
            "max_physical_kwh": physical_max_kwh,
            "physical_feasible": physical_feasible,
            "feasibility_status": "feasible" if physical_feasible else "infeasible_requires_excess_packs",
        }

        if not physical_feasible:
            infeasible_buses.append({
                "shift_id": bus.shift_id,
                "shift_name": bus.shift_name,
                "required_total_packs": total_required_packs,
                "max_physical_packs": int(max_packs[b_idx]),
                "required_total_kwh": optimized_kwh,
                "max_physical_kwh": physical_max_kwh,
                "excess_packs": ne_val,
                "message": (
                    f"Shift requires {total_required_packs} packs ({optimized_kwh:.1f} kWh) "
                    f"but the bus model allows at most {int(max_packs[b_idx])} packs "
                    f"({physical_max_kwh:.1f} kWh)."
                ),
            })

    # Per-bus summary (SOC + charging sessions)
    power_val = np.zeros((num_steps, num_buses))
    soc_val = np.zeros((num_steps + 1, num_buses))
    connect_val = np.zeros((num_steps, num_buses))
    for t in range(num_steps):
        for b in range(num_buses):
            connect_val[t, b] = float(m.connect[t, b].value or 0.0)
            power_val[t, b] = float(m.power[t, b].value or 0.0)
    for t in range(num_steps + 1):
        for b in range(num_buses):
            soc_val[t, b] = float(m.soc[t, b].value or 0.0)

    first_t = arrays["first_t"]
    per_bus_summary: List[dict] = []
    for b_idx, bus in enumerate(buses):
        sessions = _extract_sessions(
            connect_val[:, b_idx], power_val[:, b_idx],
            station_at_minute[:, b_idx], stations, first_t,
        )
        per_bus_summary.append({
            "shift_id": bus.shift_id,
            "shift_name": bus.shift_name,
            "min_soc_kwh": float(np.min(soc_val[:, b_idx])),
            "max_soc_kwh": float(np.max(soc_val[:, b_idx])),
            "total_charged_kwh": float(np.sum(power_val[:, b_idx]) * dt),
            "num_charging_sessions": len(sessions),
            "charging_sessions": sessions,
        })

    # Station utilization
    station_util: Dict[str, dict] = {}
    for s_idx, station in enumerate(stations):
        bl_all = set()
        total_energy = 0.0
        max_concurrent = 0
        for t in range(num_steps):
            bl = buses_here.get((t, s_idx), [])
            concurrent = sum(1 for b in bl if connect_val[t, b] >= 0.5)
            max_concurrent = max(max_concurrent, concurrent)
            for b in bl:
                if connect_val[t, b] >= 0.5:
                    bl_all.add(b)
                    total_energy += power_val[t, b] * dt
        station_util[station.stop_id] = {
            "stop_name": station.stop_name,
            "peak_concurrent_buses": max_concurrent,
            "total_energy_kwh": round(total_energy, 2),
        }

    electrification_feasible = len(infeasible_buses) == 0
    electrification_summary: Dict[str, object] = {
        "status": "feasible" if electrification_feasible else "infeasible",
        "message": (
            "All buses satisfy the requested shift(s) within their physical battery limits."
            if electrification_feasible
            else (
                f"{len(infeasible_buses)} of {len(buses)} bus(es) require battery slack "
                "beyond the configured max_packs."
            )
        ),
        "num_buses": len(buses),
        "num_infeasible_buses": len(infeasible_buses),
        "infeasible_buses": infeasible_buses,
    }

    return OptimizationResult(
        solver_status=term_cond,
        objective_value=obj_val,
        solve_time_seconds=round(solve_time, 2),
        electrification_feasible=electrification_feasible,
        electrification_summary=electrification_summary,
        installed_chargers=installed_chargers,
        total_installation_cost_chf=total_install_cost,
        battery_results=battery_results,
        total_battery_cost_chf=total_battery_cost,
        total_infeasibility_penalty_chf=total_infeasibility_penalty,
        per_bus_summary=per_bus_summary,
        station_utilization=station_util,
    )


def _extract_sessions(
    connect: np.ndarray,
    power: np.ndarray,
    station_at: np.ndarray,
    stations: List[StationData],
    first_t: int,
) -> List[dict]:
    """Extract charging sessions from binary connect vector."""
    sessions: List[dict] = []
    in_session = False
    start = 0
    for t in range(len(connect)):
        if connect[t] >= 0.5 and not in_session:
            in_session = True
            start = t
        elif connect[t] < 0.5 and in_session:
            in_session = False
            s_idx = int(station_at[start])
            energy = float(np.sum(power[start:t]) / 60.0)
            sessions.append({
                "start_minute": start + first_t,
                "end_minute": t + first_t,
                "duration_minutes": t - start,
                "station_stop_id": stations[s_idx].stop_id if 0 <= s_idx < len(stations) else None,
                "station_name": stations[s_idx].stop_name if 0 <= s_idx < len(stations) else None,
                "energy_kwh": round(energy, 2),
            })
    if in_session:
        t = len(connect)
        s_idx = int(station_at[start])
        energy = float(np.sum(power[start:t]) / 60.0)
        sessions.append({
            "start_minute": start + first_t,
            "end_minute": t + first_t,
            "duration_minutes": t - start,
            "station_stop_id": stations[s_idx].stop_id if 0 <= s_idx < len(stations) else None,
            "station_name": stations[s_idx].stop_name if 0 <= s_idx < len(stations) else None,
            "energy_kwh": round(energy, 2),
        })
    return sessions
