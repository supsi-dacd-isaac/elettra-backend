"""
Service layer for meteorological data: TMY availability checks, daily-average
temperature computation, and K-means clustering.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

import numpy as np
from sqlalchemy import select, func, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sklearn.cluster import KMeans

from app.models import WeatherMeasurements, WeatherTemperatureClusters


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
        )
        for c in clusters
    ]
    db.add_all(rows)
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
