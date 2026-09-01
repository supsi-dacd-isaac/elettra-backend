"""
Prediction service: runs ConsumptionPredictor for shifts stored in the DB.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import (
    BusesModels,
    GtfsStops,
    GtfsStopsTimes,
    GtfsTrips,
    PredictionRuns,
    ShiftsStructures,
    TripPredictions,
)
from app.utils.trip_statistics import (
    compute_global_trip_statistics_combined,
    extract_route_difficulty_metrics_from_elevation,
    extract_stop_to_stop_statistics_for_schedule,
)
from app.services.elevation_profiles import load_trip_elevation_dataframe
from app.services.runtime_release import (
    PredictionStack,
    PredictionStackRelease,
    enforce_configured_model,
    resolve_prediction_selection,
    runtime_release_configuration,
    validate_model_stack_contract,
)
from app.services.model_release import get_validated_model_artifact
from app.services.vecto_auxiliary import build_vecto_auxiliary_binding
from elettra_core import RAW_TRIP_FEATURE_COLUMNS
from simulation.consumption_prediction import ConsumptionPredictor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model cache (singleton per model_name)
# ---------------------------------------------------------------------------
_predictor_cache: dict[str, ConsumptionPredictor] = {}


@dataclass(frozen=True)
class PhysicalBusMass:
    bus_length_m: float
    battery_pack_size_kwh: float
    battery_pack_weight_kg: float
    battery_packs: int
    max_passengers: int
    occupancy_percent: float
    passenger_count: float
    empty_weight_kg: float
    battery_weight_kg: float
    passenger_weight_kg: float
    total_weight_kg: float

    @property
    def battery_capacity_kwh(self) -> float:
        return self.battery_pack_size_kwh * self.battery_packs

    @property
    def battery_density_kg_per_kwh(self) -> float:
        return self.battery_pack_weight_kg / self.battery_pack_size_kwh


def physical_bus_mass(
    specs: dict,
    *,
    occupancy_percent: float,
    num_battery_packs: Optional[int] = None,
) -> PhysicalBusMass:
    """Resolve the authoritative runtime mass without silent fallbacks."""

    required = (
        "bus_length_m",
        "battery_pack_size_kwh",
        "battery_pack_weight_kg",
        "max_battery_packs",
        "max_passengers",
        "empty_weight_kg",
    )
    missing = [name for name in required if name not in specs]
    if missing:
        raise ValueError(
            "Bus model cannot provide physical prediction mass; missing specs: "
            f"{missing}"
        )

    def finite_positive(name: str) -> float:
        value = float(specs[name])
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Bus model spec {name} must be finite and positive")
        return value

    bus_length_m = finite_positive("bus_length_m")
    pack_size = finite_positive("battery_pack_size_kwh")
    pack_weight = finite_positive("battery_pack_weight_kg")
    empty_weight = finite_positive("empty_weight_kg")
    max_packs_raw = float(specs["max_battery_packs"])
    max_passengers_raw = float(specs["max_passengers"])
    if (
        not math.isfinite(max_packs_raw)
        or max_packs_raw <= 0
        or not max_packs_raw.is_integer()
    ):
        raise ValueError("Bus model spec max_battery_packs must be a positive integer")
    if (
        not math.isfinite(max_passengers_raw)
        or max_passengers_raw < 0
        or not max_passengers_raw.is_integer()
    ):
        raise ValueError("Bus model spec max_passengers must be a non-negative integer")
    max_packs = int(max_packs_raw)
    max_passengers = int(max_passengers_raw)
    packs = max_packs if num_battery_packs is None else num_battery_packs
    if isinstance(packs, bool) or not isinstance(packs, (int, np.integer)):
        raise ValueError("num_battery_packs must be an integer")
    packs = int(packs)
    if not 1 <= packs <= max_packs:
        raise ValueError(
            f"num_battery_packs must be between 1 and {max_packs}, got {packs}"
        )
    occupancy = float(occupancy_percent)
    if not math.isfinite(occupancy) or not 0 <= occupancy <= 100:
        raise ValueError("occupancy_percent must be finite and between 0 and 100")

    passenger_count = max_passengers * occupancy / 100.0
    battery_weight = packs * pack_weight
    passenger_weight = passenger_count * 70.0
    total_weight = empty_weight + battery_weight + passenger_weight
    return PhysicalBusMass(
        bus_length_m=bus_length_m,
        battery_pack_size_kwh=pack_size,
        battery_pack_weight_kg=pack_weight,
        battery_packs=packs,
        max_passengers=max_passengers,
        occupancy_percent=occupancy,
        passenger_count=passenger_count,
        empty_weight_kg=empty_weight,
        battery_weight_kg=battery_weight,
        passenger_weight_kg=passenger_weight,
        total_weight_kg=total_weight,
    )


def _bind_prediction_run_stack(
    run: PredictionRuns,
    stack_release: PredictionStackRelease,
) -> None:
    """Verify persisted provenance before executing a prediction.

    Historical rows may predate the auxiliary-estimator column, but only the
    legacy stack is allowed to repair that NULL.  A VECTO row is new by
    construction and a missing estimator would make its energy semantics
    ambiguous, so it must fail closed.
    """

    stored_estimator = getattr(run, "auxiliary_estimator_release", None)
    if stored_estimator is None:
        if stack_release.stack is not PredictionStack.LEGACY:
            raise ValueError(
                "VECTO prediction run has no persisted auxiliary estimator"
            )
    elif stored_estimator != stack_release.auxiliary_estimator:
        raise ValueError(
            "Prediction run auxiliary estimator does not match its model stack"
        )
    run.prediction_stack = stack_release.stack.value
    run.auxiliary_estimator_release = stack_release.auxiliary_estimator


def get_predictor(model_name: str) -> ConsumptionPredictor:
    enforce_configured_model(model_name)
    if model_name not in _predictor_cache:
        runtime = runtime_release_configuration()
        if runtime.production_v2_active:
            model, metadata = get_validated_model_artifact(model_name)
            predictor = ConsumptionPredictor(bucket_name="consumption-models")
            predictor.load_validated_model(model, metadata)
        else:
            predictor = ConsumptionPredictor(
                model_name=model_name,
                bucket_name="consumption-models",
            )
        _predictor_cache[model_name] = predictor
    return _predictor_cache[model_name]


# ---------------------------------------------------------------------------
# Auxiliary energy function builder
# ---------------------------------------------------------------------------

DIESEL_KWH_PER_LITER = 9.94


def _is_diesel_heating_params(curve: dict) -> bool:
    """Return True when the curve dict uses diesel-heating parameter format
    (p_base_kw, t_ref_celsius, cop, diesel_heater_efficiency) rather than
    the standard (temperature_celsius, consumption_kw) format."""
    return all(k in curve for k in ("p_base_kw", "t_ref_celsius", "cop"))


def _build_diesel_aux_energy_fn(
    default_curve: dict,
    diesel_params: dict,
):
    """Build an ``aux_energy_fn`` for diesel-heating (ebus-dh) mode.

    Returns the **electric-only** auxiliary energy seen by the battery.
    Below ``t_ref``, only ``p_base_kw`` is drawn electrically; the heating
    portion is removed (supplied by the diesel heater).  At or above
    ``t_ref``, the full default auxiliary curve remains electric.
    """
    temps = np.asarray(default_curve["temperature_celsius"], dtype=float)
    powers = np.asarray(default_curve["consumption_kw"], dtype=float)
    p_base_kw = float(diesel_params["p_base_kw"])
    t_ref = float(diesel_params["t_ref_celsius"])

    def aux_energy_fn(X_df: pd.DataFrame) -> pd.Series:
        ext_temp = X_df["avg_temp_outside_celsius"].to_numpy(dtype=float)
        duration = X_df["total_duration_minutes"].to_numpy(dtype=float)
        p_aux_default = np.interp(ext_temp, temps, powers)
        p_aux_el = np.where(ext_temp < t_ref, p_base_kw, p_aux_default)
        energy_kwh = p_aux_el * duration / 60.0
        return pd.Series(energy_kwh, index=X_df.index)

    return aux_energy_fn


def build_aux_energy_fn(bus_model_specs: dict, auxiliary_heating_type: str):
    """
    Build a callable ``aux_energy_fn(X_df) -> pd.Series`` that computes
    auxiliary (HVAC) energy per trip row using temperature-dependent power
    curves stored in ``bus_model_specs["auxiliary_consumption_kw"]``.

    The curve is selected by *auxiliary_heating_type* (e.g. ``"hp"``,
    ``"diesel"``).  If the exact key is missing, a ``"default"`` curve is
    used **only** when no heating-type-specific lookup can be resolved,
    and a warning is logged so the mismatch is visible.

    When the selected entry uses the **diesel-heating parameter format**
    (``p_base_kw``, ``t_ref_celsius``, ``cop``, ``diesel_heater_efficiency``),
    the returned function computes electric-only auxiliary (battery-side),
    splitting the heating portion off to be covered by diesel.
    """
    aux_data = bus_model_specs.get("auxiliary_consumption_kw", {})
    curve = aux_data.get(auxiliary_heating_type)
    if curve is None:
        curve = aux_data.get("default")
        if curve is not None:
            logger.warning(
                "No auxiliary consumption curve for heating type '%s'; "
                "falling back to 'default'. The bus model specs should "
                "define per-type curves (hp, diesel, …) for accurate results.",
                auxiliary_heating_type,
            )
    if not curve:
        return None

    # Diesel-heating parameter format → ebus-dh split logic
    if _is_diesel_heating_params(curve):
        default_curve = aux_data.get("default")
        if not default_curve:
            logger.error(
                "Diesel-heating mode requires a 'default' auxiliary curve "
                "to use as the reference baseline, but none was found."
            )
            return None
        return _build_diesel_aux_energy_fn(default_curve, curve)

    # Standard curve format: temperature_celsius + consumption_kw
    temps = np.asarray(curve["temperature_celsius"], dtype=float)
    powers = np.asarray(curve["consumption_kw"], dtype=float)

    def aux_energy_fn(X_df: pd.DataFrame) -> pd.Series:
        ext_temp = X_df["avg_temp_outside_celsius"].to_numpy(dtype=float)
        duration = X_df["total_duration_minutes"].to_numpy(dtype=float)
        power = np.interp(ext_temp, temps, powers)
        energy_kwh = power * duration / 60.0
        return pd.Series(energy_kwh, index=X_df.index)

    return aux_energy_fn


# ---------------------------------------------------------------------------
# Diesel-heating summary builder (ebus-dh)
# ---------------------------------------------------------------------------

def compute_diesel_heating_summary(
    bus_model_specs: dict,
    auxiliary_heating_type: str,
    external_temp_celsius: float,
    total_auxiliary_kwh: float,
    total_consumption_kwh: float,
    total_distance_km: float,
) -> dict:
    """Compute the diesel-heating breakdown for the ebus-dh summary.

    All parameters that define the diesel-heating physics (``p_base_kw``,
    ``t_ref_celsius``, COP curve, ``diesel_heater_efficiency``) are read
    from the bus-model specs — nothing is hard-coded.

    Returns a dict with ``auxiliary_breakdown``, ``diesel_heating``, and
    ``mixed_energy_totals`` blocks ready to be merged into the prediction
    run summary.
    """
    aux_data = bus_model_specs.get("auxiliary_consumption_kw", {})
    default_curve = aux_data.get("default", {})
    diesel_params = aux_data.get(auxiliary_heating_type, {})

    temps = np.asarray(default_curve["temperature_celsius"], dtype=float)
    powers = np.asarray(default_curve["consumption_kw"], dtype=float)

    p_base_kw = float(diesel_params["p_base_kw"])
    t_ref = float(diesel_params["t_ref_celsius"])
    eta_diesel = float(diesel_params["diesel_heater_efficiency"])
    cop_data = diesel_params["cop"]
    cop_temps = np.asarray(cop_data["temperature_celsius"], dtype=float)
    cop_values = np.asarray(cop_data["values"], dtype=float)

    T = external_temp_celsius
    p_aux_default = float(np.interp(T, temps, powers))

    if T < t_ref:
        p_aux_el = p_base_kw
        p_heat_el_input = max(p_aux_default - p_base_kw, 0.0)
    else:
        p_aux_el = p_aux_default
        p_heat_el_input = 0.0

    cop = float(np.interp(T, cop_temps, cop_values))
    q_heat = p_heat_el_input * cop
    p_diesel_fuel = q_heat / eta_diesel if eta_diesel > 0 else 0.0

    total_hours = total_auxiliary_kwh / p_aux_el if p_aux_el > 0 else 0.0

    base_electric_kwh = p_base_kw * total_hours

    if T < t_ref:
        cooling_electric_kwh = 0.0
        heating_electric_removed_kwh = p_heat_el_input * total_hours
    else:
        cooling_electric_kwh = max(p_aux_default - p_base_kw, 0.0) * total_hours
        heating_electric_removed_kwh = 0.0

    heating_thermal_kwh = q_heat * total_hours
    diesel_fuel_kwh = p_diesel_fuel * total_hours
    diesel_liters = diesel_fuel_kwh / DIESEL_KWH_PER_LITER if DIESEL_KWH_PER_LITER > 0 else 0.0
    diesel_liters_per_km = diesel_liters / total_distance_km if total_distance_km and total_distance_km > 0 else 0.0

    battery_total_kwh = total_consumption_kwh

    return {
        "auxiliary_breakdown": {
            "base_electric_kwh": round(base_electric_kwh, 4),
            "cooling_electric_kwh": round(cooling_electric_kwh, 4),
            "heating_electric_removed_kwh": round(heating_electric_removed_kwh, 4),
            "heating_thermal_kwh": round(heating_thermal_kwh, 4),
            "t_ref_celsius": t_ref,
            "p_base_kw": p_base_kw,
        },
        "diesel_heating": {
            "diesel_fuel_kwh": round(diesel_fuel_kwh, 4),
            "diesel_liters": round(diesel_liters, 4),
            "diesel_liters_per_km": round(diesel_liters_per_km, 6),
            "diesel_heater_efficiency": eta_diesel,
        },
        "mixed_energy_totals": {
            "battery_total_kwh": round(battery_total_kwh, 4),
            "diesel_fuel_kwh": round(diesel_fuel_kwh, 4),
            "combined_final_energy_kwh": round(battery_total_kwh + diesel_fuel_kwh, 4),
        },
    }


# ---------------------------------------------------------------------------
# Trip statistics loader (from DB)
# ---------------------------------------------------------------------------

async def load_shift_trip_statistics(
    db: AsyncSession,
    shift_id: UUID,
) -> list[dict]:
    """
    Load all trips for a shift, compute per-trip statistics, and return them
    in the JSON format expected by ``ConsumptionPredictor.predict_from_json``.
    """
    result = await db.execute(
        select(ShiftsStructures)
        .where(ShiftsStructures.shift_id == shift_id)
        .order_by(ShiftsStructures.sequence_number)
    )
    structures = result.scalars().all()
    if not structures:
        raise ValueError(f"Shift {shift_id} has no trips")

    trip_statistics = []
    for ss in structures:
        trip_id = ss.trip_id
        seq = ss.sequence_number

        # Load schedule
        sched_result = await db.execute(
            select(GtfsStops, GtfsStopsTimes.arrival_time,
                   GtfsStopsTimes.departure_time, GtfsStopsTimes.stop_sequence)
            .join(GtfsStopsTimes, GtfsStops.id == GtfsStopsTimes.stop_id)
            .filter(GtfsStopsTimes.trip_id == trip_id)
            .order_by(GtfsStopsTimes.stop_sequence)
        )
        rows = sched_result.all()
        if not rows:
            raise ValueError(
                f"Cannot load shift {shift_id}: trip {trip_id} at sequence {seq} "
                "has no schedule data"
            )

        schedule_data = [{
            "stop_id": stop.stop_id,
            "stop_name": stop.stop_name,
            "stop_lat": stop.stop_lat,
            "stop_lon": stop.stop_lon,
            "arrival_time": arrival_time,
            "departure_time": departure_time,
            "stop_sequence": stop_sequence,
        } for (stop, arrival_time, departure_time, stop_sequence) in rows]
        schedule_df = pd.DataFrame(schedule_data)

        # Load elevation from MinIO
        try:
            elevation_df = await _load_trip_elevation(db, trip_id)
        except Exception as exc:
            raise ValueError(
                f"Cannot load shift {shift_id}: elevation profile failed for "
                f"trip {trip_id} at sequence {seq}: {exc}"
            ) from exc
        if elevation_df is None or elevation_df.empty:
            raise ValueError(
                f"Cannot load shift {shift_id}: trip {trip_id} at sequence {seq} "
                "has no elevation profile"
            )

        # Compute statistics
        try:
            global_stats = compute_global_trip_statistics_combined(schedule_df, elevation_df)
            segment_stats = extract_stop_to_stop_statistics_for_schedule(schedule_df, elevation_df)
            difficulty_stats = extract_route_difficulty_metrics_from_elevation(elevation_df)
            stats = {}
            stats.update(global_stats)
            stats.update(segment_stats)
            stats.update(difficulty_stats)
            missing = [column for column in RAW_TRIP_FEATURE_COLUMNS if column not in stats]
            if missing:
                raise ValueError(f"core v2 statistics are missing fields: {missing}")
        except Exception as exc:
            raise ValueError(
                f"Cannot load shift {shift_id}: core v2 statistics failed for "
                f"trip {trip_id} at sequence {seq}: {exc}"
            ) from exc

        trip_statistics.append({
            "trip_id": str(trip_id),
            "sequence_number": seq,
            "statistics": {"statistics": stats},
        })

    if len(trip_statistics) != len(structures):
        raise RuntimeError(
            f"Shift {shift_id} statistics cardinality changed unexpectedly: "
            f"expected {len(structures)}, got {len(trip_statistics)}"
        )
    return trip_statistics


async def _load_trip_elevation(db: AsyncSession, trip_id: UUID) -> pd.DataFrame:
    trip = await db.get(GtfsTrips, trip_id)
    if not trip or not trip.shape_id:
        return pd.DataFrame()
    return await load_trip_elevation_dataframe(db, trip)


# ---------------------------------------------------------------------------
# Core prediction logic
# ---------------------------------------------------------------------------

async def predict_shift_consumption(
    db: AsyncSession,
    prediction_run_id: UUID,
    quantiles: list[float],
    num_battery_packs: Optional[int] = None,
) -> None:
    """
    Run the full prediction pipeline for a single prediction run.
    """
    run = await db.get(PredictionRuns, prediction_run_id)
    if run is None:
        raise ValueError(f"PredictionRun {prediction_run_id} not found")

    run.status = "running"
    await db.commit()

    try:
        # Load bus model specs
        bus_model = await db.get(BusesModels, run.bus_model_id)
        if bus_model is None:
            raise ValueError(f"BusModel {run.bus_model_id} not found")
        specs = bus_model.specs or {}

        mass = physical_bus_mass(
            specs,
            occupancy_percent=float(run.occupancy_percent),
            num_battery_packs=num_battery_packs,
        )
        bus_length_m = mass.bus_length_m
        packs = mass.battery_packs
        battery_capacity_kwh = mass.battery_capacity_kwh
        total_weight_kg = mass.total_weight_kg
        actual_pack_density = mass.battery_density_kg_per_kwh

        # Load trip statistics from DB
        trip_stats = await load_shift_trip_statistics(db, run.shift_id)
        if not trip_stats:
            raise ValueError(f"No valid trip statistics for shift {run.shift_id}")

        json_data = {
            "shift_id": str(run.shift_id),
            "trip_statistics": trip_stats,
        }

        stack_release = resolve_prediction_selection(
            prediction_stack=getattr(run, "prediction_stack", None) or None,
            model_name=run.model_name,
        )
        _bind_prediction_run_stack(run, stack_release)

        auxiliary_context: dict[str, object] | None = None
        if stack_release.stack is PredictionStack.LEGACY:
            aux_fn = build_aux_energy_fn(specs, run.auxiliary_heating_type)
        else:
            vecto_binding = build_vecto_auxiliary_binding(
                stack_release=stack_release,
                bus_model_specs=specs,
                occupancy_percent=float(run.occupancy_percent),
                external_temp_celsius=float(run.external_temp_celsius),
                auxiliary_heating_type=run.auxiliary_heating_type,
            )
            aux_fn = vecto_binding.energy_fn
            auxiliary_context = vecto_binding.metadata()

        # Get predictor (cached) and bind it to the same stack contract.
        predictor = get_predictor(run.model_name)
        validate_model_stack_contract(stack_release, predictor.metadata)

        # Build override_mass array (same mass for all trips)
        n_trips = len(trip_stats)
        override_mass = np.full(n_trips, total_weight_kg)
        qrf_reference_occupancy_percent = getattr(
            predictor.model,
            "qrf_reference_occupancy_percent",
            None,
        )
        qrf_reference_mass = None
        if qrf_reference_occupancy_percent is not None:
            reference_mass = physical_bus_mass(
                specs,
                occupancy_percent=float(qrf_reference_occupancy_percent),
                num_battery_packs=packs,
            )
            qrf_reference_mass = np.full(
                n_trips,
                reference_mass.total_weight_kg,
            )

        # Run prediction
        results = predictor.predict_from_json(
            json_data=json_data,
            bus_length_m=bus_length_m,
            battery_capacity_kwh=battery_capacity_kwh,
            external_temp_celsius=float(run.external_temp_celsius),
            quantiles=quantiles,
            aux_energy_fn=aux_fn,
            override_mass=override_mass,
            qrf_reference_mass=qrf_reference_mass,
            battery_pack_density_override=actual_pack_density,
        )

        def _sanitize_json(obj):
            """Recursively replace NaN/Inf floats with None for JSONB storage."""
            if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                return None
            if isinstance(obj, dict):
                return {k: _sanitize_json(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_sanitize_json(v) for v in obj]
            return obj

        # Enrich summary with auxiliary_heating_type + diesel breakdown
        summary = results.get("summary") or {}
        summary["auxiliary_heating_type"] = run.auxiliary_heating_type
        summary["prediction_stack"] = stack_release.stack.value
        summary["auxiliary_estimator_release"] = stack_release.auxiliary_estimator
        if auxiliary_context is not None:
            summary["vecto_auxiliary"] = auxiliary_context
            diesel_fuel_kwh = float(summary.get("total_diesel_fuel_kwh", 0.0))
            diesel_liters = float(summary.get("total_diesel_liters", 0.0))
            if diesel_fuel_kwh or diesel_liters:
                summary["diesel_heating"] = {
                    "diesel_fuel_kwh": round(diesel_fuel_kwh, 4),
                    "diesel_liters": round(diesel_liters, 4),
                    "diesel_liters_per_km": round(
                        diesel_liters / float(summary.get("total_distance_km", 0.0)), 6
                    )
                    if float(summary.get("total_distance_km", 0.0)) > 0
                    else 0.0,
                    "diesel_heater_efficiency": auxiliary_context[
                        "diesel_heater_efficiency"
                    ],
                }
                summary["mixed_energy_totals"] = {
                    "battery_total_kwh": round(
                        float(summary.get("total_consumption_kwh", 0.0)), 4
                    ),
                    "diesel_fuel_kwh": round(diesel_fuel_kwh, 4),
                    "combined_final_energy_kwh": round(
                        float(summary.get("total_consumption_kwh", 0.0))
                        + diesel_fuel_kwh,
                        4,
                    ),
                }

        diesel_params_entry = (
            specs.get("auxiliary_consumption_kw", {})
            .get(run.auxiliary_heating_type)
        )
        if (
            stack_release.stack is PredictionStack.LEGACY
            and diesel_params_entry
            and _is_diesel_heating_params(diesel_params_entry)
        ):
            try:
                diesel_info = compute_diesel_heating_summary(
                    bus_model_specs=specs,
                    auxiliary_heating_type=run.auxiliary_heating_type,
                    external_temp_celsius=float(run.external_temp_celsius),
                    total_auxiliary_kwh=summary.get("total_auxiliary_kwh", 0),
                    total_consumption_kwh=summary.get("total_consumption_kwh", 0),
                    total_distance_km=summary.get("total_distance_km", 0),
                )
                summary.update(diesel_info)
            except Exception:
                logger.exception("Failed to compute diesel heating summary")

        results["summary"] = summary

        # Store contextual parameters
        run.contextual_parameters = _sanitize_json({
            "battery_capacity_kwh": battery_capacity_kwh,
            "bus_length_m": bus_length_m,
            "num_battery_packs": packs,
            "total_weight_kg": total_weight_kg,
            "physical_mass": {
                "empty_weight_kg": mass.empty_weight_kg,
                "battery_weight_kg": mass.battery_weight_kg,
                "passenger_count": mass.passenger_count,
                "passenger_weight_kg": mass.passenger_weight_kg,
                "occupancy_percent": mass.occupancy_percent,
                "qrf_reference_occupancy_percent": (
                    qrf_reference_occupancy_percent
                ),
                "qrf_reference_total_weight_kg": (
                    float(qrf_reference_mass[0])
                    if qrf_reference_mass is not None
                    else None
                ),
                "total_weight_kg": mass.total_weight_kg,
            },
            "quantiles": quantiles,
            "greybox_params": results.get("greybox_params"),
            "prediction_stack": stack_release.stack.value,
            "auxiliary_estimator_release": stack_release.auxiliary_estimator,
            "auxiliary_estimator": auxiliary_context,
        })
        run.summary = _sanitize_json(results.get("summary"))

        # Store per-trip predictions
        predictions_list = results.get("predictions", [])
        for i, pred in enumerate(predictions_list):
            seq = trip_stats[i].get("sequence_number", i)
            trip_id_str = trip_stats[i].get("trip_id")

            q_dict = {}
            for q in quantiles:
                key = f"quantile_{q:.2f}"
                if key in pred:
                    val = pred[key]
                    q_dict[f"{q:.2f}"] = None if (isinstance(val, float) and math.isnan(val)) else val

            def _clean(v):
                """Replace NaN/Inf floats with None for DB storage."""
                if v is None:
                    return None
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    return None
                return v

            tp = TripPredictions(
                prediction_run_id=prediction_run_id,
                trip_id=UUID(trip_id_str),
                sequence_number=seq,
                prediction_kwh=_clean(pred.get("prediction_kwh", 0)) or 0,
                prediction_median_kwh=_clean(pred.get("prediction_median_kwh")),
                drivetrain_kwh=_clean(pred.get("drivetrain_kwh")),
                auxiliary_kwh=_clean(pred.get("auxiliary_kwh")),
                mass_sensitivity_kwh_per_kwh_batt=_clean(pred.get("mass_sensitivity_kwh_per_kwh_batt")),
                quantiles=q_dict if q_dict else None,
                component_breakdown=_sanitize_json(
                    {
                        key: _clean(pred.get(key))
                        for key in (
                            "mechanical_greybox_kwh",
                            "qrf_residual_kwh",
                            "fixed_auxiliary_kwh",
                            "hvac_electrical_kwh",
                            "diesel_fuel_kwh",
                            "diesel_liters",
                            "uncovered_thermal_kwh",
                        )
                        if pred.get(key) is not None
                    }
                )
                or None,
            )
            db.add(tp)

        run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        await db.commit()

    except Exception as e:
        logger.exception(f"Prediction failed for run {prediction_run_id}: {e}")
        try:
            await db.rollback()
            run = await db.get(PredictionRuns, prediction_run_id)
            if run is not None:
                run.status = "failed"
                run.summary = {"error": str(e)}
                await db.commit()
        except Exception:
            logger.exception(f"Failed to update prediction run {prediction_run_id} status to 'failed'")
        raise


# ---------------------------------------------------------------------------
# Background task wrapper
# ---------------------------------------------------------------------------

async def run_prediction_background(
    prediction_run_id: UUID,
    quantiles: list[float],
    num_battery_packs: Optional[int] = None,
) -> None:
    """
    Wrapper called from BackgroundTasks. Opens its own DB session.
    """
    async with AsyncSessionLocal() as db:
        try:
            await predict_shift_consumption(
                db=db,
                prediction_run_id=prediction_run_id,
                quantiles=quantiles,
                num_battery_packs=num_battery_packs,
            )
        except Exception:
            logger.exception(f"Background prediction failed for {prediction_run_id}")
