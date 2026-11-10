"""Regression tests for LCA size resolution in the shift impact endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app.core.shift_distance import RecurrenceType
from app.routers import environmental
from app.routers.yearly_analysis import _resolve_lca_vehicle


IMPACT = {
    "gwp100a": {
        "direct": 0.1,
        "directNonExhaust": 0.2,
        "energyChain": 0.3,
        "maintenance": 0.4,
        "vehicle": 0.5,
        "endOfLife": 0.6,
        "infrastructure": 0.7,
    },
}


class _ScalarRows:
    def __init__(self, row):
        self.row = row

    def scalars(self):
        return self

    def first(self):
        return self.row


class _DistanceRows:
    def __init__(self, *distances_m):
        self.distances_m = distances_m

    def all(self):
        return [SimpleNamespace(max_dist=value) for value in self.distances_m]


class _EndpointDb:
    def __init__(self, lca_row, *distances_m):
        self.results = [_ScalarRows(lca_row), _DistanceRows(*distances_m)]
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)


class _PredictionRunRows:
    def __init__(self, bus_model_id):
        self.bus_model_id = bus_model_id

    def scalar_one_or_none(self):
        return self.bus_model_id


class _YearlyDb:
    def __init__(self, bus_model):
        self.bus_model = bus_model
        self.execute_calls = 0

    async def execute(self, _statement):
        self.execute_calls += 1
        return _PredictionRunRows(self.bus_model.id)

    async def get(self, _model, _identifier):
        return self.bus_model


def _compiled(statement) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


def _owned_records(monkeypatch, specs):
    shift_id = uuid4()
    bus_id = uuid4()
    bus_model_id = uuid4()
    shift = SimpleNamespace(id=shift_id, name="40501", bus_id=bus_id)
    bus = SimpleNamespace(id=bus_id, bus_model_id=bus_model_id)
    bus_model = SimpleNamespace(
        id=bus_model_id,
        name="TPL_18m",
        specs=specs,
    )
    monkeypatch.setattr(
        environmental,
        "get_owned_shift",
        AsyncMock(return_value=shift),
    )
    monkeypatch.setattr(
        environmental,
        "get_owned_bus",
        AsyncMock(return_value=bus),
    )
    monkeypatch.setattr(
        environmental,
        "get_owned_bus_model",
        AsyncMock(return_value=bus_model),
    )
    return shift, bus_model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("specs", "expected_prefix", "expected_model_size"),
    [
        ({"bus_length_m": 18}, "18m", None),
        ({"size": "13m Standard", "bus_length_m": 18}, "13m", "13m Standard"),
    ],
)
async def test_shift_impact_uses_shared_size_resolution(
    monkeypatch,
    specs,
    expected_prefix,
    expected_model_size,
):
    shift, _bus_model = _owned_records(monkeypatch, specs)
    lca_id = uuid4()
    lca_row = SimpleNamespace(
        id=lca_id,
        name=f"{expected_prefix} BEV",
        size=f"{expected_prefix} city bus",
        powertrain="BEV",
        passenger_capacity=100,
        functional_unit="pkm",
    )
    db = _EndpointDb(lca_row, 5_000, 2_500, None)
    monkeypatch.setattr(
        environmental,
        "_lca_get",
        AsyncMock(return_value=SimpleNamespace(json=lambda: IMPACT)),
    )

    result = await environmental.get_shift_yearly_impact(
        shift_id=shift.id,
        recurrence=RecurrenceType.daily,
        passengers=1,
        custom_days=None,
        db=db,
        current_user=SimpleNamespace(id=uuid4(), company_id=uuid4()),
    )

    assert expected_prefix in _compiled(db.statements[0])
    assert result.lca_vehicle.lca_vehicle_id == lca_id
    assert result.bus_model_size == expected_model_size
    assert result.daily_distance_km == 7.5
    assert result.yearly_distance_km == 7.5 * result.recurrence_days
    assert result.yearly_impact["gwp100a"].total == pytest.approx(
        sum(IMPACT["gwp100a"].values()) * result.yearly_distance_km,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "specs",
    [
        {},
        {"size": None, "bus_length_m": None},
        {"size": "12m Unsupported", "bus_length_m": True},
        {"size": "N/A", "bus_length_m": float("nan")},
        {"size": "", "bus_length_m": "not-a-length"},
    ],
)
async def test_shift_impact_returns_actionable_422_for_unusable_metadata(
    monkeypatch,
    specs,
):
    shift, _bus_model = _owned_records(monkeypatch, specs)
    db = AsyncMock()

    with pytest.raises(HTTPException) as error:
        await environmental.get_shift_yearly_impact(
            shift_id=shift.id,
            recurrence=RecurrenceType.daily,
            passengers=1,
            custom_days=None,
            db=db,
            current_user=SimpleNamespace(id=uuid4(), company_id=uuid4()),
        )

    assert error.value.status_code == 422
    assert "specs.size=" in error.value.detail
    assert "specs.bus_length_m=" in error.value.detail
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_shift_impact_preserves_404_when_the_resolved_catalog_row_is_absent(
    monkeypatch,
):
    shift, _bus_model = _owned_records(monkeypatch, {"bus_length_m": 18})
    db = _EndpointDb(None)

    with pytest.raises(HTTPException) as error:
        await environmental.get_shift_yearly_impact(
            shift_id=shift.id,
            recurrence=RecurrenceType.daily,
            passengers=1,
            custom_days=None,
            db=db,
            current_user=SimpleNamespace(id=uuid4(), company_id=uuid4()),
        )

    assert error.value.status_code == 404
    assert "18m" in error.value.detail


@pytest.mark.asyncio
async def test_yearly_lca_resolution_fails_closed_for_malformed_legacy_length():
    bus_model = SimpleNamespace(
        id=uuid4(),
        name="Malformed legacy model",
        specs={"bus_length_m": "not-a-length"},
    )
    db = _YearlyDb(bus_model)

    result = await _resolve_lca_vehicle(db, uuid4())

    assert result is None
    assert db.execute_calls == 1
