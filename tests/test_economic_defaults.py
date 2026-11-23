import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import economic


@pytest.fixture
def economic_client(monkeypatch):
    app = FastAPI()
    app.include_router(economic.router)
    app.dependency_overrides[economic.get_current_user] = (
        lambda: SimpleNamespace(id=uuid.uuid4(), company_id=None)
    )
    app.dependency_overrides[economic.get_async_session] = lambda: None

    async def fake_yearly_distance(*_args, **_kwargs):
        return SimpleNamespace(yearly_distance_km=60_000)

    monkeypatch.setattr(economic, "compute_shift_yearly_distance", fake_yearly_distance)
    with TestClient(app) as client:
        yield client


def test_defaults_expose_battery_chemistries_and_price_points(economic_client):
    response = economic_client.get("/defaults")
    assert response.status_code == 200
    defaults = response.json()
    assert defaults["battery_chemistry_default"] == "LFP"
    assert defaults["battery_cost_per_kwh"] == 330
    assert defaults["battery_chemistries"] == {
        "LFP": {"kg_per_kwh": 6.85, "cost_per_kwh": 330},
        "NMC": {"kg_per_kwh": 1000 / 195, "cost_per_kwh": 450},
    }
    assert defaults["bus_body_price_points"] == [
        {"length_m": 9, "cost_chf": 360_000},
        {"length_m": 10, "cost_chf": 460_000},
        {"length_m": 12, "cost_chf": 490_000},
        {"length_m": 18, "cost_chf": 570_000},
    ]


@pytest.mark.parametrize(
    ("length_m", "cost_chf"),
    [
        (9, 360_000), (9.5, 410_000), (10, 460_000),
        (11, 475_000), (12, 490_000), (13, 503_333.33), (18, 570_000),
        (19, 583_333.33),
    ],
)
def test_electric_body_estimate_by_length(economic_client, length_m, cost_chf):
    response = economic_client.get("/investment/electric-bus-body", params={"length_m": length_m})
    assert response.status_code == 200
    assert response.json()["cost_chf"] == cost_chf


@pytest.mark.parametrize(
    ("overrides", "cost_chf"),
    [
        ({"quad_coeff": 0}, -45833.0 * 11 + 570000.0),
        ({"quad_coeff": 0, "lin_coeff": 0, "const_coeff": 640_000}, 640_000),
    ],
)
def test_electric_body_estimate_accepts_legacy_overrides(economic_client, overrides, cost_chf):
    response = economic_client.get(
        "/investment/electric-bus-body", params={"length_m": 11, **overrides}
    )
    assert response.status_code == 200
    assert response.json()["cost_chf"] == cost_chf


def test_full_comparison_uses_default_body_and_respects_explicit_battery_price(economic_client):
    response = economic_client.get(
        "/comparison",
        params={
            "shift_id": str(uuid.uuid4()),
            "recurrence": "custom",
            "custom_days": 1,
            "bus_length_m": 11,
            "annual_consumption_kwh": 120_000,
            "battery_capacity_kwh": 300,
            "battery_cost_per_kwh": 450,
            "charger_power_kw": 150,
        },
    )
    assert response.status_code == 200, response.text
    items = {item["name"]: item for item in response.json()["electric"]["capex_items"]}
    assert items["Bus body (w/o battery)"]["investment_chf"] == 475_000
    assert items["Battery"]["investment_chf"] == 135_000

@pytest.mark.parametrize("length", [8.99, 15, 19.01])
def test_new_body_estimate_rejects_unsupported_length_but_keeps_legacy_coefficients(economic_client, length):
    path = "/investment/electric-bus-body"
    assert economic_client.get(path, params={"length_m": length}).status_code == 422
    assert economic_client.get(path, params={"length_m": length, "const_coeff": 570000}).status_code == 200
