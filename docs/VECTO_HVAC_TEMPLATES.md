# VECTO HVAC template generation and use

## Purpose and boundary

Elettra uses the bus HVAC steady-state model (SSM) from VECTO 5.1.3 to
estimate climate-control demand. This is not a complete VECTO vehicle model:
it does not simulate a mission, powertrain, driving cycle, doors, lighting or
other fixed electrical consumers.

The transcription is derived from the
[official VECTO 5.1.3 source archive](https://code.europa.eu/vecto/vecto/-/archive/Release/v5.1.3/vecto-Release-v5.1.3.tar.gz)
at commit `cef1f3d260afa7f7c6ec09981d821e545d21b249`.

The official SSM requires an explicit vehicle declaration. The current fleet
database does not contain homologation-grade declarations, so Elettra provides
four deterministic engineering templates. They are versioned scenarios for
reproducible training and inference, not statements about a manufacturer's
certified vehicle.

The immutable declaration release is
`vecto-hvac-5.1.3-r744-templates-v1`. Its canonical JSON is packaged in
`elettra_core/data/vecto_hvac_5_1_3_r744_templates_v1.json`; its SHA-256 is
`982e4bc7fa65053dcfef943b8cc7fe60de64c834fc82bd4425e8ce0a36b5e5d2`.
The release also binds the source SHA-256 of `elettra_core.vecto_ssm`, currently
`195981d937822a8e4d001a1936a5d4712b7763858906101ab227da47cfc487bb`.

## What is shared

Training and backend inference import the same installed `elettra_core`
release:

- `elettra_core.vecto_ssm` is the faithful VECTO 5.1.3 SSM transcription;
- `elettra_core.vecto_templates` owns the Elettra declarations, length mapping,
  environmental adapter and auxiliary contracts;
- `app.services.vecto_ssm` is a compatibility re-export and contains no model
  implementation of its own.

Every model manifest must pin the core version, template release, template
JSON SHA-256 and auxiliary contract. A mismatch is an error; it must never
fall back silently to another declaration or auxiliary estimator.

## Deterministic generation

For a template length `L` in metres, the release generator applies:

```text
external surface area [m²] = 9.7 × L + 10.8
window surface area   [m²] = 1.5 × L + 3.2
interior volume       [m³] = 5.865 × L − 7.038
total HVAC capacity    [W] = 250 × interior volume
driver capacity        [W] = total capacity × 1.2 / L
passenger capacity     [W] = total capacity × (L − 1.2) / L
```

Values are rounded to six decimal places before serialization. The release is
timestamp-free and serialized as UTF-8 JSON with sorted keys and a trailing
newline, so identical source code produces identical bytes.

All templates declare the following common parameters:

| Parameter | Value |
|---|---:|
| Floor | `LowFloor` |
| Thermal transmittance `U` | 4 W/(m² K) |
| HVAC configuration | 9 |
| Driver/passenger heat pump | R744 / R744 |
| Electric resistance heaters | none |
| Driver compartment length | 1.2 m |
| Ventilation, normal/heating | 20 / 10 h⁻¹ |
| Specific ventilation power | 0.56 Wh/m³ |
| Diesel fuel-heater capacity | 30 kW when enabled, otherwise 0 kW |

The generated length-specific values are:

| Template | Surface m² | Window m² | Volume m³ | Driver capacity kW | Passenger capacity kW | Fixed non-HVAC kW |
|---:|---:|---:|---:|---:|---:|---:|
| 9 m | 98.1 | 16.7 | 45.747 | 1.52490 | 9.91185 | 2.0 |
| 10 m | 107.8 | 18.2 | 51.612 | 1.54836 | 11.35464 | 2.2 |
| 12 m | 127.2 | 21.2 | 63.342 | 1.58355 | 14.25195 | 2.5 |
| 18 m | 185.4 | 30.2 | 98.532 | 1.64220 | 22.99080 | 3.1 |

Heating and cooling use the same declared capacities. The fixed non-HVAC
figures are external Elettra scenario assumptions. They are neither calculated
nor endorsed by VECTO.

Regenerate after an intentional formula or parameter change:

```bash
python3 scripts/generate_vecto_hvac_templates.py
python3 scripts/generate_vecto_hvac_templates.py --check
```

`--check` performs a byte-for-byte comparison and never writes. A changed
artifact requires a new release identifier; an existing published release is
immutable.

## Mapping a real bus to a template

The mapping is deliberately explicit and contains unsupported gaps:

| Actual bus length | Template |
|---|---:|
| `[9.0, 9.5)` m | 9 m |
| `[9.5, 11.0)` m | 10 m |
| `[11.0, 13.0]` m | 12 m |
| `[17.0, 19.0]` m | 18 m |

Thus 10.8 m maps to 10 m and 13.0 m maps to 12 m. Values below 9 m,
between 13 and 17 m, above 19 m, missing values and non-finite values fail
closed. Nearest-neighbour extrapolation is not allowed.

## Passengers and weather

Passenger count is not embedded in a template. At inference it is calculated
from the selected bus specification and request:

```text
passengers = max_passengers × occupancy_percent / 100
```

The training data does not contain observed occupancy. The intended training
scenario uses half of the cohort capacity: 25 passengers for 10/10.8 m,
30 for 12 m and 50 for 18 m. This training assumption belongs in the model
manifest; changing it requires retraining, not regenerating the vehicle
template.

The default global horizontal irradiance (GHI) is 100 W/m². For every call the
adapter:

1. validates temperature and GHI;
2. selects the VECTO `DefaultClimatic.aenv` row nearest in temperature;
3. breaks ties by nearest irradiance and then row ID;
4. retains that row's COP and heater-efficiency maps;
5. substitutes the requested temperature and scenario GHI.

COP is selected, not interpolated. The chosen environmental row ID, actual
temperature, GHI, passengers, real length and template length are prediction
provenance and should be persisted by the caller.

## Diesel and non-diesel operation

`auxiliary_heating_type="diesel"` declares a 30 kW fuel heater alongside the
electrical R744 heat pump. VECTO's heating-distribution tables decide the load
split. `p_fuel_kw` is reported separately and never added to battery energy.
The adapter also reports `fuel_l_per_hour = p_fuel_kw / 9.94 kWh/L`; 9.94 is
the frozen lower-heating-value assumption for diesel, not a VECTO constant.

`auxiliary_heating_type="default"` sets fuel-heater capacity to zero. The R744
heat pump remains present. A backend using this scenario must report unmet
thermal demand if the declared electrical equipment cannot cover it; it must
not silently invent a diesel or resistance heater.

Delivered and unmet heating are computed inside the SSM calculation while the
thermal heat-pump, electric-heater and fuel-heater contributions are still
available. `unmet_thermal_demand_kw` is therefore not reconstructed from the
aggregate electrical result by the backend.

## Auxiliary contracts and model stacks

The template adapter exposes two non-interchangeable contracts:

| Contract | Electrical value returned | Intended model |
|---|---|---|
| `vecto-hvac-only` | electrical HVAC | G2 learns its own fixed electrical load |
| `vecto-complete` | electrical HVAC + template fixed baseline | G0 transfer model has no internal fixed load |

The complete contract does **not** mean complete VECTO vehicle simulation. It
means the VECTO SSM HVAC output plus Elettra's declared fixed non-HVAC baseline.

The stack ownership rule is:

| Stack | Removed for training | Learned by grey box | Added at inference |
|---|---|---|---|
| `legacy` | current data-driven treatment | current behavior | legacy curves |
| `vecto-g2` | VECTO electrical HVAC | powertrain and affine fixed load | VECTO electrical HVAC |
| `vecto-g0-transfer` | complete data-driven auxiliaries | residual powertrain | VECTO HVAC and template baseline |

Mixing `vecto-g2` with `vecto-complete` double-counts fixed load. Mixing the G0
transfer model with `vecto-hvac-only` omits fixed load. Model-release validation
must reject both combinations.

## Worked 12 m example

For a 12 m bus, 30 passengers, −5 °C, 100 W/m² GHI and diesel heating:

1. geometry is 127.2 m² external surface, 21.2 m² glazing and 63.342 m³;
2. total heating/cooling capacity is 15.8355 kW, split into 1.58355 kW driver
   and 14.25195 kW passenger capacity;
3. exact climatic row 2 is selected, retaining R744 COP 2.04 and fuel-heater
   efficiency 0.80;
4. VECTO produces 9.875125 kW heating demand, 3.743238 kW electrical HVAC and
   3.703172 kW fuel input, equivalent to 0.372553 L/h at 9.94 kWh/L;
5. `vecto-hvac-only` returns 3.743238 kW electrical demand;
6. `vecto-complete` adds the explicit 2.5 kW baseline and returns 6.243238 kW.

For a duration `t` in hours, each power component is converted to energy by
`E = P × t`. Fuel and electrical energy remain separate.

Example API use:

```python
from elettra_core.vecto_templates import vecto_template_auxiliary_power

estimate = vecto_template_auxiliary_power(
    bus_length_m=12.0,
    number_of_passengers=30.0,
    temperature_celsius=-5.0,
    solar_irradiance_wm2=100.0,
    auxiliary_contract="vecto-hvac-only",
    auxiliary_heating_type="diesel",
)
electrical_hvac_kw = estimate.result.p_hvac_electrical_kw
diesel_input_kw = estimate.result.p_fuel_kw
```

## Validation, provenance and licensing

The low-level implementation is compared with the official .NET 8 VECTO
5.1.3 assemblies on 18 oracle cases and 126 scalar outputs. The maximum
observed absolute difference is `1.14e-13 W`. The official archive and DLLs are
test inputs only and are not included in Git or the production image.

The transcription is copyright the European Union and licensed under
EUPL-1.2. See `VECTO_SSM_PROVENANCE.md`, `THIRD_PARTY_NOTICES.md` and
`tests/vecto_oracle/README.md`. The SUPSI release owner approved the documented
MIT/EUPL component boundary for public distribution on 2026-08-31. Every
distribution must retain both licence texts, the modification/provenance notice
and a link to the exact public source tag.

## Troubleshooting

- **Unsupported bus length:** add no implicit fallback. Obtain a reviewed
  declaration or publish a new template release.
- **Checksum mismatch:** do not edit generated JSON manually. Restore it or
  intentionally change the generator and assign a new release ID.
- **Unexpected HVAC discontinuity:** inspect the selected climatic row. COP is
  stepwise because interpolation is intentionally disabled.
- **Unexpected battery demand:** verify the manifest contract and fixed-load
  owner before changing parameters.
- **No diesel demand in mild weather:** this can be a valid SSM result; fuel
  capacity does not imply that the heater is always active.
- **Training/backend disagreement:** compare the exact core version, template
  release SHA-256, contract, passenger count, temperature, GHI and heating type.
