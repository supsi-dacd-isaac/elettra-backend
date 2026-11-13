"""
Optimization service: orchestrates prediction lookup, data preparation, and MILP solving.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from elettra_core import PASSENGER_MASS_KG
from app.core.config import get_cached_settings

from app.models import (
    Buses,
    BusesModels,
    Depots,
    GtfsStops,
    GtfsStopsTimes,
    GtfsTrips,
    OptimizationRuns,
    PredictionRuns,
    Shifts,
    ShiftsStructures,
    TripPredictions,
)
from simulation.battery_policy import historical_battery_params, operating_assumptions, policy_from_params
from simulation.optimization_model import (
    BusData,
    OptimizationConfig,
    OptimizationResult,
    StationData,
    TripData,
    solve_optimization,
)
from simulation.optimization_contract import CONTRACT_VERSION, pack_count, select_references
from simulation.optimization_verification import optimize_and_verify
from simulation.infeasibility_diagnostics import diagnose_infeasible_cycle
from simulation.aggregate_consumption import AggregateOptions, PredictiveDistributionUnavailable
from simulation.energy_analysis import build_energy_analysis
from app.services.aggregate_consumption import load_aggregate_profiles
from app.services.runtime_release import PredictionStack, resolve_prediction_selection
from app.services.optimization_process import run_workers, threads_per_worker

logger = logging.getLogger(__name__)


def effective_mip_rel_gap(params: dict) -> float:
    """Keep explicit request values (including zero); otherwise use runtime default."""
    value = params.get("mip_rel_gap")
    return get_cached_settings().optimization_mip_rel_gap if value is None else value


class OptimizationInputValidationError(ValueError):
    """Structured validation failure raised before the solver is started."""

    def __init__(self, issues: list[dict], shifts: list[dict]):
        if not issues:
            raise ValueError("OptimizationInputValidationError requires at least one issue")
        self.issues = [dict(issue) for issue in issues]
        self.shifts = [dict(shift) for shift in shifts]
        super().__init__("; ".join(str(issue["message"]) for issue in self.issues))

    def failure_payload(self) -> dict:
        failed_by_shift: dict[str, list[str]] = {}
        for issue in self.issues:
            failed_by_shift.setdefault(str(issue["shift_id"]), []).append(str(issue["code"]))

        blocking_shift_ids = list(failed_by_shift)
        shift_outcomes = []
        for shift in self.shifts:
            shift_id = str(shift["shift_id"])
            outcome = {
                "shift_id": shift_id,
                "shift_name": str(shift["shift_name"]),
            }
            if shift_id in failed_by_shift:
                outcome.update(
                    status="validation_failed",
                    failure_codes=failed_by_shift[shift_id],
                )
            else:
                outcome.update(
                    status="not_evaluated",
                    blocked_by_shift_ids=blocking_shift_ids,
                )
            shift_outcomes.append(outcome)

        return {
            "code": "input_validation_failed",
            "phase": "input_validation",
            "issues": self.issues,
            "shift_outcomes": shift_outcomes,
        }


def _daily_cycle_validation_issues(
    boundaries: list[dict],
    *,
    depot_stop_ids: set[str],
    selected_station_stop_ids: set[str],
    stop_name_map: dict[str, str],
) -> list[dict]:
    """Return every per-shift daily-cycle issue without stopping at the first."""

    issues = []
    for boundary in boundaries:
        shift_id = str(boundary["shift_id"])
        shift_name = str(boundary["shift_name"])
        start_stop_id = str(boundary["first_stop_id"])
        end_stop_id = str(boundary["last_stop_id"])
        details = {
            "start_stop_id": start_stop_id,
            "start_stop_name": stop_name_map.get(start_stop_id, start_stop_id),
            "end_stop_id": end_stop_id,
            "end_stop_name": stop_name_map.get(end_stop_id, end_stop_id),
        }

        if start_stop_id != end_stop_id:
            issues.append({
                "code": "daily_cycle_depot_mismatch",
                "shift_id": shift_id,
                "shift_name": shift_name,
                "message": (
                    f"{shift_name}: daily repetition requires the existing trips "
                    "to start and end at the same depot; no transfers are added"
                ),
                "details": details,
            })
        elif start_stop_id not in depot_stop_ids:
            issues.append({
                "code": "daily_cycle_stop_not_depot",
                "shift_id": shift_id,
                "shift_name": shift_name,
                "message": (
                    f"{shift_name}: daily repetition requires the common start/end "
                    "stop to be a registered depot"
                ),
                "details": details,
            })
        elif start_stop_id not in selected_station_stop_ids:
            issues.append({
                "code": "daily_cycle_depot_not_selected",
                "shift_id": shift_id,
                "shift_name": shift_name,
                "message": f"{shift_name}: select the start/end depot in charging infrastructure",
                "details": details,
            })
    return issues


def _failed_run_results(
    error: Exception,
    *,
    execution_metadata: dict | None,
    finished_at: datetime,
) -> dict:
    """Serialize a failed run while retaining the legacy error string."""

    results = {"error": str(error)}
    if isinstance(error, OptimizationInputValidationError):
        results["failure"] = error.failure_payload()
    if execution_metadata:
        results["execution"] = {
            **execution_metadata,
            "finished_at": finished_at.isoformat(),
        }
    return results


_COMPONENT_BREAKDOWN_KEYS = (
    "mechanical_greybox_kwh",
    "qrf_residual_kwh",
    "fixed_auxiliary_kwh",
    "hvac_electrical_kwh",
    "diesel_fuel_kwh",
    "diesel_liters",
    "uncovered_thermal_kwh",
)


def _aggregate_prediction_components(
    breakdowns: list[dict | None], *, solver_consumption: str
) -> dict | None:
    """Aggregate mean prediction components without mislabelling quantiles."""

    present = [value for value in breakdowns if value]
    if not present:
        return None
    if len(present) != len(breakdowns):
        raise ValueError(
            "prediction component breakdown is only partially available"
        )
    totals = {key: 0.0 for key in _COMPONENT_BREAKDOWN_KEYS}
    for value in present:
        for key in _COMPONENT_BREAKDOWN_KEYS:
            raw = value.get(key, 0.0)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"invalid prediction component {key}")
            totals[key] += float(raw)
    return {
        "basis": "mean_prediction_components",
        "solver_consumption": solver_consumption,
        "trip_prediction_count": len(present),
        "totals": totals,
    }


async def _prediction_component_summary(
    db: AsyncSession,
    prediction_run_ids: list[UUID],
    *,
    solver_consumption: str,
) -> dict | None:
    result = await db.execute(
        select(TripPredictions.component_breakdown).where(
            TripPredictions.prediction_run_id.in_(prediction_run_ids)
        )
    )
    result = _aggregate_prediction_components(
        list(result.scalars().all()), solver_consumption=solver_consumption
    )
    if result is not None:
        from app.services.diesel_heating import diesel_liters_from_kwh
        rows = await db.execute(select(PredictionRuns).where(PredictionRuns.id.in_(prediction_run_ids)))
        runs = list(rows.scalars().all())
        if any((r.contextual_parameters or {}).get('hvac_inputs') for r in runs):
            totals = result['totals']
            totals['hvac_in_trip_electrical_kwh'] = totals['hvac_electrical_kwh']
            for target, source in [('hvac_terminal_electrical_kwh', 'total_hvac_terminal_electrical_kwh'),
                                   ('hvac_terminal_diesel_fuel_kwh', 'total_hvac_terminal_diesel_fuel_kwh'),
                                   ('hvac_terminal_uncovered_thermal_kwh', 'total_hvac_terminal_uncovered_thermal_kwh')]:
                totals[target] = sum(float((r.summary or {}).get(source, 0)) for r in runs)
            totals['hvac_electrical_kwh'] += totals['hvac_terminal_electrical_kwh']
            totals['diesel_fuel_kwh'] += totals['hvac_terminal_diesel_fuel_kwh']
            totals['diesel_liters'] += diesel_liters_from_kwh(totals['hvac_terminal_diesel_fuel_kwh'])
            totals['uncovered_thermal_kwh'] += totals['hvac_terminal_uncovered_thermal_kwh']
    return result


def _time_str_to_minutes(time_str: str | None) -> int | None:
    if not time_str:
        return None
    try:
        h, m, s = map(int, time_str.split(":"))
        return h * 60 + m
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Auto-prediction: find or create prediction runs
# ---------------------------------------------------------------------------

async def _resolve_bus_model_id(db: AsyncSession, shift_id: UUID) -> UUID | None:
    """Resolve bus_model_id from shift -> bus -> bus_model chain."""
    shift = await db.get(Shifts, shift_id)
    if shift is None or shift.bus_id is None:
        return None
    bus = await db.get(Buses, shift.bus_id)
    if bus is None:
        return None
    return bus.bus_model_id


async def ensure_predictions(
    db: AsyncSession,
    user_id: UUID,
    shift_ids: list[UUID],
    bus_model_id: UUID | None,
    prediction_params: dict,
    *, pending_jobs=None, statistics_cache=None, trip_cache=None, hvac_cache=None,
) -> list[UUID]:
    """
    For each shift, find a completed prediction run matching the given parameters.
    If not found, create and execute one. Returns the list of prediction_run_ids.
    """
    from app.services.prediction import predict_consumption_jobs

    jobs = pending_jobs if pending_jobs is not None else []

    selected_stack = resolve_prediction_selection(
        prediction_stack=prediction_params.get("prediction_stack"),
        model_name=str(prediction_params["model_name"]),
    )
    requested_quantiles = [
        float(value)
        for value in prediction_params.get("quantiles", [0.05, 0.5, 0.95])
    ]
    requested_packs = prediction_params.get("num_battery_packs")
    from app.services.hvac_policy import LEGACY_POLICY, resolve_policy, run_policy, fingerprint
    from app.services.hvac_context import load_schedules
    hvac_cache = hvac_cache if hvac_cache is not None else {}
    policy = resolve_policy(prediction_params.get('auxiliary_policy_id'),
        annual=prediction_params.get('yearly_analysis_id') is not None,
        legacy_stack=selected_stack.stack is PredictionStack.LEGACY)
    inherited_inputs = prediction_params.get('hvac_inputs')
    expected_inputs = {}
    if policy != LEGACY_POLICY:
        if inherited_inputs:
            if len(shift_ids) != 1 or inherited_inputs['policy_id'] != policy:
                raise ValueError('HVAC inherited inputs must describe one matching duty')
            expected_inputs[str(shift_ids[0])] = inherited_inputs
        else:
            key = ('current-schedules', str(user_id), tuple(map(str, shift_ids)))
            if key not in hvac_cache:
                hvac_cache[key] = await load_schedules(db, user_id, shift_ids)
            expected_inputs = {sid: dict(schedule=schedule, policy_id=policy)
                               for sid, schedule in hvac_cache[key].items()}

    run_ids: list[UUID] = []
    for shift_id in shift_ids:
        resolved_model_id = bus_model_id
        if resolved_model_id is None:
            resolved_model_id = await _resolve_bus_model_id(db, shift_id)
        if resolved_model_id is None:
            raise ValueError(
                f"Cannot determine bus_model_id for shift {shift_id}. "
                "Provide bus_model_id in the request or assign a bus with a model to the shift."
            )
        resolved_bus_model = await db.get(BusesModels, resolved_model_id)
        if resolved_bus_model is None:
            raise ValueError(f"Bus model {resolved_model_id} no longer exists")
        specs = resolved_bus_model.specs or {}
        effective_packs = int(
            requested_packs
            if requested_packs is not None
            else specs.get("max_battery_packs", 14)
        )
        expected_capacity = float(specs.get("battery_pack_size_kwh", 37)) * effective_packs
        expected_bus_length = float(specs.get("bus_length_m", 18))
        expected_weight = (
            float(specs.get("empty_weight_kg", 18000))
            + effective_packs * float(specs.get("battery_pack_weight_kg", 253))
            + float(specs.get("max_passengers", 120))
            * float(prediction_params["occupancy_percent"])
            / 100.0
            * (
                70.0
                if selected_stack.stack is PredictionStack.LEGACY
                else PASSENGER_MASS_KG
            )
        )

        result = await db.execute(
            select(PredictionRuns).where(
                and_(
                    PredictionRuns.shift_id == shift_id,
                    PredictionRuns.user_id == user_id,
                    PredictionRuns.bus_model_id == resolved_model_id,
                    PredictionRuns.model_name == prediction_params["model_name"],
                    PredictionRuns.prediction_stack == selected_stack.stack.value,
                    or_(
                        PredictionRuns.auxiliary_estimator_release
                        == selected_stack.auxiliary_estimator,
                        and_(
                            selected_stack.stack.value == "legacy",
                            PredictionRuns.auxiliary_estimator_release.is_(None),
                        ),
                    ),
                    PredictionRuns.external_temp_celsius == prediction_params["external_temp_celsius"],
                    PredictionRuns.occupancy_percent == prediction_params["occupancy_percent"],
                    PredictionRuns.auxiliary_heating_type == prediction_params["auxiliary_heating_type"],
                    PredictionRuns.status == "completed",
                )
            ).order_by(PredictionRuns.created_at.desc())
        )
        existing = None
        for candidate in result.scalars().all():
            context = candidate.contextual_parameters or {}
            if run_policy(candidate) != policy:
                continue
            if policy != LEGACY_POLICY and context.get('hvac_input_fingerprint') != fingerprint(expected_inputs[str(shift_id)]):
                continue
            context_quantiles = context.get("quantiles")
            if not isinstance(context_quantiles, list) or [
                float(value) for value in context_quantiles
            ] != requested_quantiles:
                continue
            if context.get("num_battery_packs") != effective_packs:
                continue
            try:
                context_capacity = float(context["battery_capacity_kwh"])
                context_weight = float(context["total_weight_kg"])
                context_bus_length = float(context["bus_length_m"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isclose(
                context_capacity,
                expected_capacity,
                rel_tol=0.0,
                abs_tol=1e-9,
            ) or not math.isclose(
                context_bus_length,
                expected_bus_length,
                rel_tol=0.0,
                abs_tol=1e-9,
            ) or not math.isclose(
                context_weight,
                expected_weight,
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                continue
            existing = candidate
            break
        if existing:
            logger.info("Reusing prediction run %s for shift %s", existing.id, shift_id)
            run_ids.append(existing.id)
            continue

        logger.info("Creating new prediction for shift %s", shift_id)
        run = PredictionRuns(
            user_id=user_id,
            shift_id=shift_id,
            bus_model_id=resolved_model_id,
            model_name=prediction_params["model_name"],
            prediction_stack=selected_stack.stack.value,
            auxiliary_estimator_release=selected_stack.auxiliary_estimator,
            external_temp_celsius=prediction_params["external_temp_celsius"],
            auxiliary_heating_type=prediction_params["auxiliary_heating_type"],
            occupancy_percent=prediction_params["occupancy_percent"],
            yearly_analysis_id=prediction_params.get('yearly_analysis_id'),
            contextual_parameters={'auxiliary_policy_id': policy, **({
                'hvac_inputs': expected_inputs[str(shift_id)],
                'hvac_input_fingerprint': fingerprint(expected_inputs[str(shift_id)]),
            } if policy != LEGACY_POLICY else {})},
            status="pending",
        )
        db.add(run)
        await db.flush()
        jobs.append((run.id, effective_packs))
        run_ids.append(run.id)

    if pending_jobs is None and jobs:
        await db.commit()
        await predict_consumption_jobs(db, jobs, requested_quantiles,
                                       statistics_cache=statistics_cache, trip_cache=trip_cache, hvac_cache=hvac_cache)

    return run_ids


# ---------------------------------------------------------------------------
# Prepare optimization input from DB data
# ---------------------------------------------------------------------------

def _get_consumption_value(pred: TripPredictions, quantile_consumption: str) -> float:
    """Extract the requested consumption value from a TripPredictions row."""
    if quantile_consumption == "mean":
        return float(pred.prediction_kwh)
    if quantile_consumption == "median":
        if pred.prediction_median_kwh is None:
            raise ValueError("prediction row has no median consumption")
        return float(pred.prediction_median_kwh)
    q_data = pred.quantiles or {}
    val = q_data.get(quantile_consumption)
    if val is not None:
        return float(val)
    raise ValueError(
        f"prediction row has no requested quantile {quantile_consumption!r}"
    )


def _prediction_provenance(
    prediction_runs: list[PredictionRuns],
) -> dict[str, object]:
    """Validate model/stack/aux identity before optimization consumes energy."""

    from app.services.hvac_policy import run_policy
    identities = set()
    for run in prediction_runs:
        selected = resolve_prediction_selection(
            prediction_stack=getattr(run, "prediction_stack", None) or None,
            model_name=run.model_name,
        )
        stored_estimator = getattr(run, "auxiliary_estimator_release", None)
        legacy_unversioned = (
            stored_estimator is None and selected.stack.value == "legacy"
        )
        if not legacy_unversioned and stored_estimator != selected.auxiliary_estimator:
            raise ValueError(
                f"Prediction run {run.id} auxiliary estimator does not match "
                "its registered prediction stack"
            )
        identities.add(
            (
                selected.stack.value,
                selected.model_release,
                selected.auxiliary_estimator,
                run_policy(run),
            )
        )
    if len(identities) != 1:
        raise ValueError(
            "Optimization prediction runs must share one model, stack and "
            "auxiliary estimator"
        )
    stack, model, estimator, policy = next(iter(identities))
    return {
        "prediction_stack": stack,
        "model_release": model,
        "auxiliary_estimator_release": estimator,
        "auxiliary_policy_id": policy,
        "prediction_run_ids": [str(run.id) for run in prediction_runs],
    }


async def prepare_optimization_input(
    db: AsyncSession,
    optimization_run: OptimizationRuns,
    prediction_run_ids: list[UUID],
    *, reference_ids: dict[str, str] | None = None,
) -> tuple[list[BusData], list[StationData], OptimizationConfig]:
    """Load all data from DB and build structured inputs for the solver."""
    params = historical_battery_params(optimization_run.input_params)
    quantile_consumption = params.get("quantile_consumption", "mean")

    _bus_model_cache: dict[UUID, dict] = {}

    async def _get_specs_for_bus_model(bus_model_id: UUID) -> dict:
        """Load and cache specs for an explicit simulation bus model."""
        if bus_model_id in _bus_model_cache:
            return _bus_model_cache[bus_model_id]
        model = await db.get(BusesModels, bus_model_id)
        if model is None:
            raise ValueError(f"Bus model {bus_model_id} not found")
        specs = {**(model.specs or {}), "_bus_model_id": str(bus_model_id),
                 "_bus_model_name": getattr(model, "name", None)}
        _bus_model_cache[bus_model_id] = specs
        return specs

    # Build station lookup: stop_id (str) -> station config
    charging_stations_raw = params.get("charging_stations", [])
    stop_ids_needed = [cs["stop_id"] for cs in charging_stations_raw]

    # Resolve stop names
    stop_name_map: dict[str, str] = {}
    if stop_ids_needed:
        result = await db.execute(
            select(GtfsStops.id, GtfsStops.stop_name).where(
                GtfsStops.id.in_(stop_ids_needed)
            )
        )
        for row in result.all():
            stop_name_map[str(row[0])] = row[1] or str(row[0])

    station_list: list[StationData] = []
    for cs in charging_stations_raw:
        sid = cs["stop_id"]
        station_list.append(StationData(
            stop_id=str(sid),
            stop_name=stop_name_map.get(str(sid), str(sid)),
            slot_costs_chf=cs.get("slot_costs_chf") or [],
            cost_model=cs.get("cost_model"),
            num_fixed_slots=cs.get("num_slots"),
            max_total_power_kw=cs.get("max_total_power_kw", 450),
            max_power_per_slot_kw=cs.get("max_power_per_slot_kw"),
        ))
    station_stop_id_to_idx = {s.stop_id: i for i, s in enumerate(station_list)}
    depot_stop_ids = set()
    if station_list or params.get("daily_cycle"):
        depot_stop_ids = {str(sid) for sid in (await db.execute(select(Depots.stop_id).where(
            Depots.user_id == optimization_run.user_id))).scalars() if sid is not None}

    for station in station_list:
        station.is_depot = station.stop_id in depot_stop_ids

    prediction_runs: list[PredictionRuns] = []
    for pred_run_id in prediction_run_ids:
        pred_run = await db.get(PredictionRuns, pred_run_id)
        if pred_run is None:
            raise ValueError(f"Prediction run {pred_run_id} not found")
        prediction_runs.append(pred_run)
    _prediction_provenance(prediction_runs)
    selected = select_references(
        prediction_runs, params["shift_ids"], mode=optimization_run.mode,
        user_id=optimization_run.user_id,
        explicit=reference_ids or params.get("reference_prediction_run_ids"),
    )
    # Compare the complete ordered occurrence sequence, not just unique trips.
    # This rejects stale/partial catalogues and preserves repeated trip IDs.
    trip_predictions = {}
    expected_by_shift = {}
    for pred in prediction_runs:
        sid = str(pred.shift_id)
        if sid not in expected_by_shift:
            rows = await db.execute(select(ShiftsStructures).where(
                ShiftsStructures.shift_id == pred.shift_id
            ).order_by(ShiftsStructures.sequence_number))
            expected_by_shift[sid] = [(r.sequence_number, str(r.trip_id)) for r in rows.scalars().all()]
        rows = await db.execute(select(TripPredictions).where(
            TripPredictions.prediction_run_id == pred.id
        ).order_by(TripPredictions.sequence_number))
        predictions = list(rows.scalars().all())
        if not predictions or [(r.sequence_number, str(r.trip_id)) for r in predictions] != expected_by_shift[sid]:
            raise ValueError(f"Prediction {pred.id} does not cover the current complete shift sequence")
        for row in predictions:
            energy = _get_consumption_value(row, quantile_consumption)
            sensitivity = row.mass_sensitivity_kwh_per_kwh_batt
            if sensitivity is None or not math.isfinite(float(sensitivity)) or not math.isfinite(energy):
                raise ValueError(f"Prediction {pred.id} has missing or non-finite energy/mass sensitivity")
        trip_predictions[str(pred.id)] = predictions
    prediction_runs = list(selected.values())
    from app.services.hvac_context import load_schedules
    from app.services.hvac_policy import LEGACY_POLICY, run_policy, fingerprint
    operational_runs = [r for r in prediction_runs if run_policy(r) != LEGACY_POLICY]
    if operational_runs:
        schedules = await load_schedules(db, optimization_run.user_id, [r.shift_id for r in operational_runs])
        for pred in operational_runs:
            context = pred.contextual_parameters or {}
            inputs = context.get('hvac_inputs') or {}
            if (fingerprint(inputs) != context.get('hvac_input_fingerprint')
                    or inputs.get('schedule') != schedules[str(pred.shift_id)]):
                raise ValueError('HVAC timetable changed; recalculate predictions')
            if 'hvac_terminal_intervals' not in context:
                raise ValueError('Operational HVAC prediction is missing its terminal timeline')

    # Build every physical trip sequence before validating the daily cycle.  A
    # fleet evaluation is one solver problem, so one invalid vehicle block
    # prevents the solver from starting; collecting all invalid blocks here
    # lets the result identify the actual causes without labelling every block
    # as failed.
    prepared_trip_data: dict[str, dict] = {}
    shift_contexts: list[dict] = []
    daily_cycle_boundaries: list[dict] = []
    for pred_run in prediction_runs:
        shift = await db.get(Shifts, pred_run.shift_id)
        shift_name = shift.name if shift else str(pred_run.shift_id)
        shift_context = {
            "shift_id": str(pred_run.shift_id),
            "shift_name": shift_name,
        }
        shift_contexts.append(shift_context)

        trip_data_list: list[TripData] = []
        first_stop_id = last_stop_id = None
        for tp in trip_predictions[str(pred_run.id)]:
            stop_times_result = await db.execute(
                select(GtfsStopsTimes)
                .where(GtfsStopsTimes.trip_id == tp.trip_id)
                .order_by(GtfsStopsTimes.stop_sequence)
            )
            stop_times = stop_times_result.scalars().all()
            if not stop_times:
                raise ValueError(f"Trip {tp.trip_id} has no stop times")

            first_st = stop_times[0]
            last_st = stop_times[-1]
            dep_min = _time_str_to_minutes(first_st.departure_time)
            arr_min = _time_str_to_minutes(last_st.arrival_time)
            if dep_min is None or arr_min is None:
                raise ValueError(f"Trip {tp.trip_id} has invalid stop times")

            end_stop_id = str(last_st.stop_id)
            if first_stop_id is None:
                first_stop_id = str(first_st.stop_id)
            last_stop_id = end_stop_id
            trip_data_list.append(TripData(
                trip_id=str(tp.trip_id),
                departure_minute=dep_min,
                arrival_minute=arr_min,
                end_station_idx=station_stop_id_to_idx.get(end_stop_id, -1),
                base_energy_kwh=_get_consumption_value(tp, quantile_consumption),
                sensitivity=float(tp.mass_sensitivity_kwh_per_kwh_batt),
            ))

        prepared_trip_data[str(pred_run.id)] = {
            **shift_context,
            "trips": trip_data_list,
            "first_stop_id": first_stop_id,
            "last_stop_id": last_stop_id,
        }
        daily_cycle_boundaries.append({
            **shift_context,
            "first_stop_id": first_stop_id,
            "last_stop_id": last_stop_id,
        })

    if params.get("daily_cycle"):
        boundary_stop_ids = {
            stop_id
            for boundary in daily_cycle_boundaries
            for stop_id in (boundary["first_stop_id"], boundary["last_stop_id"])
            if stop_id is not None
        }
        if boundary_stop_ids:
            result = await db.execute(
                select(GtfsStops.id, GtfsStops.stop_name).where(
                    GtfsStops.id.in_(boundary_stop_ids)
                )
            )
            stop_name_map.update({
                str(stop_id): stop_name or str(stop_id)
                for stop_id, stop_name in result.all()
            })
        issues = _daily_cycle_validation_issues(
            daily_cycle_boundaries,
            depot_stop_ids=depot_stop_ids,
            selected_station_stop_ids=set(station_stop_id_to_idx),
            stop_name_map=stop_name_map,
        )
        if issues:
            raise OptimizationInputValidationError(issues, shift_contexts)

    explicit_bus_model_id: UUID | None = optimization_run.bus_model_id
    if explicit_bus_model_id is not None:
        mismatched_prediction_ids = [
            str(pred.id)
            for pred in prediction_runs
            if pred.bus_model_id != explicit_bus_model_id
        ]
        if mismatched_prediction_ids:
            raise ValueError(
                "Prediction run bus_model_id does not match optimization bus_model_id "
                f"{explicit_bus_model_id}: {', '.join(mismatched_prediction_ids)}"
            )

    shared_specs = (
        await _get_specs_for_bus_model(explicit_bus_model_id)
        if explicit_bus_model_id is not None
        else None
    )

    # Build per-bus data from prediction runs.
    # Precedence for physical bus specs:
    # explicit optimization bus_model_id -> prediction bus_model_id -> shift bus model.
    buses: list[BusData] = []

    async def _get_specs_for_shift(shift_id: UUID) -> dict:
        """Resolve bus model specs for a shift via shift->bus->bus_model."""
        shift = await db.get(Shifts, shift_id)
        if shift is None:
            return shared_specs or {}
        bus = await db.get(Buses, shift.bus_id) if shift.bus_id else None
        if bus is None or bus.bus_model_id is None:
            return shared_specs or {}
        return await _get_specs_for_bus_model(bus.bus_model_id)

    for pred_run in prediction_runs:
        prepared = prepared_trip_data[str(pred_run.id)]
        shift_name = prepared["shift_name"]

        # Per-bus specs
        specs = (
            shared_specs
            if shared_specs is not None
            else await _get_specs_for_bus_model(pred_run.bus_model_id)
            if pred_run.bus_model_id is not None
            else await _get_specs_for_shift(pred_run.shift_id)
        )
        pack_size = float(specs.get("battery_pack_size_kwh", 37))
        min_packs_spec = int(specs.get("min_battery_packs", 10))
        max_packs_spec = int(specs.get("max_battery_packs", 14))
        max_charging_power = float(specs.get("max_charging_power_kw", 450))
        pack_cost = specs.get("battery_pack_cost_chf")
        if pack_cost is None and params.get("battery_cost_per_kwh") is not None:
            pack_cost = float(params["battery_cost_per_kwh"]) * pack_size
        if pack_cost is not None:
            pack_cost = float(pack_cost)
            if not math.isfinite(pack_cost) or pack_cost < 0:
                raise ValueError(f"{shift_name}: invalid battery pack cost in the bus model")
        if optimization_run.mode == "joint" and pack_cost is None:
            raise ValueError(f"{shift_name}: set battery_pack_cost_chf in the bus model before joint optimization")
        bus_cost = specs.get("cost")
        if bus_cost is not None:
            bus_cost = float(bus_cost)
            if not math.isfinite(bus_cost) or bus_cost < 0:
                raise ValueError(f"{shift_name}: invalid bus cost in the bus model")

        ctx = pred_run.contextual_parameters or {}
        ref_packs = pack_count(pred_run)
        ref_capacity = ctx.get("battery_capacity_kwh")
        if ref_capacity is None or not math.isclose(float(ref_capacity), ref_packs * pack_size, abs_tol=1e-6):
            raise ValueError("Prediction capacity does not match the physical bus model")

        configured_capacity = max_packs_spec * pack_size
        battery_offset = configured_capacity - ref_capacity

        trip_data_list = prepared["trips"]
        last_stop_id = prepared["last_stop_id"]
        terminal_timeline = {}
        for interval in ctx.get('hvac_terminal_intervals', []):
            for item in interval['minute_energy']:
                minute = item['minute']
                terminal_timeline[minute] = terminal_timeline.get(minute, 0.) + item['electrical_kwh']
        buses.append(BusData(
            shift_id=str(pred_run.shift_id),
            shift_name=shift_name,
            battery_capacity_kwh=configured_capacity,
            battery_offset_kwh=battery_offset,
            max_charging_power_kw=max_charging_power,
            pack_size_kwh=pack_size,
            min_packs=min_packs_spec,
            max_packs=max_packs_spec,
            reference_packs=int(ref_packs),
            trips=trip_data_list,
            depot_station_idx=station_stop_id_to_idx.get(last_stop_id, -1),
            battery_pack_cost_chf=pack_cost,
            bus_cost_chf=bus_cost,
            bus_model_id=specs.get("_bus_model_id"),
            bus_model_name=specs.get("_bus_model_name"),
            terminal_hvac_kwh=terminal_timeline,
        ))

    # Build OptimizationConfig
    opt_config = OptimizationConfig(
        mode=optimization_run.mode,
        min_soc=params.get("min_soc", 0.4),
        max_soc=params.get("max_soc", 0.9),
        state_of_health=params.get("state_of_health", 1.0),
        battery_soc_policy=params["battery_soc_policy"],
        allowed_min_soc=params["allowed_min_soc"], allowed_max_soc=params["allowed_max_soc"],
        layover_reduction_minutes=params["layover_reduction_minutes"],
        battery_cost_per_kwh=params.get("battery_cost_per_kwh") or 0.0,
        max_battery_penalty_per_kwh=params.get("max_battery_penalty_per_kwh", 1e6),
        battery_sizing_mode=params.get("battery_sizing_mode", "per_bus"),
        min_session_duration_minutes=params.get("min_session_duration_minutes", 0),
        session_connection_minutes=params.get("session_connection_minutes", 0),
        lock_entire_dwell=params.get("lock_entire_dwell", True),
        cp_slack_minutes=params.get("cp_slack_minutes", 0),
        session_penalty_weight=params.get("session_penalty_weight", 0.01),
        early_charging_weight=params.get("early_charging_weight", 0.0),
        soc_increase_weight=params.get("soc_increase_weight", 1e4),
        depot_dwell_minutes_after=params.get("depot_dwell_minutes_after", 0),
        daily_cycle=params.get("daily_cycle", False),
        depot_peak_shaving=params.get("depot_peak_shaving", False),
        solver_name=params.get("solver_name", "highs"),
        solver_threads=threads_per_worker(),
        max_solver_time_seconds=params.get("max_solver_time_seconds"),
        mip_rel_gap=effective_mip_rel_gap(params),
        mip_abs_gap=params.get("mip_abs_gap"),
        feasibility_tol=params.get("feasibility_tol"),
        optimality_tol=params.get("optimality_tol"),
    )

    return buses, station_list, opt_config


# ---------------------------------------------------------------------------
# Main optimization workflow
# ---------------------------------------------------------------------------

async def run_optimization(
    db: AsyncSession,
    optimization_run_id: UUID,
    *, profile_cache=None, statistics_cache=None, execution_metadata=None,
) -> None:
    """Execute the full optimization pipeline for a given run."""
    run = await db.get(OptimizationRuns, optimization_run_id)
    if run is None:
        raise ValueError(f"Optimization run {optimization_run_id} not found")

    try:
        run.status = "running"
        # Persist the effective default in the existing JSON for reproducibility.
        run.input_params = {**run.input_params,
                            "mip_rel_gap": effective_mip_rel_gap(run.input_params)}
        await db.commit()

        params = historical_battery_params(run.input_params)
        run.input_params = {**params, "operating_assumptions": operating_assumptions(params)}
        await db.commit()
        pipeline_started = time.monotonic()
        basis = params.get("consumption_basis") or (
            "mean" if params.get("quantile_consumption", "mean") == "mean" else "legacy_trip_quantile")
        aggregate_options = AggregateOptions.from_params(params)
        profile_cache = {} if profile_cache is None else profile_cache
        statistics_cache = {} if statistics_cache is None else statistics_cache
        budget = params.get("optimization_time_budget_seconds", 900)

        forecast_trip_cache = {}
        hvac_cache = {}

        async def profiles_for(references):
            requests = []
            for pid in dict.fromkeys(references.values()):
                if pid in profile_cache:
                    continue
                prediction = await db.get(PredictionRuns, UUID(pid))
                sampling_options = aggregate_options
                if not params.get("charging_stations"):
                    model = await db.get(BusesModels, prediction.bus_model_id)
                    pack_kwh = (model.specs or {}).get("battery_pack_size_kwh") if model else None
                    if pack_kwh:
                        sampling_options = replace(aggregate_options, capacity_step_kwh=float(pack_kwh) *
                            policy_from_params(params).usable_fraction_initial)
                requests.append((prediction, sampling_options))
            if requests:
                remaining = budget - (time.monotonic() - pipeline_started)
                if remaining <= 0:
                    raise TimeoutError("Aggregate forecast time budget exhausted")
                async with asyncio.timeout(remaining):
                    profile_cache.update(await load_aggregate_profiles(db, requests,
                        statistics_cache=statistics_cache, trip_cache=forecast_trip_cache, hvac_cache=hvac_cache))
            return {sid: profile_cache[pid] for sid, pid in references.items()}

        async def apply_basis(prepared, references):
            if basis != "aggregate_q95":
                return prepared
            profiles = await profiles_for(references)
            if any(len(bus.trips) != len(profiles[bus.shift_id][basis]) for bus in prepared):
                raise ValueError("Aggregate profile does not cover every trip occurrence")
            return [replace(bus, trips=[replace(trip, base_energy_kwh=energy)
                       for trip, energy in zip(bus.trips, profiles[bus.shift_id][basis])])
                    for bus in prepared]

        prediction_run_ids = run.prediction_run_ids

        # Auto-prediction if needed
        if not prediction_run_ids:
            pred_params = params.get("prediction_params", {})
            prediction_run_ids = await ensure_predictions(
                db=db,
                user_id=run.user_id,
                shift_ids=[UUID(sid) for sid in params["shift_ids"]],
                bus_model_id=run.bus_model_id,
                prediction_params=pred_params,
                statistics_cache=statistics_cache, trip_cache=forecast_trip_cache,
                hvac_cache=hvac_cache,
            )
            run.prediction_run_ids = [str(rid) for rid in prediction_run_ids]
            await db.commit()
        else:
            prediction_run_ids = [UUID(rid) if isinstance(rid, str) else rid for rid in prediction_run_ids]

        provenance_runs: list[PredictionRuns] = []
        for prediction_run_id in prediction_run_ids:
            prediction_run = await db.get(PredictionRuns, prediction_run_id)
            if prediction_run is None:
                raise ValueError(f"Prediction run {prediction_run_id} not found")
            provenance_runs.append(prediction_run)
        prediction_provenance = _prediction_provenance(provenance_runs)
        initial = select_references(
            provenance_runs, params["shift_ids"], mode=run.mode, user_id=run.user_id,
            explicit=params.get("reference_prediction_run_ids"),
        )
        initial_ids = {sid: str(pred.id) for sid, pred in initial.items()}
        quantile_consumption = str(
            (run.input_params or {}).get("quantile_consumption", "mean")
        )
        prediction_components = await _prediction_component_summary(
            db,
            [pred.id for pred in initial.values()],
            solver_consumption=basis if basis == "aggregate_q95" else quantile_consumption,
        )
        persisted_params = dict(run.input_params or {})
        persisted_params["prediction_provenance"] = prediction_provenance
        persisted_params["prediction_component_breakdown"] = prediction_components
        persisted_params["optimization_contract_version"] = CONTRACT_VERSION
        persisted_params["reference_prediction_run_ids"] = initial_ids
        run.input_params = persisted_params
        await db.commit()

        # Prepare and solve
        buses, stations, opt_config = await prepare_optimization_input(
            db, run, prediction_run_ids,
        )
        buses = await apply_basis(buses, initial_ids)
        exact_bus_cache = {}

        async def load_exact(packs):
            from app.services.prediction import predict_consumption_jobs

            exact_ids = {}
            pending = {}
            for sid, count in packs.items():
                candidates = [pred for pred in provenance_runs
                              if str(pred.shift_id) == sid and pack_count(pred) == count]
                if candidates:
                    pred = max(candidates, key=lambda item: str(item.id))
                else:
                    ref = initial[sid]
                    quantiles = (ref.contextual_parameters or {}).get("quantiles", [0.05, 0.5, 0.95])
                    contract = (ref.model_name, ref.prediction_stack, ref.auxiliary_estimator_release, tuple(quantiles))
                    jobs = pending.setdefault(contract, [])
                    created = await ensure_predictions(
                        db, run.user_id, [ref.shift_id], ref.bus_model_id,
                        {"model_name": ref.model_name, "prediction_stack": ref.prediction_stack,
                         "external_temp_celsius": ref.external_temp_celsius,
                         "occupancy_percent": ref.occupancy_percent,
                         "auxiliary_heating_type": ref.auxiliary_heating_type,
                         "auxiliary_policy_id": (ref.contextual_parameters or {}).get('auxiliary_policy_id', 'legacy-fixed-100-v1'),
                         "hvac_inputs": (ref.contextual_parameters or {}).get('hvac_inputs'),
                         "yearly_analysis_id": getattr(ref, 'yearly_analysis_id', None),
                         "quantiles": (ref.contextual_parameters or {}).get("quantiles", [0.05, 0.5, 0.95]),
                         "num_battery_packs": count},
                        pending_jobs=jobs, hvac_cache=hvac_cache,
                    )
                    pred = await db.get(PredictionRuns, created[0])
                    provenance_runs.append(pred)
                    prediction_run_ids.append(pred.id)
                exact_ids[sid] = str(pred.id)
            if any(pending.values()):
                await db.commit()
                for contract, jobs in pending.items():
                    if jobs:
                        await predict_consumption_jobs(db, jobs, list(contract[-1]),
                            statistics_cache=statistics_cache, trip_cache=forecast_trip_cache, hvac_cache=hvac_cache)
            run.prediction_run_ids = [str(pid) for pid in prediction_run_ids]
            await db.commit()
            exact_buses, _, _ = await prepare_optimization_input(
                db, run, [UUID(pid) for pid in exact_ids.values()], reference_ids=exact_ids,
            )
            exact_buses = await apply_basis(exact_buses, exact_ids)
            exact_bus_cache.update({bus.shift_id: bus for bus in exact_buses})
            return exact_buses, exact_ids

        async def progress(state):
            logger.info('Optimization phase: %s (iteration %s, elapsed %.1fs)',
                        state.get('phase'), state.get('iteration'), time.monotonic()-pipeline_started)
            run.results = {"contract_version": CONTRACT_VERSION,
                           **({"execution": execution_metadata} if execution_metadata else {}),
                           "forecast_verification": {"status": "running", **state}}
            await db.commit()

        result, verification = await optimize_and_verify(
            buses, stations, opt_config, load_exact,
            time_budget_seconds=max(.001, budget-(time.monotonic()-pipeline_started)),
            on_progress=progress,
        )
        diagnostics = None
        if result.solver_status == "infeasible":
            await progress({"phase": "infeasibility_diagnostics"})
            diagnostics = await diagnose_infeasible_cycle(
                buses, stations, opt_config, load_exact,
                time_budget_seconds=budget-(time.monotonic()-pipeline_started))
        selected_ids = verification["final_reference_prediction_run_ids"] or initial_ids
        selected_runs = [pred for pred in provenance_runs if str(pred.id) in selected_ids.values()]
        prediction_provenance = _prediction_provenance(selected_runs)
        prediction_components = await _prediction_component_summary(
            db, [pred.id for pred in selected_runs],
            solver_consumption=basis if basis == "aggregate_q95" else quantile_consumption,
        )
        analysis = {"status": "unavailable", "optimization_basis": basis,
                    "reason": "Historical per-trip basis; create a new aggregate evaluation"}
        if params.get("consumption_basis"):
            profiles = {}
            if verification["status"] == "verified":
                try:
                    profiles.update(await profiles_for(selected_ids))
                except (PredictiveDistributionUnavailable, TimeoutError) as exc:
                    profiles = {sid: {"error": str(exc)} for sid in selected_ids}
            analysis = await asyncio.to_thread(build_energy_analysis,
                [exact_bus_cache.get(bus.shift_id, bus) for bus in buses], result, profiles,
                opt_config, basis=basis, verified=verification["status"] == "verified")
        analysis["pipeline_elapsed_seconds"] = time.monotonic()-pipeline_started
        run.input_params = {**run.input_params,
                            "prediction_component_breakdown": prediction_components,
                            "prediction_provenance": prediction_provenance}

        run.results = {
            "contract_version": CONTRACT_VERSION,
            "initial_reference_prediction_run_ids": initial_ids,
            "selected_prediction_run_ids": selected_ids,
            "forecast_verification": verification,
            "consumption_analysis": analysis,
            "operating_assumptions": operating_assumptions(params),
            "objective_value": result.objective_value,
            "solver_status": result.solver_status,
            "solve_time_seconds": result.solve_time_seconds,
            "electrification_feasible": result.electrification_feasible,
            "electrification_summary": result.electrification_summary,
            "installed_chargers": result.installed_chargers,
            "total_installation_cost_chf": result.total_installation_cost_chf,
            "battery_results": result.battery_results,
            "total_battery_cost_chf": result.total_battery_cost_chf,
            "total_infeasibility_penalty_chf": result.total_infeasibility_penalty_chf,
            "per_bus_summary": result.per_bus_summary,
            "station_utilization": result.station_utilization,
            "charging_schedule_policy": result.charging_schedule_policy,
            "daily_cycle": result.daily_cycle,
            "infeasibility_diagnostics": diagnostics,
            "prediction_provenance": prediction_provenance,
            "prediction_component_breakdown": prediction_components,
        }
        if result.solver_status.startswith("error"):
            run.status = "failed"
        else:
            run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        if execution_metadata:
            run.results = {**run.results, "execution": {**execution_metadata,
                "finished_at": run.completed_at.isoformat()}}
        await db.commit()

    except Exception as e:
        logger.exception("Optimization failed for run %s: %s", optimization_run_id, e)
        try:
            await db.rollback()
            run = await db.get(OptimizationRuns, optimization_run_id)
            if run is not None:
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                run.results = _failed_run_results(
                    e,
                    execution_metadata=execution_metadata,
                    finished_at=run.completed_at,
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to update optimization run %s status to 'failed'", optimization_run_id)


# ---------------------------------------------------------------------------
# Background task wrapper
# ---------------------------------------------------------------------------

async def run_optimization_background(optimization_run_id: UUID, comparison_run_id: UUID | None = None) -> None:
    """Run each basis in an independent process; wait for both to finish."""
    await run_workers(*(key for key in (optimization_run_id, comparison_run_id) if key is not None))
