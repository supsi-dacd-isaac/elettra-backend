"""Convex reserve preference for the exact-forecast, fixed-design solve."""
from pyomo.environ import ConstraintList, NonNegativeReals, Set, Var, value

POLICY_VERSION = "capacity-weighted-soc-reserve-v1"
SEGMENTS = 20


def apply_reserve_objective(model, buses, config, first_minute):
    # This objective must never influence battery or infrastructure sizing.
    if (config.fixed_battery_packs is None or config.fixed_station_slots is None
            or config.allow_excess_packs):
        raise ValueError("SOC reserve optimization requires an exact fixed design")
    if not all(v.fixed for v in model.n_packs.values()) or not all(
            v.fixed for v in model.install.values()):
        raise ValueError("Battery packs and installed slots must be fixed")
    if not all(v.fixed and value(v) == 0 for v in model.n_excess_packs.values()):
        raise ValueError("SOC reserve optimization cannot use diagnostic packs")
    if not any(value(v) > .5 for v in model.install.values()):
        return {"version": POLICY_VERSION, "status": "no_charging_opportunities"}
    # Count only each bus's own duty, not another bus's later finishing time.
    points = [(t, i) for i, bus in enumerate(buses)
              for t in model.T1
              if min(trip.departure_minute for trip in bus.trips) - first_minute <= t
              <= max(trip.arrival_minute for trip in bus.trips) - first_minute + 1]
    model.ReserveIndex = Set(dimen=2, initialize=points)
    model.reserve_penalty = Var(model.ReserveIndex, domain=NonNegativeReals)
    model.reserve_epigraph = ConstraintList()
    objective = 0
    for t, i in points:
        capacity = value(model.n_packs[i]) * buses[i].pack_size_kwh * config.state_of_health
        usable = capacity * (config.max_soc - config.min_soc)
        if usable <= 0:
            raise ValueError("SOC reserve optimization requires positive usable capacity")
        reserve = (model.soc[t, i] - capacity * config.min_soc) / usable
        for j in range(SEGMENTS):
            lo, hi = j / SEGMENTS, (j + 1) / SEGMENTS
            slope = ((1 - hi) ** 2 - (1 - lo) ** 2) / (hi - lo)
            model.reserve_epigraph.add(
                model.reserve_penalty[t, i] >= slope * (reserve - lo) + (1 - lo) ** 2)
        objective += usable * model.reserve_penalty[t, i] / 60
    model.obj.set_value(objective)
    return {"version": POLICY_VERSION, "status": "optimized", "segments": SEGMENTS,
            "priority": "lower_soc_soft_preference_subject_to_fleet_feasibility",
            "occupancy": "entire_dwell" if config.lock_entire_dwell else "configured_sessions",
            "design_fixed": True}
