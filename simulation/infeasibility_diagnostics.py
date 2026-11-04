"""Explain a failed daily cycle without presenting a relaxed plan as feasible."""
from __future__ import annotations

import logging
from dataclasses import replace

from simulation.optimization_verification import optimize_and_verify

logger = logging.getLogger(__name__)


def depot_recharge_limits(buses, stations, config, result, *, verified=True):
    """Optimistic individual recharge bounds on the diagnostic daytime design.

    These bounds ignore competing buses and interval rounding: a positive deficit
    therefore proves that even exclusive use of the configured power is too small
    for this design. They are not a new, feasible overnight charging schedule.
    """
    summaries = {row['shift_id']: row for row in result.per_bus_summary}
    rows, reasons = {}, []
    for bus in buses:
        battery = result.battery_results[bus.shift_id]
        if battery['excess_packs']:
            reasons.append({'code': 'battery_limit' if battery['physical_limit_exceeded'] else 'fixed_battery',
                'shift_id': bus.shift_id, 'shift_name': bus.shift_name,
                'required_packs': battery['required_total_packs'],
                'available_packs': battery['max_physical_packs'] if battery['physical_limit_exceeded'] else battery['optimized_packs']})
        # Virtual overflow SOC is not an exact physical design. Never quantify
        # its overnight shortfall as a verified energy requirement.
        if not verified or battery['excess_packs'] or not 0 <= bus.depot_station_idx < len(stations):
            continue
        station = stations[bus.depot_station_idx]
        timeline = summaries[bus.shift_id]['energy_timeline']
        start = max(t.arrival_minute for t in bus.trips) + 1
        end = min(t.departure_minute for t in bus.trips) + 1440
        target = timeline['soc_kwh'][0]
        terminal = timeline['soc_kwh'][start - timeline['first_minute']]
        needed = max(0.0, target - terminal)
        slots = (station.num_fixed_slots or 0) if config.mode == 'battery_only' else len(station.slot_costs_chf)
        hours = max(0, end - start - config.session_connection_minutes) / 60
        if end - start < config.min_session_duration_minutes:
            hours = 0
        power = min(station.max_total_power_kw, station.max_power_per_slot_kw or bus.max_charging_power_kw,
                    bus.max_charging_power_kw) if slots > 0 else 0
        maximum = hours * power
        row = {'shift_id': bus.shift_id, 'shift_name': bus.shift_name,
               'stop_id': station.stop_id, 'stop_name': station.stop_name,
               'required_recharge_kwh': needed, 'maximum_recharge_kwh': maximum,
               'minimum_shortfall_kwh': max(0, needed - maximum),
               'available_power_kw_dc': power, 'available_hours': hours,
               'minimum_average_power_kw_dc': needed / hours if hours > 0 else None,
               'available_plugs': slots, 'arrival_minute': start - 1,
               'next_departure_minute': end}
        rows[bus.shift_id] = row
        if needed > 1e-4 and row['minimum_shortfall_kwh'] > 1e-4:
            code = 'depot_no_plug' if slots == 0 else 'depot_time' if hours == 0 else 'depot_power'
            reasons.append({'code': code, **row})
    # A connected bus occupies its plug for the whole depot stay. Report a
    # conflict on this design even when each bus individually has enough power.
    for station in stations:
        parked = [r for r in rows.values() if r['stop_id'] == station.stop_id and r['required_recharge_kwh'] > 1e-4]
        if not parked:
            continue
        peak = max(sum((minute - (r['arrival_minute']+1)) % 1440 < r['next_departure_minute']-r['arrival_minute']-1
                       for r in parked) for minute in range(1440))
        if peak > parked[0]['available_plugs']:
            reasons.append({'code': 'depot_plugs', 'stop_id': station.stop_id,
                'stop_name': station.stop_name, 'required_plugs': peak,
                'available_plugs': parked[0]['available_plugs'],
                'shift_ids': [r['shift_id'] for r in parked]})
    return rows, reasons


async def diagnose_infeasible_cycle(buses, stations, config, load_exact, *, time_budget_seconds):
    diagnostic = {'version': 'infeasibility-diagnostics-v1', 'status': 'unavailable',
                  'scope': 'vehicle_blocks_without_next_departure_recharge',
                  'battery_results': {}, 'reasons': []}
    if not config.daily_cycle or time_budget_seconds <= 0:
        diagnostic['reason'] = 'not_daily_cycle' if not config.daily_cycle else 'time_budget_exhausted'
        return diagnostic
    try:
        # Preserve trips, in-service chargers, occupancy and battery rules.
        # Relax only the requirement to restore the initial SOC for tomorrow.
        daytime, verification = await optimize_and_verify(
            buses, stations, replace(config, daily_cycle=False, depot_peak_shaving=False,
                                     depot_dwell_minutes_after=0), load_exact,
            time_budget_seconds=min(60, time_budget_seconds))
        diagnostic['forecast_verification'] = verification
        if verification['status'] not in {'verified', 'physical_limits_exceeded'}:
            diagnostic['reason'] = verification['status']
            return diagnostic
        per_shift, reasons = depot_recharge_limits(buses, stations, config, daytime,
            verified=verification['status'] == 'verified')
        if not reasons:
            reasons = [{'code': 'depot_constraints'}]
        diagnostic.update(status='available', battery_results=daytime.battery_results,
                          per_shift=per_shift, reasons=reasons)
    except Exception:
        # Diagnostic failure must not overwrite the original proven infeasibility.
        logger.exception('Could not diagnose infeasible daily cycle')
        diagnostic['reason'] = 'diagnostic_failed'
    return diagnostic
