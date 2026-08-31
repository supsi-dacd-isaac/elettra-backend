from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from app.services.hybrid_temperature import (
    HybridTemperatureError,
    canonical_coordinates,
    fetch_hybrid_temperature_series,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _pvgis_payload() -> dict:
    selected_years = {month: (2020 if month == 2 else 2019) for month in range(1, 13)}
    rows = []
    cursor = datetime(2030, 1, 1)
    end = datetime(2031, 1, 1)
    while cursor < end:
        source = cursor.replace(year=selected_years[cursor.month])
        rows.append(
            {
                "time(UTC)": source.strftime("%Y%m%d:%H%M"),
                "T2m": 99.0,
                "RH": 50,
                "G(h)": 0,
                "Gb(n)": 0,
                "Gd(h)": 0,
                "IR(h)": 300,
                "WS10m": 1,
                "WD10m": 180,
                "SP": 95000,
            }
        )
        cursor += timedelta(hours=1)
    return {
        "inputs": {"location": {"elevation": 900}},
        "outputs": {
            "tmy_hourly": rows,
            "months_selected": [
                {"month": month, "year": selected_years[month]}
                for month in range(1, 13)
            ],
        },
    }


def _openmeteo_payload(
    start_date: str,
    end_date: str,
    *,
    omit_first: bool = False,
    non_finite_first: bool = False,
) -> dict:
    cursor = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date) + timedelta(days=1)
    times = []
    temperatures = []
    while cursor < end:
        times.append(cursor.strftime("%Y-%m-%dT%H:%M"))
        temperatures.append(cursor.month + cursor.hour / 100.0)
        cursor += timedelta(hours=1)
    if omit_first:
        times.pop(0)
        temperatures.pop(0)
    if non_finite_first:
        temperatures[0] = "nan"
    return {
        "latitude": 46.8,
        "longitude": 7.1,
        "elevation": 638.0,
        "generationtime_ms": 1.0,
        "hourly": {"time": times, "temperature_2m": temperatures},
    }


@pytest.mark.anyio
async def test_hybrid_uses_direct_openmeteo_samples_and_dem():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "re.jrc.ec.europa.eu" in request.url.host:
            return httpx.Response(200, json=_pvgis_payload())
        params = parse_qs(request.url.query.decode())
        assert params["hourly"] == ["temperature_2m"]
        assert params["timezone"] == ["GMT"]
        assert params["models"] == ["best_match"]
        assert params["cell_selection"] == ["land"]
        assert "elevation" not in params
        return httpx.Response(
            200,
            json=_openmeteo_payload(params["start_date"][0], params["end_date"][0]),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        series = await fetch_hybrid_temperature_series(
            46.810561,
            7.153451,
            coerce_year=2030,
            client=client,
        )

    assert (series.latitude, series.longitude) == (46.81056, 7.15345)
    assert len(series.rows) == 8760
    assert len(requests) == 13
    assert series.rows[0].temp_air == 1.0
    assert series.rows[1].temp_air == 1.01
    assert series.rows[0].pvgis_temp_air == 99.0
    february = next(item for item in series.months_selected if item["month"] == 2)
    assert february["year"] == 2020
    assert series.rows[-1].time_utc == datetime.fromisoformat(
        "2030-12-31T23:00:00+00:00"
    )


@pytest.mark.anyio
async def test_hybrid_rejects_missing_selected_timestamp():
    async def handler(request: httpx.Request) -> httpx.Response:
        if "re.jrc.ec.europa.eu" in request.url.host:
            return httpx.Response(200, json=_pvgis_payload())
        params = parse_qs(request.url.query.decode())
        return httpx.Response(
            200,
            json=_openmeteo_payload(
                params["start_date"][0],
                params["end_date"][0],
                omit_first=params["start_date"][0].endswith("-03-01"),
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HybridTemperatureError, match="missing PVGIS timestamp"):
            await fetch_hybrid_temperature_series(
                46.81, 7.15, coerce_year=2030, client=client
            )


@pytest.mark.anyio
async def test_hybrid_retries_retryable_http_response():
    pvgis_attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal pvgis_attempts
        if "re.jrc.ec.europa.eu" in request.url.host:
            pvgis_attempts += 1
            if pvgis_attempts == 1:
                return httpx.Response(503, headers={"Retry-After": "0"})
            return httpx.Response(200, json=_pvgis_payload())
        params = parse_qs(request.url.query.decode())
        return httpx.Response(
            200,
            json=_openmeteo_payload(
                params["start_date"][0], params["end_date"][0]
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        series = await fetch_hybrid_temperature_series(
            46.81, 7.15, coerce_year=2030, client=client
        )
    assert pvgis_attempts == 2
    assert len(series.rows) == 8760


@pytest.mark.anyio
async def test_hybrid_does_not_retry_non_retryable_http_response():
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"reason": "bad coordinates"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HybridTemperatureError, match="request failed"):
            await fetch_hybrid_temperature_series(
                46.81, 7.15, coerce_year=2030, client=client
            )
    assert attempts == 1


@pytest.mark.anyio
async def test_hybrid_rejects_non_finite_openmeteo_temperature():
    async def handler(request: httpx.Request) -> httpx.Response:
        if "re.jrc.ec.europa.eu" in request.url.host:
            return httpx.Response(200, json=_pvgis_payload())
        params = parse_qs(request.url.query.decode())
        return httpx.Response(
            200,
            json=_openmeteo_payload(
                params["start_date"][0],
                params["end_date"][0],
                non_finite_first=params["start_date"][0].endswith("-04-01"),
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HybridTemperatureError, match="invalid temperature"):
            await fetch_hybrid_temperature_series(
                46.81, 7.15, coerce_year=2030, client=client
            )


@pytest.mark.anyio
async def test_hybrid_rejects_incomplete_pvgis_series():
    payload = _pvgis_payload()
    payload["outputs"]["tmy_hourly"].pop()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(HybridTemperatureError, match="exactly 8760"):
            await fetch_hybrid_temperature_series(
                46.81, 7.15, coerce_year=2030, client=client
            )


def test_canonical_coordinates_use_five_decimals_and_validate_ranges():
    assert canonical_coordinates(46.810561, 7.153451) == (46.81056, 7.15345)
    assert canonical_coordinates(46.810564, 7.153454) != canonical_coordinates(
        46.810576, 7.153466
    )
    with pytest.raises(ValueError):
        canonical_coordinates(91, 7)
