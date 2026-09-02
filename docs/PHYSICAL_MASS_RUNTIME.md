# Runtime physical mass contract

The consumption model is trained with a documented hourly passenger-load
assumption, but prediction requests use the selected bus model and the requested
occupancy as the authoritative source of mass.

For every prediction run the backend calculates:

```text
passenger_count = max_passengers × occupancy_percent / 100
battery_mass = num_battery_packs × battery_pack_weight_kg
passenger_mass = passenger_count × model passenger_mass_kg
override_mass = empty_weight_kg + battery_mass + passenger_mass
```

New VECTO releases use the immutable `68 kg/passenger` contract shared by the
training pipeline and backend. Historical legacy artifacts retain their
serialized convention; pre-2.2 artifacts that do not declare it continue to
use `70 kg/passenger`. This compatibility rule is model-specific and is never
used as the default for a new VECTO model.

`empty_weight_kg` is battery-excluded unladen mass. Batteries and passengers
must therefore not already be included in it. The following bus-model fields are
required and no production fallback is applied:

- `bus_length_m`
- `empty_weight_kg`
- `battery_pack_size_kwh`
- `battery_pack_weight_kg`
- `max_battery_packs`
- `max_passengers`

The requested pack count must be between one and `max_battery_packs`; occupancy
must be between 0 and 100%. Invalid or incomplete physical data fail the run
rather than silently selecting a generic 18 m bus.

The resulting mass is passed to the grey box as `override_mass`. It replaces
the entire implicit training mass. It is also audited in
`prediction_runs.contextual_parameters.physical_mass`, including empty,
battery and passenger contributions and the fractional passenger count.

## Interaction with the residual QRF

The VECTO production model pins `greybox_pred_kwh` in its selected-feature
contract. The grey-box prediction is first recomputed with `override_mass`, then
the same value is passed to the QRF. The QRF therefore sees the mechanical
estimate corresponding to the requested occupancy; it is not fed a stale
training-mass estimate.

The trained hourly occupancy profile is not a runtime passenger forecast. It is
used only to keep the drivetrain coefficients on a physically consistent mass
scale in the absence of historical APC labels. At runtime the request wins.
The serialized legacy-compatible wrapper can reproduce that hourly profile for
offline evaluation when no mass is supplied. This is a fallback-only contract:
the backend always passes the bus-specific `override_mass`, and a regression
test verifies that the explicit value takes precedence unchanged.

## Release checks

Before promotion, verify on the exact release artifact that:

1. TPF test specs (`empty=10235 kg`, ten 275 kg packs, capacity 70) with a new
   VECTO model produce 12,985 kg at 0%, 15,365 kg at 50% and 17,745 kg at
   100% occupancy.
2. Mechanical energy is finite and increases from 0% to 50% to 100% for the
   production TPF route sample.
3. The serialized QRF feature order contains `greybox_pred_kwh` and the value
   changes when `override_mass` changes.
4. Model metadata records fixed Solaris chassis coefficients, the OGD
   reference occupancy and the `68 kg/passenger` convention; legacy releases
   retain their historical serialized convention.
5. Startup preflight binds model checksums, shared-core commit, elevation
   release and prediction-stack auxiliary contract before serving traffic.

Future versions should replace the anchored hourly profile with a separately
validated passenger model using APC/ticketing labels. Route or direction effects
should only be introduced with partial pooling and held-out-date/operator
validation; energy residuals alone do not identify absolute passenger counts.
