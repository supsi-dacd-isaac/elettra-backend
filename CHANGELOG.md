# Changelog

## [1.6.0] - 2026-11-10

- Promote the functionality validated in `1.6.0-rc.2`, with no functional changes.
- Preserve operational HVAC policies and immutable weather inputs for feasibility and annual analysis, including authenticated reproduction exports.
- Make optimization relative MIP tolerance configurable with `ELETTRA_OPTIMIZATION_MIP_REL_GAP` (default `1e-6`) for sizing and fixed-design verification.
- Keep long fleet diagnostics and tolerance benchmarks opt-in; routine release checks do not run them.
- Require migration `db/migrations/010_hvac_weather_snapshots.sql` on installations without the HVAC snapshot table. Existing results are not rewritten.
- Keep the frozen VECTO core and published HVAC templates unchanged. Pair with frontend `0.11.0`.
