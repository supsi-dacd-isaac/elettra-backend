from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.routers import yearly_analysis as router
from app.schemas.database import YearlyAnalysisCreate, YearlyAnalysisUpdate
from fastapi import HTTPException


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


@pytest.mark.asyncio
async def test_new_yearly_analysis_rejects_mixed_source_but_existing_history_can_be_edited(monkeypatch):
    owner=NS(id=uuid4(),company_id=uuid4());run_id=uuid4()
    snapshot={'version':1,'source':'recorded','shifts':{'a':[{'gtfs_year':2026,'gtfs_file_date':'2025-09-27'}],'b':[{'gtfs_year':2027,'gtfs_file_date':'2026-11-04'}]}}
    run=NS(id=run_id,input_params={'gtfs_provenance':snapshot})
    monkeypatch.setattr(router,'get_owned_optimization_run',AsyncMock(return_value=run))
    monkeypatch.setattr(router,'_require_verified_optimization',lambda *args:None)
    db=NS(add=lambda obj:None,flush=AsyncMock(),commit=AsyncMock(),refresh=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await router.create_yearly_analysis(YearlyAnalysisCreate(name='Mixed',optimization_run_id=run_id),db,owner)
    assert exc.value.status_code==422
    db.commit.assert_not_awaited()
    historical=NS(optimization_run_id=run_id,features={'gtfs_provenance':snapshot},weather_temperature_series_id=None)
    monkeypatch.setattr(router,'get_owned_yearly_analysis',AsyncMock(return_value=historical))
    updated=await router.update_yearly_analysis(uuid4(),YearlyAnalysisUpdate(name='Historical renamed'),db,owner)
    assert updated.features['gtfs_provenance']==snapshot


@pytest.mark.asyncio
async def test_new_feasibility_rejects_different_versions_before_profiles_or_persistence(monkeypatch):
    from fastapi import BackgroundTasks
    from app.routers import simulation
    from app.schemas.requests import OptimizationRequest
    from app.services import gtfs_datasets
    ids=[uuid4(),uuid4()];owner=NS(id=uuid4(),company_id=uuid4())
    monkeypatch.setattr(simulation,'get_owned_shift',AsyncMock(return_value=NS(bus_id=None)))
    monkeypatch.setattr(gtfs_datasets,'capture_provenance',AsyncMock(return_value={'shifts':{str(ids[0]):[{'gtfs_year':2027,'gtfs_file_date':'2026-08-22'}],str(ids[1]):[{'gtfs_year':2027,'gtfs_file_date':'2026-11-04'}]}}))
    profiles=AsyncMock();monkeypatch.setattr(simulation,'ensure_shift_profiles_ready',profiles)
    db=NS(commit=AsyncMock())
    request=OptimizationRequest(name='Mixed',mode='battery_only',shift_ids=ids,prediction_params={'external_temp_celsius':15})
    with pytest.raises(HTTPException) as exc:
        await simulation.create_optimization_run(BackgroundTasks(),request,db,owner)
    assert exc.value.status_code==422
    profiles.assert_not_awaited();db.commit.assert_not_awaited()
