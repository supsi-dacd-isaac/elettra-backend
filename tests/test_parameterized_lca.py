import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.services.parameterized_lca import (
    LcaIncomplete, MobitoolClient, PHASES, build_vehicles, catalog, impacts,
    normalize_electric, parameters,
    evaluate_fleet,
    representative_vehicles,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures/mobitool_grid94_reference.json").read_text())
PAIRS = {r["params"]["electricityConsumption"]: r["body"] for r in FIXTURE["responses"] if r["params"]}


def test_frozen_reference_linearity_and_units():
    for i, phases in PAIRS[0].items():
        for p in PHASES:
            assert PAIRS[150][i][p] == pytest.approx(phases[p]+1.5*(PAIRS[100][i][p]-phases[p]))
    result = normalize_electric(PAIRS[0], PAIRS[100], dc_kwh=94, distance_km=100, native_multiplier=1.265)
    assert sum(result["gwp100a"]["electricity_operation"].values()) == pytest.approx(128*100)
    assert result["gwp100a"]["other_lifecycle"]["vehicle"] == pytest.approx(PAIRS[0]["gwp100a"]["vehicle"]*100)
    pe = result["primaryEnergy"]["electricity_operation"]
    assert pe["direct"] == pytest.approx(94*3.6)
    assert sum(pe.values()) == pytest.approx((1+PAIRS[100]["primaryEnergy"]["energyChain"])/1.265*100*3.6)


def test_negative_bookkeeping_not_clipped():
    ref = copy.deepcopy(PAIRS[100]); ref["primaryEnergyNonRenewable"]["energyChain"] = -.95
    result = normalize_electric(PAIRS[0], ref, dc_kwh=100, distance_km=100, native_multiplier=1.265)
    assert result["primaryEnergyNonRenewable"]["electricity_operation"]["energyChain"] < 0


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_missing_phase_never_becomes_zero(bad):
    raw = copy.deepcopy(PAIRS[100]); raw["gwp100a"]["infrastructure"] = bad
    with pytest.raises(LcaIncomplete): impacts(raw)


def inputs():
    runs = [SimpleNamespace(id=str(i), shift_id=s, bus_model_id="bus", external_temp_celsius=t,
            auxiliary_heating_type="default", contextual_parameters={"bus_length_m":12,"battery_capacity_kwh":400,
            "physical_mass":{"passenger_count":20}}) for i,(s,t) in enumerate([("a",0),("a",20),("b",0),("b",20)])]
    energy = {"scenarios":[{"prediction_run_id":r.id,"annual_distance_km":10000,"annual_electric_kwh":15000} for r in runs]}
    features = {"scenarios":[{"temperature":0},{"temperature":20}]}
    return runs,energy,features


def test_weather_not_vehicle_count_and_mileage_reparameterizes_lifetime():
    runs,energy,features=inputs()
    vehicles=build_vehicles(runs,energy,features,{})
    assert len(vehicles)==2
    assert vehicles[0]["annual_km"]==20000
    assert vehicles[0]["lifetime_diesel_bus"] == 12
    assert build_vehicles(runs, energy, features, {}, {"lifetime_diesel_bus": 10})[0]["lifetime_diesel_bus"] == 10
    assert vehicles[0]["sources"]["battery_chemistry"]=="declared_default"
    scaled=build_vehicles(runs,energy,features,{}, {"annual_km":80000})
    meta=catalog()["vehicles"][catalog()["classes"]["13m"]["electric"]]
    pars=parameters(scaled[0],meta)
    assert pars["lifetimeKilometers"]==480000
    assert pars["batteryLifetimeReplacements"]==1
    scaled[0]["lifetime_bus"]=16
    assert parameters(scaled[0],meta)["batteryLifetimeReplacements"]==1
    assert scaled[0]["battery_capacity_kwh"]==400


def test_shift_mileage_override_changes_only_the_selected_physical_shift():
    runs, energy, features = inputs()
    baseline = build_vehicles(runs, energy, features, {})
    scaled = build_vehicles(
        runs,
        energy,
        features,
        {},
        {"shift_id": "a", "annual_km": 30000},
    )

    baseline_by_shift = {vehicle["shift_id"]: vehicle for vehicle in baseline}
    scaled_by_shift = {vehicle["shift_id"]: vehicle for vehicle in scaled}

    assert scaled_by_shift["a"]["annual_km"] == 30000
    assert scaled_by_shift["a"]["dc_kwh"] == pytest.approx(45000)
    assert scaled_by_shift["b"]["annual_km"] == baseline_by_shift["b"]["annual_km"]
    assert scaled_by_shift["b"]["dc_kwh"] == baseline_by_shift["b"]["dc_kwh"]


def test_shift_mileage_override_rejects_an_unknown_physical_shift():
    runs, energy, features = inputs()
    with pytest.raises(LcaIncomplete, match="not part of the yearly analysis"):
        build_vehicles(
            runs,
            energy,
            features,
            {},
            {"shift_id": "missing", "annual_km": 30000},
        )


def test_duplicate_weather_missing_weather_and_missing_snapshot_fail_closed():
    runs,energy,features=inputs()
    with pytest.raises(LcaIncomplete): build_vehicles(runs+[runs[0]],energy,features,{})
    with pytest.raises(LcaIncomplete): build_vehicles(runs[:-1],energy,features,{})
    runs[0].contextual_parameters.pop("battery_capacity_kwh")
    with pytest.raises(LcaIncomplete): build_vehicles(runs,energy,features,{"bus":{"battery_capacity_kwh":400}})


@pytest.mark.asyncio
async def test_version_and_upstream_pins():
    with pytest.raises(LcaIncomplete): MobitoolClient("https://wrong.example")
    client=MobitoolClient(catalog()["base_url"], transport=httpx.MockTransport(lambda r:httpx.Response(200,headers={"lca-data-version":"wrong"},json=PAIRS[100])))
    with pytest.raises(LcaIncomplete): await client.impact("vehicle",{"vkm":"true"})


@pytest.mark.asyncio
async def test_live_contract_parameters_forwarded_with_fixture():
    def handle(req):
        assert req.url.params["vkm"]=="true"
        assert req.url.params["electricityConsumption"]=="100"
        return httpx.Response(200,headers={"lca-data-version":catalog()["data_version"]},json=PAIRS[100])
    client=MobitoolClient(catalog()["base_url"],transport=httpx.MockTransport(handle))
    result=await client.impact("vehicle",{"vkm":"true","electricityConsumption":100})
    assert len(result["response_sha256"])==64


ACCEPTANCE = json.loads((Path(__file__).parent / "fixtures/mobitool_grid94_acceptance.json").read_text())


@pytest.mark.asyncio
async def test_frozen_real_service_variants_replay_and_reconciliation():
    class FrozenClient:
        base_url = catalog()["base_url"]
        async def impact(self, vehicle_id, params):
            for v in ACCEPTANCE["vehicles"]:
                for r in v.get("upstream_requests", []):
                    if r["vehicle_id"] == vehicle_id and r["parameters"] == params:
                        return r
            raise LcaIncomplete("No complete fixture for these parameters")
    vehicles = ACCEPTANCE["vehicles"]
    result = await evaluate_fleet(vehicles, FrozenClient())
    assert result["status"] == "incomplete"
    assert result["indicators"] == {}
    for got, expected in zip(result["vehicles"][:-1], vehicles[:-1]):
        assert got["indicators"] == expected["indicators"]
        for indicator in got["indicators"].values():
            assert indicator["total"] == pytest.approx(sum(indicator["phases"].values()))
            assert indicator["total"] == pytest.approx(sum(indicator[k] for k in ("electricity_operation", "diesel_heating", "other_lifecycle")))
    complete = await evaluate_fleet(vehicles[:3], FrozenClient())
    assert complete["status"] == "complete"
    assert complete["vehicle_count"] == 3
    assert complete["energy_boundary"]["grid_kwh"] == pytest.approx(270000/.94)
    assert [v["reference_size"] for v in complete["vehicles"]] == ["9m", "13m", "18m"]


def test_capacity_chemistry_replacements_and_lifetime_affect_inventories_not_supply():
    cases = ACCEPTANCE["vehicles"]
    baseline = cases[1]["indicators"]["gwp100a"]
    for i in (3, 4, 5, 6, 7):
        varied = cases[i]["indicators"]["gwp100a"]
        assert varied["electricity_operation"] == pytest.approx(baseline["electricity_operation"])
        assert varied["other_lifecycle"] != baseline["other_lifecycle"]
    assert cases[3]["indicators"]["gwp100a"]["other_lifecycle"] > baseline["other_lifecycle"]
    assert cases[6]["indicators"]["gwp100a"]["other_lifecycle"] > baseline["other_lifecycle"]
    # More km increases running impacts but not annual vehicle production by 50%.
    mileage = cases[8]["indicators"]["gwp100a"]
    assert mileage["electricity_operation"] == pytest.approx(1.5*baseline["electricity_operation"])
    assert mileage["phases"]["vehicle"] == pytest.approx(baseline["phases"]["vehicle"])
    assert mileage["other_lifecycle"] != pytest.approx(1.5*baseline["other_lifecycle"])


def test_snapshot_precedes_mutated_specs_and_nominal_capacity_is_preserved():
    runs, energy, features = inputs()
    for r in runs:
        r.contextual_parameters["assessment_metadata"] = {"bus_lifetime": 16, "battery_chemistry": "LFP"}
    vehicles = build_vehicles(runs, energy, features, {"bus": {"bus_lifetime": 7, "battery_chemistry": "NMC"}})
    assert vehicles[0]["lifetime_bus"] == 16
    assert vehicles[0]["battery_chemistry"] == "LFP"
    assert vehicles[0]["battery_capacity_kwh"] == 400
    assert vehicles[0]["sources"]["lifetime_bus"] == "prediction_snapshot"


def test_representative_bus_uses_distance_weighted_consumption_and_equal_mileage():
    runs, energy, features = inputs()
    for scenario in energy["scenarios"][:2]:
        scenario.update(annual_distance_km=40000, annual_electric_kwh=72000)
    for scenario in energy["scenarios"][2:]:
        scenario.update(annual_distance_km=10000, annual_electric_kwh=8000)
    groups = representative_vehicles(build_vehicles(runs, energy, features, {}))
    assert len(groups) == 1
    assert groups[0]["vehicle_count"] == 2
    assert groups[0]["annual_km"] == 50000
    assert groups[0]["dc_kwh"] == 80000
    assert groups[0]["dc_kwh"] / groups[0]["annual_km"] == 1.6
    assert groups[0]["shift_ids"] == ["a", "b"]
    runs[-1].contextual_parameters["battery_capacity_kwh"] = 500
    runs[-2].contextual_parameters["battery_capacity_kwh"] = 500
    assert len(representative_vehicles(build_vehicles(runs, energy, features, {}))) == 2


def test_saved_comparator_and_group_mileage_are_shared_by_finance_and_lca():
    runs, energy, features = inputs()
    features["assessment_assumptions"] = {"tco": {"bodyLife": 15, "batteryLife": 9,
        "dieselLife": 11, "dieselConsumption": .6, "annualKmByShift": {"a": 40000, "b": 10000}}}
    vehicles = build_vehicles(runs, energy, features, {})
    assert [v["annual_km"] for v in vehicles] == [40000, 10000]
    assert [v["dc_kwh"] for v in vehicles] == [60000, 15000]
    assert all(v["lifetime_diesel_bus"] == 11 and v["diesel_consumption_l_per_km"] == .6 for v in vehicles)
    preview = build_vehicles(runs, energy, features, {}, {"annual_km_by_shift": {"a": 20000, "b": 30000}})
    assert [v["annual_km"] for v in preview] == [20000, 30000]
    group = representative_vehicles(preview)[0]
    assert group["annual_km"] == 25000
    assert group["dc_kwh"] == 37500
    with pytest.raises(LcaIncomplete, match="every selected"):
        build_vehicles(runs, energy, features, {}, {"annual_km_by_shift": {"a": 20000}})
    with pytest.raises(LcaIncomplete):
        build_vehicles(runs, energy, features, {}, {"annual_km_by_shift": {"a": -1, "b": 20000}})


@pytest.mark.asyncio
async def test_representative_lca_evaluates_once_and_scales_impacts(monkeypatch):
    runs, energy, features = inputs()
    groups = representative_vehicles(build_vehicles(runs, energy, features, {}))
    calls = []
    async def evaluate(vehicle, client):
        calls.append(vehicle)
        indicator = {"electricity_operation": 100, "diesel_heating": 0, "other_lifecycle": 200,
                     "total": 300, "diesel_comparator_total": 500,
                     **{key: dict.fromkeys(PHASES, 10) for key in ("phases", "diesel_comparator_phases", "diesel_heating_phases")}}
        return {**vehicle, "status": "complete", "indicators": {key: indicator for key in PAIRS[0]}}
    monkeypatch.setattr("app.services.parameterized_lca.evaluate_vehicle", evaluate)
    result = await evaluate_fleet(groups, SimpleNamespace(base_url="fixture"))
    assert len(calls) == 1
    assert result["representative_count"] == 1
    assert result["vehicle_count"] == 2
    assert result["annual_km"] == 40000
    assert result["energy_boundary"]["dc_kwh"] == 60000
    assert result["indicators"]["gwp100a"]["total"] == 600
    assert result["indicators"]["gwp100a"]["phases"]["vehicle"] == 20


@pytest.mark.asyncio
async def test_timeout_is_incomplete_not_fabricated_zero():
    def timeout(req):
        raise httpx.ReadTimeout("test timeout", request=req)
    client = MobitoolClient(catalog()["base_url"], transport=httpx.MockTransport(timeout))
    with pytest.raises(LcaIncomplete, match="timeout"):
        await client.impact("id", {})
