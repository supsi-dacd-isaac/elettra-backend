"""Read-only authenticated checks against the isolated release candidate API."""
import json
import time
import httpx


with httpx.Client(base_url="http://127.0.0.1:18002", timeout=120) as client:
    login = client.post("/auth/login", json={"email":"ci-release@example.com", "password":"CI-release-gate_Str0ng!"})
    login.raise_for_status()
    client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
    checks = []
    for case in ("f05b2037-5754-4e13-8029-f95e2921d895", "bf7d839a-fd4b-4f26-8a5d-3f415e907ef1"):
        for params in ({}, {"annual_km":90000}):
            start = time.monotonic()
            response = client.get(f"/api/v1/yearly-analysis/{case}/lca", params=params)
            response.raise_for_status()
            body = response.json()
            assert body["status"] == "complete", body
            assert body["vehicle_count"] == 1
            assert abs(body["energy_boundary"]["grid_kwh"]*.94-body["energy_boundary"]["dc_kwh"]) < 1e-6
            for indicator in body["indicators"].values():
                assert abs(sum(indicator["phases"].values())-indicator["total"]) < 1e-6
            checks.append({"case":case,"annual_km":body["annual_km"],"gwp_t":body["indicators"]["gwp100a"]["total"]/1e6,"seconds":time.monotonic()-start})
        costs = client.get(f"/api/v1/yearly-analysis/{case}/costs", params={"bus_length_m":13})
        costs.raise_for_status()
        boundary = costs.json()["assumptions"]["energy_boundary"]
        assert abs(boundary["dc_kwh"]-boundary["grid_kwh"]*.94) < 1e-6
    response = client.get("/api/v1/economic/opex/electric-energy", params={"annual_consumption_kwh":94,"energy_price_per_kwh":.2})
    response.raise_for_status()
    assert response.json()["cost_per_year_chf"] == 20
    print(json.dumps({"status":"passed", "checks":checks}, indent=2))
