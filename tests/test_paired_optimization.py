from types import SimpleNamespace as NS
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
import pytest
import pytest_asyncio
from pydantic import ValidationError

from app.models import OptimizationRuns
from app.routers import simulation as router
from app.schemas.requests import OptimizationRequest


@pytest_asyncio.fixture
async def paired_db_session():
    import os
    from sqlalchemy.engine import make_url
    from app.database import AsyncSessionLocal, get_database_url
    url=make_url(get_database_url())
    isolated=(url.database.startswith('elettra_test_') and
              os.environ.get('ELETTRA_EPHEMERAL_TEST_DATABASE')==url.database)
    if not isolated and os.environ.get('GITHUB_ACTIONS')!='true':
        pytest.skip('Paired integration test requires an explicitly isolated database')
    async with AsyncSessionLocal() as db:
        yield db


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['battery_only','charging_only','joint'])
@pytest.mark.parametrize('basis',[None,'mean','aggregate_q95'])
@pytest.mark.parametrize('explicit_pair',[False,True])
async def test_paired_creation_is_one_transaction_and_one_background_task(monkeypatch,mode,basis,explicit_pair):
    owner,sid,pid,model=uuid4(),uuid4(),uuid4(),uuid4()
    pred=NS(id=pid,user_id=owner,shift_id=sid,bus_model_id=model,status='completed',
        model_name='test',prediction_stack='legacy',auxiliary_estimator_release=None,
        contextual_parameters={'num_battery_packs':3})
    checked=[]
    async def owned_shift(db,key,user,company):
        assert (key,user)==(sid,owner);checked.append('shift')
    async def owned_prediction(db,key,user):
        assert (key,user)==(pid,owner);checked.append('prediction');return pred
    async def profiles(*args): checked.append('profiles')
    monkeypatch.setattr(router,'get_owned_shift',owned_shift)
    monkeypatch.setattr(router,'get_owned_prediction_run',owned_prediction)
    monkeypatch.setattr(router,'ensure_shift_profiles_ready',profiles)
    monkeypatch.setattr(router,'resolve_prediction_selection',lambda **kw:
        NS(stack=NS(value='legacy'),model_release='test',auxiliary_estimator='test'))
    class DB:
        rows=[]
        commits=0
        def add(self,row): self.rows.append(row)
        async def flush(self):
            for row in self.rows:
                if row.id is None:row.id=uuid4()
        async def commit(self):
            self.commits+=1
            assert len(self.rows)==2
            assert all(set(r.input_params['basis_run_ids'])=={'mean','aggregate_q95'} for r in self.rows)
    db=DB();tasks=BackgroundTasks()
    request=OptimizationRequest(name='Pair',mode=mode,shift_ids=[sid],prediction_run_ids=[pid],
        reference_prediction_run_ids={sid:pid},battery_cost_per_kwh=300,
        consumption_basis=basis,**({'evaluate_both_bases':True} if explicit_pair else {}))
    response=await router.create_optimization_run(tasks,request,db,NS(id=owner,company_id=uuid4()))
    mean,q95=db.rows
    assert response.optimization_run_id==mean.id
    assert response.basis_run_ids=={'mean':mean.id,'aggregate_q95':q95.id}
    assert checked==['shift','prediction','profiles']
    assert db.commits==1 and len(tasks.tasks)==1
    assert tasks.tasks[0].args==(mean.id,q95.id)
    assert mean.input_params['consumption_basis']=='mean'
    assert q95.input_params['consumption_basis']=='aggregate_q95'
    assert q95.input_params['paired_basis_role']=='comparison'
    for row in db.rows:
        assert row.user_id==owner and row.mode==mode
        assert row.input_params['reference_prediction_run_ids']=={str(sid):str(pid)}


def test_explicit_single_basis_and_historical_inputs_remain_supported():
    params=dict(name='Historical',mode='battery_only',shift_ids=[uuid4()],prediction_run_ids=[uuid4()])
    assert not OptimizationRequest(**params,evaluate_both_bases=False).evaluate_both_bases
    assert not OptimizationRequest(**params,quantile_consumption='0.95').evaluate_both_bases
    assert not OptimizationRequest(**params,lock_entire_dwell=False).evaluate_both_bases


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['pending','running','completed','failed'])
async def test_paired_lookup_checks_both_owners_and_never_starts_work(monkeypatch,status):
    first,second,owner=uuid4(),uuid4(),uuid4()
    source=NS(id=first,input_params={'basis_run_ids':{'mean':str(first),'aggregate_q95':str(second)}})
    target=NS(id=second,status=status)
    accesses=[]
    async def owned(db,key,user):
        assert user==owner;accesses.append(key)
        return source if key==first else target
    monkeypatch.setattr(router,'get_owned_optimization_run',owned)
    result=await router.find_basis_comparison(first,'aggregate_q95',object(),NS(id=owner))
    assert result=={'optimization_run_id':str(second),'status':status}
    assert accesses==[first,second]
    async def foreign(db,key,user):
        if key==second:raise HTTPException(404)
        return source
    monkeypatch.setattr(router,'get_owned_optimization_run',foreign)
    with pytest.raises(HTTPException) as exc:
        await router.find_basis_comparison(first,'aggregate_q95',object(),NS(id=owner))
    assert exc.value.status_code==404


@pytest.mark.asyncio
async def test_background_dispatches_both_processes_together(monkeypatch):
    from app.services import optimization as service
    first, second = uuid4(), uuid4()
    calls = []
    async def workers(*ids):
        calls.append(ids)
    monkeypatch.setattr(service, 'run_workers', workers)
    await service.run_optimization_background(first, second)
    await service.run_optimization_background(first)
    assert calls == [(first, second), (first,)]


def test_paired_request_requires_full_dwell_and_rejects_legacy_quantiles():
    kwargs=dict(name='Pair',mode='battery_only',shift_ids=[uuid4()],prediction_run_ids=[uuid4()],
                evaluate_both_bases=True)
    for invalid in [{'lock_entire_dwell':False},{'quantile_consumption':'0.95'}]:
        with pytest.raises(ValidationError):OptimizationRequest(**kwargs,**invalid)


@pytest.mark.asyncio
async def test_deleting_a_pair_deletes_both_but_refuses_running_work(monkeypatch):
    owner=uuid4();ids=[uuid4(),uuid4()]
    rows=[NS(id=i,status='completed',input_params={'basis_run_ids':dict(zip(['mean','aggregate_q95'],map(str,ids)))}) for i in ids]
    class DB:
        deleted=[]
        async def execute(self,statement):
            if statement.is_select and 'optimization_runs' in str(statement):
                return NS(scalar_one_or_none=lambda:rows[0])
            return NS(scalars=lambda:NS(all=lambda:[]))
        async def delete(self,row):self.deleted.append(row.id)
        async def commit(self):pass
    async def owned(db,key,user):
        assert key==ids[1] and user==owner
        return rows[1]
    monkeypatch.setattr(router,'get_owned_optimization_run',owned)
    db=DB()
    rows[1].status='running'
    with pytest.raises(HTTPException) as exc:
        await router.delete_optimization_run(ids[0],db,NS(id=owner))
    assert exc.value.status_code==409 and not db.deleted
    rows[1].status='completed'
    await router.delete_optimization_run(ids[0],db,NS(id=owner))
    assert set(db.deleted)==set(ids)


@pytest.mark.asyncio
async def test_list_groups_pairs_but_yearly_selector_can_request_each_design(paired_db_session):
    from sqlalchemy import select
    from app.models import Users
    from app.schemas.pagination import PaginationParams
    db=paired_db_session
    user=(await db.execute(select(Users).where(Users.email=='ci-release@example.com'))).scalar_one()
    ids={'mean':str(uuid4()),'aggregate_q95':str(uuid4())}
    rows=[]
    for basis,rid in ids.items():
        row=OptimizationRuns(id=rid,user_id=user.id,mode='battery_only',name='Pair test',
            status='completed',input_params={'consumption_basis':basis,'basis_run_ids':ids,
                'paired_basis_role':'primary' if basis=='mean' else 'comparison'},
            results={'electrification_feasible':basis=='mean','solver_status':'optimal',
                'forecast_verification':{'status':'verified' if basis=='mean' else 'physical_limits_exceeded'}})
        rows.append(row);db.add(row)
    await db.commit()
    try:
        grouped=await router.list_optimization_runs(PaginationParams(skip=0,limit=100),db,user,False)
        by_id={str(r.id):r for r in grouped.items}
        assert ids['mean'] in by_id and ids['aggregate_q95'] not in by_id
        summaries=by_id[ids['mean']].basis_summaries
        assert summaries['mean']['electrification_feasible'] is True
        assert summaries['aggregate_q95']['electrification_feasible'] is False
        for rid in ids.values():
            detail = await router.get_optimization_run(rid, db, user)
            assert detail.basis_summaries == summaries
        expanded=await router.list_optimization_runs(PaginationParams(skip=0,limit=100),db,user,True)
        by_id={str(r.id):r for r in expanded.items}
        assert set(ids.values()) <= set(by_id)
        assert all(by_id[rid].basis_summaries is None for rid in ids.values())
    finally:
        for row in rows:await db.delete(row)
        await db.commit()
