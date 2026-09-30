"""Sequential mechanical optimisation with full-predictor feasibility checks.

The predictor is supplied by the service. Neither QRF nor its parameters are
changed here. Verification proves feasibility of a fixed design, not global
optimality for the nonlinear predictor.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from simulation.optimization_model import solve_optimization


async def optimize_and_verify(buses, stations, config, load_exact, *,
                              time_budget_seconds=900, solve=solve_optimization, on_progress=None):
    started = time.monotonic()
    history = []
    visited = set()
    final_references = None
    result = None
    status = "iteration_limit"

    def remaining_config(**overrides):
        remaining = time_budget_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("Optimization and verification time budget exhausted")
        return replace(config, max_solver_time_seconds=min(
            config.max_solver_time_seconds or remaining, remaining), **overrides)

    try:
        for iteration in range(1, 4):
            if on_progress:
                await on_progress({"iteration": iteration, "phase": "sizing", "iterations": history})
            result = await asyncio.to_thread(solve, buses, stations, remaining_config())
            entry = {"iteration": iteration, "solver_status": result.solver_status,
                     "reference_packs": {b.shift_id: b.reference_packs for b in buses},
                     "linear_objective_value": result.objective_value}
            history.append(entry)
            if result.solver_status != "optimal":
                status = "solver_not_optimal"
                break
            if not result.electrification_feasible:
                status = "physical_limits_exceeded"
                break
            packs = {sid: row["optimized_packs"] for sid, row in result.battery_results.items()}
            slots = {sid: row["num_slots"] for sid, row in result.installed_chargers.items()}
            entry["selected_packs"] = packs
            entry["installed_slots"] = slots
            key = (tuple(sorted(packs.items())), tuple(sorted(slots.items())))
            if key in visited:
                status = "cycle_detected"
                break
            visited.add(key)
            remaining_config()
            if on_progress:
                await on_progress({"iteration": iteration, "phase": "full_forecast", "iterations": history})
            async with asyncio.timeout(max(.001, time_budget_seconds - (time.monotonic() - started))):
                exact_buses, references = await load_exact(packs)
            # The same trip estimator and schedule, at the chosen physical mass.
            check_config = remaining_config(fixed_battery_packs=packs,
                                            fixed_station_slots=slots,
                                            allow_excess_packs=False)
            if on_progress:
                await on_progress({"iteration": iteration, "phase": "fixed_design_verification", "iterations": history})
            checked = await asyncio.to_thread(solve, exact_buses, stations, check_config)
            entry["verification_solver_status"] = checked.solver_status
            entry["exact_prediction_run_ids"] = references
            entry["linear_energy_kwh"] = {
                b.shift_id: sum(t.base_energy_kwh + t.sensitivity *
                               (packs[b.shift_id] - b.reference_packs) * b.pack_size_kwh
                               for t in b.trips) for b in buses}
            entry["exact_energy_kwh"] = {b.shift_id: sum(t.base_energy_kwh for t in b.trips)
                                          for b in exact_buses}
            if (time.monotonic() - started) >= time_budget_seconds:
                status = "time_budget_exhausted"
                break
            if checked.solver_status == "optimal" and checked.electrification_feasible:
                # Publish the verified charging/SOC schedule; retain the sizing
                # objective separately (the fixed-design objective is different).
                checked.objective_value = result.objective_value
                result = checked
                status = "verified"
                final_references = references
                break
            # Failed exact verification: re-anchor, not arbitrarily add a pack.
            buses = exact_buses
            final_references = references
    except TimeoutError:
        status = "time_budget_exhausted"
    elapsed = time.monotonic() - started
    if result is None:
        raise TimeoutError("No optimization result within the time budget")
    if status != "verified":
        result.electrification_feasible = False
        result.electrification_summary = {
            **result.electrification_summary, "status": "unverified",
            "message": f"Full-forecast feasibility not established: {status}",
        }
        for row in result.battery_results.values():
            row["physical_feasible"] = False
            row["feasibility_status"] = "unverified"
    result.solve_time_seconds = elapsed
    return result, {
        "status": status, "iterations": history, "elapsed_seconds": elapsed,
        "time_budget_seconds": time_budget_seconds,
        "final_reference_prediction_run_ids": final_references,
        "optimality_scope": "linearized_sizing_only; fixed_design_full_forecast_feasibility",
    }
