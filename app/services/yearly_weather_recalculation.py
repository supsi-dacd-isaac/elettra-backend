"""Bind yearly analyses to weather series and recalculate temperature scenarios."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import (
    Depots,
    GtfsStops,
    GtfsStopsTimes,
    OptimizationRuns,
    PredictionRuns,
    ShiftsStructures,
    TripPredictions,
    Users,
    WeatherTemperatureClusters,
    WeatherTemperatureSeries,
    YearlyAnalysis,
    YearlyAnalysisWeatherRevisions,
)
from app.services.prediction import predict_shift_consumption


DEFAULT_CLUSTER_START = "05:00"
DEFAULT_CLUSTER_END = "24:00"
MAX_SERIES_DISTANCE_KM = 25.0


class AnalysisWeatherResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class AnalysisWeatherBinding:
    series: WeatherTemperatureSeries
    k: int
    start_time: str
    end_time: str
    clusters: tuple[WeatherTemperatureClusters, ...]
    resolution: str


def _distance_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    radius = 6371.0088
    phi_a = math.radians(lat_a)
    phi_b = math.radians(lat_b)
    d_phi = math.radians(lat_b - lat_a)
    d_lambda = math.radians(lon_b - lon_a)
    hav = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(hav), math.sqrt(1 - hav))


def _shift_ids(features: Mapping[str, Any]) -> list[UUID]:
    raw_ids = (features.get("config") or {}).get("shift_ids") or []
    values: list[UUID] = []
    for raw in raw_ids:
        try:
            values.append(UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    return values


async def _analysis_owner_id(db: AsyncSession, analysis: YearlyAnalysis) -> UUID:
    if analysis.optimization_run_id is not None:
        optimization = await db.get(OptimizationRuns, analysis.optimization_run_id)
        if optimization is not None:
            return optimization.user_id
    result = await db.execute(
        select(PredictionRuns.user_id)
        .where(PredictionRuns.yearly_analysis_id == analysis.id)
        .distinct()
    )
    owner_ids = list(result.scalars().all())
    if len(owner_ids) != 1:
        raise AnalysisWeatherResolutionError(
            f"analysis {analysis.id} has no unambiguous owner"
        )
    return owner_ids[0]


async def _depot_point_for_analysis(
    db: AsyncSession, analysis: YearlyAnalysis, owner_id: UUID
) -> tuple[float, float, str]:
    result = await db.execute(
        select(Depots, GtfsStops)
        .join(GtfsStops, GtfsStops.id == Depots.stop_id)
        .where(
            and_(
                Depots.user_id == owner_id,
                GtfsStops.stop_lat.is_not(None),
                GtfsStops.stop_lon.is_not(None),
            )
        )
    )
    owner_depots = list(result.all())
    shift_ids = _shift_ids(analysis.features or {})
    if shift_ids and owner_depots:
        stop_result = await db.execute(
            select(GtfsStopsTimes.stop_id)
            .join(ShiftsStructures, ShiftsStructures.trip_id == GtfsStopsTimes.trip_id)
            .where(ShiftsStructures.shift_id.in_(shift_ids))
            .distinct()
        )
        shift_stop_ids = set(stop_result.scalars().all())
        used = [(depot, stop) for depot, stop in owner_depots if depot.stop_id in shift_stop_ids]
        if len(used) == 1:
            depot, stop = used[0]
            return float(stop.stop_lat), float(stop.stop_lon), f"shift-depot:{depot.id}"
        if len(used) > 1:
            raise AnalysisWeatherResolutionError(
                f"analysis {analysis.id} uses multiple depot stops"
            )
    if len(owner_depots) == 1:
        depot, stop = owner_depots[0]
        return float(stop.stop_lat), float(stop.stop_lon), f"owner-depot:{depot.id}"
    if len(owner_depots) > 1:
        raise AnalysisWeatherResolutionError(
            f"analysis {analysis.id} owner has multiple candidate depots"
        )

    owner = await db.get(Users, owner_id)
    if owner is None:
        raise AnalysisWeatherResolutionError(f"analysis {analysis.id} owner not found")
    agency_result = await db.execute(
        select(Depots, GtfsStops)
        .join(Users, Users.id == Depots.user_id)
        .join(GtfsStops, GtfsStops.id == Depots.stop_id)
        .where(
            and_(
                Users.company_id == owner.company_id,
                GtfsStops.stop_lat.is_not(None),
                GtfsStops.stop_lon.is_not(None),
            )
        )
    )
    agency_depots = list(agency_result.all())
    unique_points = {
        (round(float(stop.stop_lat), 5), round(float(stop.stop_lon), 5), depot.id)
        for depot, stop in agency_depots
    }
    if len(unique_points) != 1:
        raise AnalysisWeatherResolutionError(
            f"analysis {analysis.id} agency has {len(unique_points)} candidate depots"
        )
    latitude, longitude, depot_id = next(iter(unique_points))
    return latitude, longitude, f"agency-depot:{depot_id}"


async def _nearest_active_series(
    db: AsyncSession, latitude: float, longitude: float
) -> WeatherTemperatureSeries:
    result = await db.execute(
        select(WeatherTemperatureSeries).where(
            WeatherTemperatureSeries.status == "applied"
        )
    )
    candidates = list(result.scalars().all())
    if not candidates:
        raise AnalysisWeatherResolutionError("no active corrected weather series")
    ranked = sorted(
        (
            (
                _distance_km(
                    latitude,
                    longitude,
                    float(series.latitude),
                    float(series.longitude),
                ),
                series,
            )
            for series in candidates
        ),
        key=lambda item: (item[0], str(item[1].id)),
    )
    if ranked[0][0] > MAX_SERIES_DISTANCE_KM:
        raise AnalysisWeatherResolutionError(
            f"nearest corrected series is {ranked[0][0]:.1f} km from the depot"
        )
    if len(ranked) > 1 and abs(ranked[1][0] - ranked[0][0]) < 0.001:
        raise AnalysisWeatherResolutionError("two corrected series are equally near")
    return ranked[0][1]


async def resolve_analysis_weather_binding(
    db: AsyncSession,
    analysis: YearlyAnalysis,
    *,
    explicit: Mapping[str, Any] | None = None,
    owner_id: UUID | None = None,
    cluster_k: int | None = None,
    cluster_start_time: str | None = None,
    cluster_end_time: str | None = None,
) -> AnalysisWeatherBinding:
    scenarios = (analysis.features or {}).get("scenarios") or []
    if not scenarios:
        raise AnalysisWeatherResolutionError(f"analysis {analysis.id} has no scenarios")

    if explicit is not None:
        latitude = float(explicit["latitude"])
        longitude = float(explicit["longitude"])
        series = await _nearest_active_series(db, latitude, longitude)
        k = int(explicit.get("k", cluster_k or len(scenarios)))
        start_time = str(
            explicit.get("start_time", cluster_start_time or DEFAULT_CLUSTER_START)
        )
        end_time = str(
            explicit.get("end_time", cluster_end_time or DEFAULT_CLUSTER_END)
        )
        resolution = "explicit-map"
    else:
        owner_id = owner_id or await _analysis_owner_id(db, analysis)
        latitude, longitude, resolution = await _depot_point_for_analysis(
            db, analysis, owner_id
        )
        series = await _nearest_active_series(db, latitude, longitude)
        k = cluster_k or len(scenarios)
        start_time = cluster_start_time or DEFAULT_CLUSTER_START
        end_time = cluster_end_time or DEFAULT_CLUSTER_END

    cluster_result = await db.execute(
        select(WeatherTemperatureClusters)
        .where(
            and_(
                WeatherTemperatureClusters.latitude == series.latitude,
                WeatherTemperatureClusters.longitude == series.longitude,
                WeatherTemperatureClusters.k == k,
                WeatherTemperatureClusters.start_time == start_time,
                WeatherTemperatureClusters.end_time == end_time,
                WeatherTemperatureClusters.temperature_series_id == series.id,
            )
        )
        .order_by(WeatherTemperatureClusters.cluster_id)
    )
    clusters = tuple(cluster_result.scalars().all())
    if len(clusters) != k:
        raise AnalysisWeatherResolutionError(
            f"corrected cluster configuration is missing for analysis {analysis.id}"
        )
    return AnalysisWeatherBinding(series, k, start_time, end_time, clusters, resolution)


async def binding_for_series_id(
    db: AsyncSession,
    series_id: UUID,
    *,
    k: int,
    start_time: str,
    end_time: str,
) -> AnalysisWeatherBinding:
    series = await db.get(WeatherTemperatureSeries, series_id)
    if series is None or series.status != "applied":
        raise AnalysisWeatherResolutionError("weather temperature series is not active")
    result = await db.execute(
        select(WeatherTemperatureClusters)
        .where(
            and_(
                WeatherTemperatureClusters.temperature_series_id == series.id,
                WeatherTemperatureClusters.k == k,
                WeatherTemperatureClusters.start_time == start_time,
                WeatherTemperatureClusters.end_time == end_time,
            )
        )
        .order_by(WeatherTemperatureClusters.cluster_id)
    )
    clusters = tuple(result.scalars().all())
    if len(clusters) != k:
        raise AnalysisWeatherResolutionError("weather cluster configuration is missing")
    return AnalysisWeatherBinding(series, k, start_time, end_time, clusters, "explicit-series")


async def _prediction_kpis(db: AsyncSession, run: PredictionRuns) -> dict[str, Any]:
    summary = run.summary or {}
    total_energy = float(summary.get("total_consumption_kwh", 0.0))
    distance = float(summary.get("total_distance_km", 0.0))
    auxiliary = float(summary.get("total_auxiliary_kwh", 0.0))
    drivetrain = float(summary.get("total_drivetrain_kwh", total_energy - auxiliary))
    result = await db.execute(
        select(TripPredictions)
        .where(TripPredictions.prediction_run_id == run.id)
        .order_by(TripPredictions.sequence_number)
    )
    trip_rows = list(result.scalars().all())
    quantile_totals: dict[str, float] = {}
    for row in trip_rows:
        for key, value in (row.quantiles or {}).items():
            if value is not None:
                quantile_totals[key] = quantile_totals.get(key, 0.0) + float(value)

    def q_label(raw: str) -> str:
        return f"q{round(float(raw) * 100):02d}"

    quantiles = {q_label(key): value for key, value in sorted(quantile_totals.items())}
    drivetrain_quantiles = {
        key: value - auxiliary for key, value in quantiles.items()
    }
    safe_distance = distance if distance > 0 else 1.0
    return {
        "feasible": None,
        "quantiles": quantiles,
        "distanceKm": distance,
        "energyPerKm": total_energy / safe_distance,
        "solverStatus": "prediction-only",
        "totalEnergyKwh": total_energy,
        "auxiliaryPerKmKwh": auxiliary / safe_distance,
        "auxiliaryEnergyKwh": auxiliary,
        "drivetrainPerKmKwh": drivetrain / safe_distance,
        "drivetrainEnergyKwh": drivetrain,
        "drivetrainQuantiles": drivetrain_quantiles,
        "drivetrainPerKmQuantiles": {
            key: value / safe_distance for key, value in drivetrain_quantiles.items()
        },
        "consumptionPerKmQuantiles": {
            key: value / safe_distance for key, value in quantiles.items()
        },
    }


async def build_yearly_results(
    db: AsyncSession,
    previous_results: Mapping[str, Any],
    scenarios: list[dict[str, Any]],
    runs: list[PredictionRuns],
) -> dict[str, Any]:
    if len(scenarios) != len(runs):
        raise ValueError("one completed prediction run is required per weather scenario")
    scenario_results: list[dict[str, Any]] = []
    yearly_energy = 0.0
    yearly_auxiliary = 0.0
    yearly_drivetrain = 0.0
    yearly_distance = 0.0
    for scenario, run in zip(scenarios, runs, strict=True):
        kpis = await _prediction_kpis(db, run)
        occurrences = int(scenario["occurrences"])
        scenario_results.append(
            {
                "kpis": kpis,
                "error": None,
                "label": scenario["label"],
                "predRunId": str(run.id),
                "occurrences": occurrences,
                "temperature": float(scenario["temperature"]),
            }
        )
        yearly_energy += kpis["totalEnergyKwh"] * occurrences
        yearly_auxiliary += kpis["auxiliaryEnergyKwh"] * occurrences
        yearly_drivetrain += kpis["drivetrainEnergyKwh"] * occurrences
        yearly_distance += kpis["distanceKm"] * occurrences

    results = dict(previous_results or {})
    results["scenarioResults"] = scenario_results
    results["yearlyTotals"] = {
        "distanceKm": yearly_distance,
        "totalEnergyKwh": yearly_energy,
        "auxiliaryEnergyKwh": yearly_auxiliary,
        "drivetrainEnergyKwh": yearly_drivetrain,
    }
    return results


def _assert_results_close(expected: Any, actual: Any, path: str = "results") -> None:
    """Compare persisted client results with the centralized backend builder."""

    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        if expected != actual:
            raise ValueError(f"historical result mismatch at {path}")
        return
    if isinstance(expected, (int, float)):
        if not isinstance(actual, (int, float)) or not math.isclose(
            float(expected), float(actual), rel_tol=1e-7, abs_tol=1e-4
        ):
            raise ValueError(f"historical numeric result mismatch at {path}")
        return
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            raise ValueError(f"historical result length mismatch at {path}")
        for index, (old, new) in enumerate(zip(expected, actual, strict=True)):
            _assert_results_close(old, new, f"{path}[{index}]")
        return
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise ValueError(f"historical result type mismatch at {path}")
        for key, value in expected.items():
            if key not in actual:
                raise ValueError(f"historical result missing {path}.{key}")
            _assert_results_close(value, actual[key], f"{path}.{key}")
        return
    raise ValueError(f"unsupported historical result type at {path}")


def _build_energy_summary_blob(
    features: Mapping[str, Any], scenarios: list[dict[str, Any]], runs: list[PredictionRuns]
) -> dict[str, Any]:
    global_aux_type = (features.get("config") or {}).get(
        "auxiliary_heating_type", "default"
    )
    scenario_rows: list[dict[str, Any]] = []
    totals = {
        "distance_km": 0.0,
        "electric_kwh": 0.0,
        "auxiliary_kwh": 0.0,
        "drivetrain_kwh": 0.0,
    }
    diesel_fuel_kwh = 0.0
    diesel_liters = 0.0
    for scenario, run in zip(scenarios, runs, strict=True):
        summary = run.summary or {}
        occurrences = int(scenario["occurrences"])
        daily_energy = float(summary.get("total_consumption_kwh", 0.0))
        daily_distance = float(summary.get("total_distance_km", 0.0))
        daily_aux = float(summary.get("total_auxiliary_kwh", 0.0))
        daily_drivetrain = float(summary.get("total_drivetrain_kwh", 0.0))
        scenario_rows.append(
            {
                "prediction_run_id": str(run.id),
                "temperature_celsius": float(run.external_temp_celsius),
                "occurrences": occurrences,
                "auxiliary_heating_type": run.auxiliary_heating_type,
                "daily_electric_kwh": round(daily_energy, 4),
                "daily_distance_km": round(daily_distance, 4),
                "daily_auxiliary_kwh": round(daily_aux, 4),
                "daily_drivetrain_kwh": round(daily_drivetrain, 4),
                "diesel_heating": summary.get("diesel_heating"),
                "annual_electric_kwh": round(daily_energy * occurrences, 4),
                "annual_distance_km": round(daily_distance * occurrences, 4),
                "annual_auxiliary_kwh": round(daily_aux * occurrences, 4),
                "annual_drivetrain_kwh": round(daily_drivetrain * occurrences, 4),
                "annual_diesel_fuel_kwh": round(
                    float((summary.get("diesel_heating") or {}).get("diesel_fuel_kwh", 0.0))
                    * occurrences,
                    4,
                ),
                "annual_diesel_liters": round(
                    float((summary.get("diesel_heating") or {}).get("diesel_liters", 0.0))
                    * occurrences,
                    4,
                ),
            }
        )
        totals["distance_km"] += daily_distance * occurrences
        totals["electric_kwh"] += daily_energy * occurrences
        totals["auxiliary_kwh"] += daily_aux * occurrences
        totals["drivetrain_kwh"] += daily_drivetrain * occurrences
        diesel = summary.get("diesel_heating") or {}
        diesel_fuel_kwh += float(diesel.get("diesel_fuel_kwh", 0.0)) * occurrences
        diesel_liters += float(diesel.get("diesel_liters", 0.0)) * occurrences
    diesel_summary = None
    if diesel_fuel_kwh or diesel_liters:
        diesel_summary = {
            "diesel_fuel_kwh": round(diesel_fuel_kwh, 4),
            "diesel_liters": round(diesel_liters, 4),
        }
        totals["diesel_fuel_kwh"] = diesel_fuel_kwh
        totals["diesel_liters"] = diesel_liters
        totals["combined_final_energy_kwh"] = totals["electric_kwh"] + diesel_fuel_kwh
    return {
        "auxiliary_heating_type": global_aux_type,
        "yearly_totals": {key: round(value, 4) for key, value in totals.items()},
        "yearly_diesel_heating": diesel_summary,
        "scenarios": scenario_rows,
    }


async def _completed_runs(db: AsyncSession, analysis_id: UUID) -> list[PredictionRuns]:
    result = await db.execute(
        select(PredictionRuns)
        .where(PredictionRuns.yearly_analysis_id == analysis_id)
        .order_by(PredictionRuns.external_temp_celsius, PredictionRuns.id)
    )
    return list(result.scalars().all())


async def _fail_unfinished_revision_runs(
    db: AsyncSession,
    revision: YearlyAnalysisWeatherRevisions,
) -> None:
    """Close replacement runs that can no longer be part of an atomic swap."""

    if not revision.new_prediction_run_ids:
        return
    run_ids = [UUID(value) for value in revision.new_prediction_run_ids]
    result = await db.execute(
        select(PredictionRuns).where(PredictionRuns.id.in_(run_ids))
    )
    for run in result.scalars().all():
        if run.status in {"pending", "running"}:
            run.status = "failed"


async def _reconcile_interrupted_revisions(db: AsyncSession) -> None:
    """Make a resumed batch explicit and leave no orphan run looking active."""

    result = await db.execute(
        select(YearlyAnalysisWeatherRevisions).where(
            YearlyAnalysisWeatherRevisions.status.in_(["pending", "failed"])
        )
    )
    for revision in result.scalars().all():
        await _fail_unfinished_revision_runs(db, revision)
        if revision.status == "pending":
            revision.status = "failed"
            revision.last_error = "interrupted before completion; closed by resume"
    await db.commit()


async def recalculate_yearly_analysis(
    analysis_id: UUID,
    *,
    explicit_mapping: Mapping[str, Any] | None,
    resume: bool,
) -> bool:
    async with AsyncSessionLocal() as db:
        analysis = await db.get(YearlyAnalysis, analysis_id)
        if analysis is None:
            raise ValueError(f"yearly analysis {analysis_id} not found")
        binding = await resolve_analysis_weather_binding(
            db, analysis, explicit=explicit_mapping
        )
        if resume:
            existing_result = await db.execute(
                select(YearlyAnalysisWeatherRevisions.id).where(
                    and_(
                        YearlyAnalysisWeatherRevisions.yearly_analysis_id == analysis.id,
                        YearlyAnalysisWeatherRevisions.new_temperature_series_id
                        == binding.series.id,
                        YearlyAnalysisWeatherRevisions.status == "completed",
                    )
                )
            )
            if existing_result.scalar_one_or_none() is not None:
                return False

        old_runs = await _completed_runs(db, analysis.id)
        old_scenarios = list((analysis.features or {}).get("scenarios") or [])
        if len(old_runs) != len(old_scenarios) or not old_runs:
            raise ValueError(
                f"analysis {analysis.id} must have one prediction run per scenario"
            )
        if any(run.status != "completed" for run in old_runs):
            raise ValueError(f"analysis {analysis.id} has incomplete prediction runs")

        previous_results = (analysis.features or {}).get("results") or {}
        rebuilt_previous = await build_yearly_results(
            db, previous_results, old_scenarios, old_runs
        )
        for validated_key in ("scenarioResults", "yearlyTotals"):
            if validated_key in previous_results:
                _assert_results_close(
                    previous_results[validated_key],
                    rebuilt_previous[validated_key],
                    f"results.{validated_key}",
                )

        new_scenarios = [
            {
                "label": f"Cluster {index}",
                "occurrences": int(cluster.occurrences),
                "temperature": float(cluster.centroid_daily_avg_temp),
            }
            for index, cluster in enumerate(binding.clusters, start=1)
        ]
        revision = YearlyAnalysisWeatherRevisions(
            yearly_analysis_id=analysis.id,
            previous_temperature_series_id=analysis.weather_temperature_series_id,
            new_temperature_series_id=binding.series.id,
            previous_cluster_k=analysis.weather_cluster_k,
            previous_cluster_start_time=analysis.weather_cluster_start_time,
            previous_cluster_end_time=analysis.weather_cluster_end_time,
            previous_features=deepcopy(analysis.features or {}),
            previous_prediction_run_ids=[str(run.id) for run in old_runs],
            new_prediction_run_ids=[],
            status="pending",
        )
        db.add(revision)
        await db.flush()

        new_runs: list[PredictionRuns] = []
        run_arguments: list[tuple[UUID, list[float], int | None]] = []
        for old_run, scenario in zip(old_runs, new_scenarios, strict=True):
            context = old_run.contextual_parameters or {}
            quantiles = [float(value) for value in context.get("quantiles", [0.05, 0.5, 0.95])]
            packs = context.get("num_battery_packs")
            new_run = PredictionRuns(
                user_id=old_run.user_id,
                shift_id=old_run.shift_id,
                bus_model_id=old_run.bus_model_id,
                yearly_analysis_id=None,
                model_name=old_run.model_name,
                external_temp_celsius=scenario["temperature"],
                auxiliary_heating_type=old_run.auxiliary_heating_type,
                occupancy_percent=old_run.occupancy_percent,
                status="pending",
            )
            db.add(new_run)
            await db.flush()
            new_runs.append(new_run)
            run_arguments.append((new_run.id, quantiles, int(packs) if packs is not None else None))
        revision.new_prediction_run_ids = [str(run.id) for run in new_runs]
        await db.commit()
        revision_id = revision.id

    try:
        for run_id, quantiles, packs in run_arguments:
            async with AsyncSessionLocal() as prediction_db:
                await predict_shift_consumption(
                    prediction_db,
                    run_id,
                    quantiles=quantiles,
                    num_battery_packs=packs,
                )
    except BaseException as exc:
        async with AsyncSessionLocal() as db:
            revision = await db.get(YearlyAnalysisWeatherRevisions, revision_id)
            if revision is not None:
                await _fail_unfinished_revision_runs(db, revision)
                revision.status = "failed"
                revision.last_error = str(exc)
                await db.commit()
        raise

    async with AsyncSessionLocal() as db:
        analysis = await db.get(YearlyAnalysis, analysis_id)
        revision = await db.get(YearlyAnalysisWeatherRevisions, revision_id)
        if analysis is None or revision is None:
            raise ValueError("analysis or recalculation revision disappeared")
        old_runs = await _completed_runs(db, analysis.id)
        new_result = await db.execute(
            select(PredictionRuns)
            .where(PredictionRuns.id.in_([UUID(value) for value in revision.new_prediction_run_ids]))
            .order_by(PredictionRuns.external_temp_celsius, PredictionRuns.id)
        )
        new_runs = list(new_result.scalars().all())
        if len(new_runs) != len(new_scenarios) or any(
            run.status != "completed" for run in new_runs
        ):
            raise ValueError("not all replacement prediction runs completed")

        updated_features = deepcopy(analysis.features or {})
        updated_features["scenarios"] = new_scenarios
        updated_features["results"] = await build_yearly_results(
            db,
            updated_features.get("results") or {},
            new_scenarios,
            new_runs,
        )
        updated_features["energy_summary"] = _build_energy_summary_blob(
            updated_features, new_scenarios, new_runs
        )
        meta = dict(updated_features.get("meta") or {})
        meta["weatherResolution"] = binding.resolution
        meta["weatherProcessingVersion"] = binding.series.processing_version
        updated_features["meta"] = meta

        for old_run in old_runs:
            old_run.yearly_analysis_id = None
        for new_run in new_runs:
            new_run.yearly_analysis_id = analysis.id
        analysis.features = updated_features
        analysis.weather_temperature_series_id = binding.series.id
        analysis.weather_cluster_k = binding.k
        analysis.weather_cluster_start_time = binding.start_time
        analysis.weather_cluster_end_time = binding.end_time
        revision.status = "completed"
        revision.completed_at = datetime.now(timezone.utc)
        await db.commit()
    return True


def _load_mapping(path: Path | None) -> dict[str, Mapping[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("analysis mapping must be a JSON object keyed by analysis UUID")
    return payload


async def recalculate_all_yearly_analyses(
    *, resume: bool, mapping_path: Path | None
) -> int:
    mapping = _load_mapping(mapping_path)
    async with AsyncSessionLocal() as db:
        if resume:
            await _reconcile_interrupted_revisions(db)
        result = await db.execute(select(YearlyAnalysis).order_by(YearlyAnalysis.created_at))
        analyses = [
            analysis
            for analysis in result.scalars().all()
            if (analysis.features or {}).get("scenarios")
        ]
    failures: list[dict[str, str]] = []
    changed = 0
    skipped = 0
    for index, analysis in enumerate(analyses, start=1):
        print(f"[{index}/{len(analyses)}] recalculating {analysis.id} {analysis.name}")
        try:
            was_changed = await recalculate_yearly_analysis(
                analysis.id,
                explicit_mapping=mapping.get(str(analysis.id)),
                resume=resume,
            )
            changed += int(was_changed)
            skipped += int(not was_changed)
        except Exception as exc:
            failures.append({"analysis_id": str(analysis.id), "error": str(exc)})
            print(f"  FAILED: {exc}")
    print(json.dumps({"changed": changed, "skipped": skipped, "failures": failures}, indent=2))
    return 1 if failures else 0


async def rollback_yearly_analyses_for_series(series_id: UUID) -> None:
    """Restore the latest completed yearly-analysis revision for a series."""

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(YearlyAnalysisWeatherRevisions)
            .where(
                and_(
                    YearlyAnalysisWeatherRevisions.new_temperature_series_id == series_id,
                    YearlyAnalysisWeatherRevisions.status == "completed",
                )
            )
            .order_by(YearlyAnalysisWeatherRevisions.created_at.desc())
        )
        revisions = list(result.scalars().all())
        for revision in revisions:
            analysis = await db.get(YearlyAnalysis, revision.yearly_analysis_id)
            if analysis is None:
                continue
            current_runs = await _completed_runs(db, analysis.id)
            previous_ids = [UUID(value) for value in revision.previous_prediction_run_ids]
            previous_result = await db.execute(
                select(PredictionRuns).where(PredictionRuns.id.in_(previous_ids))
            )
            previous_runs = list(previous_result.scalars().all())
            if len(previous_runs) != len(previous_ids):
                raise ValueError(
                    f"cannot rollback analysis {analysis.id}: previous runs are missing"
                )
            for run in current_runs:
                run.yearly_analysis_id = None
            for run in previous_runs:
                run.yearly_analysis_id = analysis.id
            analysis.features = deepcopy(revision.previous_features)
            analysis.weather_temperature_series_id = revision.previous_temperature_series_id
            analysis.weather_cluster_k = revision.previous_cluster_k
            analysis.weather_cluster_start_time = revision.previous_cluster_start_time
            analysis.weather_cluster_end_time = revision.previous_cluster_end_time
            revision.status = "rolled_back"
            revision.rolled_back_at = datetime.now(timezone.utc)
        await db.commit()
