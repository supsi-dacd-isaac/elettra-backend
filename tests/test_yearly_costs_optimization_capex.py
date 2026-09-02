import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.database import get_async_session
from app.models import OptimizationRuns, YearlyAnalysis
from app.routers import yearly_analysis
from main import app


API_BASE = "/api/v1/yearly-analysis"


def _energy_summary(yearly_analysis_id: uuid.UUID) -> dict:
    return {
        "yearly_analysis_id": yearly_analysis_id,
        "auxiliary_heating_type": "default",
        "yearly_totals": {
            "electric_kwh": 1000.0,
            "distance_km": 100.0,
            "diesel_liters": 0.0,
            "diesel_fuel_kwh": 0.0,
        },
        "scenarios": [
            {
                "temperature_celsius": 15.0,
                "occurrences": 365,
                "daily_electric_kwh": 10.0,
                "daily_distance_km": 1.0,
                "diesel_heating": None,
                "annual_electric_kwh": 1000.0,
                "annual_distance_km": 100.0,
                "annual_diesel_liters": 0.0,
            }
        ],
        "yearly_diesel_heating": None,
    }


def _valid_results() -> dict:
    return {
        "solver_status": "optimal",
        "objective_value": 999_999_999.0,
        "electrification_feasible": True,
        "total_battery_cost_chf": 1.0,
        "total_infeasibility_penalty_chf": 888_888_888.0,
        "total_installation_cost_chf": 12_345.0,
        "installed_chargers": {
            "stop-1": {"stop_name": "Depot", "num_slots": 2, "cost_chf": 12_345.0}
        },
        "battery_results": {
            "shift-1": {
                "optimized_kwh": 400.0,
                "optimized_packs": 10,
                "physical_feasible": True,
            }
        },
        "per_bus_summary": [
            {"shift_id": "shift-1", "shift_name": "A"},
            {"shift_id": "shift-1", "shift_name": "A"},
        ],
    }


class FakeDb:
    def __init__(
        self,
        *,
        yearly_analysis_id: uuid.UUID,
        user_id: uuid.UUID,
        optimization_run_id: uuid.UUID | None,
        optimization_status: str = "completed",
        optimization_results: dict | None = None,
        optimization_user_id: uuid.UUID | None = None,
    ):
        self.yearly_analysis_id = yearly_analysis_id
        self.user_id = user_id
        self.optimization_user_id = optimization_user_id or user_id
        self.optimization_run_id = optimization_run_id
        self.optimization_status = optimization_status
        self.optimization_results = optimization_results
        self.optimization_missing = False

    async def get(self, model, object_id):
        if model is YearlyAnalysis and object_id == self.yearly_analysis_id:
            return SimpleNamespace(
                id=self.yearly_analysis_id,
                optimization_run_id=self.optimization_run_id,
                features={},
            )
        if (
            model is OptimizationRuns
            and self.optimization_run_id is not None
            and object_id == self.optimization_run_id
            and not self.optimization_missing
        ):
            return SimpleNamespace(
                id=self.optimization_run_id,
                user_id=self.optimization_user_id,
                status=self.optimization_status,
                results=self.optimization_results,
            )
        return None


@pytest.fixture
def fake_yearly_costs(monkeypatch):
    yearly_analysis_id = uuid.uuid4()
    user_id = uuid.uuid4()
    optimization_run_id = uuid.uuid4()
    fake_db = FakeDb(
        yearly_analysis_id=yearly_analysis_id,
        user_id=user_id,
        optimization_run_id=optimization_run_id,
        optimization_results=_valid_results(),
    )

    async def override_db():
        yield fake_db

    async def override_user():
        return SimpleNamespace(id=user_id)

    async def fake_energy_summary(db, requested_yearly_analysis_id):
        assert requested_yearly_analysis_id == yearly_analysis_id
        return _energy_summary(yearly_analysis_id)

    original_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_async_session] = override_db
    app.dependency_overrides[get_current_user] = override_user
    monkeypatch.setattr(yearly_analysis, "get_fresh_energy_summary", fake_energy_summary)
    try:
        yield SimpleNamespace(
            yearly_analysis_id=yearly_analysis_id,
            user_id=user_id,
            optimization_run_id=optimization_run_id,
            db=fake_db,
        )
    finally:
        app.dependency_overrides = original_overrides


def _get_costs(client: TestClient, yearly_analysis_id: uuid.UUID, **params):
    base_params = {"bus_length_m": 12.0}
    base_params.update(params)
    return client.get(f"{API_BASE}/{yearly_analysis_id}/costs", params=base_params)


def test_yearly_costs_include_capex_false_still_omits_manual_inputs(
    client: TestClient,
    fake_yearly_costs,
):
    response = _get_costs(client, fake_yearly_costs.yearly_analysis_id)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ebus"]["capex_items"] is None
    assert payload["diesel_comparator"]["capex_items"] is None
    assert payload["ebus"]["opex_items"][0]["name"] == "Energy"


def test_yearly_costs_manual_capex_default_requires_old_inputs(
    client: TestClient,
    fake_yearly_costs,
):
    response = _get_costs(
        client,
        fake_yearly_costs.yearly_analysis_id,
        include_capex=True,
    )

    assert response.status_code == 422
    assert "battery_capacity_kwh is required" in response.json()["detail"]


def test_yearly_costs_manual_capex_flow_is_unchanged(
    client: TestClient,
    fake_yearly_costs,
):
    response = _get_costs(
        client,
        fake_yearly_costs.yearly_analysis_id,
        include_capex=True,
        battery_capacity_kwh=500.0,
        charger_power_kw=300.0,
        battery_cost_per_kwh=100.0,
        charger_cost_per_kw=2.0,
        charger_cost_const=10.0,
        grid_connection_fee_per_kw=3.0,
        grid_connection_fee_const=20.0,
        energy_price_per_kwh=0.5,
    )

    assert response.status_code == 200, response.text
    items = {item["name"]: item for item in response.json()["ebus"]["capex_items"]}
    assert items["Battery"]["investment_chf"] == 50_000.0
    assert items["Charger"]["investment_chf"] == 610.0
    assert items["Grid connection"]["investment_chf"] == 920.0
    assert "Optimized charging infrastructure" not in items


def test_yearly_costs_manual_capex_source_still_requires_charger_power(
    client: TestClient,
    fake_yearly_costs,
):
    response = _get_costs(
        client,
        fake_yearly_costs.yearly_analysis_id,
        include_capex=True,
        capex_source="manual",
        battery_capacity_kwh=500.0,
    )

    assert response.status_code == 422
    assert "charger_power_kw is required" in response.json()["detail"]


def test_yearly_costs_optimization_capex_uses_linked_run_without_manual_inputs(
    client: TestClient,
    fake_yearly_costs,
):
    response = _get_costs(
        client,
        fake_yearly_costs.yearly_analysis_id,
        include_capex=True,
        capex_source="optimization",
        battery_cost_per_kwh=100.0,
        energy_price_per_kwh=0.5,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    items = {item["name"]: item for item in payload["ebus"]["capex_items"]}
    assert items["Battery"]["investment_chf"] == 80_000.0
    assert items["Optimized charging infrastructure"]["investment_chf"] == 12_345.0
    assert "Charger" not in items
    assert "Grid connection" not in items
    assert payload["ebus"]["opex_items"][0]["cost_chf_per_year"] == 500.0


@pytest.mark.parametrize(
    ("mutation", "expected_status", "expected_detail"),
    [
        (
            lambda env: setattr(env.db, "optimization_run_id", None),
            422,
            "optimization_run_id",
        ),
        (
            lambda env: setattr(env.db, "optimization_missing", True),
            409,
            "not found",
        ),
        (
            lambda env: setattr(env.db, "optimization_user_id", uuid.uuid4()),
            403,
            "not accessible",
        ),
        (
            lambda env: setattr(env.db, "optimization_status", "running"),
            409,
            "must be completed",
        ),
        (
            lambda env: setattr(env.db, "optimization_results", None),
            409,
            "has no results",
        ),
        (
            lambda env: env.db.optimization_results.update(
                {"electrification_feasible": False}
            ),
            409,
            "not electrification-feasible",
        ),
        (
            lambda env: env.db.optimization_results.pop("per_bus_summary"),
            422,
            "per-bus summary",
        ),
        (
            lambda env: env.db.optimization_results.update(
                {"solver_status": "infeasible"}
            ),
            409,
            "solver_status must be optimal",
        ),
    ],
)
def test_yearly_costs_optimization_capex_clear_errors(
    client: TestClient,
    fake_yearly_costs,
    mutation,
    expected_status,
    expected_detail,
):
    mutation(fake_yearly_costs)

    response = _get_costs(
        client,
        fake_yearly_costs.yearly_analysis_id,
        include_capex=True,
        capex_source="optimization",
    )

    assert response.status_code == expected_status, response.text
    assert expected_detail in response.json()["detail"]


def test_yearly_costs_invalid_capex_source_is_validation_error(
    client: TestClient,
    fake_yearly_costs,
):
    response = _get_costs(
        client,
        fake_yearly_costs.yearly_analysis_id,
        include_capex=True,
        capex_source="bogus",
    )

    assert response.status_code == 422


def test_extract_optimization_capex_rejects_inconsistent_charger_costs():
    results = _valid_results()
    results["total_installation_cost_chf"] = 1.0

    with pytest.raises(Exception) as exc_info:
        yearly_analysis._extract_optimization_capex_inputs(results)

    assert "inconsistent" in str(exc_info.value)
