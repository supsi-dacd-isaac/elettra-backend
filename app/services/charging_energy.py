"""Reporting boundary only; never imported by the DC optimizer or predictors."""
import math

GRID_TO_BUS_EFFICIENCY = 0.94
CHARGING_POLICY = "grid-to-bus-dc-94-v1"


def grid_from_dc(value: float) -> float:
    """Convert a nonnegative DC quantity (kWh or kW) to its grid equivalent.

    The same fixed infrastructure efficiency applies to depot and opportunity
    charging. No extra battery-storage efficiency or preconditioning is added.
    Do not call on individual regenerative trip energies or on AC inputs.
    """
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError("DC energy/power must be finite and nonnegative")
    return value / GRID_TO_BUS_EFFICIENCY


def energy_boundary(dc_kwh: float) -> dict:
    grid = grid_from_dc(dc_kwh)
    return {
        "policy": CHARGING_POLICY,
        "dc_kwh": float(dc_kwh),
        "grid_kwh": grid,
        "charging_losses_kwh": grid - float(dc_kwh),
        "grid_to_bus_efficiency": GRID_TO_BUS_EFFICIENCY,
        "preconditioning_included": False,
        "separate_battery_storage_losses_included": False,
    }


def connection_estimate(dc_kw: float, *, supplied_ac_kw: float | None = None) -> dict:
    """A downstream estimate, not a new constraint on the DC optimizer."""
    dc_kw = float(dc_kw)
    inferred = grid_from_dc(dc_kw)
    ac_kw = inferred if supplied_ac_kw is None else float(supplied_ac_kw)
    if not math.isfinite(ac_kw) or ac_kw < 0:
        raise ValueError("AC connection power must be finite and nonnegative")
    return {"policy": CHARGING_POLICY, "dc_power_kw": dc_kw, "ac_connection_power_kw": ac_kw,
            "source": "dc_divided_by_efficiency" if supplied_ac_kw is None else "supplied_ac",
            "ac_limit_verified_by_optimizer": False}


def station_connections(input_stations: list, installed: dict) -> list:
    """One connection per station, not per slot, weather scenario or bus."""
    rows, seen = [], set()
    for station in input_stations:
        sid = str(station["stop_id"])
        if sid in seen:
            raise ValueError("Duplicate charging station identity")
        seen.add(sid)
        slots = (installed.get(sid) or {}).get("num_slots", station.get("num_slots"))
        if slots is None:
            raise ValueError("Missing installed station slot count")
        if slots <= 0:
            continue
        dc = station["max_total_power_kw"]
        if station.get("max_power_per_slot_kw") is not None:
            dc = min(dc, slots * station["max_power_per_slot_kw"])
        rows.append({"stop_id": sid, **connection_estimate(dc,
            supplied_ac_kw=station.get("grid_connection_power_kw_ac"))})
    return rows
