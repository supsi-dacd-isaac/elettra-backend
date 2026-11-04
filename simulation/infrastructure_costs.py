"""Infrastructure CAPEX shared by sizing and result reporting (all CHF, kW AC)."""

def infrastructure_cost_breakdown(model, dc_power_kw, slots):
    count = int(slots)
    active = count > 0
    grid_kw = float(dc_power_kw) / float(model["ac_dc_efficiency"]) if active else 0.0
    chargers = count * float(model["point_cost_chf"])
    fixed = float(model["site_fixed_cost_chf"]) if active else 0.0
    power = grid_kw * float(model["connection_cost_per_kw_chf"])
    return {
        "station_type": model["station_type"],
        "source": model.get("source", "custom"),
        "point_unit_cost_chf": float(model["point_cost_chf"]),
        "chargers_cost_chf": chargers,
        "site_fixed_cost_chf": fixed,
        "site_power_cost_chf": power,
        "grid_connection_power_kw_ac": grid_kw,
        "connection_cost_per_kw_chf": float(model["connection_cost_per_kw_chf"]),
        "ac_dc_efficiency": float(model["ac_dc_efficiency"]),
        "total_cost_chf": chargers + fixed + power,
    }


def station_slot_costs(station, mode):
    if station.cost_model is not None:
        count = station.num_fixed_slots or 0
        costs = [float(station.cost_model["point_cost_chf"])] * count
        if costs:
            parts = infrastructure_cost_breakdown(station.cost_model, station.max_total_power_kw, count)
            costs[0] += parts["site_fixed_cost_chf"] + parts["site_power_cost_chf"]
        return costs
    if mode == "battery_only" and station.num_fixed_slots is not None:
        return [0.0] * station.num_fixed_slots  # Historical fixed infrastructure has no cost inputs.
    return list(station.slot_costs_chf or [])
