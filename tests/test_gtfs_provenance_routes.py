from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.routers import yearly_analysis as router
from app.schemas.database import YearlyAnalysisCreate, YearlyAnalysisUpdate


@pytest.mark.asyncio
async def test_yearly_create_inherits_server_snapshot_and_feature_updates_preserve_it(monkeypatch):
    owner=NS(id=uuid4()); run_id=uuid4(); shift_id=str(uuid4())
    snapshot={'version':1,'source':'recorded','shifts':{shift_id:[{'gtfs_year':2025,'gtfs_file_date':'2025-10-02'}]}}
    run=NS(id=run_id,user_id=owner.id,input_params={'gtfs_provenance':snapshot})
    owned_run=AsyncMock(return_value=run)
    monkeypatch.setattr(router,'get_owned_optimization_run',owned_run)
    monkeypatch.setattr(router,'_require_verified_optimization',lambda *args:None)
    db=NS(add=lambda obj:None,flush=AsyncMock(),commit=AsyncMock(),refresh=AsyncMock())
    obj=await router.create_yearly_analysis(YearlyAnalysisCreate(name='Test',optimization_run_id=run_id,features={'gtfs_provenance':{'forged':True}}),db,owner)
    assert obj.features['gtfs_provenance']==snapshot
    assert obj.features['gtfs_provenance'] is not snapshot
    monkeypatch.setattr(router,'get_owned_yearly_analysis',AsyncMock(return_value=obj))
    result=await router.update_yearly_analysis(uuid4(),YearlyAnalysisUpdate(features={'label':'changed','gtfs_provenance':{'forged':True}}),db,owner)
    assert result.features['gtfs_provenance']==snapshot
    assert result.features['label']=='changed'
    owned_run.assert_awaited_once_with(db,run_id,owner.id)


@pytest.mark.asyncio
async def test_historical_origin_never_fabricates_execution_metadata(monkeypatch):
    owner=NS(id=uuid4());run_id=uuid4()
    monkeypatch.setattr(router,'get_owned_optimization_run',AsyncMock(return_value=NS(input_params={})))
    monkeypatch.setattr(router,'_require_verified_optimization',lambda *args:None)
    db=NS(add=lambda obj:None,flush=AsyncMock(),commit=AsyncMock(),refresh=AsyncMock())
    obj=await router.create_yearly_analysis(YearlyAnalysisCreate(name='Historical',optimization_run_id=run_id,features={'gtfs_provenance':{'forged':True}}),db,owner)
    assert 'gtfs_provenance' not in obj.features
