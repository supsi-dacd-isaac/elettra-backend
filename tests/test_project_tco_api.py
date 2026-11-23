from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.database import get_async_session
from main import app


@pytest.fixture
def project_preview():
    analysis_id = uuid4()
    user_id = uuid4()
    queries = []

    class ReadOnlyDb:
        async def execute(self, statement):
            params = statement.compile().params.values()
            queries.append(statement)
            assert user_id in params
            analysis = SimpleNamespace(id=analysis_id) if analysis_id in params else None
            return SimpleNamespace(scalar_one_or_none=lambda: analysis)

    async def override_db():
        yield ReadOnlyDb()

    async def override_user():
        return SimpleNamespace(id=user_id)

    original_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_async_session] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        yield SimpleNamespace(
            client=TestClient(app),
            url=f"/api/v1/yearly-analysis/{analysis_id}/costs/project",
            queries=queries,
        )
    finally:
        app.dependency_overrides = original_overrides


def test_project_preview_matches_browser_cash_flows(project_preview):
    electric = {
        "assets": [
            {"key": key, "initial": cost, "life": life}
            for key, cost, life in [
                ("body", 800, 8), ("battery", 200, 2), ("points", 60, 4),
                ("site", 30, 5), ("connection", 10, 5),
            ]
        ],
        "operating": [
            {"key": key, "annual": cost}
            for key, cost in [
                ("electricity", 20), ("maintenance", 20), ("infraMaintenance", 10), ("heater", 10),
            ]
        ],
    }
    diesel = {
        "assets": [{"key": "dieselVehicle", "initial": 600, "life": 10}],
        "operating": [{"key": "fuel", "annual": 40}, {"key": "maintenance", "annual": 10}],
    }
    response = project_preview.client.post(
        project_preview.url,
        json={
            "horizon": 5,
            "rate": 0,
            "scopes": [
                {"id": "fleet", "km": 1000, "electric": electric, "diesel": diesel},
                {"id": "without-electric", "km": 0, "diesel": diesel},
                {"id": "without-diesel", "km": 1000, "electric": electric, "diesel": None},
            ],
        },
    )

    assert response.status_code == 200, response.text
    fleet, diesel_only, electric_only = response.json()["scopes"]
    assert [fleet["id"], diesel_only["id"], electric_only["id"]] == [
        "fleet", "without-electric", "without-diesel",
    ]
    # Reference totals from the browser's computeFleetTco and computeDieselTco.
    assert fleet["electric"]["presentValue"] == 1415
    assert fleet["electric"]["annualEquivalent"] == 283
    assert fleet["electric"]["perKm"] == 0.283
    assert fleet["electric"]["discountedReplacements"] == 460
    assert fleet["electric"]["discountedResidual"] == 445
    assert fleet["electric"]["components"][1]["replacements"] == [
        {"year": 2, "cost": 200, "discounted": 200},
        {"year": 4, "cost": 200, "discounted": 200},
    ]
    assert fleet["electric"]["components"][3]["replacements"] == []
    assert fleet["diesel"]["presentValue"] == 550
    assert fleet["diesel"]["annualEquivalent"] == 110
    assert fleet["diesel"]["perKm"] == 0.11
    assert fleet["diesel"]["residual"] == 300
    assert fleet["diesel"]["operating"] == diesel["operating"]
    assert diesel_only["electric"] is None
    assert diesel_only["diesel"]["perKm"] is None
    assert electric_only["electric"] == fleet["electric"]
    assert electric_only["diesel"] is None
    assert len(project_preview.queries) == 1


def test_project_preview_requires_authentication(project_preview):
    app.dependency_overrides.pop(get_current_user)
    response = project_preview.client.post(
        project_preview.url, json={"horizon": 5, "rate": 0, "scopes": []},
    )
    assert response.status_code in {401, 403}
    assert project_preview.queries == []


def test_project_preview_hides_unowned_analyses(project_preview):
    response = project_preview.client.post(
        f"/api/v1/yearly-analysis/{uuid4()}/costs/project",
        json={"horizon": 5, "rate": 0, "scopes": []},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Yearly analysis not found"
    assert len(project_preview.queries) == 1


@pytest.mark.parametrize(
    "body,expected_error",
    [
        ('{"horizon":61,"rate":0,"scopes":[]}', "less_than_equal"),
        ('{"horizon":true,"rate":0,"scopes":[]}', "int_type"),
        ('{"horizon":5,"rate":1e309,"scopes":[]}', "finite_number"),
        ('{"horizon":5,"rate":0,"scopes":[{"id":"x","km":NaN}]}', "finite_number"),
        ('{"horizon":5,"rate":0,"scopes":[{"id":"x","km":"12"}]}', "float_type"),
        ('{"horizon":5,"rate":0,"scopes":[{"id":"x","km":-1}]}', "greater_than_equal"),
        (
            '{"horizon":5,"rate":0,"scopes":[{"id":"x","km":1},{"id":"x","km":1}]}',
            "value_error",
        ),
        (
            '{"horizon":5,"rate":0,"scopes":[{"id":"x","km":1,'
            '"electric":{"assets":[{"key":"battery","initial":1,"life":0}],"operating":[]}}]}',
            "greater_than_equal",
        ),
    ],
)
def test_project_preview_rejects_malformed_input(project_preview, body, expected_error):
    response = project_preview.client.post(
        project_preview.url, content=body, headers={"content-type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["type"] == expected_error
    assert project_preview.queries == []


def test_project_preview_rejects_excessive_cash_flow_output(project_preview):
    electric = {
        "assets": [{"key": str(index), "initial": 1, "life": 1} for index in range(64)],
        "operating": [],
    }
    response = project_preview.client.post(
        project_preview.url,
        json={
            "horizon": 60,
            "rate": 0,
            "scopes": [{"id": str(index), "km": 1, "electric": electric} for index in range(27)],
        },
    )
    assert response.status_code == 422, response.text
    assert "100,000" in response.json()["detail"][0]["msg"]
    assert project_preview.queries == []
