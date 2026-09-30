"""Versioned vehicle LCA on the DC boundary; no changes to energy predictions.

Mobitool zero/reference-consumption pairs separate energy supply from vehicle
inventories. Its native charger AND storage loss multipliers are neutralised;
ELETTRA uses 94% grid-to-DC efficiency and no separate storage-loss model.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

import httpx

from app.services.charging_energy import energy_boundary, grid_from_dc
from app.services.diesel_heating import diesel_heating_factor, diesel_heating_config

METHOD = "mobitool-parameterized-grid94-v1"
PHASES = ("direct", "directNonExhaust", "energyChain", "maintenance", "vehicle", "endOfLife", "infrastructure")
UNITS = {"gwp100a": "g CO2-eq", "nox": "mg NO2-eq", "pm10": "mg PM10", "primaryEnergy": "MJ", "primaryEnergyNonRenewable": "MJ"}
_CACHE: OrderedDict = OrderedDict()


class LcaIncomplete(ValueError):
    pass


@lru_cache(maxsize=1)
def catalog():
    return json.loads((Path(__file__).resolve().parents[2] / "config/lca_catalog.json").read_text())


@lru_cache(maxsize=1)
def economic_defaults():
    return json.loads((Path(__file__).resolve().parents[2] / "config/economic_defaults.json").read_text())


def number(value, name, *, positive=False):
    if value is None or isinstance(value, bool):
        raise LcaIncomplete(f"Missing numeric {name}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise LcaIncomplete(f"Invalid {name}") from exc
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise LcaIncomplete(f"Invalid {name}: must be finite and {'positive' if positive else 'nonnegative'}")
    return result


def impacts(raw):
    """Do not silently turn missing phases into zero or clip bookkeeping credits."""
    if not isinstance(raw, dict):
        raise LcaIncomplete("Mobitool returned a non-object impact payload")
    result = {}
    for indicator in UNITS:
        if not isinstance(raw.get(indicator), dict):
            raise LcaIncomplete(f"Mobitool returned incomplete {indicator}")
        result[indicator] = {}
        for phase in PHASES:
            value = raw.get(indicator, {}).get(phase)
            if not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value):
                raise LcaIncomplete(f"Mobitool returned incomplete {indicator}.{phase}")
            result[indicator][phase] = float(value)
    return result


def normalize_electric(zero, reference, *, dc_kwh, distance_km, native_multiplier):
    """Pairs are per vkm, reference consumption 100 kWh/100 km = 1 kWh/km.

    Primary energy: direct retains the API's DC-use convention. The energy
    chain is the adjusted operational total minus direct use, not a naive
    rescaling of energyChain alone (which already subtracts direct energy).
    """
    zero, reference = impacts(zero), impacts(reference)
    result = {}
    grid = grid_from_dc(dc_kwh)
    for indicator in UNITS:
        factor = 3.6 if indicator.startswith("primaryEnergy") else 1.0
        delta = {p: reference[indicator][p] - zero[indicator][p] for p in PHASES}
        if any(abs(delta[p]) > 1e-8 for p in PHASES if p not in ("direct", "energyChain")):
            raise LcaIncomplete("Mobitool consumption changes non-energy inventories")
        other = {p: zero[indicator][p] * distance_km * factor for p in PHASES}
        operation = dict.fromkeys(PHASES, 0.0)
        operational_total = sum(delta.values()) / native_multiplier * grid * factor
        operation["direct"] = delta["direct"] * dc_kwh * factor
        operation["energyChain"] = operational_total - operation["direct"]
        result[indicator] = {"electricity_operation": operation, "other_lifecycle": other}
    return result


class MobitoolClient:
    def __init__(self, base_url, transport=None):
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        if self.base_url != catalog()["base_url"]:
            raise LcaIncomplete("Configured Mobitool upstream does not match the frozen catalog")

    async def impact(self, vehicle_id, params):
        version = catalog()["data_version"]
        key = (self.base_url, version, vehicle_id, json.dumps(params, sort_keys=True))
        cached = _CACHE.get(key) if self.transport is None else None
        if cached and time.monotonic() - cached[0] < 600:
            _CACHE.move_to_end(key)
            return copy.deepcopy(cached[1])
        try:
            async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
                response = await client.get(f"{self.base_url}/vehicle/{vehicle_id}/impact", params=params)
            response.raise_for_status()
            if response.headers.get("lca-data-version") != version:
                raise LcaIncomplete("Mobitool data version mismatch")
            raw = response.json()
            impacts(raw)
        except (httpx.HTTPError, ValueError) as exc:
            raise LcaIncomplete(f"Mobitool unavailable or invalid: {exc}") from exc
        evidence = {"vehicle_id": vehicle_id, "parameters": params, "data_version": version,
                    "response_sha256": hashlib.sha256(response.content).hexdigest(), "response": raw}
        if self.transport is None:
            _CACHE[key] = (time.monotonic(), evidence)
            while len(_CACHE) > 512:
                _CACHE.popitem(last=False)
        return copy.deepcopy(evidence)


def build_vehicles(runs, energy, features, specs_by_model, overrides=None):
    """One vehicle per physical shift, never per weather scenario.

    Physical capacities and lengths require immutable prediction snapshots.
    Missing chemistry/lifetimes may use explicitly attributed current metadata;
    defaults are declared, not silently presented as vehicle measurements.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v is not None}
    saved = (features or {}).get("assessment_assumptions") or {}
    assumptions = {**saved, **overrides}
    features = features or {}
    scenarios = {str(s["prediction_run_id"]): s for s in energy["scenarios"]}
    if len(scenarios) != len(energy["scenarios"]):
        raise LcaIncomplete("Duplicate prediction run in the annual energy ledger")
    groups, seen = {}, set()
    def temperature(value):
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise LcaIncomplete("Missing weather scenario temperature") from exc
        if not math.isfinite(value):
            raise LcaIncomplete("Invalid weather scenario temperature")
        return round(value, 2)
    required_temps = {temperature(s.get("temperature")) for s in features.get("scenarios", [])}
    for run in runs:
        if run.shift_id is None:
            raise LcaIncomplete("Physical shift identity is missing")
        sid, temp = str(run.shift_id), temperature(run.external_temp_celsius)
        if (sid, temp) in seen:
            raise LcaIncomplete("Duplicate physical shift / weather scenario")
        seen.add((sid, temp))
        cp = run.contextual_parameters or {}
        length = number(cp.get("bus_length_m"), "snapshot bus length", positive=True)
        capacity = number(cp.get("battery_capacity_kwh"), "snapshot nominal battery capacity", positive=True)
        pax = number((cp.get("physical_mass") or {}).get("passenger_count"), "snapshot passenger count")
        specs = specs_by_model.get(str(run.bus_model_id), {})
        snapshot = cp.get("assessment_metadata") or {}
        def resolve(key, aliases, default):
            if key in assumptions:
                return assumptions[key], "assessment_assumptions"
            for source, data in (("prediction_snapshot", snapshot), ("current_model_metadata", specs)):
                for alias in aliases:
                    if data.get(alias) is not None:
                        return data[alias], source
            return default, "declared_default"
        chemistry, chem_source = resolve("battery_chemistry", ("battery_chemistry", "batteryChemistry"), "NMC")
        life, life_source = resolve("lifetime_bus", ("bus_lifetime", "bus_lifetime_years"), economic_defaults()["lifetime_bus"])
        batt_life, batt_source = resolve("lifetime_battery", ("battery_pack_lifetime", "battery_pack_lifetime_years"), economic_defaults()["lifetime_battery"])
        diesel_life, diesel_source = resolve("lifetime_diesel_bus", ("lifetime_diesel_bus",), economic_defaults()["lifetime_diesel_bus"])
        fixed = {"bus_model_id": str(run.bus_model_id), "bus_length_m": length, "battery_capacity_kwh": capacity,
                 "battery_chemistry": str(chemistry).upper(), "lifetime_bus": number(life, "bus lifetime", positive=True),
                 "lifetime_battery": number(batt_life, "battery lifetime", positive=True),
                 "lifetime_diesel_bus": number(diesel_life, "diesel lifetime", positive=True)}
        if sid not in groups:
            groups[sid] = {"shift_id": sid, **fixed, "annual_km": 0., "dc_kwh": 0., "diesel_heating_liters": 0.,
                           "pax_km": 0., "prediction_run_ids": [], "sources": {
                               "physical_parameters": "prediction_snapshot", "battery_chemistry": chem_source,
                               "lifetime_bus": life_source, "lifetime_battery": batt_source, "lifetime_diesel_bus": diesel_source}}
        g = groups[sid]
        if any(g[key] != value for key, value in fixed.items()):
            raise LcaIncomplete("Physical vehicle parameters differ between weather scenarios")
        if str(run.id) not in scenarios:
            raise LcaIncomplete("Missing energy ledger for a prediction run")
        s = scenarios[str(run.id)]
        km = number(s.get("annual_distance_km"), "annual distance")
        g["annual_km"] += km
        g["dc_kwh"] += number(s.get("annual_electric_kwh"), "annual DC energy")
        if run.auxiliary_heating_type == "diesel":
            g["diesel_heating_liters"] += number(s.get("annual_diesel_liters"), "diesel heater quantity")
        g["pax_km"] += pax * km
        g["prediction_run_ids"].append(str(run.id))
    total = sum(g["annual_km"] for g in groups.values())
    number(total, "total annual distance", positive=True)
    scale = number(assumptions.get("annual_km", total), "requested annual distance", positive=True) / total
    for sid, g in groups.items():
        if {t for shift, t in seen if shift == sid} != required_temps:
            raise LcaIncomplete("Missing weather scenario for a physical shift")
        number(g["annual_km"], "vehicle annual distance", positive=True)
        g["passengers"] = g.pop("pax_km") / g["annual_km"]
        for key in ("annual_km", "dc_kwh", "diesel_heating_liters"):
            g[key] *= scale
        g["electricity_mix"] = assumptions.get("electricity_mix", (features.get("config") or {}).get("electricity_mix", "CONSUMER_PHYSICAL"))
        g["sources"]["electricity_mix"] = "case" if "electricity_mix" in assumptions or "electricity_mix" in (features.get("config") or {}) else "declared_default"
        diesel_default = economic_defaults()["diesel_consumption_per_m"]*g["bus_length_m"]+economic_defaults()["diesel_consumption_const"]
        g["diesel_consumption_l_per_km"] = number(assumptions.get("diesel_consumption_l_per_km", diesel_default), "diesel consumption", positive=True)
        g["capacity_boundary"] = "installed pack capacity before SOC operating window and SOH"
        g["energy_boundary"] = energy_boundary(g["dc_kwh"])
    return list(groups.values())


def parameters(vehicle, metadata, *, diesel=False):
    km = vehicle["annual_km"]
    result = {"vkm": "true", "distance": 1, "kilometersPerYear": km,
              "lifetimeKilometers": km*vehicle["lifetime_diesel_bus" if diesel else "lifetime_bus"],
              "passengers": vehicle["passengers"]}
    if diesel:
        result["fuelConsumption"] = vehicle["diesel_consumption_l_per_km"]*100
        result["fuelBlend"] = metadata["fuelBlend"]["defaultValue"]
    else:
        chemistry = vehicle["battery_chemistry"]
        mix = vehicle["electricity_mix"]
        if chemistry not in metadata["batteryChemistry"]["allowedValues"] or mix not in metadata["electricityMix"]["allowedValues"]:
            raise LcaIncomplete("Unsupported battery chemistry or electricity mix")
        result.update(electricEnergyStored=vehicle["battery_capacity_kwh"], batteryChemistry=chemistry,
                      batteryLifetimeReplacements=max(math.ceil(vehicle["lifetime_bus"]/vehicle["lifetime_battery"])-1, 0),
                      electricityMix=mix)
    return result


def range_warnings(params, metadata):
    return [f"{key}={value} outside Mobitool documented range (not clipped)" for key, value in params.items()
            if isinstance(value, (int, float)) and isinstance(metadata.get(key), dict)
            and (value < metadata[key].get("minValue", -math.inf) or value > metadata[key].get("maxValue", math.inf))]


async def evaluate_vehicle(vehicle, client):
    length = vehicle["bus_length_m"]
    size = "9m" if length <= 10 else "13m" if length <= 14 else "18m"
    refs = catalog()["classes"][size]
    electric_id = refs["lto" if vehicle["battery_chemistry"] == "LTO" else "electric"]
    electric_meta = catalog()["vehicles"][electric_id]
    diesel_meta = catalog()["vehicles"][refs["diesel"]]
    ep, dp = parameters(vehicle, electric_meta), parameters(vehicle, diesel_meta, diesel=True)
    zero, ref, diesel = await asyncio.gather(
        client.impact(electric_id, {**ep, "electricityConsumption": 0}),
        client.impact(electric_id, {**ep, "electricityConsumption": 100}),
        client.impact(refs["diesel"], dp),
    )
    multiplier = (1+electric_meta["batteryChargeLoss"])*(1+electric_meta["chargerLoss"])
    electricity = normalize_electric(zero["response"], ref["response"], dc_kwh=vehicle["dc_kwh"],
                                     distance_km=vehicle["annual_km"], native_multiplier=multiplier)
    diesel_impacts = impacts(diesel["response"])
    indicators = {}
    for indicator, parts in electricity.items():
        heater = dict.fromkeys(PHASES, 0.0)
        for p in ("direct", "energyChain"):
            heater[p] = vehicle["diesel_heating_liters"]*diesel_heating_factor(indicator, p)
        phases = {p: parts["electricity_operation"][p]+parts["other_lifecycle"][p]+heater[p] for p in PHASES}
        factor = 3.6 if indicator.startswith("primaryEnergy") else 1
        comparator = {p: diesel_impacts[indicator][p]*vehicle["annual_km"]*factor for p in PHASES}
        indicators[indicator] = {"unit": UNITS[indicator], "electricity_operation": sum(parts["electricity_operation"].values()),
                                 "diesel_heating": sum(heater.values()), "other_lifecycle": sum(parts["other_lifecycle"].values()),
                                 "phases": phases, "total": sum(phases.values()), "diesel_comparator_phases": comparator,
                                 "diesel_comparator_total": sum(comparator.values()), "diesel_heating_phases": heater}
    actual_params = {**ep, "electricityConsumption": vehicle["dc_kwh"]/vehicle["annual_km"]*100}
    return {**vehicle, "reference_size": size, "status": "complete", "indicators": indicators,
            "warnings": range_warnings(actual_params, electric_meta)+range_warnings(dp, diesel_meta),
            "upstream_requests": [zero, ref, diesel], "native_loss_multiplier_removed": multiplier,
            "actual_dc_consumption_kwh_per_100km": actual_params["electricityConsumption"]}


async def evaluate_fleet(vehicles, client):
    if not vehicles:
        raise LcaIncomplete("No physical vehicles available for lifecycle assessment")
    # Bounded concurrency: at most two vehicles / six upstream requests.
    semaphore = asyncio.Semaphore(2)
    async def safe(vehicle):
        async with semaphore:
            try:
                return await evaluate_vehicle(vehicle, client)
            except LcaIncomplete as exc:
                return {**vehicle, "status": "incomplete", "reason": str(exc)}
    results = await asyncio.gather(*(safe(v) for v in vehicles))
    complete = all(v["status"] == "complete" for v in results)
    totals = {}
    if complete:
        for ind in UNITS:
            parts = [v["indicators"][ind] for v in results]
            totals[ind] = {"unit": UNITS[ind]}
            for key in ("electricity_operation", "diesel_heating", "other_lifecycle", "total", "diesel_comparator_total"):
                totals[ind][key] = sum(p[key] for p in parts)
            for key in ("phases", "diesel_comparator_phases", "diesel_heating_phases"):
                totals[ind][key] = {phase: sum(p[key][phase] for p in parts) for phase in PHASES}
            totals[ind]["saving"] = totals[ind]["diesel_comparator_total"]-totals[ind]["total"]
    return {"methodology_version": METHOD, "status": "complete" if complete else "incomplete", "vehicles": results,
            "vehicle_count": len(vehicles), "annual_km": sum(v["annual_km"] for v in vehicles),
            "energy_boundary": energy_boundary(sum(v["dc_kwh"] for v in vehicles)), "indicators": totals,
            "provenance": {"base_url": client.base_url, "data_version": catalog()["data_version"],
                           "diesel_heating_methodology": diesel_heating_config()["methodology_version"]},
            "limitations": ["Representative Mobitool inventories, not manufacturer-specific LCA.",
                            "Charging infrastructure is a generic inventory, not the engineered depot.",
                            "No preconditioning or separate internal battery-storage losses.",
                            *diesel_heating_config()["limitations"]]}
