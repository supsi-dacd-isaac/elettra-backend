"""Capture owned timetable/weather inputs before inference; replay saved inputs."""
from __future__ import annotations

from collections import defaultdict
import math
from uuid import UUID

import numpy as np
from sklearn.cluster import KMeans
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models import (Depots, GtfsAgencies, GtfsRoutes, GtfsStopsTimes, GtfsTrips,
                        HvacWeatherSnapshots, ShiftsStructures, WeatherTemperatureSeries,
                        WeatherMeasurements, YearlyAnalysis)
from app.services.hvac_policy import (ANNUAL_POLICY, LEGACY_POLICY, fingerprint,
                                     gtfs_minute, run_policy, validate_schedule, service_origins_utc, assumptions)


async def load_schedules(db, owner, shift_ids):
    """One schedule query for the complete batch, including repeated trips."""
    depots = set((await db.execute(select(Depots.stop_id).where(Depots.user_id == owner))).scalars().all())
    rows = (await db.execute(select(
        ShiftsStructures.shift_id, ShiftsStructures.sequence_number, ShiftsStructures.trip_id,
        GtfsStopsTimes.arrival_time, GtfsStopsTimes.departure_time, GtfsStopsTimes.stop_id,
        GtfsAgencies.agency_timezone,
    ).join(GtfsStopsTimes, GtfsStopsTimes.trip_id == ShiftsStructures.trip_id)
     .join(GtfsTrips, GtfsTrips.id == ShiftsStructures.trip_id)
     .join(GtfsRoutes, GtfsRoutes.id == GtfsTrips.route_id)
     .join(GtfsAgencies, GtfsAgencies.id == GtfsRoutes.agency_id)
     .where(ShiftsStructures.shift_id.in_(shift_ids))
     .order_by(ShiftsStructures.shift_id, ShiftsStructures.sequence_number, GtfsStopsTimes.stop_sequence))).all()
    occurrences = defaultdict(list)
    for r in rows:
        occurrences[(str(r.shift_id), r.sequence_number)].append(r)
    schedules = defaultdict(list)
    for (sid, sequence), stops in occurrences.items():
        first, last = stops[0], stops[-1]
        schedules[sid].append(dict(trip_id=str(first.trip_id), sequence_number=sequence,
            start_minute=gtfs_minute(first.arrival_time), end_minute=gtfs_minute(last.departure_time),
            start_stop_id=str(first.stop_id), end_stop_id=str(last.stop_id),
            start_is_depot=first.stop_id in depots, end_is_depot=last.stop_id in depots,
            timezone=first.agency_timezone))
    for sid in map(str, shift_ids):
        validate_schedule(schedules[sid])
    return dict(schedules)


def cluster_calendar(payload, scenarios, start_time='05:00', end_time='24:00'):
    """Reproduce the saved temperature clustering, never nearest rounded means."""
    start, end = gtfs_minute(start_time + ':00'), gtfs_minute(end_time + ':00')
    hours = np.arange(24) * 60
    mask = (hours >= start) & (hours < end) if start < end else (hours >= start) | (hours < end)
    temperatures = np.asarray(payload['temperature_c'], dtype=float)
    if temperatures.shape != (8760,) or not np.isfinite(temperatures).all() or not mask.any():
        raise ValueError('Annual HVAC needs complete temperatures and a valid cluster window')
    daily = temperatures.reshape(365, 24)[:, mask].mean(axis=1)
    k = len(scenarios)
    if not 1 <= k <= 365:
        raise ValueError('Annual HVAC needs temperature scenarios')
    model = KMeans(n_clusters=k, random_state=42, n_init=10).fit(daily[:, None])
    order = np.argsort(model.cluster_centers_.ravel())
    sorted_scenarios = sorted(scenarios, key=lambda s: float(s['temperature']))
    dates = [t[:10] for t in payload['time_utc'][::24]]
    result = {}
    for scenario, cluster in zip(sorted_scenarios, order, strict=True):
        temperature = float(scenario['temperature'])
        indexes = np.flatnonzero(model.labels_ == cluster)
        if abs(float(model.cluster_centers_[cluster, 0]) - temperature) > .055:
            raise ValueError('HVAC scenario temperature does not match the saved weather clustering')
        if int(scenario['occurrences']) != len(indexes):
            raise ValueError('HVAC scenario occurrences do not match weather cluster membership')
        result[temperature] = [dates[i] for i in indexes]
    return result


async def capture_weather(db, analysis):
    features = analysis.features or {}
    ref = (features.get('config') or {}).get('weather_reference') or {}
    sid = analysis.weather_temperature_series_id or ref.get('weather_temperature_series_id')
    series = await db.get(WeatherTemperatureSeries, UUID(str(sid))) if sid else None
    if series is None:
        raise ValueError('Annual HVAC requires a bound PVGIS weather series')
    if series.status != 'applied':
        raise ValueError('Cannot bind mutable weather rows to an inactive weather series; use a saved snapshot')
    records = list((await db.execute(select(WeatherMeasurements).where(
        WeatherMeasurements.latitude == series.latitude,
        WeatherMeasurements.longitude == series.longitude,
    ).order_by(WeatherMeasurements.time_utc))).scalars().all())
    metadata = series.pvgis_metadata or {}
    offset = (metadata.get('location') or {}).get('irradiance_time_offset')
    if offset is None:
        raise ValueError('PVGIS irradiance time offset is required for annual HVAC')
    payload = dict(schema_version=1, weather_series_id=str(series.id),
        latitude=float(series.latitude), longitude=float(series.longitude),
        provider=series.provider, processing_version=series.processing_version,
        pvgis_metadata=metadata, pvgis_months_selected=series.pvgis_months_selected,
        irradiance_time_offset_hours=float(offset),
        time_utc=[r.time_utc.isoformat() for r in records],
        temperature_c=[float(r.temp_air) if r.temp_air is not None else None for r in records],
        ghi=[float(r.ghi) if r.ghi is not None else None for r in records],
        provenance=('coherent-temperature-and-solar-selected-months-v2' if series.processing_version == 'elettra-hybrid-temperature-solar-v2'
                    else 'snapshot-of-effective-stored-values; historical GHI source years are not inferred'))
    if len(records) != 8760 or any(v is None or not math.isfinite(v) for v in payload['ghi']) or any(v < 0 or v > 2000 for v in payload['ghi']):
        raise ValueError('Annual HVAC requires 8760 valid GHI values between 0 and 2000 W/m2')
    sha = fingerprint(payload)
    await db.execute(insert(HvacWeatherSnapshots).values(sha256=sha, payload=payload)
                     .on_conflict_do_nothing(index_elements=['sha256']))
    calendar = cluster_calendar(payload, features.get('scenarios') or [],
        analysis.weather_cluster_start_time or '05:00', analysis.weather_cluster_end_time or '24:00')
    return sha, payload, calendar


async def prepare_hvac_contexts(db, runs, cache):
    """Persist once before CPU work; completed runs retain their original inputs."""
    active = [r for r in runs if run_policy(r) != LEGACY_POLICY]
    missing = [r for r in active if not (r.contextual_parameters or {}).get('hvac_inputs')]
    if missing:
        if len({r.user_id for r in missing}) != 1:
            raise ValueError('HVAC input capture requires one owner')
        shifts = list(dict.fromkeys(r.shift_id for r in missing))
        schedules = await load_schedules(db, missing[0].user_id, shifts)
        for run in missing:
            if run.status == 'completed':
                raise ValueError('Completed operational HVAC prediction is missing its input snapshot')
            inputs = dict(schedule=schedules[str(run.shift_id)], policy_id=run_policy(run))
            if run_policy(run) == ANNUAL_POLICY:
                key = ('weather-analysis', str(run.yearly_analysis_id))
                if key not in cache:
                    analysis = await db.get(YearlyAnalysis, run.yearly_analysis_id)
                    if analysis is None:
                        raise ValueError('Annual HVAC analysis no longer exists')
                    cache[key] = await capture_weather(db, analysis)
                    cache[('weather-window', str(run.yearly_analysis_id))] = (
                        analysis.weather_cluster_start_time or '05:00', analysis.weather_cluster_end_time or '24:00')
                sha, payload, calendar = cache[key]
                cluster_start, cluster_end = cache[('weather-window', str(run.yearly_analysis_id))]
                temp = float(run.external_temp_celsius)
                matches = [t for t in calendar if abs(t - temp) < 1e-6]
                if len(matches) != 1:
                    raise ValueError('Annual HVAC scenario is not present in the saved analysis')
                inputs.update(weather_snapshot_sha256=sha, cluster_dates=calendar[matches[0]])
                inputs['service_origins_utc'] = service_origins_utc(inputs['cluster_dates'], inputs['schedule'][0]['timezone'])
                inputs['temperature_clustering'] = dict(method='KMeans', random_state=42, n_init=10,
                    k=len(calendar), temperature_c=temp, occurrences=len(inputs['cluster_dates']),
                    start_time=cluster_start, end_time=cluster_end, window_timezone='UTC')
                cache[('weather', sha)] = payload
            context = dict(run.contextual_parameters or {})
            context.update(hvac_inputs=inputs, hvac_input_fingerprint=fingerprint(inputs))
            context['auxiliary_policy'] = assumptions(run_policy(run))
            run.contextual_parameters = context
    for run in active:
        if run.status != 'completed':
            run.contextual_parameters = {**run.contextual_parameters, 'auxiliary_policy': assumptions(run_policy(run))}
        inputs = run.contextual_parameters['hvac_inputs']
        if inputs.get('policy_id') != run_policy(run):
            raise ValueError('HVAC saved policy does not match prediction policy')
        if fingerprint(inputs) != run.contextual_parameters.get('hvac_input_fingerprint'):
            raise ValueError('HVAC input fingerprint mismatch')
        cache['hvac_last_minute'] = max(cache.get('hvac_last_minute', 0),
                                      math.ceil(max(r['end_minute'] for r in inputs['schedule'])))
        sha = inputs.get('weather_snapshot_sha256')
        if sha and ('weather', sha) not in cache:
            snapshot = await db.get(HvacWeatherSnapshots, sha)
            if snapshot is None or fingerprint(snapshot.payload) != sha:
                raise ValueError('HVAC weather snapshot is missing or has an invalid checksum')
            cache[('weather', sha)] = snapshot.payload
    return cache
