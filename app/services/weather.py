"""
Service layer for meteorological data: TMY availability checks, daily-average
temperature computation, and K-means clustering.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

import numpy as np
from sqlalchemy import select, func, and_, delete, text
from sqlalchemy.ext.asyncio import AsyncSession
from sklearn.cluster import KMeans

from app.models import (
    WeatherMeasurements,
    WeatherTemperatureClusters,
    WeatherTemperatureSeries,
)
from app.services.hybrid_temperature import (
    EXPECTED_HOURS,
    HYBRID_PROVIDER,
    OPENMETEO_MODEL,
    PROCESSING_VERSION,
    HybridTemperatureSeries,
)


# ---------------------------------------------------------------------------
# TMY helpers
# ---------------------------------------------------------------------------

async def count_weather_records(
    db: AsyncSession, latitude: Decimal, longitude: Decimal
) -> int:
    result = await db.execute(
        select(func.count(WeatherMeasurements.id)).filter(
            and_(
                WeatherMeasurements.latitude == latitude,
                WeatherMeasurements.longitude == longitude,
            )
        )
    )
    return result.scalar() or 0


async def fetch_weather_records(
    db: AsyncSession, latitude: Decimal, longitude: Decimal
) -> list[WeatherMeasurements]:
    result = await db.execute(
        select(WeatherMeasurements)
        .filter(
            and_(
                WeatherMeasurements.latitude == latitude,
                WeatherMeasurements.longitude == longitude,
            )
        )
        .order_by(WeatherMeasurements.time_utc)
    )
    return list(result.scalars().all())


async def get_active_temperature_series(
    db: AsyncSession, latitude: Decimal, longitude: Decimal
) -> WeatherTemperatureSeries | None:
    result = await db.execute(
        select(WeatherTemperatureSeries)
        .where(
            and_(
                WeatherTemperatureSeries.latitude == latitude,
                WeatherTemperatureSeries.longitude == longitude,
                WeatherTemperatureSeries.status == "applied",
            )
        )
        .order_by(WeatherTemperatureSeries.generated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_clustering_configs(
    db: AsyncSession, latitude: Decimal, longitude: Decimal
) -> list[tuple[int, str, str]]:
    result = await db.execute(
        select(
            WeatherTemperatureClusters.k,
            WeatherTemperatureClusters.start_time,
            WeatherTemperatureClusters.end_time,
        )
        .where(
            and_(
                WeatherTemperatureClusters.latitude == latitude,
                WeatherTemperatureClusters.longitude == longitude,
            )
        )
        .distinct()
        .order_by(
            WeatherTemperatureClusters.k,
            WeatherTemperatureClusters.start_time,
            WeatherTemperatureClusters.end_time,
        )
    )
    return [(int(k), str(start), str(end)) for k, start, end in result.all()]


async def apply_hybrid_temperature_series(
    db: AsyncSession,
    hybrid: HybridTemperatureSeries,
    *,
    resume: bool = False,
) -> tuple[WeatherTemperatureSeries, bool]:
    """Atomically make *hybrid* active and rebuild existing cluster caches.

    Returns ``(series_row, applied)``.  With ``resume=True`` an already active
    series produced by the same algorithm/model is returned without rewriting
    temperatures.
    """

    latitude = Decimal(str(hybrid.latitude))
    longitude = Decimal(str(hybrid.longitude))
    requested_latitude = Decimal(str(round(hybrid.requested_latitude, 5)))
    requested_longitude = Decimal(str(round(hybrid.requested_longitude, 5)))
    if len(hybrid.rows) != EXPECTED_HOURS:
        raise ValueError(f"hybrid series must contain {EXPECTED_HOURS} rows")

    lock_key = f"weather-temperature:{latitude}:{longitude}"
    try:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
        active = await get_active_temperature_series(db, latitude, longitude)
        if (
            resume
            and active is not None
            and active.provider == HYBRID_PROVIDER
            and active.openmeteo_model == OPENMETEO_MODEL
            and active.processing_version == PROCESSING_VERSION
            and active.row_count == EXPECTED_HOURS
        ):
            await db.commit()
            return active, False

        records = await fetch_weather_records(db, latitude, longitude)
        if records and len(records) != EXPECTED_HOURS:
            raise ValueError(
                f"existing weather series at {latitude},{longitude} has "
                f"{len(records)} rows instead of {EXPECTED_HOURS}"
            )
        configs = await list_clustering_configs(db, latitude, longitude)

        if active is not None:
            active.status = "superseded"
            await db.flush()

        series_row = WeatherTemperatureSeries(
            latitude=latitude,
            longitude=longitude,
            requested_latitude=requested_latitude,
            requested_longitude=requested_longitude,
            provider=HYBRID_PROVIDER,
            openmeteo_model=OPENMETEO_MODEL,
            processing_version=PROCESSING_VERSION,
            status="applied",
            pvgis_months_selected=list(hybrid.months_selected),
            pvgis_metadata=dict(hybrid.pvgis_metadata),
            openmeteo_metadata=list(hybrid.openmeteo_metadata),
            row_count=EXPECTED_HOURS,
            applied_at=datetime.datetime.now(datetime.timezone.utc),
        )
        db.add(series_row)
        await db.flush()

        if records:
            by_time = {record.time_utc: record for record in records}
            if len(by_time) != EXPECTED_HOURS:
                raise ValueError("existing weather series contains duplicate timestamps")
            for row in hybrid.rows:
                record = by_time.get(row.time_utc)
                if record is None:
                    raise ValueError(f"existing weather series is missing {row.time_utc}")
                if record.temp_air_original is None:
                    record.temp_air_original = record.temp_air
                record.temp_air = row.temp_air
        else:
            new_records: list[WeatherMeasurements] = []
            for row in hybrid.rows:
                clean = sanitize_weather_values(
                    pressure=row.pvgis_fields.get("pressure"),
                    relative_humidity=row.pvgis_fields.get("relative_humidity"),
                    wind_direction=row.pvgis_fields.get("wind_direction"),
                    wind_speed=row.pvgis_fields.get("wind_speed"),
                )
                new_records.append(
                    WeatherMeasurements(
                        time_utc=row.time_utc,
                        latitude=latitude,
                        longitude=longitude,
                        temp_air=row.temp_air,
                        temp_air_original=row.pvgis_temp_air,
                        relative_humidity=clean["relative_humidity"],
                        ghi=row.pvgis_fields.get("ghi"),
                        dni=row.pvgis_fields.get("dni"),
                        dhi=row.pvgis_fields.get("dhi"),
                        ir_h=row.pvgis_fields.get("ir_h"),
                        wind_speed=clean["wind_speed"],
                        wind_direction=clean["wind_direction"],
                        pressure=clean["pressure"],
                    )
                )
            db.add_all(new_records)
            records = new_records
        await db.flush()

        for k, start_time, end_time in configs:
            daily_avgs = compute_daily_avg_temps(records, start_time, end_time)
            clusters = run_kmeans_clustering(daily_avgs, k)
            await save_clustering(
                db,
                latitude,
                longitude,
                k,
                start_time,
                end_time,
                clusters,
                temperature_series_id=series_row.id,
                commit=False,
            )
        await db.commit()
        await db.refresh(series_row)
        return series_row, True
    except Exception:
        await db.rollback()
        raise


async def rollback_hybrid_temperature_series(
    db: AsyncSession, latitude: Decimal, longitude: Decimal
) -> WeatherTemperatureSeries:
    """Restore the pre-hybrid temperature and rebuild derived clusters."""

    try:
        lock_key = f"weather-temperature:{latitude}:{longitude}"
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
        active = await get_active_temperature_series(db, latitude, longitude)
        if active is None:
            raise ValueError(f"no active hybrid series at {latitude},{longitude}")
        records = await fetch_weather_records(db, latitude, longitude)
        if len(records) != EXPECTED_HOURS:
            raise ValueError("cannot rollback an incomplete weather series")
        if any(record.temp_air_original is None for record in records):
            raise ValueError("cannot rollback: original temperature backup is incomplete")
        configs = await list_clustering_configs(db, latitude, longitude)
        for record in records:
            record.temp_air = record.temp_air_original
        active.status = "rolled_back"
        active.rolled_back_at = datetime.datetime.now(datetime.timezone.utc)
        await db.flush()
        for k, start_time, end_time in configs:
            clusters = run_kmeans_clustering(
                compute_daily_avg_temps(records, start_time, end_time), k
            )
            await save_clustering(
                db,
                latitude,
                longitude,
                k,
                start_time,
                end_time,
                clusters,
                temperature_series_id=None,
                commit=False,
            )
        await db.commit()
        return active
    except Exception:
        await db.rollback()
        raise


# ---------------------------------------------------------------------------
# PVGIS value sanitization
# ---------------------------------------------------------------------------
# External PVGIS data occasionally contains values that slightly violate our
# DB check constraints (e.g. wind_speed = -0.03).  Rather than loosening the
# constraints we clamp/normalize values before insertion.

def sanitize_weather_values(
    *,
    pressure: float | int | None,
    relative_humidity: float | None,
    wind_direction: float | None,
    wind_speed: float | None,
) -> dict:
    """Return a dict with sanitized copies of the four constrained fields.

    Rules (mirror the CHECK constraints on ``weather_measurements``):
      pressure           – must be > 0; non-positive → None
      relative_humidity  – clamped to [0, 100]
      wind_direction     – normalized to [0, 360) via modulo
      wind_speed         – must be >= 0; negative → 0
    """
    if pressure is not None and pressure <= 0:
        pressure = None

    if relative_humidity is not None:
        relative_humidity = max(0.0, min(100.0, float(relative_humidity)))

    if wind_direction is not None:
        wind_direction = float(wind_direction) % 360.0

    if wind_speed is not None and wind_speed < 0:
        wind_speed = 0.0

    return {
        "pressure": pressure,
        "relative_humidity": relative_humidity,
        "wind_direction": wind_direction,
        "wind_speed": wind_speed,
    }


# ---------------------------------------------------------------------------
# Clustering helpers
# ---------------------------------------------------------------------------

def _parse_time_str(t: str) -> tuple[int, int]:
    """Parse 'HH:MM' into (hour, minute).  Accepts '24:00' as (24, 0)."""
    parts = t.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: {t!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 24) or not (0 <= m <= 59):
        raise ValueError(f"Invalid time: {t!r}")
    if h == 24 and m != 0:
        raise ValueError("Only '24:00' is accepted for hour 24")
    return h, m


def _time_in_window(dt_utc: datetime.datetime, start_h: int, start_m: int,
                    end_h: int, end_m: int) -> bool:
    """Return True if the time-of-day part of *dt_utc* falls within
    [start, end).  When end is 24:00 we treat the upper bound as inclusive
    of 23:59."""
    t_minutes = dt_utc.hour * 60 + dt_utc.minute
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    return start_minutes <= t_minutes < end_minutes


def compute_daily_avg_temps(
    records: list[WeatherMeasurements],
    start_time: str,
    end_time: str,
) -> dict[datetime.date, float]:
    """Compute daily-average temp_air within the given time window.

    Returns a dict mapping calendar date -> average temperature.
    Days with no valid data are omitted.
    """
    start_h, start_m = _parse_time_str(start_time)
    end_h, end_m = _parse_time_str(end_time)

    day_sums: dict[datetime.date, float] = {}
    day_counts: dict[datetime.date, int] = {}

    for rec in records:
        if rec.temp_air is None:
            continue
        if not _time_in_window(rec.time_utc, start_h, start_m, end_h, end_m):
            continue
        d = rec.time_utc.date()
        day_sums[d] = day_sums.get(d, 0.0) + float(rec.temp_air)
        day_counts[d] = day_counts.get(d, 0) + 1

    return {d: day_sums[d] / day_counts[d] for d in day_sums}


def run_kmeans_clustering(
    daily_avgs: dict[datetime.date, float], k: int
) -> list[dict]:
    """Run K-means on a 1-D daily-average temperature series.

    Returns a sorted list of cluster dicts:
        [{"cluster_id": 0, "centroid_daily_avg_temp": ..., "occurrences": ...}, ...]
    """
    values = np.array(list(daily_avgs.values())).reshape(-1, 1)
    kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
    kmeans.fit(values)

    centroids = kmeans.cluster_centers_.flatten()
    labels = kmeans.labels_

    cluster_info: list[dict] = []
    for cid in range(k):
        cluster_info.append({
            "centroid_daily_avg_temp": round(float(centroids[cid]), 2),
            "occurrences": int(np.sum(labels == cid)),
        })

    cluster_info.sort(key=lambda c: c["centroid_daily_avg_temp"])
    for idx, ci in enumerate(cluster_info):
        ci["cluster_id"] = idx

    return cluster_info


# ---------------------------------------------------------------------------
# DB persistence for clustering
# ---------------------------------------------------------------------------

async def save_clustering(
    db: AsyncSession,
    latitude: Decimal,
    longitude: Decimal,
    k: int,
    start_time: str,
    end_time: str,
    clusters: list[dict],
    *,
    temperature_series_id: UUID | None = None,
    commit: bool = True,
) -> None:
    """Delete existing rows for the same config and insert fresh ones."""
    await db.execute(
        delete(WeatherTemperatureClusters).where(
            and_(
                WeatherTemperatureClusters.latitude == latitude,
                WeatherTemperatureClusters.longitude == longitude,
                WeatherTemperatureClusters.k == k,
                WeatherTemperatureClusters.start_time == start_time,
                WeatherTemperatureClusters.end_time == end_time,
            )
        )
    )

    rows = [
        WeatherTemperatureClusters(
            latitude=latitude,
            longitude=longitude,
            k=k,
            start_time=start_time,
            end_time=end_time,
            cluster_id=c["cluster_id"],
            centroid_daily_avg_temp=c["centroid_daily_avg_temp"],
            occurrences=c["occurrences"],
            temperature_series_id=temperature_series_id,
        )
        for c in clusters
    ]
    db.add_all(rows)
    if commit:
        await db.commit()


async def get_saved_clustering(
    db: AsyncSession,
    latitude: Decimal,
    longitude: Decimal,
    k: int,
    start_time: str,
    end_time: str,
) -> Optional[list[dict]]:
    """Return saved clustering rows for exact config, or None if not found."""
    result = await db.execute(
        select(WeatherTemperatureClusters).where(
            and_(
                WeatherTemperatureClusters.latitude == latitude,
                WeatherTemperatureClusters.longitude == longitude,
                WeatherTemperatureClusters.k == k,
                WeatherTemperatureClusters.start_time == start_time,
                WeatherTemperatureClusters.end_time == end_time,
            )
        ).order_by(WeatherTemperatureClusters.cluster_id)
    )
    rows = result.scalars().all()
    if not rows:
        return None
    return [
        {
            "cluster_id": r.cluster_id,
            "centroid_daily_avg_temp": float(r.centroid_daily_avg_temp),
            "occurrences": r.occurrences,
        }
        for r in rows
    ]
