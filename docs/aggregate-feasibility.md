# Mean and aggregate-Q95 feasibility

New clients submit `consumption_basis: mean | aggregate_q95`. Normal requests
default to calculating both designs, including requests from older browser
bundles that omit `evaluate_both_bases`. Explicit `false` retains single-basis
behaviour for historical comparison flows. Legacy per-trip quantiles or partial
dwell occupancy retain their single-basis default. `quantile_consumption:
"0.95"` still means the individual trip quantile; it cannot be combined with an
explicit new basis.

The frontend now also submits `evaluate_both_bases: true`. One request validates
the inputs and atomically creates a Mean run and an aggregate-Q95 run, linked by
`input_params.basis_run_ids`. The response includes both IDs. Each has its own
design, full-mass verification, schedule, status and provenance. A single
background task calculates them in independent bounded worker processes, each with a local cache
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

## Parallel execution resources

Mean and Q95 run in separate Python processes with separate database sessions,
model validation and prediction caches. The response still contains both run IDs;
completion of one basis does not cancel or replace the other. Proven infeasibility
is a completed result, distinct from a technical failure.

`ELETTRA_OPTIMIZATION_WORKERS` limits concurrent optimization processes per API
process (default 2). Additional evaluations queue. Set
`ELETTRA_OPTIMIZATION_THREADS_PER_WORKER` to cap HiGHS/Gurobi and numerical-library
threads per worker (default 2). Restart the API after changing these settings.
For Elettra's 32 physical cores, a starting configuration is 2 workers × 16 threads
with one API process; benchmark under real concurrent load before increasing it.
Multiple API replicas multiply this budget. Logical threads are not physical cores.
The same solver cap applies to verification, charging priority and peak shaving.

Each result records worker PID, thread limit and start/end timestamps in
`results.execution`, allowing actual overlap to be checked. Fresh processes have
model initialization overhead and intentionally do not share mutable in-memory
caches. A crash marks only its run as failed; cancellation reaps the child process.

## Infeasible daily-cycle diagnostics

A proven infeasible daily cycle retains its infeasible status. Within the remaining
run budget (up to 60 seconds), a separate diagnostic sizes and verifies the same
vehicle blocks with only next-departure energy closure removed. In-service charging,
whole-dwell plug occupation, the selected optimization mode and physical battery
limits remain unchanged. Virtual excess packs remain explicitly unverified.

`results.infeasibility_diagnostics` holds this separate battery result, forecast
verification and reason codes. Never substitute it for the full evaluation's
hardware, costs, SOC schedule or paired feasibility. For charger-only optimization,
the diagnostic battery is the configured fixed size, not a new minimum size.

Overnight diagnostics compare energy needed by the diagnostic daytime design with
an optimistic bound using exclusive charger access and the available dwell time.
A positive shortfall proves the configured power is insufficient for that design;
shared plug conflicts are also reported with whole-stay occupation. These are not
an overnight schedule or globally optimized infrastructure upgrades. If no single
limiting resource can be isolated, the UI identifies the daily-cycle constraints
without inventing a cause. Failed or timed-out diagnostics never change the original
result and never manufacture a battery requirement.

## Infrastructure and battery investment inputs

New requests store a `cost_model` on each station: point cost (CHF), fixed common
site cost (CHF), connection coefficient (CHF/kW AC), AC/DC efficiency and source.
For an active site, investment is `n * point_cost + site_fixed_cost +
connection_cost_per_kw * configured_site_dc_power / efficiency`; a site with no
installed points costs zero. Common costs are charged once, on the first slot;
the solver's ordered-slot constraint prevents skipping that slot. The configured
site capacity is retained even if fewer points are selected. Peak shaving does
not retroactively reduce connection investment.

Local UI defaults (editable in Advanced costs, collapsed initially):

| Site | DC power/point | CHF/point | Fixed CHF/site | CHF/kW AC |
| --- | ---: | ---: | ---: | ---: |
| Depot | 150 kW | 110,000 | 250,000 | 250 |
| Terminus / pantograph | 450 kW | 430,000 | 320,000 | 320 |

Efficiency defaults to 95%. These are modeling assumptions calibrated to the
total-infrastructure ranges in [INFRAS 2026](https://www.infras.ch/media/filer_public/79/e5/79e599d9-0dae-491e-ac2b-19f07385834a/4166a_technologiestudie_elektrobusse_vov_schlussbericht.pdf#page=49),
using the fixed-plus-power structure of [Grauers et al. 2020](https://research.chalmers.se/publication/518986/file/518986_Fulltext.pdf#page=10).
They are not three separately published INFRAS prices. Calibration assumes ten
150 kW points per depot (about 174.5 kCHF/point including shared costs) and one
450 kW pantograph (about 901.6 kCHF total); ten depot points is our reference
assumption, not a rule applied to the actual configuration.

All three modes report investment. Charging-only and joint include the complete
site investment in their objective; battery-only reports the fixed infrastructure
investment without using it to size batteries. Results persist `cost_breakdown`
per station for display and annual analysis. Annual CAPEX separates charging
points from site connection/common infrastructure, using their respective
lifetimes. Historical results without cost inputs retain their prior semantics;
they must be recalculated to obtain this breakdown.

Joint sizing uses each bus model's `battery_pack_cost_chf` rather than a UI CHF/kWh
override. The solver's incremental battery objective and the full selected
battery investment are stored separately. `selected_battery_cost_chf` is used by
linked annual analysis. The old request-level CHF/kWh field remains only as a
fallback for legacy clients whose bus models lack a pack price.

The General result lists every selected vehicle block (one bus per block), its
physical selected packs and nominal capacity, bus price excluding the battery,
full battery investment and complete vehicle investment, with fleet totals.
New results snapshot the model ID/name, body price and pack price so a subsequent
model edit does not change historical investment. Legacy shared-model results can
use current model prices with an explicit disclosure; unavailable prices remain
unknown instead of producing a misleading partial total. Diagnostic excess packs
are never included in installed investment.

Battery-only and joint requests offer `per_bus` (independent sizes) or `uniform`
(one pack count across every selected vehicle block, also across routes). Uniform
sizing requires equal pack capacities and adds equality constraints inside the
solver, including forecast verification; it is not a post-processing maximum.
Charging-only keeps the user-selected fixed battery size. The API continues to
accept `per_route` for legacy clients, but new UI requests no longer offer it.
