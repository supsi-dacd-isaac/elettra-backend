"""HTTP/SQL regression on an explicitly isolated PostgreSQL database.

Run with ``python -m unittest tests.test_gtfs_datasets_isolated``. The test
refuses the staging/production database and rolls back its fixture transaction.
"""
import os
import unittest
from datetime import date
from uuid import uuid4

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.auth import create_access_token
from app.database import get_async_session
from app.models import GtfsAgencies, GtfsCalendar, GtfsRoutes, GtfsTrips, Users, Buses, Shifts, ShiftsStructures
from app.routers import gtfs, user
from app.services.gtfs_datasets import capture_provenance


class DatasetHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_shift_invariants_and_access(self):
        url = make_url(os.environ.get('DATABASE_URL', 'postgresql+asyncpg://unused/unused'))
        if not url.database.startswith('elettra_test_') or url.host != 'gtfs-db':
            self.skipTest('Requires the isolated gtfs-db container and an elettra_test_* database')
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    async with AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode='create_savepoint') as db:
                        companies = [GtfsAgencies(id=uuid4(),gtfs_agency_id=str(uuid4()),agency_name='Test',agency_url='https://example.invalid',agency_timezone='Europe/Zurich') for _ in range(2)]
                        db.add_all(companies);await db.flush()
                        owner = Users(id=uuid4(),company_id=companies[0].id,email=f'{uuid4()}@example.invalid',full_name='Test',password_hash='unused',role='analyst')
                        foreign = Users(id=uuid4(),company_id=companies[1].id,email=f'{uuid4()}@example.invalid',full_name='Other',password_hash='unused',role='analyst')
                        viewer = Users(id=uuid4(),company_id=companies[0].id,email=f'{uuid4()}@example.invalid',full_name='Viewer',password_hash='unused',role='viewer')
                        db.add_all([owner,foreign,viewer]);await db.flush()
                        bus=Buses(id=uuid4(),user_id=owner.id,name='Dataset test',specs={});db.add(bus)
                        dates=[(2025,'2025-10-02'),(2026,'2025-09-27'),(2027,'2026-08-22'),(2027,'2026-11-04'),(2099,'2098-01-01')]
                        trips=[]
                        for index,(year,file_date) in enumerate(dates):
                            calendar=GtfsCalendar(id=uuid4(),service_id=str(uuid4()),**dict.fromkeys(['monday','tuesday','wednesday','thursday','friday','saturday','sunday'],1),start_date=date(year-1,12,13),end_date=date(year,12,11))
                            route=GtfsRoutes(id=uuid4(),route_id=str(uuid4()),agency_id=companies[index==4].id,gtfs_year=year,gtfs_file_date=date.fromisoformat(file_date),route_type=700,route_short_name=str(year))
                            db.add_all([calendar,route]);await db.flush()
                            trip=GtfsTrips(id=uuid4(),trip_id=str(uuid4()),route_id=route.id,service_id=calendar.id,gtfs_service_id=calendar.service_id,status='gtfs')
                            db.add(trip);trips.append(trip)
                        # An auxiliary trip attached to a different year must not determine the edition.
                        aux=GtfsTrips(id=uuid4(),trip_id=str(uuid4()),route_id=trips[1].route_id,service_id=trips[1].service_id,gtfs_service_id='aux',status='depot')
                        db.add(aux);await db.flush()
                        legacy=Shifts(id=uuid4(),name='No GTFS',bus_id=bus.id)
                        mixed=Shifts(id=uuid4(),name='Historical mixed',bus_id=bus.id)
                        db.add_all([legacy,mixed]);await db.flush()
                        db.add_all([ShiftsStructures(shift_id=legacy.id,trip_id=aux.id,sequence_number=1),ShiftsStructures(shift_id=mixed.id,trip_id=trips[0].id,sequence_number=1),ShiftsStructures(shift_id=mixed.id,trip_id=trips[1].id,sequence_number=2)])
                        await db.flush()
                        app=FastAPI();app.include_router(gtfs.router,prefix='/api/v1/gtfs');app.include_router(user.router,prefix='/api/v1/user')
                        async def session():yield db
                        app.dependency_overrides[get_async_session]=session
                        def headers(who):return {'Authorization':'Bearer '+create_access_token({'sub':str(who.id)})}
                        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers=headers(owner)) as client:
                            response=await client.get('/api/v1/gtfs/datasets');self.assertEqual(response.status_code,200,response.text)
                            catalog=response.json();self.assertEqual([(d['gtfs_year'],d['gtfs_file_date']) for d in catalog],list(reversed(dates[:4])))
                            self.assertEqual(catalog[0]['valid_from'],'2026-12-13');self.assertEqual(catalog[0]['valid_to'],'2027-12-11')
                            unauth=await client.get('/api/v1/gtfs/datasets',headers={'Authorization':''});self.assertIn(unauth.status_code,(401,403))
                            other=await client.get('/api/v1/gtfs/datasets',headers=headers(foreign));self.assertEqual([d['gtfs_year'] for d in other.json()],[2099])
                            path='/api/v1/user/shifts/'
                            body={'name':'New','bus_id':str(bus.id),'trip_ids':[str(trips[0].id),str(aux.id)]}
                            denied=await client.post(path,json=body,headers=headers(viewer));self.assertEqual(denied.status_code,403)
                            denied=await client.post(path,json=body,headers=headers(foreign));self.assertEqual(denied.status_code,404)
                            wrong=await client.post(path,json={**body,'gtfs_dataset':{'gtfs_year':2027,'gtfs_file_date':'2026-11-04'}});self.assertEqual(wrong.status_code,422)
                            response=await client.post(path,json=body);self.assertEqual(response.status_code,200,response.text)
                            saved=response.json();self.assertEqual([d['gtfs_year'] for d in saved['gtfs_datasets']],[2025]);sid=saved['id']
                            for ids in ([trips[1].id],[aux.id],[],[trips[0].id,trips[1].id]):
                                response=await client.put(path+sid,json={'trip_ids':list(map(str,ids))});self.assertEqual(response.status_code,422,response.text)
                            response=await client.post(path,json={**body,'trip_ids':[str(trips[2].id),str(trips[3].id)]});self.assertEqual(response.status_code,422)
                            response=await client.post(path,json={**body,'trip_ids':[str(trips[4].id)]});self.assertEqual(response.status_code,404)
                            # Older clients omit the reference; ordinary duplication keeps the edition.
                            duplicate=await client.post(path,json={**body,'name':'Copy'});self.assertEqual(duplicate.json()['gtfs_datasets'],saved['gtfs_datasets'])
                            for year_index in (1,2,3):
                                edition=catalog[3-year_index]
                                response=await client.post(path,json={**body,'trip_ids':[str(trips[year_index].id)],'gtfs_dataset':{key:edition[key] for key in ('gtfs_year','gtfs_file_date')}})
                                self.assertEqual(response.status_code,200,response.text)
                            response=await client.get(path+str(legacy.id));self.assertEqual(response.json()['gtfs_datasets'],[])
                            response=await client.get(path+str(mixed.id));self.assertEqual(len(response.json()['gtfs_datasets']),2)
                            response=await client.get(path+sid+'/info');self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['gtfs_datasets'],saved['gtfs_datasets'])
                            response=await client.get(path);self.assertEqual(response.status_code,200,response.text)
                            listing={r['id']:r for r in response.json()['items']};self.assertEqual(listing[sid]['gtfs_datasets'],saved['gtfs_datasets'])
                            response=await client.get(path+sid,headers=headers(foreign));self.assertEqual(response.status_code,404)
                            snapshot=await capture_provenance(db,[legacy.id,mixed.id],owner.company_id)
                            self.assertEqual(snapshot['shifts'][str(legacy.id)],[]);self.assertEqual(len(snapshot['shifts'][str(mixed.id)]),2)
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()


if __name__ == '__main__':
    unittest.main()
