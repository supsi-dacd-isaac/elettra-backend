"""PVGIS-selected, Open-Meteo-corrected hourly temperature series.

PVGIS is used only to select the twelve typical source months and to retain
the legacy meteorological fields exposed by the API.  ``temperature_2m`` is
read from Open-Meteo at the exact PVGIS source timestamps.  This module does
not implement EPW interval averaging or cross-month blending: Elettra stores
an hourly temperature sample, not an EPW file.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
import math
from typing import Any, Mapping

import httpx


PVGIS_TMY_URL = "https://re.jrc.ec.europa.eu/api/v5_3/tmy"
OPENMETEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HYBRID_PROVIDER = "pvgis-openmeteo"
OPENMETEO_MODEL = "best_match"
PROCESSING_VERSION = "elettra-hybrid-temperature-v1"
EXPECTED_HOURS = 8_760
MAX_OPENMETEO_CONCURRENCY = 4
MAX_ATTEMPTS = 3


class HybridTemperatureError(RuntimeError):
    """Raised when an upstream response cannot produce a valid TMY series."""


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    fallback = float(2 ** (attempt - 1))
    if not retry_after:
        return fallback
    try:
        return max(float(retry_after), 0.0)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0.0)
        except (TypeError, ValueError, OverflowError):
            return fallback


@dataclass(frozen=True)
class HybridTemperatureRow:
    time_utc: datetime
    source_time_utc: datetime
    temp_air: float
    pvgis_temp_air: float | None
    pvgis_fields: Mapping[str, float | int | None]


@dataclass(frozen=True)
class HybridTemperatureSeries:
    requested_latitude: float
    requested_longitude: float
    latitude: float
    longitude: float
    coerce_year: int
    rows: tuple[HybridTemperatureRow, ...]
    months_selected: tuple[Mapping[str, int], ...]
    pvgis_metadata: Mapping[str, Any]
    openmeteo_metadata: tuple[Mapping[str, Any], ...]


def canonical_coordinates(latitude: float, longitude: float) -> tuple[float, float]:
    """Return stable cache coordinates at roughly metre precision."""

    lat = round(float(latitude), 5)
    lon = round(float(longitude), 5)
    if not -90 <= lat <= 90:
        raise ValueError("latitude must be between -90 and 90")
    if not -180 <= lon <= 180:
        raise ValueError("longitude must be between -180 and 180")
    return (0.0 if lat == 0 else lat, 0.0 if lon == 0 else lon)


def coordinate_decimals(latitude: float, longitude: float) -> tuple[Decimal, Decimal]:
    lat, lon = canonical_coordinates(latitude, longitude)
    return Decimal(str(lat)), Decimal(str(lon))


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pvgis_value(row: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        if name in row:
            return _float_or_none(row[name])
    return None


def _extract_pvgis_fields(row: Mapping[str, Any]) -> dict[str, float | int | None]:
    pressure = _pvgis_value(row, "SP", "pressure")
    return {
        "relative_humidity": _pvgis_value(row, "RH", "relative_humidity"),
        "ghi": _pvgis_value(row, "G(h)", "ghi"),
        "dni": _pvgis_value(row, "Gb(n)", "dni"),
        "dhi": _pvgis_value(row, "Gd(h)", "dhi"),
        "ir_h": _pvgis_value(row, "IR(h)", "ir_h"),
        "wind_speed": _pvgis_value(row, "WS10m", "wind_speed"),
        "wind_direction": _pvgis_value(row, "WD10m", "wind_direction"),
        "pressure": int(round(pressure)) if pressure is not None else None,
    }


async def _get_json_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Mapping[str, Any],
    label: str,
) -> Mapping[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.get(url, params=params)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    response.raise_for_status()
                delay = _retry_delay(response.headers.get("Retry-After"), attempt)
                await asyncio.sleep(min(max(delay, 0.0), 10.0))
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("error"):
                reason = payload.get("reason") if isinstance(payload, dict) else None
                raise HybridTemperatureError(
                    f"{label} returned an invalid response: {reason or 'unknown error'}"
                )
            return payload
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_error = exc
            retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                exc.response.status_code == 429 or exc.response.status_code >= 500
            )
            if not retryable or attempt == MAX_ATTEMPTS:
                break
            await asyncio.sleep(2 ** (attempt - 1))
        except ValueError as exc:
            raise HybridTemperatureError(f"{label} returned invalid JSON") from exc
    raise HybridTemperatureError(f"{label} request failed: {last_error}") from last_error


def _parse_pvgis_payload(
    payload: Mapping[str, Any],
) -> tuple[list[tuple[datetime, Mapping[str, Any]]], dict[int, int], Mapping[str, Any]]:
    try:
        outputs = payload["outputs"]
        hourly = outputs["tmy_hourly"]
        selected = outputs["months_selected"]
    except (KeyError, TypeError) as exc:
        raise HybridTemperatureError("PVGIS response is missing TMY outputs") from exc

    if not isinstance(hourly, list) or len(hourly) != EXPECTED_HOURS:
        raise HybridTemperatureError(
            f"PVGIS TMY must contain exactly {EXPECTED_HOURS} hourly rows"
        )
    if not isinstance(selected, list) or len(selected) != 12:
        raise HybridTemperatureError("PVGIS must select exactly twelve source months")

    selected_years: dict[int, int] = {}
    for item in selected:
        try:
            month = int(item["month"])
            year = int(item["year"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HybridTemperatureError("PVGIS selected-month metadata is invalid") from exc
        if month in selected_years or not 1 <= month <= 12:
            raise HybridTemperatureError("PVGIS selected months are duplicated or invalid")
        selected_years[month] = year
    if set(selected_years) != set(range(1, 13)):
        raise HybridTemperatureError("PVGIS did not return one source year per month")

    parsed: list[tuple[datetime, Mapping[str, Any]]] = []
    seen: set[datetime] = set()
    for index, row in enumerate(hourly):
        if not isinstance(row, dict):
            raise HybridTemperatureError(f"PVGIS hourly row {index} is invalid")
        raw_time = row.get("time(UTC)") or row.get("time")
        try:
            source_time = datetime.strptime(str(raw_time), "%Y%m%d:%H%M")
        except ValueError as exc:
            raise HybridTemperatureError(
                f"PVGIS timestamp at row {index} is invalid"
            ) from exc
        if source_time in seen:
            raise HybridTemperatureError(f"PVGIS duplicated timestamp {source_time}")
        if selected_years.get(source_time.month) != source_time.year:
            raise HybridTemperatureError(
                f"PVGIS timestamp {source_time} does not match selected source year"
            )
        seen.add(source_time)
        parsed.append((source_time, row))

    return parsed, selected_years, payload.get("inputs", {})


async def _fetch_openmeteo_month(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    *,
    latitude: float,
    longitude: float,
    month: int,
    year: int,
    model: str,
) -> tuple[int, dict[datetime, float], Mapping[str, Any]]:
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    month_start = datetime(year, month, 1)
    end_date = (next_month - timedelta(days=1)).date()
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": month_start.date().isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": "temperature_2m",
        "timezone": "GMT",
        "models": model,
        "cell_selection": "land",
    }
    async with semaphore:
        payload = await _get_json_with_retry(
            client,
            OPENMETEO_ARCHIVE_URL,
            params=params,
            label=f"Open-Meteo {year}-{month:02d}",
        )

    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise HybridTemperatureError("Open-Meteo response is missing hourly data")
    times = hourly.get("time")
    temperatures = hourly.get("temperature_2m")
    if not isinstance(times, list) or not isinstance(temperatures, list):
        raise HybridTemperatureError("Open-Meteo response is missing temperature_2m")
    if len(times) != len(temperatures):
        raise HybridTemperatureError("Open-Meteo time and temperature lengths differ")

    values: dict[datetime, float] = {}
    for index, (raw_time, raw_value) in enumerate(zip(times, temperatures, strict=True)):
        try:
            timestamp = datetime.fromisoformat(str(raw_time))
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise HybridTemperatureError(
                f"Open-Meteo returned invalid data at index {index}"
            ) from exc
        if timestamp.tzinfo is not None:
            timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
        if timestamp in values or not math.isfinite(value) or not -90 <= value <= 70:
            raise HybridTemperatureError(
                f"Open-Meteo returned invalid temperature at {timestamp}"
            )
        values[timestamp] = value

    metadata = {
        "target_month": month,
        "source_year": year,
        "grid_latitude": payload.get("latitude"),
        "grid_longitude": payload.get("longitude"),
        "returned_elevation_m": payload.get("elevation"),
        "generation_time_ms": payload.get("generationtime_ms"),
    }
    return month, values, metadata


async def fetch_hybrid_temperature_series(
    latitude: float,
    longitude: float,
    *,
    coerce_year: int,
    model: str = OPENMETEO_MODEL,
    client: httpx.AsyncClient | None = None,
) -> HybridTemperatureSeries:
    """Fetch and validate a complete 8,760-row hybrid temperature TMY."""

    requested_latitude = float(latitude)
    requested_longitude = float(longitude)
    canonical_latitude, canonical_longitude = canonical_coordinates(latitude, longitude)
    if (datetime(coerce_year + 1, 1, 1) - datetime(coerce_year, 1, 1)).days != 365:
        raise HybridTemperatureError("coerce_year must be a non-leap year")

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0))
    try:
        pvgis_payload = await _get_json_with_retry(
            client,
            PVGIS_TMY_URL,
            params={
                "lat": canonical_latitude,
                "lon": canonical_longitude,
                "outputformat": "json",
            },
            label="PVGIS TMY",
        )
        pvgis_rows, selected_years, pvgis_metadata = _parse_pvgis_payload(
            pvgis_payload
        )
        semaphore = asyncio.Semaphore(MAX_OPENMETEO_CONCURRENCY)
        fetched = await asyncio.gather(
            *(
                _fetch_openmeteo_month(
                    client,
                    semaphore,
                    latitude=canonical_latitude,
                    longitude=canonical_longitude,
                    month=month,
                    year=selected_years[month],
                    model=model,
                )
                for month in range(1, 13)
            )
        )
    finally:
        if owns_client:
            await client.aclose()

    openmeteo_values = {month: values for month, values, _metadata in fetched}
    openmeteo_metadata = tuple(metadata for _month, _values, metadata in sorted(fetched))
    rows: list[HybridTemperatureRow] = []
    target_seen: set[datetime] = set()
    for source_time, pvgis_row in pvgis_rows:
        try:
            temperature = openmeteo_values[source_time.month][source_time]
        except KeyError as exc:
            raise HybridTemperatureError(
                f"Open-Meteo is missing PVGIS timestamp {source_time.isoformat()}"
            ) from exc
        try:
            target_time = datetime(
                coerce_year,
                source_time.month,
                source_time.day,
                source_time.hour,
                source_time.minute,
                tzinfo=timezone.utc,
            )
        except ValueError as exc:
            raise HybridTemperatureError(
                f"PVGIS timestamp cannot be mapped to {coerce_year}: {source_time}"
            ) from exc
        if target_time in target_seen:
            raise HybridTemperatureError(f"Duplicate synthetic timestamp {target_time}")
        target_seen.add(target_time)
        rows.append(
            HybridTemperatureRow(
                time_utc=target_time,
                source_time_utc=source_time.replace(tzinfo=timezone.utc),
                temp_air=temperature,
                pvgis_temp_air=_pvgis_value(pvgis_row, "T2m", "temp_air"),
                pvgis_fields=_extract_pvgis_fields(pvgis_row),
            )
        )

    rows.sort(key=lambda item: item.time_utc)
    expected_start = datetime(coerce_year, 1, 1, tzinfo=timezone.utc)
    if len(rows) != EXPECTED_HOURS or rows[0].time_utc != expected_start:
        raise HybridTemperatureError("Hybrid TMY does not cover the complete target year")
    for index, row in enumerate(rows):
        expected = expected_start + timedelta(hours=index)
        if row.time_utc != expected:
            raise HybridTemperatureError(
                f"Hybrid TMY is discontinuous at index {index}: {row.time_utc}"
            )

    return HybridTemperatureSeries(
        requested_latitude=requested_latitude,
        requested_longitude=requested_longitude,
        latitude=canonical_latitude,
        longitude=canonical_longitude,
        coerce_year=coerce_year,
        rows=tuple(rows),
        months_selected=tuple(
            {"month": month, "year": selected_years[month]} for month in range(1, 13)
        ),
        pvgis_metadata=pvgis_metadata,
        openmeteo_metadata=openmeteo_metadata,
    )
