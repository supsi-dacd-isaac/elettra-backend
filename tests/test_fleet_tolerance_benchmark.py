"""One-off staging benchmark for MIP convergence; skipped in normal test runs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest

import test_fleet_optimization as fleet


ROUTES = {"401", "404", "407"}
RELATIVE_GAPS = [1e-3, 1e-4, 1e-6, 1e-8, 0.0]
MODES = ("battery_only", "joint")
ARTIFACT = Path(os.getenv(
    "ELETTRA_FLEET_TOLERANCE_RESULTS",
    "/tmp/elettra-fleet-tolerance-401-404-407.json",
))

pytestmark = pytest.mark.skipif(
    os.getenv("ELETTRA_RUN_FLEET_TOLERANCE_BENCHMARK") != "1",
    reason="One-off staging convergence benchmark",
)


@pytest.fixture(scope="module")
def benchmark_env(client, request):
    selected = [row for row in fleet.load_shift_files() if row["route"] in ROUTES]
    assert len(selected) == 21
    assert {row["route"] for row in selected} == ROUTES
    return fleet.build_fleet_env(client, request, selected)


def _terminal_pair(client, token, ids, *, max_wait=1620):
    started = time.monotonic()
    last = {}
    while time.monotonic() - started < max_wait:
        for basis, run_id in ids.items():
            response = client.get(
                f"{fleet.SIM_BASE}/optimization-runs/{run_id}",
                headers=fleet.auth_headers(token),
            )
            assert response.status_code == 200, response.text
            last[basis] = response.json()
        if all(row["status"] in {"completed", "failed"} for row in last.values()):
            return last
        time.sleep(1)
    pytest.fail(f"Paired optimization did not terminate in {max_wait}s: {last}")


def _design(results):
    batteries = {
        key: value.get("optimized_packs")
        for key, value in (results.get("battery_results") or {}).items()
    }
    chargers = {
        key: {
            "slots": value.get("num_slots"),
            "power_kw": value.get("installed_power_kw"),
        }
        for key, value in (results.get("installed_chargers") or {}).items()
    }
    return {"battery_packs": batteries, "chargers": chargers}


def _summary(run, *, wall_seconds):
    results = run.get("results") or {}
    verification = results.get("forecast_verification") or {}
    return {
        "id": run["id"],
        "name": run.get("name"),
        "status": run["status"],
        "solver_status": results.get("solver_status"),
        "verification_status": verification.get("status"),
        "conclusive": fleet.optimization_is_conclusive(run),
        "wall_seconds_for_pair": wall_seconds,
        "pipeline_seconds": results.get("solve_time_seconds"),
        "solver_seconds": verification.get("solver_seconds"),
        "iterations": len(verification.get("iterations") or []),
        "objective": results.get("objective_value"),
        "installation_cost_chf": results.get("total_installation_cost_chf"),
        "battery_cost_chf": results.get("total_battery_cost_chf"),
        "infeasibility_penalty_chf": results.get("total_infeasibility_penalty_chf"),
        "electrification_feasible": results.get("electrification_feasible"),
        "design": _design(results),
    }


def _write(report):
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(report, indent=2, default=str))


def _add_reference_errors(report):
    rows = report["runs"]
    for mode in MODES:
        for basis in ("mean", "aggregate_q95"):
            reference = rows.get(f"{mode}|0|{basis}")
            if not reference or reference["objective"] is None:
                continue
            for gap in RELATIVE_GAPS:
                row = rows.get(f"{mode}|{gap:g}|{basis}")
                if not row or row["objective"] is None:
                    continue
                absolute = row["objective"] - reference["objective"]
                row["objective_delta_from_zero"] = absolute
                row["objective_relative_error_from_zero"] = (
                    abs(absolute) / max(1.0, abs(reference["objective"]))
                )
                row["battery_design_matches_zero"] = (
                    row["design"]["battery_packs"]
                    == reference["design"]["battery_packs"]
                )
                row["charger_design_matches_zero"] = (
                    row["design"]["chargers"]
                    == reference["design"]["chargers"]
                )


def test_fleet_mip_tolerance_convergence(client, benchmark_env):
    token = benchmark_env["token"]
    shift_ids, prediction_ids = fleet._collect_all_ids(benchmark_env)
    candidates = benchmark_env["charging_stations_config"]
    fixed = [{
        "stop_id": station["stop_id"],
        "num_slots": 2,
        "max_total_power_kw": station["max_total_power_kw"],
        **({"max_power_per_slot_kw": station["max_power_per_slot_kw"]}
           if "max_power_per_slot_kw" in station else {}),
    } for station in candidates]
    report = {
        "scope": {"routes": sorted(ROUTES), "shifts": len(shift_ids),
                  "candidate_stations": len(candidates)},
        "relative_gaps": RELATIVE_GAPS,
        "limits": {"solver_seconds": 1200, "pipeline_seconds": 1500,
                   "absolute_gap": 0.0},
        "runs": {},
    }
    _write(report)

    for mode in MODES:
        for gap in RELATIVE_GAPS:
            body = {
                "name": f"One-off tolerance {mode} routes 401-404-407 gap {gap:g}",
                "mode": mode,
                "shift_ids": shift_ids,
                "prediction_run_ids": prediction_ids,
                "charging_stations": fixed if mode == "battery_only" else candidates,
                "min_soc": 0.4,
                "max_soc": 0.9,
                "lock_entire_dwell": True,
                "solver_name": "highs",
                "max_solver_time_seconds": 1200,
                "optimization_time_budget_seconds": 1500,
                "mip_rel_gap": gap,
                "mip_abs_gap": 0.0,
                "evaluate_both_bases": True,
            }
            if mode == "joint":
                body.update(battery_cost_per_kwh=300.0,
                            max_battery_penalty_per_kwh=1e6,
                            battery_sizing_mode="per_route")
            started = time.monotonic()
            response = client.post(
                f"{fleet.SIM_BASE}/optimization-runs/",
                json=body,
                headers=fleet.auth_headers(token),
            )
            assert response.status_code == 200, response.text
            ids = response.json()["basis_run_ids"]
            benchmark_env["optimization_run_ids"].extend(ids.values())
            outcomes = _terminal_pair(client, token, ids)
            wall_seconds = time.monotonic() - started
            for basis, run in outcomes.items():
                report["runs"][f"{mode}|{gap:g}|{basis}"] = _summary(
                    run, wall_seconds=wall_seconds)
            _add_reference_errors(report)
            _write(report)

    _add_reference_errors(report)
    _write(report)
    assert len(report["runs"]) == len(MODES) * len(RELATIVE_GAPS) * 2
