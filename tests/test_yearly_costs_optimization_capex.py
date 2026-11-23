import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.database import get_async_session
from app.models import OptimizationRuns, PredictionRuns, YearlyAnalysis
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
        self.vehicle_prices = []
        self.features = {}

    def _yearly_analysis(self):
        return SimpleNamespace(
            id=self.yearly_analysis_id,
            optimization_run_id=self.optimization_run_id,
            features=self.features,
        )

    def _optimization_run(self):
        if (
            self.optimization_run_id is None
            or self.optimization_missing
            or self.optimization_user_id != self.user_id
        ):
            return None
        return SimpleNamespace(
            id=self.optimization_run_id,
            user_id=self.optimization_user_id,
            status=self.optimization_status,
            results=self.optimization_results,
        )

    async def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        if entity is YearlyAnalysis:
            value = self._yearly_analysis()
        elif entity is OptimizationRuns:
            value = self._optimization_run()
        elif entity is PredictionRuns:
            return SimpleNamespace(all=lambda: self.vehicle_prices)
        else:
            raise AssertionError(f"unexpected ownership query: {statement!s}")

        class Result:
            def scalar_one_or_none(self):
                return value

        return Result()

    async def get(self, model, object_id):
        if model is YearlyAnalysis and object_id == self.yearly_analysis_id:
            return self._yearly_analysis()
        if (
            model is OptimizationRuns
            and self.optimization_run_id is not None
            and object_id == self.optimization_run_id
            and not self.optimization_missing
        ):
            return self._optimization_run()
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

    async def fake_energy_summary(db, requested_yearly_analysis_id, **options):
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
        bus_no_batt_quad_coeff=0.0,
        bus_no_batt_lin_coeff=0.0,
        bus_no_batt_const=640_000,
        charger_cost_per_kw=2.0,
        charger_cost_const=10.0,
        grid_connection_fee_per_kw=3.0,
        grid_connection_fee_const=20.0,
        energy_price_per_kwh=0.5,
    )

    assert response.status_code == 200, response.text
    items = {item["name"]: item for item in response.json()["ebus"]["capex_items"]}
    assert items["Battery"]["investment_chf"] == 50_000.0
    assert items["Bus body (w/o battery)"]["investment_chf"] == 640_000
    assert items["Charger"]["investment_chf"] == 610.0
    assert items["Grid connection"]["investment_chf"] == round(3 * 300 / .94 + 20, 2)
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


@pytest.mark.parametrize(
    ("battery_price", "battery_investment"),
    [({}, 264_000), ({"battery_cost_per_kwh": 100.0}, 80_000)],
)
def test_yearly_costs_optimization_capex_uses_linked_run_without_manual_inputs(
    client: TestClient,
    fake_yearly_costs,
    battery_price,
    battery_investment,
):
    response = _get_costs(
        client,
        fake_yearly_costs.yearly_analysis_id,
        include_capex=True,
        capex_source="optimization",
        energy_price_per_kwh=0.5,
        **battery_price,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    items = {item["name"]: item for item in payload["ebus"]["capex_items"]}
    assert items["Battery"]["investment_chf"] == battery_investment
    assert items["Bus body (w/o battery)"]["investment_chf"] == 2 * 490_000
    assert items["Optimized charging infrastructure"]["investment_chf"] == 12_345.0
    assert "Charger" not in items
    assert "Grid connection" not in items
    assert payload["ebus"]["opex_items"][0]["cost_chf_per_year"] == round(500 / .94, 2)


def test_yearly_costs_preserve_pack_price_and_split_site_investment(client, fake_yearly_costs):
    results = fake_yearly_costs.db.optimization_results
    results["battery_results"]["shift-1"]["selected_battery_cost_chf"] = 60_000
    results["installed_chargers"]["stop-1"]["cost_breakdown"] = {
        "chargers_cost_chf": 8000,
        "site_fixed_cost_chf": 3000,
        "site_power_cost_chf": 1345,
    }
    response = _get_costs(
        client, fake_yearly_costs.yearly_analysis_id, include_capex=True,
        capex_source="optimization", battery_cost_per_kwh=999,
        lifetime_charger=15, lifetime_connection=30,
    )
    assert response.status_code == 200, response.text
    items = {item["name"]: item for item in response.json()["ebus"]["capex_items"]}
    # The fixture contains two buses with the same block: price each battery,
    # while the shared site investment is counted only once.
    assert items["Battery"]["investment_chf"] == 120_000
    assert items["Charging points"]["investment_chf"] == 8000
    assert items["Charging points"]["lifetime_years"] == 15
    assert items["Site connection and shared infrastructure"]["investment_chf"] == 4345
    assert items["Site connection and shared infrastructure"]["lifetime_years"] == 30
    assert "Optimized charging infrastructure" not in items


@pytest.mark.parametrize(
    ("body_overrides", "body_investment"),
    [({}, 1_290_000), ({"bus_no_batt_quad_coeff": 0, "bus_no_batt_lin_coeff": 0,
                        "bus_no_batt_const": 610_000}, 1_220_000)],
)
def test_yearly_optimization_capex_uses_saved_fleet_quotes(
    client, fake_yearly_costs, body_overrides, body_investment,
):
    results = fake_yearly_costs.db.optimization_results
    rows = results["battery_results"]
    rows["shift-1"].update(bus_cost_chf=640_000, battery_pack_cost_chf=25_000)
    results["per_bus_summary"][1].update(bus_body_cost_chf=650_000, battery_pack_cost_chf=26_000)
    # Some optimizer versions already include the combined investment.
    rows["shift-1"]["selected_vehicle_cost_chf"] = 890_000
    response = _get_costs(
        client, fake_yearly_costs.yearly_analysis_id, include_capex=True,
        capex_source="optimization", **body_overrides,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    items = {item["name"]: item for item in payload["ebus"]["capex_items"]}
    assert items["Bus body (w/o battery)"]["investment_chf"] == body_investment
    assert items["Battery"]["investment_chf"] == 510_000
    diesel_body = 625 * 12 ** 2 + 6250 * 12 + 185_000
    assert payload["diesel_comparator"]["capex_items"][0]["investment_chf"] == 2 * diesel_body


def test_yearly_optimization_uses_chemistry_for_unquoted_battery_only(client, fake_yearly_costs):
    db = fake_yearly_costs.db
    model_id = uuid.uuid4()
    db.vehicle_prices = [
        ("shift-2", model_id, {"battery_chemistry": "NMC"}, {"battery_chemistry": "LFP"}),
    ]
    results = db.optimization_results
    results["per_bus_summary"][1]["shift_id"] = "shift-2"
    results["battery_results"]["shift-1"]["selected_battery_cost_chf"] = 60_000
    results["battery_results"]["shift-2"] = {"optimized_kwh": 300, "bus_model_id": str(model_id)}
    response = _get_costs(
        client, fake_yearly_costs.yearly_analysis_id, include_capex=True,
        capex_source="optimization",
    )
    assert response.status_code == 200, response.text
    items = {item["name"]: item for item in response.json()["ebus"]["capex_items"]}
    assert items["Battery"]["investment_chf"] == 60_000 + 300 * 450


def test_yearly_manual_capex_uses_linked_model_quote_and_preserves_explicit_rate(
    client, fake_yearly_costs,
):
    fake_yearly_costs.db.vehicle_prices = [
        ("shift-1", uuid.uuid4(), {}, {
            "cost": 645_000, "battery_pack_cost_chf": 28_000, "battery_pack_size_kwh": 40,
        }),
    ]
    base_params = {"include_capex": True, "battery_capacity_kwh": 200, "charger_power_kw": 150}
    for price_params, expected_battery in [({}, 140_000), ({"battery_cost_per_kwh": 450}, 90_000)]:
        response = _get_costs(
            client, fake_yearly_costs.yearly_analysis_id, **base_params, **price_params,
        )
        assert response.status_code == 200, response.text
        items = {item["name"]: item for item in response.json()["ebus"]["capex_items"]}
        assert items["Bus body (w/o battery)"]["investment_chf"] == 645_000
        assert items["Battery"]["investment_chf"] == expected_battery


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
            404,
            "not found",
        ),
        (
            lambda env: setattr(env.db, "optimization_user_id", uuid.uuid4()),
            404,
            "Optimization run not found",
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
        yearly_analysis._extract_optimization_capex_inputs(results, body_estimate_chf=490_000)

    assert "inconsistent" in str(exc_info.value)


def test_project_and_legacy_get_costs_reconcile_tpl_quote(client, fake_yearly_costs, monkeypatch):
    async def annual_activity(*args, **options):
        energy = _energy_summary(fake_yearly_costs.yearly_analysis_id)
        energy["yearly_totals"].update(distance_km=17996.39584399179, electric_kwh=23090.608383)
        return energy

    monkeypatch.setattr(yearly_analysis, "get_fresh_energy_summary", annual_activity)
    params = dict(include_capex=True, bus_length_m=10.75, battery_capacity_kwh=400,
                  charger_power_kw=80, battery_cost_per_kwh=330)
    project = _get_costs(client, fake_yearly_costs.yearly_analysis_id, **params).json()
    legacy = _get_costs(client, fake_yearly_costs.yearly_analysis_id, **params, tco_method="asset_lifetime").json()
    assert project["assumptions"]["project_horizon_years"] == 12
    assert project["ebus"]["total_annual_cost_chf_per_year"] == 81066.22
    assert project["annual_saving_chf"] == -28542.46
    assert legacy["ebus"]["total_annual_cost_chf_per_year"] == 80500.61
    assert legacy["assumptions"]["project_horizon_years"] is None


def test_project_overrides_saved_horizon_and_infrastructure_budget(client, fake_yearly_costs):
    fake_yearly_costs.db.features = {"assessment_assumptions": {"tco": {
        "horizon": 10, "rate": 0, "bodyLife": 15, "dieselCapex": 123456,
        "maintenancePerKm": 0.4, "energyPrice": 0.5,
        "infrastructureMaintenanceMode": "fixed", "infrastructureMaintenance": 2300,
    }}}
    params = dict(include_capex=True, capex_source="optimization")
    def get(**extra):
        reply = _get_costs(client, fake_yearly_costs.yearly_analysis_id, **params, **extra)
        assert reply.status_code == 200, reply.text
        return reply.json()
    saved = get()
    assert saved["assumptions"]["project_horizon_years"] == 10
    assert saved["assumptions"]["interest_rate"] == 0
    assert saved["diesel_comparator"]["capex_items"][0]["investment_chf"] == 246912
    assert saved["ebus"]["total_opex_chf_per_year"] == round(1000 / .94 * .5 + 100 * .4 + 2300, 2)
    overrides = get(project_horizon_years=4, infrastructure_maintenance_chf_per_year=0)
    assert overrides["assumptions"]["project_horizon_years"] == 4
    assert saved["ebus"]["total_opex_chf_per_year"] - overrides["ebus"]["total_opex_chf_per_year"] == 2300
    free_energy = get(energy_price_per_kwh=0, fuel_cost_per_l=0)
    assert free_energy["ebus"]["total_opex_chf_per_year"] == 2340
    legacy = get(tco_method="asset_lifetime")
    assert legacy["assumptions"]["interest_rate"] == .03
    assert legacy["ebus"]["total_opex_chf_per_year"] < overrides["ebus"]["total_opex_chf_per_year"]


def test_project_heater_and_site_maintenance_follow_dashboard_rates(client, fake_yearly_costs, monkeypatch):
    async def annual_heating(*args, **options):
        energy = _energy_summary(fake_yearly_costs.yearly_analysis_id)
        energy["auxiliary_heating_type"] = "diesel"
        energy["diesel_heating_data"] = {"data_status": "available"}
        energy["yearly_totals"]["diesel_liters"] = 50
        energy["scenarios"][0]["annual_diesel_liters"] = 50
        return energy

    monkeypatch.setattr(yearly_analysis, "get_fresh_energy_summary", annual_heating)
    fake_yearly_costs.db.features = {"assessment_assumptions": {"tco": {
        "infrastructureMaintenanceMode": "rate", "infrastructureMaintenanceRate": 0.03,
    }}}
    def query(method):
        r = _get_costs(client, fake_yearly_costs.yearly_analysis_id, include_capex=True,
                       capex_source="optimization", tco_method=method, fuel_cost_per_l=2)
        assert r.status_code == 200
        data = r.json()
        return {item["name"]: item["cost_chf_per_year"] for item in data["ebus"]["opex_items"]}, data["scenarios"][0]

    project, scenario = query("project_horizon")
    assert project["Infrastructure maintenance"] == round(12345 * .03, 2)
    assert project["Diesel heating fuel"] == 100
    assert project["Diesel heating maintenance"] == scenario["annual_diesel_heating_maint_cost_chf"] == 10
    legacy, _ = query("asset_lifetime")
    assert legacy["Diesel heating maintenance"] == round(legacy["Maintenance"] * .1, 2)
