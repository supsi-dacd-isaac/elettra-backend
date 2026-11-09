"""Versioned operational HVAC assumptions, outside the frozen VECTO core.

The solar input is all-sky GHI. Clouding=1 is implemented by cancelling the
upstream clouding multiplier *after* selecting its climatic row on physical GHI.
No module globals or published declaration bytes are changed.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import math
from zoneinfo import ZoneInfo

import numpy as np

from elettra_core.vecto_ssm import (VECTO_DEFAULT_COMFORT_POLICY, vecto_auxiliary_power,
                                   default_environmental_condition)
from elettra_core.vecto_templates import (
    load_template_release,
)

LEGACY_POLICY = 'legacy-fixed-100-v1'
FEASIBILITY_POLICY = 'feasibility-ghi-temperature-cap10-v1'
ANNUAL_POLICY = 'annual-pvgis-local-cap10-v1'
POLICIES = (LEGACY_POLICY, FEASIBILITY_POLICY, ANNUAL_POLICY)
METHOD_VERSION = 'hvac-operational-v1'
ENERGY_KEYS = ('hvac_electrical_kwh', 'diesel_fuel_kwh', 'uncovered_thermal_kwh')


def fingerprint(value) -> str:
    # PostgreSQL JSONB canonicalizes negative zero. Hash the same numerical
    # representation before insertion and after retrieval, including metadata.
    def canonical(item):
        if isinstance(item, dict):
            return {key: canonical(part) for key, part in item.items()}
        if isinstance(item, (list, tuple)):
            return [canonical(part) for part in item]
        return 0.0 if isinstance(item, float) and item == 0 else item
    return hashlib.sha256(json.dumps(canonical(value), sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def feasibility_ghi(temperature: float) -> float:
    t = float(temperature)
    if not math.isfinite(t):
        raise ValueError('HVAC temperature must be finite')
    return float(np.interp(t, [10., 17., 25.], [50., 800., 1000.]))


def resolve_policy(requested, *, annual=False, legacy_stack=False) -> str:
    value = requested or (LEGACY_POLICY if legacy_stack else ANNUAL_POLICY if annual else FEASIBILITY_POLICY)
    if value not in POLICIES:
        raise ValueError('Unknown auxiliary_policy_id')
    if legacy_stack and value != LEGACY_POLICY:
        raise ValueError('Operational HVAC policies require a VECTO prediction stack')
    if value == ANNUAL_POLICY and not annual:
        raise ValueError('PVGIS HVAC policy requires a yearly analysis')
    if value == FEASIBILITY_POLICY and annual:
        raise ValueError('Yearly analysis requires PVGIS or the explicit historical HVAC policy')
    return value


def run_policy(run) -> str:
    return ((getattr(run, 'contextual_parameters', None) or {}).get('auxiliary_policy_id')
            or LEGACY_POLICY)


def assumptions(policy_id: str) -> dict:
    if policy_id not in POLICIES:
        raise ValueError('Unknown auxiliary_policy_id')
    legacy = policy_id == LEGACY_POLICY
    return dict(
        method_version=METHOD_VERSION, policy_id=policy_id,
        solar_clouding='original-0.65-below-17-otherwise-0.80' if legacy else 1.0,
        solar_fixed_multiplier=0.25, glazing_factor=0.95,
        solar_interpretation='inherited-fixed-input' if legacy else 'global-horizontal-all-sky',
        solar_geometry='inherited-SSM-window-factor; no route-orientation transposition',
        temperature_mode='constant-scenario-temperature',
        feasibility_ghi_knots=([{'temperature_c': t, 'ghi_wm2': g}
                               for t, g in [(10, 50), (17, 800), (25, 1000)]]
                              if policy_id == FEASIBILITY_POLICY else None),
        feasibility_interpolation='linear; clamp outside knots; one GHI per temperature',
        feasibility_interpretation='engineering-stress-assumption; not a weather percentile',
        terminal_hvac=dict(max_minutes=0 if legacy else 10, placement='before-next-departure',
                           passengers=0, same_stop_only=True, depots_excluded=True,
                           fixed_auxiliaries_included=False),
        fixed_auxiliary_scope='scheduled-trips-only',
        thermal_dynamics='steady-state; no cabin inertia or door-opening model',
        annual_time_method='GTFS local noon minus 12 elapsed hours; agency timezone and DST',
        annual_integration='linear GHI interpolation at PVGIS observation times; minute midpoints',
        annual_recurrence='saved temperature-cluster occurrences; representative timetable',
        lookup_ghi_step_wm2=1., lookup_transition_method='direct VECTO evaluation',
    )


def gtfs_minute(value: str) -> float:
    try:
        h, m, s = map(int, value.split(':'))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError('Invalid GTFS time for HVAC') from exc
    if h < 0 or not 0 <= m < 60 or not 0 <= s < 60:
        raise ValueError('Invalid GTFS time for HVAC')
    return h * 60 + m + s / 60


def validate_schedule(schedule: list[dict]) -> None:
    if not schedule:
        raise ValueError('HVAC requires a complete timetable')
    for item in schedule:
        start, end = item['start_minute'], item['end_minute']
        if not all(math.isfinite(v) for v in [start, end]) or not 0 <= start < end <= 4320:
            raise ValueError('HVAC timetable requires positive trips within three service days')
        ZoneInfo(item['timezone'])
    if len({r['timezone'] for r in schedule}) != 1:
        raise ValueError('HVAC duty must use one agency timezone')
    if len({r['sequence_number'] for r in schedule}) != len(schedule):
        raise ValueError('HVAC timetable has duplicate occurrence sequences')
    for previous, following in zip(schedule, schedule[1:]):
        if previous['end_minute'] > following['start_minute']:
            raise ValueError('HVAC timetable contains overlapping or unordered trips')


def terminal_intervals(schedule: list[dict], cap=10.) -> list[dict]:
    validate_schedule(schedule)
    result = []
    for previous, following in zip(schedule, schedule[1:]):
        gap = following['start_minute'] - previous['end_minute']
        if (gap <= 0 or cap <= 0 or previous['end_stop_id'] != following['start_stop_id']
                or previous.get('end_is_depot') or following.get('start_is_depot')):
            continue
        result.append(dict(start_minute=following['start_minute'] - min(gap, cap),
                           end_minute=following['start_minute'], stop_id=previous['end_stop_id'],
                           next_sequence_number=following['sequence_number'], gap_minutes=gap))
    return result


@lru_cache(maxsize=64)
def nearest_climatic_rows(temperature):
    """Cache the frozen selector's first (temperature) tie-break once per T."""
    rows = tuple(default_environmental_condition(index) for index in range(1, 12))
    distance = min(abs(row.temperature_celsius - temperature) for row in rows)
    return tuple(row for row in rows if abs(row.temperature_celsius - temperature) == distance)


class SolarResponse:
    """Small one-watt response table shared by all duties with identical inputs."""
    def __init__(self, length, passengers, temperature, heating_type, clouding_one=True, *, release=None):
        self.template = (release or load_template_release()).template_for_bus_length(length)
        self.inputs = self.template.ssm_inputs(number_of_passengers=passengers,
                                               auxiliary_heating_type=heating_type)
        self.temperature = float(temperature)
        if not math.isfinite(self.temperature):
            raise ValueError('HVAC temperature must be finite')
        self._climatic_rows = nearest_climatic_rows(self.temperature)
        self.clouding_one = clouding_one
        self._table = np.empty((0, 3))
        self._signatures = []

    def direct(self, ghi):
        physical_ghi = float(ghi)
        if not math.isfinite(physical_ghi) or physical_ghi < 0:
            raise ValueError('HVAC GHI must be finite and non-negative')
        # Same lexicographic selector as the frozen template adapter. Avoid
        # constructing/validating eleven identical rows at every grid point.
        selected = min(self._climatic_rows, key=lambda row: (
            abs(row.solar_irradiance_wm2 - physical_ghi), row.environmental_id))
        original = .65 if self.temperature < 17 else .8
        environment = replace(selected, temperature_celsius=self.temperature,
            solar_irradiance_wm2=physical_ghi / original if self.clouding_one else physical_ghi)
        result = vecto_auxiliary_power(environment=environment, inputs=self.inputs)
        values = np.array([result.p_hvac_electrical_kw, result.p_fuel_kw,
                           result.unmet_thermal_demand_kw])
        return values, (environment.environmental_id, result.mode)

    def values(self, ghi):
        data = np.asarray(ghi, dtype=float)
        if not np.isfinite(data).all() or (data < 0).any():
            raise ValueError('HVAC GHI must be finite and non-negative')
        if not data.size:
            return np.empty((*data.shape, 3))
        stop = int(np.ceil(data.max())) + 2
        if stop > len(self._table):
            extra = [self.direct(g) for g in range(len(self._table), stop)]
            self._table = np.concatenate([self._table, np.array([r[0] for r in extra])])
            self._signatures.extend(r[1] for r in extra)
        flat = data.ravel()
        lo = np.floor(flat).astype(int)
        fraction = flat - lo
        output = self._table[lo] * (1 - fraction[:, None]) + self._table[lo + 1] * fraction[:, None]
        # Never interpolate through discontinuities (ventilation or COP/row).
        transitions = np.array([a != b for a, b in zip(self._signatures, self._signatures[1:])])
        exact = transitions[lo] & (fraction != 0)
        if exact.any():
            unique, inverse = np.unique(flat[exact], return_inverse=True)
            output[exact] = np.array([self.direct(g)[0] for g in unique])[inverse]
        return output.reshape((*data.shape, 3))


@lru_cache(maxsize=1)
def declaration_metadata():
    release = load_template_release()
    return dict(release_id=release.release_id, release_sha256=release.content_sha256,
                ssm_source_sha256=release.ssm_source_sha256)


def service_origins_utc(dates, zone):
    tz = ZoneInfo(zone)
    return [(datetime.fromisoformat(date).replace(hour=12, tzinfo=tz).astimezone(timezone.utc)
             - timedelta(hours=12)).isoformat() for date in dates]


def annual_ghi(snapshot: dict, dates: list[str], zone: str, last_minute: int, *, saved_origins=None) -> np.ndarray:
    """PVGIS observations on a synthetic UTC year -> local service-minute GHI."""
    times = [datetime.fromisoformat(t) for t in snapshot['time_utc']]
    if len(times) != 8760 or any(t.utcoffset() != timedelta(0) for t in times):
        raise ValueError('Annual HVAC requires 8760 UTC weather observations')
    if any(b - a != timedelta(hours=1) for a, b in zip(times, times[1:])):
        raise ValueError('Annual HVAC weather timestamps must be consecutive hourly observations')
    if (times[0] != datetime(times[0].year, 1, 1, tzinfo=timezone.utc)
            or times[-1] + timedelta(hours=1) != datetime(times[0].year + 1, 1, 1, tzinfo=timezone.utc)):
        raise ValueError('Annual HVAC weather must cover one complete non-leap UTC year')
    ghi = np.asarray(snapshot['ghi'], dtype=float)
    if ghi.shape != (8760,) or not np.isfinite(ghi).all() or (ghi < 0).any():
        raise ValueError('Annual HVAC requires complete finite non-negative GHI')
    offset = float(snapshot['irradiance_time_offset_hours'])
    if not math.isfinite(offset) or not -1 <= offset <= 1:
        raise ValueError('Invalid PVGIS irradiance time offset')
    origins = []
    if not dates or len(dates) != len(set(dates)):
        raise ValueError('Annual HVAC requires distinct source dates for the scenario')
    resolved_origins = saved_origins if saved_origins is not None else service_origins_utc(dates, zone)
    if len(resolved_origins) != len(dates):
        raise ValueError('HVAC service origins must cover every cluster date')
    for value, origin in zip(dates, resolved_origins, strict=True):
        date = datetime.fromisoformat(value).date()
        if date.year != times[0].year:
            raise ValueError('HVAC cluster dates and weather year differ')
        start = datetime.fromisoformat(origin)
        if start.utcoffset() != timedelta(0) or abs((start.date() - date).days) > 1:
            raise ValueError('Invalid saved UTC service origin')
        origins.append((start - times[0]).total_seconds())
    positions = np.array(origins)[:, None] + (np.arange(last_minute) + .5)[None, :] * 60
    # A TMY repeats cyclically; extended GTFS trips can cross its year boundary.
    positions = (positions - offset * 3600) % (8760 * 3600)
    return np.interp(positions, np.arange(8761) * 3600, np.r_[ghi, ghi[0]])


def integral(prefix: np.ndarray, start: float, end: float):
    """Energy from cumulative minute-midpoint power, including partial minutes."""
    def at(value):
        index = int(math.floor(value))
        if index == len(prefix) - 1:
            return prefix[index]
        return prefix[index] + (prefix[index + 1] - prefix[index]) * (value - index)
    return at(end) - at(start)


def calculate_operational_hvac(*, policy_id, schedule, length, passengers, temperature,
                               heating_type, weather=None, dates=None, cache=None,
                               weather_sha256=None, saved_origins=None):
    if policy_id not in (FEASIBILITY_POLICY, ANNUAL_POLICY):
        raise ValueError('Operational HVAC requires an explicit operational policy')
    validate_schedule(schedule)
    cache = cache if cache is not None else {}
    last = max(int(math.ceil(max(r['end_minute'] for r in schedule))), cache.get('hvac_last_minute', 0))
    zone = schedule[0]['timezone']
    # The service validates the immutable archive once when populating its
    # request cache. Avoid hashing the same 8760 records for every duty/mass.
    weather_key = weather_sha256 or (fingerprint(weather) if weather is not None else None)
    if policy_id == ANNUAL_POLICY:
        key = ('ghi', weather_key, tuple(dates or []), tuple(saved_origins or []), zone, last)
        if key not in cache:
            cache[key] = annual_ghi(weather, dates, zone, last, saved_origins=saved_origins)
        ghi = cache[key]
    else:
        ghi = np.full((1, last), feasibility_ghi(temperature))
    prefixes = {}
    if 'vecto_template_release' not in cache:
        cache['vecto_template_release'] = load_template_release()
    for count in dict.fromkeys([float(passengers), 0.]):
        response_key = ('response', length, count, temperature, heating_type)
        if response_key not in cache:
            cache[response_key] = SolarResponse(length, count, temperature, heating_type,
                                               release=cache['vecto_template_release'])
        key = ('power', response_key, policy_id, weather_key, tuple(dates or []), tuple(saved_origins or []), zone, last)
        if key not in cache:
            if policy_id == FEASIBILITY_POLICY:
                power = np.tile(cache[response_key].direct(ghi[0, 0])[0], (last, 1))
            else:
                power = cache[response_key].values(ghi).mean(axis=0)
            cache[key] = np.vstack([np.zeros(3), np.cumsum(power / 60, axis=0)])
        prefixes[count] = cache[key]
    trips = np.array([integral(prefixes[float(passengers)], r['start_minute'], r['end_minute'])
                      for r in schedule])
    terminals = []
    for stop in terminal_intervals(schedule):
        start, end = stop['start_minute'], stop['end_minute']
        energy = integral(prefixes[0.], start, end)
        # Each value belongs to [minute, minute+1), same as solver charging.
        minute_energy = []
        for minute in range(math.floor(start), math.ceil(end)):
            part = integral(prefixes[0.], max(start, minute), min(end, minute + 1))
            minute_energy.append(dict(minute=minute, electrical_kwh=float(part[0])))
        terminals.append({**stop, **dict(zip(ENERGY_KEYS, map(float, energy))),
                          'minute_energy': minute_energy})
    terminal_totals = {key: sum(r[key] for r in terminals) for key in ENERGY_KEYS}
    ghi_prefix = np.r_[0., np.cumsum(ghi.mean(axis=0))]
    trip_minutes = sum(r['end_minute'] - r['start_minute'] for r in schedule)
    mean_ghi = sum(integral(ghi_prefix, r['start_minute'], r['end_minute']) for r in schedule) / trip_minutes
    metadata = {**assumptions(policy_id), **declaration_metadata(),
                'temperature_c': float(temperature), 'passengers_in_trips': float(passengers),
                'comfort_policy': asdict(VECTO_DEFAULT_COMFORT_POLICY),
                'agency_timezone': zone, 'trip_minutes': trip_minutes,
                'terminal_hvac_minutes': sum(r['end_minute'] - r['start_minute'] for r in terminals),
                'ghi_trip_weighted_mean_wm2': float(mean_ghi),
                'ghi_reference_wm2': feasibility_ghi(temperature) if policy_id == FEASIBILITY_POLICY else None,
                'weather_snapshot_sha256': weather_key, 'cluster_dates': dates,
                'cluster_occurrences': len(dates) if dates else None,
                'typical_year': int(dates[0][:4]) if dates else None,
                'schedule_sha256': fingerprint(schedule),
                'terminal_totals': terminal_totals}
    return trips, terminals, metadata
