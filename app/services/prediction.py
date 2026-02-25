"""
Prediction service: runs ConsumptionPredictor for shifts stored in the DB.
"""

import io
import logging
import os
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import numpy as np
import pandas as pd
from minio import Minio
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
from simulation.consumption_prediction import ConsumptionPredictor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model cache (singleton per model_name)
# ---------------------------------------------------------------------------
_predictor_cache: dict[str, ConsumptionPredictor] = {}


def get_predictor(model_name: str) -> ConsumptionPredictor:
    if model_name not in _predictor_cache:
        predictor = ConsumptionPredictor(
            model_name=model_name,
            bucket_name="consumption-models",
        )
        _predictor_cache[model_name] = predictor
    return _predictor_cache[model_name]


# ---------------------------------------------------------------------------
# Auxiliary energy function builder
# ---------------------------------------------------------------------------

def build_aux_energy_fn(bus_model_specs: dict, auxiliary_heating_type: str):
    """
    Build a callable ``aux_energy_fn(X_df) -> pd.Series`` that computes
    auxiliary (HVAC) energy per trip row using temperature-dependent power
    curves stored in ``bus_model_specs["auxiliary_consumption_kw"]``.
    """
    aux_data = bus_model_specs.get("auxiliary_consumption_kw", {})
    curve = aux_data.get(auxiliary_heating_type) or aux_data.get("default")
    if not curve:
        return None

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
            logger.warning(f"No schedule data for trip {trip_id}, skipping")
            continue

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
        elevation_df = await _load_trip_elevation(db, trip_id)

        # Compute statistics
        try:
            global_stats = compute_global_trip_statistics_combined(schedule_df, elevation_df)
            segment_stats = extract_stop_to_stop_statistics_for_schedule(schedule_df, elevation_df)
            difficulty_stats = extract_route_difficulty_metrics_from_elevation(elevation_df)
            stats = {}
            stats.update(global_stats)
            stats.update(segment_stats)
            stats.update(difficulty_stats)
        except Exception as e:
            logger.warning(f"Failed to compute statistics for trip {trip_id}: {e}")
            continue

        trip_statistics.append({
            "trip_id": str(trip_id),
            "sequence_number": seq,
            "statistics": {"statistics": stats},
        })

    return trip_statistics


async def _load_trip_elevation(db: AsyncSession, trip_id: UUID) -> pd.DataFrame:
    trip = await db.get(GtfsTrips, trip_id)
    if not trip or not trip.shape_id:
        return pd.DataFrame()
    try:
        endpoint = os.getenv("MINIO_ENDPOINT", "minio:9000")
        access_key = os.getenv("AWS_ACCESS_KEY_ID", "minio_user")
        secret_key = os.getenv("AWS_SECRET_ACCESS_KEY", "minio_password")
        secure = os.getenv("MINIO_SECURE", "false").lower() in ("1", "true", "yes", "on")
        client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        response = client.get_object("elevation-profiles", f"{trip.shape_id}.parquet")
        try:
            data = response.read()
        finally:
            response.close()
            response.release_conn()
        df = pd.read_parquet(io.BytesIO(data))
        if "cumulative_distance_m" not in df.columns and len(df) > 1:
            from math import radians, cos, sin, asin, sqrt
            distances = [0.0]
            for i in range(1, len(df)):
                lat1 = radians(df.iloc[i - 1]["latitude"])
                lon1 = radians(df.iloc[i - 1]["longitude"])
                lat2 = radians(df.iloc[i]["latitude"])
                lon2 = radians(df.iloc[i]["longitude"])
                dlat = lat2 - lat1
                dlon = lon2 - lon1
                a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
                c = 2 * asin(sqrt(a))
                distances.append(distances[-1] + c * 6371000)
            df["cumulative_distance_m"] = distances
        return df
    except Exception:
        return pd.DataFrame()


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

        bus_length_m = float(specs.get("bus_length_m", 18))
        battery_pack_size_kwh = float(specs.get("battery_pack_size_kwh", 37))
        battery_pack_weight_kg = float(specs.get("battery_pack_weight_kg", 253))
        max_battery_packs = int(specs.get("max_battery_packs", 14))
        max_passengers = int(specs.get("max_passengers", 120))
        empty_weight_kg = float(specs.get("empty_weight_kg", 18000))

        packs = num_battery_packs if num_battery_packs is not None else max_battery_packs
        battery_capacity_kwh = battery_pack_size_kwh * packs
        occupancy = float(run.occupancy_percent)
        passenger_weight = max_passengers * (occupancy / 100.0) * 70.0
        total_weight_kg = empty_weight_kg + packs * battery_pack_weight_kg + passenger_weight

        # Actual battery pack density (kg per kWh of battery)
        actual_pack_density = battery_pack_weight_kg / battery_pack_size_kwh

        # Load trip statistics from DB
        trip_stats = await load_shift_trip_statistics(db, run.shift_id)
        if not trip_stats:
            raise ValueError(f"No valid trip statistics for shift {run.shift_id}")

        json_data = {
            "shift_id": str(run.shift_id),
            "trip_statistics": trip_stats,
        }

        # Build aux energy function
        aux_fn = build_aux_energy_fn(specs, run.auxiliary_heating_type)

        # Get predictor (cached)
        predictor = get_predictor(run.model_name)

        # Build override_mass array (same mass for all trips)
        n_trips = len(trip_stats)
        override_mass = np.full(n_trips, total_weight_kg)

        # Run prediction
        results = predictor.predict_from_json(
            json_data=json_data,
            bus_length_m=bus_length_m,
            battery_capacity_kwh=battery_capacity_kwh,
            external_temp_celsius=float(run.external_temp_celsius),
            quantiles=quantiles,
            aux_energy_fn=aux_fn,
            override_mass=override_mass,
            battery_pack_density_override=actual_pack_density,
        )

        # Store contextual parameters
        run.contextual_parameters = {
            "battery_capacity_kwh": battery_capacity_kwh,
            "bus_length_m": bus_length_m,
            "num_battery_packs": packs,
            "total_weight_kg": total_weight_kg,
            "quantiles": quantiles,
            "greybox_params": results.get("greybox_params"),
        }
        run.summary = results.get("summary")

        # Store per-trip predictions
        predictions_list = results.get("predictions", [])
        for i, pred in enumerate(predictions_list):
            seq = trip_stats[i].get("sequence_number", i)
            trip_id_str = trip_stats[i].get("trip_id")

            q_dict = {}
            for q in quantiles:
                key = f"quantile_{q:.2f}"
                if key in pred:
                    q_dict[f"{q:.2f}"] = pred[key]

            tp = TripPredictions(
                prediction_run_id=prediction_run_id,
                trip_id=UUID(trip_id_str),
                sequence_number=seq,
                prediction_kwh=pred.get("prediction_kwh", 0),
                prediction_median_kwh=pred.get("prediction_median_kwh"),
                drivetrain_kwh=pred.get("drivetrain_kwh"),
                auxiliary_kwh=pred.get("auxiliary_kwh"),
                mass_sensitivity_kwh_per_kwh_batt=pred.get("mass_sensitivity_kwh_per_kwh_batt"),
                quantiles=q_dict if q_dict else None,
            )
            db.add(tp)

        run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        await db.commit()

    except Exception as e:
        logger.exception(f"Prediction failed for run {prediction_run_id}: {e}")
        run.status = "failed"
        run.summary = {"error": str(e)}
        await db.commit()
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
