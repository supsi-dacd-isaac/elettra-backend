from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math

import pytest
from sqlalchemy import delete, text

from app.database import AsyncSessionLocal
from app.models import (
    WeatherMeasurements,
    WeatherTemperatureClusters,
    WeatherTemperatureSeries,
)
from app.services.hybrid_temperature import HybridTemperatureRow, HybridTemperatureSeries
from app.services.weather import (
    apply_hybrid_temperature_series,
    compute_daily_avg_temps,
    fetch_weather_records,
    get_active_temperature_series,
    rollback_hybrid_temperature_series,
    run_kmeans_clustering,
    save_clustering,
)


LATITUDE = Decimal("-54.32109")
LONGITUDE = Decimal("-123.45678")


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _cleanup() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(WeatherTemperatureClusters).where(
                WeatherTemperatureClusters.latitude == LATITUDE,
                WeatherTemperatureClusters.longitude == LONGITUDE,
            )
        )
        await db.execute(
            delete(WeatherMeasurements).where(
                WeatherMeasurements.latitude == LATITUDE,
                WeatherMeasurements.longitude == LONGITUDE,
            )
        )
        await db.execute(
            delete(WeatherTemperatureSeries).where(
                WeatherTemperatureSeries.latitude == LATITUDE,
                WeatherTemperatureSeries.longitude == LONGITUDE,
            )
        )
        await db.commit()


@pytest.mark.anyio
async def test_apply_resume_cluster_provenance_and_rollback():
    await _cleanup()
    start = datetime(2030, 1, 1, tzinfo=timezone.utc)
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO weather_measurements
                        (time_utc, latitude, longitude, temp_air)
                    SELECT
                        CAST(:start_time AS timestamptz)
                            + make_interval(hours => hour_index::integer),
                        :latitude,
                        :longitude,
                        8.0 + 10.0 * sin(2.0 * pi() * hour_index / 8760.0)
                    FROM generate_series(0, 8759) AS hour_index
                    """
                ),
                {
                    "start_time": start,
                    "latitude": LATITUDE,
                    "longitude": LONGITUDE,
                },
            )
            await db.commit()
            records = await fetch_weather_records(db, LATITUDE, LONGITUDE)
            for k in (4, 8):
                clusters = run_kmeans_clustering(
                    compute_daily_avg_temps(records, "05:00", "24:00"), k
                )
                await save_clustering(
                    db,
                    LATITUDE,
                    LONGITUDE,
                    k,
                    "05:00",
                    "24:00",
                    clusters,
                )

        rows = tuple(
            HybridTemperatureRow(
                time_utc=start + timedelta(hours=index),
                source_time_utc=start + timedelta(hours=index),
                temp_air=12.0 + math.sin(index / 100.0),
                pvgis_temp_air=8.0,
                pvgis_fields={},
            )
            for index in range(8760)
        )
        hybrid = HybridTemperatureSeries(
            requested_latitude=float(LATITUDE),
            requested_longitude=float(LONGITUDE),
            latitude=float(LATITUDE),
            longitude=float(LONGITUDE),
            coerce_year=2030,
            rows=rows,
            months_selected=tuple(
                {"month": month, "year": 2019} for month in range(1, 13)
            ),
            pvgis_metadata={},
            openmeteo_metadata=tuple(
                {"target_month": month, "returned_elevation_m": 100.0}
                for month in range(1, 13)
            ),
        )

        async with AsyncSessionLocal() as db:
            series, applied = await apply_hybrid_temperature_series(db, hybrid)
            assert applied is True
            series_id = series.id

        async with AsyncSessionLocal() as db:
            records = await fetch_weather_records(db, LATITUDE, LONGITUDE)
            assert len(records) == 8760
            assert all(record.temp_air_original is not None for record in records)
            assert records[0].temp_air == pytest.approx(12.0)
            cluster_rows = (
                await db.execute(
                    text(
                        """
                        SELECT k, count(*), count(DISTINCT temperature_series_id)
                        FROM weather_temperature_clusters
                        WHERE latitude=:latitude AND longitude=:longitude
                        GROUP BY k ORDER BY k
                        """
                    ),
                    {"latitude": LATITUDE, "longitude": LONGITUDE},
                )
            ).all()
            assert cluster_rows == [(4, 4, 1), (8, 8, 1)]
            active = await get_active_temperature_series(db, LATITUDE, LONGITUDE)
            assert active is not None and active.id == series_id

        async with AsyncSessionLocal() as db:
            resumed, applied = await apply_hybrid_temperature_series(
                db, hybrid, resume=True
            )
            assert resumed.id == series_id
            assert applied is False

        revised = replace(
            hybrid,
            rows=tuple(
                replace(row, temp_air=row.temp_air + 1.0) for row in hybrid.rows
            ),
        )
        async with AsyncSessionLocal() as db:
            superseding, applied = await apply_hybrid_temperature_series(db, revised)
            assert applied is True
            assert superseding.id != series_id

        async with AsyncSessionLocal() as db:
            records = await fetch_weather_records(db, LATITUDE, LONGITUDE)
            assert records[0].temp_air == pytest.approx(13.0)
            assert records[0].temp_air_original == pytest.approx(8.0)
            active = await get_active_temperature_series(db, LATITUDE, LONGITUDE)
            assert active is not None and active.id == superseding.id

        async with AsyncSessionLocal() as db:
            await rollback_hybrid_temperature_series(db, LATITUDE, LONGITUDE)
        async with AsyncSessionLocal() as db:
            records = await fetch_weather_records(db, LATITUDE, LONGITUDE)
            assert records[0].temp_air == records[0].temp_air_original
            assert await get_active_temperature_series(db, LATITUDE, LONGITUDE) is None
    finally:
        await _cleanup()
