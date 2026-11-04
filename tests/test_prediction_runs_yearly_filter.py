from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routers import simulation


@pytest.mark.asyncio
async def test_annual_prediction_list_filters_the_owned_analysis(monkeypatch):
    owner, annual = uuid4(), uuid4()
    authorize = AsyncMock()
    monkeypatch.setattr(simulation, 'get_owned_yearly_analysis', authorize)
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: ['owned annual runs']))
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    rows = await simulation.list_prediction_runs(db, SimpleNamespace(id=owner), annual)
    authorize.assert_awaited_once_with(db, annual, owner)
    query = db.execute.call_args.args[0].compile()
    assert 'prediction_runs.yearly_analysis_id =' in str(query)
    assert annual in query.params.values()
    assert rows == ['owned annual runs']


@pytest.mark.asyncio
async def test_foreign_annual_analysis_does_not_return_predictions(monkeypatch):
    monkeypatch.setattr(simulation, 'get_owned_yearly_analysis', AsyncMock(side_effect=HTTPException(404)))
    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await simulation.list_prediction_runs(db, SimpleNamespace(id=uuid4()), uuid4())
    assert error.value.status_code == 404
    db.execute.assert_not_awaited()
