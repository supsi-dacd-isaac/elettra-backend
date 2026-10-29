"""Read-only construction of aggregate profiles for a prediction reference."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math

import numpy as np
from sqlalchemy import select

from app.models import BusesModels, TripPredictions
from app.services.prediction import (
    PredictionStack, _model_passenger_mass_kg, build_aux_energy_fn,
    build_vecto_auxiliary_binding, get_predictor, load_shift_trip_statistics,
    physical_bus_mass, resolve_prediction_selection, validate_model_stack_contract,
)
from simulation.aggregate_consumption import (
    AggregateOptions, PredictiveDistributionUnavailable, aggregate_profile,
    empirical_qrf_support, shift_seed,
)
from simulation.optimization_contract import pack_count


async def load_aggregate_profile(db, prediction, *, options=AggregateOptions(), statistics_cache=None):
    """Use the same features, physical mass and auxiliary estimator as prediction.

    The cache is scoped to one optimization request; no user/model/context can
    cross its boundary. Existing prediction rows are never rewritten.
    """
    specs_model = await db.get(BusesModels, prediction.bus_model_id)
    if specs_model is None:
        raise PredictiveDistributionUnavailable("The prediction's bus model is no longer available")
    specs = specs_model.specs or {}
    stack = resolve_prediction_selection(prediction_stack=prediction.prediction_stack or None,
                                         model_name=prediction.model_name)
    predictor = get_predictor(prediction.model_name)
    validate_model_stack_contract(stack, predictor.metadata)
    mass = physical_bus_mass(specs, occupancy_percent=float(prediction.occupancy_percent),
                             num_battery_packs=pack_count(prediction),
                             passenger_mass_kg=_model_passenger_mass_kg(stack, predictor))
    context = prediction.contextual_parameters or {}
    for field, expected in (("total_weight_kg", mass.total_weight_kg),
                            ("battery_capacity_kwh", mass.battery_capacity_kwh)):
        if not math.isclose(float(context.get(field, -1)), expected, abs_tol=1e-6):
            raise PredictiveDistributionUnavailable("Prediction mass/capacity is stale; recalculate its forecast")
    sid = str(prediction.shift_id)
    cache = statistics_cache if statistics_cache is not None else {}
    if sid not in cache:
        cache[sid] = await load_shift_trip_statistics(db, prediction.shift_id)
    trip_statistics = cache[sid]
    query = await db.execute(select(TripPredictions).where(
        TripPredictions.prediction_run_id == prediction.id
    ).order_by(TripPredictions.sequence_number))
    stored = list(query.scalars().all())
    occurrences = [(int(row["sequence_number"]), str(row["trip_id"])) for row in trip_statistics]
    if occurrences != [(row.sequence_number, str(row.trip_id)) for row in stored]:
        raise PredictiveDistributionUnavailable("Prediction no longer covers the complete ordered shift")

    if stack.stack is PredictionStack.LEGACY:
        aux = build_aux_energy_fn(specs, prediction.auxiliary_heating_type)
    else:
        aux = build_vecto_auxiliary_binding(
            stack_release=stack, bus_model_specs=specs,
            occupancy_percent=float(prediction.occupancy_percent),
            external_temp_celsius=float(prediction.external_temp_celsius),
            auxiliary_heating_type=prediction.auxiliary_heating_type,
        ).energy_fn
    reference_occupancy = getattr(predictor.model, "qrf_reference_occupancy_percent", None)
    reference_mass = None
    if reference_occupancy is not None:
        reference = physical_bus_mass(specs, occupancy_percent=float(reference_occupancy),
            num_battery_packs=mass.battery_packs,
            passenger_mass_kg=_model_passenger_mass_kg(stack, predictor))
        reference_mass = np.full(len(stored), reference.total_weight_kg)

    def calculate():
        frame = predictor.prepare_features(trip_statistics, mass.bus_length_m,
            mass.battery_capacity_kwh, float(prediction.external_temp_celsius))
        support, mean, distribution = empirical_qrf_support(predictor, frame,
            override_mass=np.full(len(stored), mass.total_weight_kg),
            qrf_reference_mass=reference_mass, aux_energy_fn=aux)
        if not np.allclose(mean, np.asarray([row.prediction_kwh for row in stored], dtype=float), atol=1e-6, rtol=1e-7):
            raise PredictiveDistributionUnavailable(
                "Current model/features do not reproduce the reference forecast; recalculate predictions"
            )
        result = aggregate_profile(support, options=options, seed=shift_seed(options.seed, sid))
        result["_support"] = support  # request-local only; never persisted in JSONB
        result["statistics"].update(distribution)
        result["reference"] = {
            "prediction_run_id": str(prediction.id), "shift_id": sid,
            "model_name": prediction.model_name, "prediction_stack": prediction.prediction_stack,
            "auxiliary_estimator_release": prediction.auxiliary_estimator_release,
            "battery_packs": mass.battery_packs, "battery_capacity_kwh": mass.battery_capacity_kwh,
            "total_weight_kg": mass.total_weight_kg,
            "input_fingerprint": hashlib.sha256(json.dumps({
                "features": frame.to_dict(orient="list"), "mass": mass.total_weight_kg,
                "model": prediction.model_name, "context": context,
                "occurrences": occurrences,
            }, sort_keys=True, default=str).encode()).hexdigest(),
        }
        result["occurrences"] = [{"sequence_number": sequence, "trip_id": trip}
                                 for sequence, trip in occurrences]
        return result

    return await asyncio.to_thread(calculate)
