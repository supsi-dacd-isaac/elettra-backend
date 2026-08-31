#!/usr/bin/env python3
"""Plan, apply and rollback the PVGIS/Open-Meteo temperature backfill."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.config import get_cached_settings  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.models import WeatherMeasurements, WeatherTemperatureSeries  # noqa: E402
from app.services.hybrid_temperature import (  # noqa: E402
    HYBRID_PROVIDER,
    OPENMETEO_MODEL,
    PROCESSING_VERSION,
    HybridTemperatureRow,
    HybridTemperatureSeries,
    fetch_hybrid_temperature_series,
)
from app.services.weather import (  # noqa: E402
    apply_hybrid_temperature_series,
    fetch_weather_records,
    get_active_temperature_series,
    rollback_hybrid_temperature_series,
)


BUNDLE_SCHEMA_VERSION = 1


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, Decimal)):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _temperature_checksum(records: list[WeatherMeasurements]) -> str:
    digest = hashlib.sha256()
    for record in records:
        timestamp = record.time_utc.astimezone(timezone.utc).isoformat()
        value = "null" if record.temp_air is None else format(float(record.temp_air), ".9g")
        digest.update(f"{timestamp}|{value}\n".encode("utf-8"))
    return digest.hexdigest()


def _bundle_checksum(entry: dict[str, Any]) -> str:
    payload = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=_json_default)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _series_to_entry(
    series: HybridTemperatureSeries,
    *,
    baseline_checksum: str,
    baseline_temperatures: list[float],
) -> dict[str, Any]:
    corrected = [row.temp_air for row in series.rows]
    entry = {
        "latitude": series.latitude,
        "longitude": series.longitude,
        "requested_latitude": series.requested_latitude,
        "requested_longitude": series.requested_longitude,
        "coerce_year": series.coerce_year,
        "baseline_checksum": baseline_checksum,
        "months_selected": list(series.months_selected),
        "pvgis_metadata": dict(series.pvgis_metadata),
        "openmeteo_metadata": list(series.openmeteo_metadata),
        "diagnostics": {
            "baseline_mean_c": sum(baseline_temperatures) / len(baseline_temperatures),
            "corrected_mean_c": sum(corrected) / len(corrected),
            "mean_delta_c": (
                sum(corrected) / len(corrected)
                - sum(baseline_temperatures) / len(baseline_temperatures)
            ),
            "baseline_min_c": min(baseline_temperatures),
            "baseline_max_c": max(baseline_temperatures),
            "corrected_min_c": min(corrected),
            "corrected_max_c": max(corrected),
        },
        "rows": [
            {
                "time_utc": row.time_utc.isoformat(),
                "source_time_utc": row.source_time_utc.isoformat(),
                "temp_air": row.temp_air,
                "pvgis_temp_air": row.pvgis_temp_air,
                "pvgis_fields": dict(row.pvgis_fields),
            }
            for row in series.rows
        ],
    }
    entry["entry_checksum"] = _bundle_checksum(entry)
    return entry


def _entry_to_series(entry: dict[str, Any]) -> HybridTemperatureSeries:
    checksum = entry.get("entry_checksum")
    unsigned = dict(entry)
    unsigned.pop("entry_checksum", None)
    if checksum != _bundle_checksum(unsigned):
        raise ValueError(
            f"bundle entry checksum mismatch at {entry.get('latitude')},{entry.get('longitude')}"
        )
    rows = tuple(
        HybridTemperatureRow(
            time_utc=datetime.fromisoformat(row["time_utc"]),
            source_time_utc=datetime.fromisoformat(row["source_time_utc"]),
            temp_air=float(row["temp_air"]),
            pvgis_temp_air=(
                float(row["pvgis_temp_air"])
                if row.get("pvgis_temp_air") is not None
                else None
            ),
            pvgis_fields=row.get("pvgis_fields") or {},
        )
        for row in entry["rows"]
    )
    return HybridTemperatureSeries(
        requested_latitude=float(entry["requested_latitude"]),
        requested_longitude=float(entry["requested_longitude"]),
        latitude=float(entry["latitude"]),
        longitude=float(entry["longitude"]),
        coerce_year=int(entry["coerce_year"]),
        rows=rows,
        months_selected=tuple(entry["months_selected"]),
        pvgis_metadata=entry.get("pvgis_metadata") or {},
        openmeteo_metadata=tuple(entry.get("openmeteo_metadata") or []),
    )


def _write_bundle(path: Path, bundle: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(bundle, handle, default=_json_default, separators=(",", ":"))


def _read_bundle(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        bundle = json.load(handle)
    manifest_checksum = bundle.get("bundle_checksum")
    if manifest_checksum is not None:
        unsigned = dict(bundle)
        unsigned.pop("bundle_checksum", None)
        if manifest_checksum != _bundle_checksum(unsigned):
            raise ValueError("bundle manifest checksum mismatch")
    if bundle.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise ValueError("unsupported bundle schema version")
    if bundle.get("provider") != HYBRID_PROVIDER:
        raise ValueError("bundle temperature provider does not match this deployment")
    if bundle.get("processing_version") != PROCESSING_VERSION:
        raise ValueError("bundle processing version does not match this deployment")
    if bundle.get("openmeteo_model") != OPENMETEO_MODEL:
        raise ValueError("bundle Open-Meteo model does not match this deployment")
    entries = bundle.get("entries")
    failures = bundle.get("failures")
    if not isinstance(entries, list) or not isinstance(failures, list):
        raise ValueError("bundle inventory is invalid")
    if failures:
        raise ValueError("bundle contains planning failures and cannot be applied")
    if bundle.get("inventory_count") != len(entries):
        raise ValueError("bundle inventory count does not match its entries")
    return bundle


async def _weather_coordinates() -> list[tuple[Decimal, Decimal]]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WeatherMeasurements.latitude, WeatherMeasurements.longitude)
            .distinct()
            .order_by(WeatherMeasurements.latitude, WeatherMeasurements.longitude)
        )
        return [(latitude, longitude) for latitude, longitude in result.all()]


async def plan_bundle(bundle_path: Path) -> int:
    settings = get_cached_settings()
    entries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    coordinates = await _weather_coordinates()
    for index, (latitude, longitude) in enumerate(coordinates, start=1):
        label = f"{latitude},{longitude}"
        print(f"[{index}/{len(coordinates)}] planning {label}", flush=True)
        try:
            async with AsyncSessionLocal() as db:
                records = await fetch_weather_records(db, latitude, longitude)
            if len(records) != 8_760 or any(record.temp_air is None for record in records):
                raise ValueError(f"baseline is incomplete ({len(records)} rows)")
            baseline_temperatures = [float(record.temp_air) for record in records]
            baseline_checksum = _temperature_checksum(records)
            series = await fetch_hybrid_temperature_series(
                float(latitude),
                float(longitude),
                coerce_year=settings.pvgis_coerce_year,
            )
            entries.append(
                _series_to_entry(
                    series,
                    baseline_checksum=baseline_checksum,
                    baseline_temperatures=baseline_temperatures,
                )
            )
        except Exception as exc:
            failures.append({"coordinate": label, "error": str(exc)})
            print(f"  FAILED: {exc}", file=sys.stderr, flush=True)

    bundle = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "provider": HYBRID_PROVIDER,
        "processing_version": PROCESSING_VERSION,
        "openmeteo_model": OPENMETEO_MODEL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coerce_year": settings.pvgis_coerce_year,
        "inventory_count": len(coordinates),
        "entries": entries,
        "failures": failures,
    }
    bundle["bundle_checksum"] = _bundle_checksum(bundle)
    _write_bundle(bundle_path, bundle)
    print(
        f"bundle={bundle_path} planned={len(entries)} failed={len(failures)}",
        flush=True,
    )
    return 1 if failures else 0


async def apply_weather(bundle_path: Path, *, resume: bool) -> int:
    bundle = _read_bundle(bundle_path)
    failures: list[dict[str, str]] = []
    applied = 0
    skipped = 0
    entries = bundle.get("entries") or []
    for index, entry in enumerate(entries, start=1):
        latitude = Decimal(str(entry["latitude"]))
        longitude = Decimal(str(entry["longitude"]))
        label = f"{latitude},{longitude}"
        print(f"[{index}/{len(entries)}] applying {label}", flush=True)
        try:
            series = _entry_to_series(entry)
            async with AsyncSessionLocal() as db:
                active = await get_active_temperature_series(db, latitude, longitude)
                if (
                    resume
                    and active is not None
                    and active.provider == HYBRID_PROVIDER
                    and active.openmeteo_model == OPENMETEO_MODEL
                    and active.processing_version == PROCESSING_VERSION
                ):
                    skipped += 1
                    continue
                records = await fetch_weather_records(db, latitude, longitude)
                if _temperature_checksum(records) != entry["baseline_checksum"]:
                    raise ValueError("database baseline checksum differs from reviewed bundle")
                _row, changed = await apply_hybrid_temperature_series(
                    db, series, resume=resume
                )
                applied += int(changed)
                skipped += int(not changed)
        except Exception as exc:
            failures.append({"coordinate": label, "error": str(exc)})
            print(f"  FAILED: {exc}", file=sys.stderr, flush=True)
    print(
        json.dumps(
            {"applied": applied, "skipped": skipped, "failures": failures},
            indent=2,
        )
    )
    return 1 if failures else 0


async def rollback_weather(*, all_series: bool, latitude: float | None, longitude: float | None) -> int:
    if all_series:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(WeatherTemperatureSeries.latitude, WeatherTemperatureSeries.longitude)
                .where(WeatherTemperatureSeries.status == "applied")
                .order_by(WeatherTemperatureSeries.latitude, WeatherTemperatureSeries.longitude)
            )
            coordinates = list(result.all())
    else:
        if latitude is None or longitude is None:
            raise ValueError("provide --all or both --latitude and --longitude")
        coordinates = [(Decimal(str(round(latitude, 5))), Decimal(str(round(longitude, 5))))]

    failures: list[dict[str, str]] = []
    for lat, lon in coordinates:
        try:
            async with AsyncSessionLocal() as db:
                active = await get_active_temperature_series(db, lat, lon)
                if active is None:
                    raise ValueError("no active hybrid series")
                active_id = active.id
            from app.services.yearly_weather_recalculation import (
                rollback_yearly_analyses_for_series,
            )

            await rollback_yearly_analyses_for_series(active_id)
            async with AsyncSessionLocal() as db:
                await rollback_hybrid_temperature_series(db, lat, lon)
            print(f"rolled back {lat},{lon}")
        except Exception as exc:
            failures.append({"coordinate": f"{lat},{lon}", "error": str(exc)})
            print(f"FAILED {lat},{lon}: {exc}", file=sys.stderr)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="download and validate an immutable bundle")
    plan.add_argument("--bundle", type=Path, required=True)

    apply = subparsers.add_parser("apply-weather", help="apply a reviewed bundle")
    apply.add_argument("--bundle", type=Path, required=True)
    apply.add_argument("--resume", action="store_true")

    recalculate = subparsers.add_parser(
        "recalculate-analyses", help="recalculate yearly analyses after weather apply"
    )
    recalculate.add_argument("--resume", action="store_true")
    recalculate.add_argument("--analysis-map", type=Path)

    rollback = subparsers.add_parser("rollback", help="restore pre-hybrid temperatures")
    rollback.add_argument("--all", action="store_true", dest="all_series")
    rollback.add_argument("--latitude", type=float)
    rollback.add_argument("--longitude", type=float)
    return parser


async def async_main(args: argparse.Namespace) -> int:
    if args.command == "plan":
        return await plan_bundle(args.bundle)
    if args.command == "apply-weather":
        return await apply_weather(args.bundle, resume=args.resume)
    if args.command == "rollback":
        return await rollback_weather(
            all_series=args.all_series,
            latitude=args.latitude,
            longitude=args.longitude,
        )
    if args.command == "recalculate-analyses":
        from app.services.yearly_weather_recalculation import recalculate_all_yearly_analyses

        return await recalculate_all_yearly_analyses(
            resume=args.resume,
            mapping_path=args.analysis_map,
        )
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(async_main(build_parser().parse_args())))
