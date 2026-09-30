"""Physical-duty contract, independent of the database and prediction engine."""
from __future__ import annotations

import math
import json
from collections import defaultdict

CONTRACT_VERSION = "physical-shift-full-forecast-v1"


def pack_count(run) -> int:
    value = (run.contextual_parameters or {}).get("num_battery_packs")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Prediction {run.id} has no explicit battery pack count")
    if not math.isfinite(value) or value < 1 or int(value) != value:
        raise ValueError(f"Prediction {run.id} has an invalid battery pack count")
    return int(value)


def select_references(runs, shift_ids, *, mode, user_id, explicit=None):
    """Select one deterministic reference per duty, never per battery variant.

    All variants remain in the comparison catalogue. Physical inputs may differ
    across duties, but not between variants of the same duty.
    """
    shifts = [str(value) for value in shift_ids]
    if not shifts or len(shifts) != len(set(shifts)):
        raise ValueError("shift_ids must be non-empty and unique")
    groups = defaultdict(list)
    identities = defaultdict(set)
    for run in runs:
        sid = str(run.shift_id)
        if sid not in shifts or str(run.user_id) != str(user_id):
            raise ValueError("Prediction must belong to the requesting user and selected shift")
        if run.status != "completed":
            raise ValueError(f"Prediction {run.id} is not completed")
        pack_count(run)
        groups[sid].append(run)
        identities[sid].add(tuple(str(getattr(run, key, None)) for key in (
            "bus_model_id", "model_name", "prediction_stack", "auxiliary_estimator_release",
            "external_temp_celsius", "occupancy_percent", "auxiliary_heating_type",
        )) + (json.dumps({key: (run.contextual_parameters or {}).get(key)
                         for key in ("bus_length_m", "greybox_params", "quantiles")}, sort_keys=True),))
    if set(groups) != set(shifts):
        raise ValueError("Prediction catalogue must cover exactly the selected shifts")
    if any(len(values) != 1 for values in identities.values()):
        raise ValueError("Battery variants must share bus model, weather, occupancy, heater and model release")
    explicit = {str(k): str(v) for k, v in (explicit or {}).items()}
    if explicit and set(explicit) != set(shifts):
        raise ValueError("reference_prediction_run_ids must cover exactly the selected shifts")
    selected = {}
    for sid in sorted(shifts):
        candidates = groups[sid]
        if explicit:
            matches = [run for run in candidates if str(run.id) == explicit[sid]]
            if not matches:
                raise ValueError(f"Reference for shift {sid} is not in its prediction catalogue")
            selected[sid] = matches[0]
        else:
            if mode == "charging_only" and len({pack_count(run) for run in candidates}) != 1:
                raise ValueError("charging_only requires an explicit battery configuration per shift")
            selected[sid] = max(candidates, key=lambda run: (pack_count(run), str(run.id)))
    return selected


def result_integrity(results, shift_ids=None):
    """Read-only historical audit; never mutate or certify an old result."""
    results = results if isinstance(results, dict) else {}
    rows = results.get("per_bus_summary")
    if not isinstance(rows, list) or not rows:
        return {"status": "unverifiable", "yearly_eligible": False}
    ids = [str(row.get("shift_id")) for row in rows]
    if len(ids) != len(set(ids)):
        return {"status": "duplicate_physical_shifts", "yearly_eligible": False}
    expected = set(map(str, shift_ids)) if shift_ids else set(ids)
    if set(ids) != expected or set(results.get("battery_results", {})) != expected:
        return {"status": "incomplete_physical_shifts", "yearly_eligible": False}
    verification = results.get("forecast_verification") or {}
    verified = (results.get("contract_version") == CONTRACT_VERSION
                and verification.get("status") == "verified"
                and results.get("electrification_feasible") is True)
    return {"status": "verified" if verified else "unverified", "yearly_eligible": verified}
