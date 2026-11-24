"""Prediction intervals for one representative service day, conditional on its weather."""
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from simulation.aggregate_consumption import (
    AggregateOptions, METHOD_VERSION, PredictiveDistributionUnavailable,
    empirical_qrf_support, sample_shift_totals, shift_seed,
)


@dataclass
class DutySupport:
    run_id: str
    shift_id: str
    support: np.ndarray
    terminal_kwh: float


def scenario_intervals(duties, options=AggregateOptions()):
    """Sample individual trip occurrences, including across duties in the fleet view."""
    duties = sorted(duties, key=lambda duty: duty.shift_id)
    if not duties or len({d.shift_id for d in duties}) != len(duties):
        raise PredictiveDistributionUnavailable("A scenario must contain each physical duty exactly once")

    def interval(support, terminal, seed_key):
        seed = shift_seed(options.seed, seed_key)
        totals = sample_shift_totals(support, options=options, seed=seed)
        bounds = np.quantile(totals, [.05, .50, .95]) + terminal
        return dict(zip(('q05', 'q50', 'q95'), map(float, bounds), strict=True)), len(totals), seed

    daily = {d.run_id: interval(d.support, d.terminal_kwh, d.shift_id) for d in duties}
    if len(duties) == 1:
        fleet, fleet_samples, fleet_seed = daily[duties[0].run_id]
    else:
        fleet, fleet_samples, fleet_seed = interval(
            np.concatenate([d.support for d in duties]), sum(d.terminal_kwh for d in duties),
            'fleet:' + ','.join(d.shift_id for d in duties))
    ids = [d.run_id for d in duties]
    return {rid: dict(method=METHOD_VERSION, daily_kwh=q, scenario_daily_kwh=fleet,
                      scenario_prediction_run_ids=ids, samples=n, seed=seed,
                      scenario_samples=fleet_samples, scenario_seed=fleet_seed)
            for rid, (q, n, seed) in daily.items()}


def add_yearly_intervals(prepared, results):
    from app.services.prediction import combined_prediction_inputs
    scenarios = defaultdict(list)
    contracts = defaultdict(list)
    for item, result in zip(prepared, results, strict=True):
        contracts[(item.aux_fn is not None, item.qrf_reference_mass is not None)].append((item, result))
    for group in contracts.values():
        items = [item for item, _ in group]
        frames, features, arguments = combined_prediction_inputs(items)
        support, means, _ = empirical_qrf_support(items[0].predictor, features, **arguments)
        offset = 0
        for (item, result), frame in zip(group, frames, strict=True):
            end = offset + len(frame)
            expected = [row['prediction_kwh'] for row in result['predictions']]
            if not np.allclose(means[offset:end], expected, atol=1e-6, rtol=1e-7):
                raise PredictiveDistributionUnavailable("Daily samples do not reproduce the trip forecasts")
            run = item.run
            key = (run.contextual_parameters or {}).get('yearly_scenario_id', run.external_temp_celsius)
            terminal = sum(row['hvac_electrical_kwh'] for row in item.terminal_intervals)
            scenarios[key].append(DutySupport(str(run.id), str(run.shift_id), support[offset:end], terminal))
            offset = end
    intervals = {}
    for duties in scenarios.values():
        intervals.update(scenario_intervals(duties))
    for item, result in zip(prepared, results, strict=True):
        result['summary']['energy_uncertainty'] = intervals[str(item.run.id)]


def scenario_members(analysis, runs, prediction_run_id):
    """Bind historical requests to their saved scenario, only fall back to temperature if unambiguous."""
    by_id = {str(run.id): run for run in runs}
    target = by_id.get(str(prediction_run_id))
    if target is None:
        return []
    features = analysis.features or {}
    rows = (features.get('results') or {}).get('scenarioResults') or []
    matched = []
    for row in rows:
        ids = row.get('predRunIds') or ([row['predRunId']] if row.get('predRunId') else [])
        ids = [str(rid) for rid in ids]
        if str(target.id) in ids:
            matched.append(ids)
    if len(matched) == 1:
        ids = matched[0]
        if any(rid not in by_id for rid in ids) or len(set(ids)) != len(ids):
            raise PredictiveDistributionUnavailable("Incomplete prediction references for this scenario")
        members = [by_id[rid] for rid in ids]
    elif not matched:
        key = (target.contextual_parameters or {}).get('yearly_scenario_id')
        if key is not None:
            members = [r for r in runs if (r.contextual_parameters or {}).get('yearly_scenario_id') == key]
        else:
            temperature = float(target.external_temp_celsius)
            candidates = [s for s in features.get('scenarios', []) if s.get('temperature') == temperature]
            if len(candidates) != 1:
                raise PredictiveDistributionUnavailable("The historical scenario cannot be uniquely identified")
            members = [r for r in runs if r.external_temp_celsius == target.external_temp_celsius]
    else:
        raise PredictiveDistributionUnavailable("Ambiguous prediction references")
    shift_ids = [str(run.shift_id) for run in members]
    expected = (features.get('config', {}).get('fixedConfiguration') or {}).get('shiftIds')
    if (len(set(shift_ids)) != len(shift_ids) or (expected and set(map(str, expected)) != set(shift_ids))
            or any(run.status != 'completed' or run.external_temp_celsius != target.external_temp_celsius for run in members)):
        raise PredictiveDistributionUnavailable("Incomplete or ambiguous physical duties for the scenario")
    return members
