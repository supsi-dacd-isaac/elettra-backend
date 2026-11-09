"""Owned HVAC audit exports. Large inputs are fetched only on explicit request."""
from app.models import HvacWeatherSnapshots
from app.services.hvac_policy import assumptions, fingerprint, run_policy


def compact(value):
    if isinstance(value, dict):
        return {key: compact(item) for key, item in value.items()
                if key not in {'hvac_inputs', 'hvac_terminal_intervals', 'cluster_dates'}}
    if isinstance(value, list):
        return [compact(item) for item in value]
    return value


async def export_runs(db, runs, *, include_inputs=False):
    result = {'schema_version': 1, 'runs': [], 'weather_inputs': {}}
    for run in runs:
        context = run.contextual_parameters or {}
        policy = run_policy(run)
        summary = run.summary or {}
        stack = getattr(run, 'prediction_stack', None) or 'legacy'
        resolved = summary.get('auxiliary_assumptions') or assumptions(policy)
        if stack == 'legacy':
            resolved = dict(policy_id=policy, estimator_kind='legacy-greybox', solar_used=False,
                            description='Recorded legacy auxiliary function; no operational GHI policy applied')
        elif not summary.get('auxiliary_assumptions'):
            recorded = summary.get('vecto_auxiliary') or {}
            resolved = {**resolved, **{key: recorded.get(key) for key in (
                'comfort_policy', 'release_id', 'release_sha256', 'ssm_source_sha256',
                'number_of_passengers', 'bus_length_m')}}
        item = dict(prediction_run_id=str(run.id), shift_id=str(run.shift_id),
                    prediction_stack=stack, model_release=getattr(run, 'model_name', None),
                    temperature_c=float(run.external_temp_celsius), occupancy_percent=float(run.occupancy_percent),
                    auxiliary_heating_type=run.auxiliary_heating_type,
                    input_fingerprint=context.get('hvac_input_fingerprint'),
                    assumptions=compact(resolved),
                    components={key: value for key, value in summary.items()
                                if key.startswith('total_') and any(part in key for part in ('hvac', 'fixed_auxiliary', 'diesel', 'uncovered'))})
        if include_inputs:
            item['inputs'] = context.get('hvac_inputs')
            item['terminal_intervals'] = context.get('hvac_terminal_intervals')
            sha = (item['inputs'] or {}).get('weather_snapshot_sha256')
            if sha and sha not in result['weather_inputs']:
                snapshot = await db.get(HvacWeatherSnapshots, sha)
                if snapshot is None or fingerprint(snapshot.payload) != sha:
                    raise ValueError('HVAC weather snapshot is missing or corrupt')
                result['weather_inputs'][sha] = snapshot.payload
        result['runs'].append(item)
    return result
