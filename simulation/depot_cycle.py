"""Periodic depot occupancy and energy closure, without replaying tomorrow's trips.

Driving retains the solver's minute balance. Depot power uses 15-minute
intervals split at every arrival/departure. Resource constraints also fold
daytime sessions modulo 24 h, including their overlap with the previous night.
"""
import logging
import math
import time
from collections import defaultdict

from pyomo.environ import (Binary, ConcreteModel, ConstraintList, NonNegativeReals,
                           Objective, Set, SolverFactory, Var, value)

DAY = 1440
PEAK_TIME_LIMIT_SECONDS = 5.0
logger = logging.getLogger(__name__)


def add_daily_cycle(m, buses, stations, config, arrays):
    if not config.lock_entire_dwell:
        raise ValueError('Daily cycles require entire-dwell occupancy')
    windows = {}
    edges = set(range(0, DAY + 1, 15))
    for b, bus in enumerate(buses):
        departure = min(t.departure_minute for t in bus.trips)
        arrival = max(t.arrival_minute for t in bus.trips)
        # Consumption at arrival is booked in [arrival, arrival+1).
        start, end = arrival + 1, departure + DAY
        if not departure <= arrival < end - 1:
            raise ValueError(f'{bus.shift_name}: the duty must leave time for a daily depot stay')
        if not 0 <= bus.depot_station_idx < len(stations):
            raise ValueError(f'{bus.shift_name}: configure the actual start/end depot for the daily cycle')
        windows[b] = (start, end, bus.depot_station_idx)
        edges.update((start % DAY, end % DAY))
    edges = sorted(edges)
    intervals = list(zip(edges, edges[1:]))
    active = {(j, b) for j, (lo, hi) in enumerate(intervals) for b, (start, end, _) in windows.items()
              if (lo - start) % DAY < end - start}
    minute_interval = {minute: j for j, (lo, hi) in enumerate(intervals) for minute in range(lo, hi)}
    assignments = [(b, k) for b, (_, _, s) in windows.items() for ss, k in m.InstallIndex if ss == s]
    m.DepotAssignIndex = Set(dimen=2, initialize=assignments)
    m.depot_assign = Var(m.DepotAssignIndex, domain=Binary)
    m.DepotPowerIndex = Set(dimen=2, initialize=sorted(active))
    m.depot_power = Var(m.DepotPowerIndex, domain=NonNegativeReals)
    m.depot_rules = ConstraintList()
    connected = {b: sum(m.depot_assign[bb, k] for bb, k in assignments if bb == b) for b in windows}
    power_limits = {}
    for b in windows:
        if any(bb == b for bb, _ in assignments):
            m.depot_rules.add(connected[b] <= 1)
        start, end, s = windows[b]
        if end - start < config.min_session_duration_minutes:
            for bb, k in assignments:
                if bb == b:
                    m.depot_assign[bb, k].fix(0)
        limit = min(buses[b].max_charging_power_kw, stations[s].max_power_per_slot_kw or buses[b].max_charging_power_kw)
        for j, bb in sorted(active):
            if bb == b:
                # Respect the existing connection buffer, rounded up to a depot interval.
                absolute_start = start + ((intervals[j][0] - start) % DAY)
                available = absolute_start >= start + config.session_connection_minutes
                power_limits[j, b] = limit if available else 0
                m.depot_rules.add(m.depot_power[j, b] <= power_limits[j, b] * connected[b])
        energy = sum(m.depot_power[j, bb] * (intervals[j][1] - intervals[j][0]) / 60
                     for j, bb in sorted(active) if bb == b)
        terminal = start - arrays['first_t']
        m.depot_rules.add(m.soc[terminal, b] + energy == m.soc[0, b])

    daytime = defaultdict(list)
    for t in m.T:
        for b in m.B:
            s = int(arrays['station_at_minute'][t, b])
            if s >= 0:
                daytime[(arrays['first_t'] + t) % DAY, s].append((t, b))
    loads, occupancies, daytime_loads = {}, {}, {}
    for minute in range(DAY):
        j = minute_interval[minute]
        for s, station in enumerate(stations):
            parked = [b for b, (_, _, site) in windows.items() if site == s and (j, b) in active]
            day = daytime.get((minute, s), [])
            if not parked and not day:
                continue
            daytime_loads[minute, s] = sum(m.power[t, b] for t, b in day)
            total = daytime_loads[minute, s] + sum(m.depot_power[j, b] for b in parked)
            loads[minute, s] = total
            occupancies[minute, s] = sum(m.connect[t, b] for t, b in day) + sum(connected[b] for b in parked)
            m.depot_rules.add(total <= station.max_total_power_kw)
            for ss, k in m.InstallIndex:
                if ss != s:
                    continue
                day_use = sum(m.assign[t, b, s, k] for t, b in day if (t, b, s, k) in m.AssignIndex)
                night_use = sum(m.depot_assign[b, k] for b in parked if (b, k) in m.DepotAssignIndex)
                m.depot_rules.add(day_use + night_use <= m.install[s, k])
    m.obj.set_value(m.obj.expr + config.session_penalty_weight * sum(m.depot_assign.values()))
    return {'windows': windows, 'intervals': intervals, 'active': active, 'loads': loads, 'occupancies': occupancies,
            'daytime_loads': daytime_loads, 'power_limits': power_limits,
            'site_limits': {s: station.max_total_power_kw for s, station in enumerate(stations)},
            'first_minute': arrays['first_t'], 'peak_status': 'not_requested'}


def shave_depot_peaks(m, solver, context, config):
    """Bounded refinement on a separate, small LP; retain the feasible fallback.

    Only depot powers can change. Hardware, daytime charging, SOC and plug
    assignments (including whole-stay occupancy) never enter this LP as variables.
    Optimality is optional; every accepted plan is independently checked.
    """
    started = time.monotonic()
    logger.info('Depot peak shaving: preparing independent interval LP (at most %.1fs solving)',
                PEAK_TIME_LIMIT_SECONDS)
    sites = sorted({s for _, _, s in context['windows'].values()})
    before = {s: max((value(load) for (_, ss), load in context['loads'].items() if ss == s), default=0) for s in sites}
    snapshot = {key: value(var) for key, var in m.depot_power.items()}
    success, quality = False, 'retained_feasible_plan'
    try:
        active = sorted(context['active'])
        intervals = context['intervals']
        windows = context['windows']
        connected = {b: sum(value(m.depot_assign[bb, k]) for bb, k in m.DepotAssignIndex if bb == b)
                     for b in windows}
        bounds = {key: context['power_limits'][key] * connected[key[1]] for key in active}
        targets = {b: value(m.soc[0, b]) - value(m.soc[start-context['first_minute'], b])
                   for b, (start, _, _) in windows.items()}
        by_bus, by_interval = defaultdict(list), defaultdict(list)
        for j, b in active:
            by_bus[b].append((j, b))
            by_interval[j, windows[b][2]].append((j, b))
        # A fixed daytime load can vary inside a depot interval. Its MAXIMUM,
        # not its average, gives an equivalent constraint for every minute.
        day_max = {(j, s): max((value(context['daytime_loads'].get((minute, s), 0))
                               for minute in range(lo, hi)), default=0)
                   for j, (lo, hi) in enumerate(intervals) for s in sites}
        lp = ConcreteModel()
        lp.indices = Set(dimen=2, initialize=active)
        lp.power = Var(lp.indices, domain=NonNegativeReals,
                       bounds=lambda _, j, b: (0, bounds[j, b]), initialize=snapshot)
        lp.sites = Set(initialize=sites)
        lp.peak = Var(lp.sites, domain=NonNegativeReals,
                      bounds=lambda _, s: (0, min(context['site_limits'][s], before[s])))
        lp.rules = ConstraintList()
        for b, keys in by_bus.items():
            lp.rules.add(sum(lp.power[j, bb] * (intervals[j][1]-intervals[j][0])/60
                             for j, bb in keys) == targets[b])
        for (j, s), day in day_max.items():
            lp.rules.add(day + sum(lp.power[key] for key in by_interval[j, s]) <= lp.peak[s])
        lp.obj = Objective(expr=sum(lp.peak.values()))
        # Do not reuse the persistent MILP solver: fixing tens of thousands of
        # its variables makes model synchronization much slower than this LP.
        key = config.solver_name.lower()
        timer = 'time_limit' if key == 'highs' else 'TimeLimit'
        limit = min(PEAK_TIME_LIMIT_SECONDS, float(solver.options[timer]) if timer in solver.options
                    else PEAK_TIME_LIMIT_SECONDS)
        remaining = limit - (time.monotonic()-started)
        if remaining <= 0:
            raise TimeoutError('Depot peak refinement budget exhausted during preparation')
        refinement = SolverFactory(key)
        refinement.options[timer] = remaining
        if config.solver_threads is not None:
            refinement.options['threads' if key == 'highs' else 'Threads'] = config.solver_threads
        context['peak_model_size'] = {'variables': len(active)+len(sites), 'constraints': len(lp.rules)}
        logger.info('Depot peak shaving: solving %d variables / %d constraints, %.2fs budget',
                    len(active)+len(sites), len(lp.rules), remaining)
        solved = refinement.solve(lp, tee=False, load_solutions=False)
        if not len(solved.solution):
            raise ValueError(f'No peak refinement solution: {solved.solver.termination_condition}')
        lp.solutions.load_from(solved)
        candidate = {key: value(var) for key, var in lp.power.items()}
        tolerance = 1e-5
        if any(not math.isfinite(p) or p < -tolerance or p > bounds[key]+tolerance for key, p in candidate.items()):
            raise ValueError('Peak refinement violates charger power bounds')
        candidate = {key: max(0., p) for key, p in candidate.items()}
        for b, keys in by_bus.items():
            energy = sum(candidate[j, bb]*(intervals[j][1]-intervals[j][0])/60 for j, bb in keys)
            if abs(energy-targets[b]) > tolerance:
                raise ValueError('Peak refinement does not restore next-departure energy')
        for (j, s), day in day_max.items():
            load = day + sum(candidate[key] for key in by_interval[j, s])
            if load > min(context['site_limits'][s], before[s])+tolerance:
                raise ValueError('Peak refinement exceeds the site limit or previous peak')
        # Apply only after all depots pass validation. A timeout or invalid
        # candidate leaves the original verified schedule intact.
        for key, power in candidate.items():
            m.depot_power[key].set_value(power)
        success = True
        quality = 'optimal' if str(solved.solver.termination_condition) == 'optimal' else 'feasible'
    except Exception:
        logger.warning('Depot peak shaving retained the original feasible schedule', exc_info=True)
        for key, power in snapshot.items():
            m.depot_power[key].set_value(power)
    context.update(peak_status='optimized' if success else 'unavailable',
                   peak_solution_quality=quality,
                   peak_before=before, peak_elapsed_seconds=time.monotonic() - started)
    logger.info('Depot peak shaving finished: %s, %.3fs', quality, context['peak_elapsed_seconds'])


def daily_cycle_report(m, buses, stations, config, context, summaries, utilization):
    per_shift, station_energy = {}, defaultdict(float)
    intervals = context['intervals']
    for b, bus in enumerate(buses):
        start, end, s = context['windows'][b]
        series = []
        for j, bb in sorted(context['active']):
            if bb != b:
                continue
            lo, hi = intervals[j]
            absolute = start + (lo - start) % DAY
            power = max(0, value(m.depot_power[j, b]))
            series.append({'start_minute': absolute, 'end_minute': absolute + hi - lo,
                           'power_kw_dc': power, 'energy_kwh': power * (hi - lo) / 60})
        series.sort(key=lambda row: row['start_minute'])
        energy = sum(row['energy_kwh'] for row in series)
        station_energy[s] += energy
        final = value(m.soc[start-context['first_minute'], b])
        target = value(m.soc[0, b])
        row = next(row for row in summaries if row['shift_id'] == bus.shift_id)
        per_shift[bus.shift_id] = {
            'depot_stop_id': stations[s].stop_id, 'depot_name': stations[s].stop_name,
            'depot_arrival_minute': start - 1, 'charging_start_minute': start,
            'next_departure_minute': end, 'target_soc_kwh': target,
            'departure_soc_kwh': final + energy, 'ready': abs(final + energy - target) < 1e-4 and value(m.n_excess_packs[b]) < .5,
            'during_shift_kwh': row['total_charged_kwh'], 'outside_shift_kwh': energy,
            'total_charged_kwh': row['total_charged_kwh'] + energy,
            'slot_number': next((k+1 for bb, k in m.DepotAssignIndex if bb == b and value(m.depot_assign[bb, k]) > .5), None),
            'charging_intervals': series,
        }
    sites = {}
    depot_sites = {s for _, _, s in context['windows'].values()}
    for s, station in enumerate(stations):
        day = utilization[station.stop_id]['total_energy_kwh']
        night = station_energy[s]
        sites[station.stop_id] = {'stop_name': station.stop_name, 'is_depot': s in depot_sites,
            'during_shift_kwh': day, 'outside_shift_kwh': night, 'total_charged_kwh': day + night,
            'peak_connected_buses': round(max((value(load) for (_, ss), load in context['occupancies'].items() if ss == s), default=0)),
            'available_power_kw_dc': station.max_total_power_kw}
        if s in depot_sites and context['peak_status'] == 'optimized':
            sites[station.stop_id].update(
                simulated_peak_kw_dc=max((value(load) for (_, ss), load in context['loads'].items() if ss == s), default=0),
                previous_plan_peak_kw_dc=context['peak_before'][s])
    return {'version': 'daily-depot-cycle-v1', 'period_minutes': DAY,
            'status': 'verified' if not config.allow_excess_packs and all(row['ready'] for row in per_shift.values()) else 'unverified',
            'scope': 'selected_shifts_repeated_daily_same_bus_same_depot',
            'occupancy': 'entire_depot_stay', 'power_boundary': 'bus_dc',
            'peak_shaving': {'status': context['peak_status'], 'scope': 'depot_charging_only_fixed_daytime_plan',
                             'method': 'bounded_independent_interval_lp',
                             'solution_quality': context.get('peak_solution_quality'),
                             'model_size': context.get('peak_model_size'),
                             'elapsed_seconds': context.get('peak_elapsed_seconds', 0)},
            'per_shift': per_shift, 'stations': sites}
