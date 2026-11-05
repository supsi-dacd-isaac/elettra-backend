"""Regression tests for ELETTRA's three-role authorization boundary."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.auth import require_admin, require_analyst
from app.schemas.auth import UserUpdate
from main import app


def _route(method: str, path: str):
    for route in app.routes:
        if path == getattr(route, "path", None) and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"Missing route {method} {path}")


def _dependency_calls(method: str, path: str) -> set[object]:
    route = _route(method, path)
    return {dependency.call for dependency in route.dependant.dependencies}


@pytest.mark.parametrize("role", ["analyst", "admin"])
def test_analyst_boundary_accepts_writers(role):
    user = SimpleNamespace(role=role)
    assert require_analyst(user) is user


def test_analyst_boundary_rejects_viewers():
    with pytest.raises(HTTPException) as exc:
        require_analyst(SimpleNamespace(role="viewer"))
    assert exc.value.status_code == 403


def test_admin_boundary_rejects_analysts_and_viewers():
    for role in ("analyst", "viewer"):
        with pytest.raises(HTTPException) as exc:
            require_admin(SimpleNamespace(role=role))
        assert exc.value.status_code == 403


@pytest.mark.parametrize("field", ["role", "company_id", "created_at", "password_hash"])
def test_self_service_profile_rejects_authorization_and_internal_fields(field):
    with pytest.raises(ValidationError):
        UserUpdate.model_validate({field: "admin"})


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/user/bus-models/"),
        ("PUT", "/api/v1/user/bus-models/{model_id}"),
        ("DELETE", "/api/v1/user/bus-models/{model_id}"),
        ("POST", "/api/v1/user/buses/"),
        ("PUT", "/api/v1/user/buses/{bus_id}"),
        ("DELETE", "/api/v1/user/buses/{bus_id}"),
        ("POST", "/api/v1/user/depots/"),
        ("PUT", "/api/v1/user/depots/{depot_id}"),
        ("DELETE", "/api/v1/user/depots/{depot_id}"),
        ("POST", "/api/v1/user/shifts/"),
        ("PUT", "/api/v1/user/shifts/{shift_id}"),
        ("DELETE", "/api/v1/user/shifts/{shift_id}"),
        ("POST", "/api/v1/simulation/prediction-runs/"),
        ("POST", "/api/v1/simulation/yearly-prediction-runs/"),
        ("POST", "/api/v1/simulation/optimization-runs/"),
        ("POST", "/api/v1/simulation/optimization-runs/{run_id}/basis/{basis}"),
        ("DELETE", "/api/v1/simulation/optimization-runs/{run_id}"),
        ("POST", "/api/v1/simulation/weather-temperature-clusters/"),
        ("POST", "/api/v1/yearly-analysis/"),
        ("PATCH", "/api/v1/yearly-analysis/{yearly_analysis_id}"),
        ("DELETE", "/api/v1/yearly-analysis/{yearly_analysis_id}"),
        ("POST", "/api/v1/yearly-analysis/{yearly_analysis_id}/energy-summary"),
        ("PUT", "/api/v1/gtfs/gtfs-trips/{trip_pk}"),
        ("DELETE", "/api/v1/gtfs/gtfs-trips/{trip_pk}"),
        ("POST", "/api/v1/gtfs/aux-trip"),
    ],
)
def test_private_mutations_require_analyst(method, path):
    assert require_analyst in _dependency_calls(method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/gtfs/gtfs-routes/"),
        ("POST", "/api/v1/gtfs/gtfs-trips/"),
        ("POST", "/api/v1/gtfs/gtfs-stops/"),
        ("PUT", "/api/v1/gtfs/gtfs-stops/{stop_pk}"),
        ("DELETE", "/api/v1/gtfs/gtfs-stops/{stop_pk}"),
        ("POST", "/api/v1/agency/users/"),
        ("PUT", "/api/v1/agency/users/{user_id}"),
    ],
)
def test_shared_data_mutations_require_admin(method, path):
    assert require_admin in _dependency_calls(method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/user/bus-models/"),
        ("GET", "/api/v1/user/shifts/"),
        ("POST", "/api/v1/simulation/charging-stops/candidates"),
        ("POST", "/api/v1/simulation/trip-statistics/"),
        ("GET", "/api/v1/yearly-analysis/"),
        ("GET", "/api/v1/gtfs/gtfs-stops/"),
    ],
)
def test_viewer_read_routes_do_not_require_writer_role(method, path):
    assert require_analyst not in _dependency_calls(method, path)

