# Mean and aggregate-Q95 feasibility

New clients submit `consumption_basis: mean | aggregate_q95`. The default for
older clients is unchanged. `quantile_consumption: "0.95"` still means the
individual trip quantile; it cannot be combined with an explicit new basis.

The aggregate method samples independent occurrences from the complete
conditional predictive distribution. Repeated trip IDs get separate draws.
The equal-weight QRF adapter requires `max_samples_leaf=1`, discovers the actual
forest size, and verifies that recovered support reproduces the model mean.
Unsupported distributions fail explicitly for Q95. A verified mean design can
remain available with its aggregate diagnostics marked unavailable.

Defaults are 100,000 samples, maximum 500,000, seed 20261029, and 0.25 kWh
Monte Carlo half-width. Sampling doubles within the bound when precision is
insufficient; without charging candidates it also checks whether the Q95
interval straddles a usable-pack threshold. The Q95 profile is the conditional
occurrence mean between total-energy percentiles 94.5–95.5, reconciled to the
total Q95 using occurrence variances. This is not a simultaneous SOC envelope.

One design optimization uses the requested profile. Battery-only fixes the
infrastructure, charging-only fixes the battery, and joint sizes both. Every
selected shift and shared charger remains in the problem. Full predictions and
Q95 profiles are recalculated at the selected physical mass before fixed-design
verification. Failed verification reanchors within the existing bounded loop.
Diagnostic excess packs never constitute a verified installable configuration.

The results JSON stores `consumption_analysis`, per-occurrence profiles, seeds,
model/mass/features provenance, sampling precision, and fixed-plan diagnostics.
`per_bus_summary.energy_timeline` stores unrounded minute charge and verified SOC.
SOH scales usable capacity, while physical pack mass and nominal capacity retain
their original meanings. No database migration is required.

Mean and representative-Q95 capacity requirements replay the *same* final plan,
curtailing charging at maximum SOC without adding or moving sessions. Required
usable capacity is maximum energy drawdown at that mass and plan, not another
optimized battery size. An independent validation sample also estimates the
fraction of complete shift trajectories respecting SOC constraints. Its seed,
Wilson Monte Carlo interval and assumptions are stored. This per-shift estimate
does not certify fleet reliability or the independence assumption.

`GET /api/v1/simulation/optimization-runs/{id}/basis/{basis}` checks for an owned
compatible comparison without starting work. The corresponding POST explicitly
starts one evaluation or reuses an existing one, validating all original private
references through the normal creation endpoint. Cached comparisons belong to
the same source evaluation and sampling-method version. Historic rows are never
rewritten or silently relabelled.

Regression tests cover independent repeated occurrences, precision refinement,
the real QRF adapter, SOH, charging timing/curtailment, the three modes, shared
chargers, full mass verification, service caching, legacy requests and privacy.
The offline 40502 fixture reproduces 13/14 packs without intermediate charging
and 10/10 with a 450 kW pantograph (mean/Q95 respectively).
