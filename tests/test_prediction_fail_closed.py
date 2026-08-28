from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pandas as pd
import pytest

from elettra_core import RAW_TRIP_FEATURE_COLUMNS
from app.services.prediction import load_shift_trip_statistics


class _Result:
    def __init__(self, values, *, scalar=False):
        self._values = values
        self._scalar = scalar

    def scalars(self):
        assert self._scalar
        return self

    def all(self):
        return self._values


class _Session:
    def __init__(self, structures, schedules):
        self._results = [_Result(structures, scalar=True)] + [
            _Result(schedule) for schedule in schedules
        ]

    async def execute(self, _statement):
        return self._results.pop(0)


def _schedule_rows(label):
    stop = SimpleNamespace(
        stop_id=f"stop-{label}", stop_name=label, stop_lat=47.0, stop_lon=8.0
    )
    return [(stop, "08:00:00", "08:01:00", 1)]


def _structures():
    return [
        SimpleNamespace(trip_id=uuid4(), sequence_number=10),
        SimpleNamespace(trip_id=uuid4(), sequence_number=20),
        SimpleNamespace(trip_id=uuid4(), sequence_number=30),
    ]


def _complete_stats():
    values = {column: 1.0 for column in RAW_TRIP_FEATURE_COLUMNS}
    values["elevation_profile_type"] = "mixed"
    return values


@pytest.mark.asyncio
async def test_shift_statistics_preserve_structure_order_and_cardinality():
    structures = _structures()
    session = _Session(structures, [_schedule_rows("a"), _schedule_rows("b"), _schedule_rows("c")])
    with (
        patch("app.services.prediction._load_trip_elevation", new=AsyncMock(return_value=pd.DataFrame({"altitude_m": [1.0]}))),
        patch("app.services.prediction.compute_global_trip_statistics_combined", return_value=_complete_stats()),
        patch("app.services.prediction.extract_stop_to_stop_statistics_for_schedule", return_value={}),
        patch("app.services.prediction.extract_route_difficulty_metrics_from_elevation", return_value={}),
    ):
        result = await load_shift_trip_statistics(session, uuid4())

    assert len(result) == len(structures)
    assert [item["trip_id"] for item in result] == [str(item.trip_id) for item in structures]
    assert [item["sequence_number"] for item in result] == [10, 20, 30]


@pytest.mark.asyncio
async def test_missing_schedule_on_second_trip_fails_whole_shift():
    structures = _structures()[:2]
    session = _Session(structures, [_schedule_rows("a"), []])
    with (
        patch("app.services.prediction._load_trip_elevation", new=AsyncMock(return_value=pd.DataFrame({"altitude_m": [1.0]}))),
        patch("app.services.prediction.compute_global_trip_statistics_combined", return_value=_complete_stats()),
        patch("app.services.prediction.extract_stop_to_stop_statistics_for_schedule", return_value={}),
        patch("app.services.prediction.extract_route_difficulty_metrics_from_elevation", return_value={}),
    ):
        with pytest.raises(ValueError, match=r"sequence 20.*no schedule"):
            await load_shift_trip_statistics(session, uuid4())


@pytest.mark.asyncio
async def test_core_error_on_second_trip_fails_whole_shift():
    structures = _structures()[:2]
    session = _Session(structures, [_schedule_rows("a"), _schedule_rows("b")])
    core = [_complete_stats(), RuntimeError("broken profile contract")]
    with (
        patch("app.services.prediction._load_trip_elevation", new=AsyncMock(return_value=pd.DataFrame({"altitude_m": [1.0]}))),
        patch("app.services.prediction.compute_global_trip_statistics_combined", side_effect=core),
        patch("app.services.prediction.extract_stop_to_stop_statistics_for_schedule", return_value={}),
        patch("app.services.prediction.extract_route_difficulty_metrics_from_elevation", return_value={}),
    ):
        with pytest.raises(ValueError, match=r"sequence 20.*broken profile contract"):
            await load_shift_trip_statistics(session, uuid4())


@pytest.mark.asyncio
async def test_missing_profile_on_second_trip_fails_whole_shift():
    structures = _structures()[:2]
    session = _Session(structures, [_schedule_rows("a"), _schedule_rows("b")])
    profiles = [pd.DataFrame({"altitude_m": [1.0]}), pd.DataFrame()]
    with (
        patch("app.services.prediction._load_trip_elevation", new=AsyncMock(side_effect=profiles)),
        patch("app.services.prediction.compute_global_trip_statistics_combined", return_value=_complete_stats()),
        patch("app.services.prediction.extract_stop_to_stop_statistics_for_schedule", return_value={}),
        patch("app.services.prediction.extract_route_difficulty_metrics_from_elevation", return_value={}),
    ):
        with pytest.raises(ValueError, match=r"sequence 20.*no elevation profile"):
            await load_shift_trip_statistics(session, uuid4())
