from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pandas as pd
import pytest
from fastapi import HTTPException

from app.routers.simulation import compute_trip_statistics


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    def __init__(self, schedules, trips):
        self._schedules = iter(schedules)
        self._trips = trips

    async def execute(self, _statement):
        return _Rows(next(self._schedules))

    async def get(self, _model, trip_id):
        return self._trips.get(trip_id)


def _schedule(label):
    stop = SimpleNamespace(
        stop_id=label, stop_name=label, stop_lat=47.0, stop_lon=8.0
    )
    return [(stop, "08:00:00", "08:01:00", 1)]


@pytest.mark.asyncio
async def test_missing_first_schedule_fails_before_later_trip_can_be_paired():
    missing, valid = uuid4(), uuid4()
    db = _Db([[], _schedule("valid")], {valid: SimpleNamespace(shape_id="shape")})
    loader = AsyncMock()
    with patch("app.routers.simulation.load_trip_elevation_dataframe", new=loader):
        with pytest.raises(HTTPException) as error:
            await compute_trip_statistics(
                SimpleNamespace(trip_ids=[missing, valid]), db=db, current_user=object()
            )
    assert error.value.status_code == 422
    assert str(missing) in error.value.detail
    loader.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_second_profile_fails_without_combining_misaligned_lists():
    first, second = uuid4(), uuid4()
    trips = {
        first: SimpleNamespace(shape_id="first-shape"),
        second: SimpleNamespace(shape_id="second-shape"),
    }
    db = _Db([_schedule("first"), _schedule("second")], trips)
    profiles = [pd.DataFrame({"profile": ["first"]}), pd.DataFrame()]
    with (
        patch("app.routers.simulation.load_trip_elevation_dataframe", new=AsyncMock(side_effect=profiles)),
        patch("app.routers.simulation.combine_trip_schedules") as combine_schedules,
        patch("app.routers.simulation.combine_elevation_profiles") as combine_profiles,
    ):
        with pytest.raises(HTTPException) as error:
            await compute_trip_statistics(
                SimpleNamespace(trip_ids=[first, second]), db=db, current_user=object()
            )
    assert error.value.status_code == 404
    assert str(second) in error.value.detail
    combine_schedules.assert_not_called()
    combine_profiles.assert_not_called()


@pytest.mark.asyncio
async def test_complete_inputs_are_combined_once_in_request_order():
    first, second = uuid4(), uuid4()
    trips = {
        first: SimpleNamespace(shape_id="first-shape"),
        second: SimpleNamespace(shape_id="second-shape"),
    }
    db = _Db([_schedule("first"), _schedule("second")], trips)
    profiles = [
        pd.DataFrame({"profile": ["first"]}),
        pd.DataFrame({"profile": ["second"]}),
    ]

    def combine_schedules(items):
        assert len(items) == 2
        assert [frame.iloc[0]["stop_id"] for frame in items] == ["first", "second"]
        return pd.DataFrame({"combined": [1]})

    def combine_profiles(items):
        assert len(items) == 2
        assert [frame.iloc[0]["profile"] for frame in items] == ["first", "second"]
        return pd.DataFrame({"combined": [1]})

    with (
        patch("app.routers.simulation.load_trip_elevation_dataframe", new=AsyncMock(side_effect=profiles)),
        patch("app.routers.simulation.combine_trip_schedules", side_effect=combine_schedules),
        patch("app.routers.simulation.combine_elevation_profiles", side_effect=combine_profiles),
        patch("app.routers.simulation.compute_global_trip_statistics_combined", return_value={"ok": 1}),
        patch("app.routers.simulation.extract_stop_to_stop_statistics_for_schedule", return_value={}),
        patch("app.routers.simulation.extract_route_difficulty_metrics_from_elevation", return_value={}),
    ):
        response = await compute_trip_statistics(
            SimpleNamespace(trip_ids=[first, second]), db=db, current_user=object()
        )

    assert response.trip_ids == [first, second]
    assert response.statistics == {"ok": 1}
