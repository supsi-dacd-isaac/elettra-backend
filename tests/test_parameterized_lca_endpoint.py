import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from main import app
from app.core.auth import get_current_user
from app.database import get_async_session
from app.models import YearlyAnalysis, BusesModels
from app.routers import yearly_analysis as router
from app.services import parameterized_lca as service


@pytest.fixture
def endpoint(monkeypatch):
    owner, analysis, shift, run_id, model = [uuid.uuid4() for _ in range(5)]
    run = SimpleNamespace(id=run_id, user_id=owner, shift_id=shift, bus_model_id=model,
        external_temp_celsius=20, auxiliary_heating_type="default",
        contextual_parameters={"bus_length_m":12, "battery_capacity_kwh":400,
                               "physical_mass":{"passenger_count":20}})
    features = {"scenarios":[{"temperature":20}]}
    class Db:
        async def get(self, kind, key):
            if kind is YearlyAnalysis:
                return SimpleNamespace(features=features) if key == analysis else None
            if kind is BusesModels:
                return SimpleNamespace(specs={})
    async def session(): yield Db()
    async def runs(*args): return [run]
    async def energy(*args): return {"scenarios":[{"prediction_run_id":run_id,
        "annual_distance_km":60000, "annual_electric_kwh":90000}]}
    async def evaluate(vehicles, client):
        return {"methodology_version": service.METHOD, "status":"complete", "vehicles":vehicles}
    monkeypatch.setattr(router, "_load_prediction_runs", runs)
    monkeypatch.setattr(router, "_build_energy_summary", energy)
    monkeypatch.setattr(router, "_lca_base_url", lambda: service.catalog()["base_url"])
    monkeypatch.setattr(service, "evaluate_fleet", evaluate)
    old = app.dependency_overrides.copy()
    app.dependency_overrides[get_async_session] = session
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner)
    try:
        yield TestClient(app), f"/api/v1/yearly-analysis/{analysis}/lca", run
    finally:
        app.dependency_overrides = old


def test_contract_reparameterizes_without_mutating_snapshots(endpoint):
    client, url, run = endpoint
    data = client.get(url, params={"annual_km":30000}).json()
    assert data["status"] == "complete"
    assert data["vehicles"][0]["annual_km"] == 30000
    assert data["vehicles"][0]["dc_kwh"] == 45000
    assert run.contextual_parameters["battery_capacity_kwh"] == 400


def test_ownership_and_required_inputs(endpoint):
    client, url, run = endpoint
    owner = run.user_id
    run.user_id = uuid.uuid4()
    assert client.get(url).status_code == 403
    run.user_id = owner
    run.contextual_parameters.pop("battery_capacity_kwh")
    response = client.get(url)
    assert response.status_code == 200
    assert response.json()["status"] == "incomplete"
    assert response.json()["indicators"] == {}
    assert client.get(url, params={"annual_km":0}).status_code == 422
