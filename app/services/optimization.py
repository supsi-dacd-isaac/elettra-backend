"""
Optimization service: orchestrates prediction lookup, data preparation, and MILP solving.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import (
    Buses,
    BusesModels,
    GtfsStops,
    GtfsStopsTimes,
    GtfsTrips,
    OptimizationRuns,
    PredictionRuns,
    Shifts,
    ShiftsStructures,
    TripPredictions,
)
from simulation.optimization_model import (
    BusData,
    OptimizationConfig,
    OptimizationResult,
    StationData,
    TripData,
    solve_optimization,
)

logger = logging.getLogger(__name__)


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
) -> list[UUID]:
    """
    For each shift, find a completed prediction run matching the given parameters.
    If not found, create and execute one. Returns the list of prediction_run_ids.
    """
    from app.services.prediction import predict_shift_consumption

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

        result = await db.execute(
            select(PredictionRuns).where(
                and_(
                    PredictionRuns.shift_id == shift_id,
                    PredictionRuns.bus_model_id == resolved_model_id,
                    PredictionRuns.model_name == prediction_params["model_name"],
                    PredictionRuns.external_temp_celsius == prediction_params["external_temp_celsius"],
                    PredictionRuns.occupancy_percent == prediction_params["occupancy_percent"],
                    PredictionRuns.auxiliary_heating_type == prediction_params["auxiliary_heating_type"],
                    PredictionRuns.status == "completed",
                )
            ).limit(1)
        )
        existing = result.scalar_one_or_none()
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
            external_temp_celsius=prediction_params["external_temp_celsius"],
            auxiliary_heating_type=prediction_params["auxiliary_heating_type"],
            occupancy_percent=prediction_params["occupancy_percent"],
            status="pending",
        )
        db.add(run)
        await db.flush()
        await db.commit()

        await predict_shift_consumption(
            db=db,
            prediction_run_id=run.id,
            quantiles=prediction_params.get("quantiles", [0.05, 0.5, 0.95]),
            num_battery_packs=prediction_params.get("num_battery_packs"),
        )
        await db.refresh(run)
        if run.status != "completed":
            raise RuntimeError(f"Prediction for shift {shift_id} failed: {run.summary}")
        run_ids.append(run.id)

    return run_ids


# ---------------------------------------------------------------------------
# Prepare optimization input from DB data
# ---------------------------------------------------------------------------

def _get_consumption_value(pred: TripPredictions, quantile_consumption: str) -> float:
    """Extract the requested consumption value from a TripPredictions row."""
    if quantile_consumption == "mean":
        return float(pred.prediction_kwh)
    if quantile_consumption == "median":
        return float(pred.prediction_median_kwh or pred.prediction_kwh)
    q_data = pred.quantiles or {}
    val = q_data.get(quantile_consumption)
    if val is not None:
        return float(val)
    return float(pred.prediction_kwh)


async def prepare_optimization_input(
    db: AsyncSession,
    optimization_run: OptimizationRuns,
    prediction_run_ids: list[UUID],
) -> tuple[list[BusData], list[StationData], OptimizationConfig]:
    """Load all data from DB and build structured inputs for the solver."""
    params = optimization_run.input_params
    quantile_consumption = params.get("quantile_consumption", "mean")

    # Shared bus model (optional -- used as fallback when set)
    shared_specs: dict | None = None
    if optimization_run.bus_model_id is not None:
        shared_model = await db.get(BusesModels, optimization_run.bus_model_id)
        if shared_model is not None:
            shared_specs = shared_model.specs or {}

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
            slot_costs_chf=cs.get("slot_costs_chf", []),
            num_fixed_slots=cs.get("num_slots"),
            max_total_power_kw=cs.get("max_total_power_kw", 450),
            max_power_per_slot_kw=cs.get("max_power_per_slot_kw"),
        ))
    station_stop_id_to_idx = {s.stop_id: i for i, s in enumerate(station_list)}

    # Build per-bus data from prediction runs.
    # Each bus resolves its own specs through: prediction_run -> shift -> bus -> bus_model.
    # Falls back to the shared bus_model_id (if provided) or hard-coded defaults.
    buses: list[BusData] = []
    _bus_model_cache: dict[UUID, dict] = {}

    async def _get_specs_for_shift(shift_id: UUID) -> dict:
        """Resolve bus model specs for a shift via shift->bus->bus_model."""
        shift = await db.get(Shifts, shift_id)
        if shift is None:
            return shared_specs or {}
        bus = await db.get(Buses, shift.bus_id) if shift.bus_id else None
        if bus is None or bus.bus_model_id is None:
            return shared_specs or {}
        if bus.bus_model_id in _bus_model_cache:
            return _bus_model_cache[bus.bus_model_id]
        model = await db.get(BusesModels, bus.bus_model_id)
        specs = (model.specs if model else None) or shared_specs or {}
        _bus_model_cache[bus.bus_model_id] = specs
        return specs

    for pred_run_id in prediction_run_ids:
        pred_run = await db.get(PredictionRuns, pred_run_id)
        if pred_run is None:
            raise ValueError(f"Prediction run {pred_run_id} not found")

        shift = await db.get(Shifts, pred_run.shift_id)
        shift_name = shift.name if shift else str(pred_run.shift_id)

        # Per-bus specs
        specs = await _get_specs_for_shift(pred_run.shift_id)
        pack_size = float(specs.get("battery_pack_size_kwh", 37))
        min_packs_spec = int(specs.get("min_battery_packs", 10))
        max_packs_spec = int(specs.get("max_battery_packs", 14))
        max_charging_power = float(specs.get("max_charging_power_kw", 450))

        ctx = pred_run.contextual_parameters or {}
        ref_packs = ctx.get("num_battery_packs", min_packs_spec)
        ref_capacity = ctx.get("battery_capacity_kwh", ref_packs * pack_size)

        configured_capacity = max_packs_spec * pack_size
        battery_offset = configured_capacity - ref_capacity

        # Load trip predictions
        tp_result = await db.execute(
            select(TripPredictions)
            .where(TripPredictions.prediction_run_id == pred_run_id)
            .order_by(TripPredictions.sequence_number)
        )
        trip_preds = tp_result.scalars().all()

        trip_data_list: list[TripData] = []
        for tp in trip_preds:
            stop_times_result = await db.execute(
                select(GtfsStopsTimes)
                .where(GtfsStopsTimes.trip_id == tp.trip_id)
                .order_by(GtfsStopsTimes.stop_sequence)
            )
            stop_times = stop_times_result.scalars().all()
            if not stop_times:
                continue

            first_st = stop_times[0]
            last_st = stop_times[-1]
            dep_min = _time_str_to_minutes(first_st.departure_time)
            arr_min = _time_str_to_minutes(last_st.arrival_time)
            if dep_min is None or arr_min is None:
                continue

            end_stop_id = str(last_st.stop_id)
            end_station_idx = station_stop_id_to_idx.get(end_stop_id, -1)

            consumption = _get_consumption_value(tp, quantile_consumption)
            sensitivity = float(tp.mass_sensitivity_kwh_per_kwh_batt or 0.0)

            trip_data_list.append(TripData(
                trip_id=str(tp.trip_id),
                departure_minute=dep_min,
                arrival_minute=arr_min,
                end_station_idx=end_station_idx,
                base_energy_kwh=consumption,
                sensitivity=sensitivity,
            ))

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
        ))

    # Build OptimizationConfig
    opt_config = OptimizationConfig(
        mode=optimization_run.mode,
        min_soc=params.get("min_soc", 0.4),
        max_soc=params.get("max_soc", 0.9),
        state_of_health=params.get("state_of_health", 1.0),
        battery_cost_per_kwh=params.get("battery_cost_per_kwh", 0.0),
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
        solver_name=params.get("solver_name", "highs"),
        max_solver_time_seconds=params.get("max_solver_time_seconds"),
        mip_rel_gap=params.get("mip_rel_gap"),
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
) -> None:
    """Execute the full optimization pipeline for a given run."""
    run = await db.get(OptimizationRuns, optimization_run_id)
    if run is None:
        raise ValueError(f"Optimization run {optimization_run_id} not found")

    try:
        run.status = "running"
        await db.commit()

        params = run.input_params
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
            )
            run.prediction_run_ids = [str(rid) for rid in prediction_run_ids]
            await db.commit()
        else:
            prediction_run_ids = [UUID(rid) if isinstance(rid, str) else rid for rid in prediction_run_ids]

        # Prepare and solve
        buses, stations, opt_config = await prepare_optimization_input(
            db, run, prediction_run_ids,
        )

        result: OptimizationResult = await asyncio.to_thread(
            solve_optimization, buses, stations, opt_config,
        )

        run.results = {
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
        }
        if result.solver_status.startswith("error"):
            run.status = "failed"
        else:
            run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        await db.commit()

    except Exception as e:
        logger.exception("Optimization failed for run %s: %s", optimization_run_id, e)
        try:
            await db.rollback()
            run = await db.get(OptimizationRuns, optimization_run_id)
            if run is not None:
                run.status = "failed"
                run.results = {"error": str(e)}
                await db.commit()
        except Exception:
            logger.exception("Failed to update optimization run %s status to 'failed'", optimization_run_id)


# ---------------------------------------------------------------------------
# Background task wrapper
# ---------------------------------------------------------------------------

async def run_optimization_background(optimization_run_id: UUID) -> None:
    """Wrapper called from BackgroundTasks. Opens its own DB session."""
    async with AsyncSessionLocal() as db:
        try:
            await run_optimization(db, optimization_run_id)
        except Exception:
            logger.exception("Background optimization failed for %s", optimization_run_id)
