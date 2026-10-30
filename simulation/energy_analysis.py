"""Energy requirements on a fixed design and its verified charging schedule."""
from __future__ import annotations

import math
import time
import hashlib
import json

import numpy as np

from simulation.aggregate_consumption import METHOD_VERSION, shift_seed


def simulate_plan_coverage(bus, support, timeline, *, usable_kwh, samples, seed):
    """Replay independent trajectories on the fixed plan without another solver.

    Charging-only intervals can be collapsed: drawdown only decreases there.
    Trip arrivals retain the optimizer's minute convention and simultaneous
    scheduled charge. Surplus at SOC max is curtailed, never moved to later stops.
    """
    started = time.monotonic()
    values = np.asarray(support)
    charge = np.asarray(timeline["charge_kwh"])
    if values.ndim != 2 or values.shape[0] != len(bus.trips) or not np.isfinite(values).all():
        raise ValueError("Complete finite predictive support is required")
    arrivals = {}
    for index, trip in enumerate(bus.trips):
        arrivals.setdefault(trip.arrival_minute-timeline["first_minute"], []).append(index)
    rng = np.random.default_rng(seed)
    maximums = np.empty(samples)
    batch = max(1, min(25_000, 2_000_000//len(bus.trips)))
    for first in range(0, samples, batch):
        count = min(batch, samples-first)
        indices = rng.integers(values.shape[1], size=(count, len(bus.trips)))
        energy = values[np.arange(len(bus.trips))[None, :], indices]
        drawdown, peak = np.zeros(count), np.zeros(count)
        previous = 0
        for minute, trips in sorted(arrivals.items()):
            drawdown = np.maximum(0, drawdown-float(charge[previous:minute].sum()))
            drawdown = np.maximum(0, drawdown+energy[:, trips].sum(axis=1)-charge[minute])
            peak = np.maximum(peak, drawdown)
            previous = minute+1
        maximums[first:first+count] = peak
    successful = int(np.sum(maximums <= usable_kwh+1e-5))
    probability = successful/samples
    z = 1.959963984540054
    denominator = 1+z*z/samples
    center = (probability+z*z/(2*samples))/denominator
    radius = z*np.sqrt(probability*(1-probability)/samples+z*z/(4*samples*samples))/denominator
    return {"samples": samples, "seed": seed, "completed_trajectories": successful,
            "completion_probability": probability,
            "completion_probability_mc_ci95": [max(0,float(center-radius)), min(1,float(center+radius))],
            "required_usable_kwh_q95": float(np.quantile(maximums,.95)),
            "deficit_kwh_q95": float(np.quantile(np.maximum(0,maximums-usable_kwh),.95)),
            "scope": "per_shift_all_soc_constraints_on_fixed_plan_under_independent_trip_errors",
            "charge_policy": "curtail_at_max_soc_without_adding_or_moving_sessions",
            "elapsed_seconds": time.monotonic()-started}


def replay_profile(bus, energies, timeline, *, nominal_kwh, min_soc, max_soc, state_of_health):
    """Replay minute balances, curtailing surplus at SOC max, never adding charge.

    Maximum drawdown is the usable capacity required at this mass and on this
    charging plan. It is not the consumption total, a newly sized battery, or a
    percentile of the maximum drawdown over Monte Carlo trajectories.
    """
    charge = timeline["charge_kwh"]
    first = timeline["first_minute"]
    discharge = [0.0] * len(charge)
    if len(energies) != len(bus.trips):
        raise ValueError("Energy profile must cover every trip occurrence")
    for trip, energy in zip(bus.trips, energies):
        minute = trip.arrival_minute-first
        if not 0 <= minute < len(charge) or not math.isfinite(energy):
            raise ValueError("Invalid profile or charging timeline")
        discharge[minute] += energy
    upper = nominal_kwh * state_of_health * max_soc
    usable = nominal_kwh * state_of_health * (max_soc-min_soc)
    drawdown = maximum = curtailed = charged = 0.0
    critical = first
    soc = [upper]
    for index, (energy, supplied) in enumerate(zip(discharge, charge)):
        # Consume only the scheduled energy that fits. Regenerative surplus is
        # curtailed too; no charging opportunity is moved or increased.
        accepted = min(max(0.0, supplied), max(0.0, drawdown+energy))
        charged += accepted
        curtailed += max(0.0, supplied)-accepted
        drawdown = max(0.0, drawdown+energy-accepted)
        if drawdown > maximum:
            maximum, critical = drawdown, first+index+1
        soc.append(upper-drawdown)
    return {
        "total_consumption_kwh": sum(energies), "required_usable_kwh": maximum,
        "available_usable_kwh": usable, "margin_kwh": usable-maximum,
        "within_soc_limits": maximum <= usable+1e-5,
        "min_soc_kwh": min(soc), "critical_minute": critical,
        "scheduled_charge_kwh": sum(charge), "accepted_charge_kwh": charged,
        "curtailed_charge_kwh": curtailed,
        "soc_kwh": soc, "first_minute": first, "step_minutes": 1,
    }


def build_energy_analysis(buses, result, profiles, config, *, basis, verified):
    analysis = {
        "version": METHOD_VERSION, "optimization_basis": basis,
        "status": "available" if verified else "unverified",
        "scope": "representative_profiles_on_same_fixed_design_and_charging_plan",
        "trajectory_coverage_probability": None, "per_shift": {},
    }
    if not verified:
        return analysis
    summaries = {row["shift_id"]: row for row in result.per_bus_summary}
    for bus in buses:
        source = profiles.get(bus.shift_id)
        if not source or source.get("error"):
            analysis["per_shift"][bus.shift_id] = {
                "status": "unavailable", "reason": (source or {}).get("error", "Distribution unavailable")}
            continue
        selected = result.battery_results[bus.shift_id]["optimized_packs"]
        nominal = selected * bus.pack_size_kwh
        timeline = summaries[bus.shift_id]["energy_timeline"]
        cases = {key: replay_profile(bus, source[key], timeline,
                     nominal_kwh=nominal, min_soc=config.min_soc, max_soc=config.max_soc,
                     state_of_health=config.state_of_health) for key in ("mean", "aggregate_q95")}
        # This guard prevents publishing a diagnostic based on the wrong mass,
        # trip order, or plan while calling it an exact verified result.
        if basis in cases and not cases[basis]["within_soc_limits"]:
            raise ValueError("Energy replay does not reproduce the verified SOC feasibility")
        analysis["per_shift"][bus.shift_id] = {
            "status": "available", "selected_packs": selected, "max_packs": bus.max_packs,
            "pack_size_kwh": bus.pack_size_kwh, "nominal_kwh": nominal,
            "effective_capacity_kwh": nominal*config.state_of_health,
            "usable_kwh": nominal*config.state_of_health*(config.max_soc-config.min_soc),
            "min_soc": config.min_soc, "max_soc": config.max_soc,
            "state_of_health": config.state_of_health,
            "reference": source["reference"], "statistics": source["statistics"],
            "occurrences": source["occurrences"],
            # Preserve the timetable used by this result for display-only
            # interpolation, even if the source shift is edited later.
            "trip_intervals": [{"departure_minute": trip.departure_minute,
                                "arrival_minute": trip.arrival_minute} for trip in bus.trips],
            "trip_profiles_kwh": {key: source[key] for key in cases}, "cases": cases,
            "verified_soc_kwh": timeline["soc_kwh"],
            "plan_fingerprint": hashlib.sha256(json.dumps({
                "timeline": timeline, "packs": selected, "min_soc": config.min_soc,
                "max_soc": config.max_soc, "soh": config.state_of_health,
            }, sort_keys=True).encode()).hexdigest(),
        }
        if source.get("_support") is not None:
            analysis["per_shift"][bus.shift_id]["trajectory_validation"] = simulate_plan_coverage(
                bus, source["_support"], timeline,
                usable_kwh=nominal*config.state_of_health*(config.max_soc-config.min_soc),
                samples=source["statistics"]["samples"],
                seed=shift_seed(source["statistics"]["seed"], "fixed-plan-validation"))
    return analysis
