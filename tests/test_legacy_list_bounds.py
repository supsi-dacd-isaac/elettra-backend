import os

from fastapi.testclient import TestClient

from main import app


BOUNDED_LISTS = {
    "/api/v1/agency/users/": 100,
    "/api/v1/agency/agencies/": 100,
    "/api/v1/user/buses/": 100,
    "/api/v1/user/depots/": 100,
    "/api/v1/gtfs/gtfs-routes/": 100,
    "/api/v1/simulation/prediction-runs/": 1000,
}


def _query_parameter(path: str, name: str) -> dict:
    parameters = app.openapi()["paths"][path]["get"]["parameters"]
    return next(parameter for parameter in parameters if parameter["name"] == name)


def test_legacy_list_contracts_publish_hard_bounds():
    for path, maximum in BOUNDED_LISTS.items():
        skip_schema = _query_parameter(path, "skip")["schema"]
        limit_schema = _query_parameter(path, "limit")["schema"]
        assert skip_schema["minimum"] == 0, path
        assert limit_schema["minimum"] == 1, path
        assert limit_schema["maximum"] == maximum, path


def test_legacy_list_bounds_are_enforced_before_querying(client: TestClient):
    email = os.getenv("TEST_LOGIN_EMAIL")
    password = os.getenv("TEST_LOGIN_PASSWORD")
    assert email and password
    login = client.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    authenticated_paths = {
        path: maximum
        for path, maximum in BOUNDED_LISTS.items()
        if path != "/api/v1/agency/users/"
    }
    for path, maximum in authenticated_paths.items():
        request_headers = (
            {} if path == "/api/v1/agency/agencies/" else headers
        )
        response = client.get(
            path,
            params={"limit": maximum + 1},
            headers=request_headers,
        )
        assert response.status_code == 422, (path, response.text)

        response = client.get(
            path,
            params={"skip": -1},
            headers=request_headers,
        )
        assert response.status_code == 422, (path, response.text)
