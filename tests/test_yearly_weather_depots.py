from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine

from app.services.yearly_weather_recalculation import (
    _most_used_arrival_depot, _shift_arrival_stops_query, _depot_point_for_analysis,
)


def uid(value):
    return UUID(int=value)


def depots():
    return [(SimpleNamespace(id=uid(i), stop_id=uid(i+100)),
             SimpleNamespace(stop_name=name, stop_lat=46+i/100, stop_lon=9))
            for i, name in [(1, 'Depot A'), (2, 'Depot B')]]


def test_arrival_count_wins_over_depot_name_and_selected_order():
    arrivals = [(uid(10), uid(101)), (uid(11), uid(102)), (uid(12), uid(102))]
    for candidates in [depots(), list(reversed(depots()))]:
        assert _most_used_arrival_depot(candidates, arrivals)[0].id == uid(2)


def test_ties_are_stable_and_duplicate_blocks_do_not_count_twice():
    arrivals = [(uid(10), uid(101)), (uid(11), uid(102)), (uid(11), uid(102))]
    assert _most_used_arrival_depot(list(reversed(depots())), arrivals)[0].id == uid(1)
    assert _most_used_arrival_depot(depots(), []) is None


def test_query_counts_only_last_stop_of_last_trip_once_per_block():
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE shifts_structures (shift_id TEXT, trip_id TEXT, sequence_number INTEGER)')
        db.exec_driver_sql('CREATE TABLE gtfs_stops_times (trip_id TEXT, stop_id TEXT, stop_sequence INTEGER)')
        # First block visits A several times but ends at B; second ends at A.
        for shift, trip, sequence in [(10, 20, 1), (10, 21, 2), (11, 22, 1), (12, 23, 1)]:
            db.exec_driver_sql('INSERT INTO shifts_structures VALUES (?, ?, ?)', (uid(shift).hex, uid(trip).hex, sequence))
        for trip, stop, sequence in [(20,101,1),(20,101,2),(21,101,1),(21,102,2),(22,102,1),(22,101,2),(23,102,1)]:
            db.exec_driver_sql('INSERT INTO gtfs_stops_times VALUES (?, ?, ?)', (uid(trip).hex, uid(stop).hex, sequence))
        result = dict(db.execute(_shift_arrival_stops_query([uid(10),uid(11),uid(10)])).all())
        assert result == {uid(10):uid(102), uid(11):uid(101)}


@pytest.mark.asyncio
async def test_multiple_depots_resolve_without_rejecting_the_analysis():
    results = [depots(), [(uid(10),uid(101)),(uid(11),uid(102)),(uid(12),uid(102))]]
    class Session:
        async def execute(self, statement):
            return SimpleNamespace(all=lambda: results.pop(0))
    analysis=SimpleNamespace(id=uid(1000),features={'config':{'shift_ids':[str(uid(i)) for i in (10,11,12)]}})
    lat, lon, resolution = await _depot_point_for_analysis(Session(),analysis,uid(99))
    assert (lat,lon)==(46.02,9)
    assert resolution==f'arrival-depot:{uid(2)}'
