"""Periodic depot occupancy and energy closure, without replaying tomorrow's trips.

Driving retains the solver's minute balance. Depot power uses 15-minute
intervals split at every arrival/departure. Resource constraints also fold
daytime sessions modulo 24 h, including their overlap with the previous night.
"""
import time
from collections import defaultdict

from pyomo.environ import Binary, ConstraintList, NonNegativeReals, Set, Var, value

DAY = 1440


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
                m.depot_rules.add(m.depot_power[j, b] <= (limit if available else 0) * connected[b])
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
    loads, occupancies = {}, {}
    for minute in range(DAY):
        j = minute_interval[minute]
        for s, station in enumerate(stations):
            parked = [b for b, (_, _, site) in windows.items() if site == s and (j, b) in active]
            day = daytime.get((minute, s), [])
            if not parked and not day:
                continue
            total = sum(m.power[t, b] for t, b in day) + sum(m.depot_power[j, b] for b in parked)
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
            'first_minute': arrays['first_t'], 'peak_status': 'not_requested'}


def shave_depot_peaks(m, solver, context, config):
    """LP refinement: freeze hardware, all daytime variables and plug assignment."""
    started = time.monotonic()
    sites = sorted({s for _, _, s in context['windows'].values()})
    before = {s: max((value(load) for (_, ss), load in context['loads'].items() if ss == s), default=0) for s in sites}
    snapshot = [(var, value(var), var.fixed) for var in m.component_data_objects(Var)]
    original_objective = m.obj.expr
    for var, current, _ in snapshot:
        if var.parent_component() is not m.depot_power:
            var.fix(current)
    m.DepotPeakSites = Set(initialize=sites)
    m.depot_peak = Var(m.DepotPeakSites, domain=NonNegativeReals)
    m.depot_peak_rules = ConstraintList()
    for (minute, s), load in context['loads'].items():
        if s in sites:
            m.depot_peak_rules.add(load <= m.depot_peak[s])
    m.obj.set_value(sum(m.depot_peak.values()))
    try:
        solved = solver.solve(m, tee=False)
        success = str(solved.solver.termination_condition) == 'optimal'
    except Exception:
        success = False
    if not success:
        for var, current, _ in snapshot:
            var.set_value(current)
    for var, _, fixed in snapshot:
        if not fixed:
            var.unfix()
    m.obj.set_value(original_objective)
    context.update(peak_status='optimized' if success else 'unavailable',
                   peak_before=before, peak_elapsed_seconds=time.monotonic() - started)


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
                             'elapsed_seconds': context.get('peak_elapsed_seconds', 0)},
            'per_shift': per_shift, 'stations': sites}
