# Mean and aggregate-Q95 feasibility

New clients submit `consumption_basis: mean | aggregate_q95`. The default for
older clients is unchanged. `quantile_consumption: "0.95"` still means the
individual trip quantile; it cannot be combined with an explicit new basis.

The frontend now also submits `evaluate_both_bases: true`. One request validates
the inputs and atomically creates a Mean run and an aggregate-Q95 run, linked by
`input_params.basis_run_ids`. The response includes both IDs. Each has its own
design, full-mass verification, schedule, status and provenance. A single
background task calculates them sequentially and shares a request-local cache
of predictive distributions at matching masses. A failure of one basis does not
discard the other. The time budget is per basis. Entire-dwell occupancy is
required for paired requests.

The list shows one evaluation with separate basis summaries, hiding only the
paired comparison row. Selecting Mean/Q95 opens the corresponding complete run
without starting work, so economics and yearly analyses keep an unambiguous run
ID. Historical single-basis evaluations retain their original semantics.
Deleting either member deletes the pair atomically and detaches their yearly
analyses; deletion is rejected while either calculation is pending/running.

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

The fixed-design verification pass now optimizes charging reserve, without a
third solve or any change to selected battery packs or installed slots. It
minimizes `sum(dt * usable_capacity * (1 - usable_SOC_fraction)^2)` over each
bus's own duty including its terminal state. A convex 20-segment linear
epigraph keeps the problem MILP without extra binary variables. Low SOC has
greater marginal priority per kWh; this is a soft, horizon-wide preference
subordinate to feasibility for every bus, not a strict arrival-order dispatch
rule. Charger assignments, whole-dwell occupation, connection delays and shared
site power limits remain constraints. The reserve objective is skipped if no
charger is installed. Session outputs include `slot_number` and
`connected_without_charging_minutes`; `charging_schedule_policy` records the
method. SOC and diagnostics use this verified schedule directly. No new
simulation is needed to generate the schedule.

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
