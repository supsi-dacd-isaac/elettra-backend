# Diesel-heater fuel and emissions methodology

## Scope and calculation flow

The auxiliary diesel-heater contribution is a well-to-wheel (WTW) result:
direct heater exhaust plus production and distribution of its fuel. It is not
an absolute full-vehicle life-cycle assessment. The existing electric-side
model and full-diesel comparator are intentionally unchanged.

For every weather scenario, VECTO first determines useful heating demand and
applies the heater efficiency inside the HVAC model. Its `p_fuel_kw` output is
therefore **fuel input power**, not useful heat. Multiplication by trip duration
produces `diesel_fuel_kwh`; annualization multiplies that value by the scenario
occurrence count. The application then performs one conversion only:

```text
litres = diesel_fuel_kWh / 9.87701388888889 kWh/L
```

The efficiency is not applied again during litre or emissions calculations.
The electric contribution comes from the same prediction runs and weather
weights as the fuel contribution, so no electric-heating scenario is mixed
into the diesel-heater case.

The API reports data completeness separately from scope completeness. A result
can have all required inputs (`data_completeness.status = complete`) while
retaining a partial environmental boundary
(`scope_completeness.status = partial`). Missing or invalid fuel energy is
reported as unavailable, while an explicit measured zero remains zero.

## Verified Mobitool workbook

The source is the official **Mobitool factors v3.1**, dated 8 April 2025:

- workbook: <https://assets.energieschweiz.ch/f/174202/x/b0861efe32/mobitool-faktoren-v3-1.xlsx>
- SHA-256: `778e1a936399ed072f4cc6ce6458b82c045fbea3b181b82b0fbf036bd49f7304`
- average diesel blend: worksheet `fuel blend`, cells `F12:G12`;
- density, LHV, fossil and biogenic CO2: worksheet
  `fuels and tailpipe emissions`, cells `B6:F7`;
- upstream characterization data: worksheet `characterization factors`,
  headers `D1:I1`, fuel rows `D128:I129`.

The versioned profile `ch-average-diesel-2025-mobitool-3.1-v1` represents the
average Swiss blend assumed by Mobitool, not fuel measured from an operator and
not a fixed B7 blend:

| Component | Volume share | Density | LHV |
| --- | ---: | ---: | ---: |
| Fossil diesel | 0.941 | 0.83 kg/L | 43 MJ/kg |
| Biodiesel from used cooking oil | 0.059 | 0.88 kg/L | 38 MJ/kg |

```text
energy_MJ/L = 0.941 × 0.83 × 43 + 0.059 × 0.88 × 38
            = 35.55725 MJ/L
energy_kWh/L = 35.55725 / 3.6 = 9.87701388888889 kWh/L
```

For VECTO prediction stacks, saved fuel energy is authoritative. The
application layer recalculates litres with this profile for energy summaries,
costs and emissions. The frozen VECTO template release and its historical
9.94 kWh/L estimate remain unchanged and are retained only in provenance.
Legacy prediction stacks keep their persisted energy/litre pair and are
labelled `legacy_persisted`. No stored production data are rewritten.

## Reproducible factors

The minimum source observations are stored under
`diesel_heating.source_data` in `config/emission_defaults.json`.
`derive_diesel_heating_factors()` in `app/services/diesel_heating.py`
reproduces every phase factor and validates the configuration when loaded.

Mobitool upstream inputs per kilogram are:

| Indicator | Fossil diesel | UCO biodiesel | Unit |
| --- | ---: | ---: | --- |
| GWP100a | 655 | 576.14389 | g CO2-eq/kg |
| NOx | 2691 | 3197.9391 | mg NO2-eq/kg |
| PM10 | 56 | 83.731987 | mg PM10/kg |
| Primary energy | 55.46 | 55.912152 | MJ/kg |
| Non-renewable primary energy | 55.3140772 | 16.35508250303 | MJ/kg |

Each upstream value is converted to one litre of the blend with:

```text
upstream/L = 0.941 × 0.83 × fossil_factor/kg
           + 0.059 × 0.88 × biodiesel_factor/kg
```

Direct and upstream results are kept separate. The deterministic totals are:

| Indicator | Direct | Energy chain | WTW total | Unit |
| --- | ---: | ---: | ---: | --- |
| GWP100a | 2460.2445 | 541.4880407688 | 3001.7325407688 | g CO2-eq/L |
| NOx | 3201.467312962 | 2267.788728072 | 5469.256041034 | mg NO2-eq/L |
| PM10 | 64.9222797927 | 48.08504476504 | 113.00732455774 | mg PM10/L |
| Primary energy | 0 | 46.21888273184 | 46.21888273184 | MJ/L |
| Non-renewable primary energy | 0 | 44.05110959907332 | 44.05110959907332 | MJ/L |

The primary-energy factors already include the resource energy used in the
fuel chain. The fuel LHV is not added to them again. `MJ` is used here because
that is the physical quantity returned for these two indicators; no global
replacement of other equivalent-energy units is made.

### Direct GWP100a

Only fossil CO2 is characterized in the direct GWP100a term:

```text
0.941 × 0.83 kg/L × 3.15 kg CO2/kg × 1000 = 2460.2445 g CO2/L
```

The corresponding biogenic CO2 mass is 144.8568 g/L, but its characterization
factor is zero in the adopted Mobitool method. That accounting choice does not
remove upstream biodiesel impacts. Heater CH4 was reported as very low and is
not transferred into this model; N2O was not measured. The result is therefore
an estimate, not a complete measured direct greenhouse-gas inventory.

### Harmonized NOx convention

Humphries et al. (2024), Table 9, reports physical NO and NO2 masses as separate
species. Mobitool's UVEK/ecoinvent inventory uses the elementary flow
“Nitrogen oxides (NOx as NO2)”. Before addition, measured NO is converted to
NO2-equivalent with the molecular-mass ratio `46.0055 / 30.0061`:

```text
direct NOx = ((10.75 g NO × 46.0055/30.0061) + 0.87 g NO2)
             / 5.42 L × 1000
           = 3201.467312962 mg NO2-eq/L
```

This harmonization replaces the preliminary, non-compatible sum of species
masses (2143.9106 mg/L). The WTW total is then 5469.256041034 mg NO2-eq/L.

### TPM proxy for PM10

Humphries Table 5 provides two valid stationary total-particulate-matter (TPM)
tests. TPM is used only as an explicit proxy for PM10; it is not a PM10 size
fraction measurement and is not called soot.

```text
direct central = (105.7 + 19.6) mg / (0.96 + 0.97) L
               = 64.9222797927 mg/L
```

The two individual tests produce WTW values of 158.1892114317 and
68.2912303321 mg/L. This is an observed two-test range, not a confidence
interval, a fleet variability estimate, or a guaranteed bound. The pooled
factor is used for deterministic calculations and the high experimental
variability is exposed in API metadata.

## Lifecycle attribution and API contract

For each supported indicator:

```text
heater_direct   = yearly_litres × direct_factor
heater_upstream = yearly_litres × upstream_factor
mixed_total     = same_case_electric_total + heater_direct + heater_upstream
```

Direct heater impacts are added to `direct`, and upstream impacts to
`energyChain`; the heater total is not allocated with generic vehicle shares.
When electric-side phase shares are available, all returned phases reconcile
with the mixed total. Otherwise the heater phases remain informative while the
phase decomposition is explicitly partial. No factors are invented for
unsupported indicators.

The yearly energy, costs and emissions endpoints consume the same canonical
litre quantity and expose its source, fuel-profile version and kWh/L value.
Emission responses additionally expose `data_completeness`,
`scope_completeness`, and `diesel_heating_methodology` (boundary, factors,
units, sources, assumptions, limitations and the PM10 observed range). A VECTO
record lacking trustworthy fuel energy cannot silently become a zero or a
complete comparison. Persisted historical VECTO results are re-derived from
their stored fuel energy at read time and carry the current methodology; legacy
stack quantities remain explicitly legacy.

## Limitations

Direct NOx and TPM measurements come from one published heater and are
transferred to Elettra's assumed fuel profile; they are not measurements of the
simulated fleet. The model has no heater-specific correction for starts,
temperature, duty cycle, condition or ageing. It covers only the indicators in
the factor table and does not make the overall environmental model a complete
vehicle LCA.

## Sources and update procedure

- [Official Mobitool 3.1 page](https://www.energieschweiz.ch/umweltrechner-verkehr/ueber-dieses-tool/)
- [Mobitool 3.1 workbook](https://assets.energieschweiz.ch/f/174202/x/b0861efe32/mobitool-faktoren-v3-1.xlsx)
- [Humphries et al. (2024), SAE 2024-01-5011](https://doi.org/10.4271/2024-01-5011)
- [Official open-access heater paper](https://open-science.canada.ca/server/api/core/bitstreams/fd916a14-4051-404e-a697-9560f806ce0b/content)
- [ecoinvent LCIA v2 implementation documentation](https://support.ecoinvent.org/hubfs/Knowledge%20Base/Database/Releases/LCIA%20methods_ecoinvent%20version%202.pdf)
- [Independent SFOE/UFE 2026 comparison report](https://pubdb.bfe.admin.ch/fr/publication/download/12674)

To update the method: download the official workbook, verify and record its
SHA-256 and relevant cells, add a new profile/methodology version rather than
relabeling old results, update only `source_data` plus its source metadata,
re-run the derivation tests, review all units and conventions per indicator,
then run the yearly endpoint and frontend browser tests. Do not modify frozen
VECTO release artifacts or rewrite persisted production data.
