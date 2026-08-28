# Shared feature extraction contract

`elettra_core` is the only implementation of schedule/elevation feature
extraction used by `elettra-backend` and the offline `elettra` trainer. It is a
small package in this repository and intentionally depends only on NumPy and
pandas.

## Distance semantics

- `cumulative_distance_m` remains the planimetric GTFS-shape chainage stored in
  profile Parquet files. Existing objects and manifests are not rewritten.
- `cumulative_horizontal_distance_m` makes that meaning explicit.
- `cumulative_distance_3d_m` is derived from horizontal increments and
  `altitude_m` using `hypot(horizontal_increment, altitude_increment)`.
- Physical distance and speed features use the 3-D distance. Road gradients
  use rise over horizontal run.
- Local gradient samples require a horizontal run of at least 1 metre
  (`MIN_GRADE_RUN_M`). This prevents submetric sampling residuals and DTM
  quantization from creating extreme local slopes. Every positive run above
  the numerical tolerance still contributes to 3-D distance, ascent, descent,
  and net elevation change; segment mean gradient remains net rise over the
  segment's complete horizontal run.
- A repeated coordinate contributes zero distance even if its altitude differs;
  this prevents a profile stitch or vertical-data discontinuity being counted
  as vehicle travel.

Short stop-to-stop segments are valid observations. The former implicit filter
at 200 m was removed. Only boundaries explicitly identified by differing
`trip_index` values are excluded. Consecutive equal stop IDs are resolved from
the profile: a positive-distance `A→A` is a real loop, while a zero-distance
interval is classified as dwell and excluded from road-segment distributions.

## Sequence time semantics

Sequence duration is the sum of each trip group's internal duration plus only
non-negative layovers between adjacent groups. A nominal overlap contributes
zero layover; it is never wrapped into the following day. Real after-midnight
service must use the GTFS representation with hours at or above 24 (for
example `24:05:00`), which preserves a positive layover on the common service-
day axis. The core enforces `dwell <= duration` and the exact partition
`driving + dwell = duration`.

## Model-input preprocessing

Contract v2 defines an ordered raw frame of 64 columns: the 61 canonical
schedule/profile statistics and the three runtime context fields
`bus_length_m`, `bus_battery_kwh`, and `avg_temp_outside_celsius`. Both trainer
and backend validate this schema through `prepare_model_feature_frame`; missing
or unexpected fields fail instead of being silently replaced by zero.

`elevation_profile_type` uses a fixed category universe (`flat`,
`ascent_only`, `descent_only`, `mixed`) with `flat` as baseline. The shared
encoder always materializes the other three dummy columns, independently of
which categories occur in a batch. The contract version belongs in release and
model metadata, never in the numeric model frame.

A v2 trainer input is an immutable feature release with a `manifest.json` that
records the contract version, row count, exact X/Y schemas, sizes, and SHA-256
digests. The trainer verifies these values before it may write v2 model
metadata; unmanifested legacy X/Y cannot be labelled v2.

## Release and consumption

The Python distribution version and feature contract version follow semantic
versioning. A feature value or meaning change increments the contract major
version. After the backend change is merged, tag its commit
`elettra-core-v2.0.0`; only after that tag is remotely available, `elettra`
pins it immutably:

```text
elettra-core @ git+https://github.com/supsi-dacd-isaac/elettra-backend.git@elettra-core-v2.0.0
```

The integration order is therefore backend commit, backend tag and push, then
training dependency pin. During coordinated development, install the backend
checkout in editable mode. Do not copy `features.py` into another repository.
