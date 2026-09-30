# Grid-to-bus assessment and parameterized vehicle LCA

## Physical convention

`grid-to-bus-dc-94-v1` applies fixed 94% infrastructure efficiency to **assessment**, not to the optimizer or energy predictor. Bus acceptance, charger output and aggregate station power are DC delivered to the bus. Station power is not a verified AC grid-connection limit. SOC still increases by `P_DC * dt`; separate battery-storage losses are not modelled. There is no 97% correction. Parked preconditioning is excluded; predicted in-service HVAC remains included.

The [FOT charging-infrastructure guide](https://www.bav.admin.ch/dam/de/sd-web/IhWPhaMXCD7N/leitfaden-ladeinfrastruktur-14082025.pdf#page=17), printed page 14, illustrates 94% grid-to-bus efficiency including transformer and charging infrastructure. This is a pre-feasibility convention, not a battery electrochemical efficiency or a manufacturer guarantee.

Purchased electricity is `E_DC / 0.94`; losses are purchased minus DC energy. Thus 94 kWh DC requires 100 kWh from the grid. Charging 150 kW DC for one hour provides 150 kWh and purchases 159.574 kWh. Annual assessment assumes replenishment of consumed energy. A duty charging 150 kWh while consuming 180 kWh instead has a 30 kWh SOC deficit: its actual charging summary must not use the annual-replenishment quantity.

Charger CAPEX uses nominal DC power; the manual connection regression uses AC power. The dedicated connection endpoint accepts **already-AC** power and never converts it again. Connections are per station, not per bus or slot. Explicit optimizer slot-installation quotations remain lump-sum infrastructure inputs; they are not modified inside the objective or charged again as an inferred connection investment. Read-time connection estimates do not mutate stored solver results.

## Annual LCA contract

`GET /api/v1/yearly-analysis/{id}/lca` returns `mobitool-parameterized-grid94-v1`. Supported optional assessment inputs: `annual_km`, `lifetime_bus`, `lifetime_battery`, `lifetime_diesel_bus`, `battery_chemistry`, `electricity_mix`, `diesel_consumption_l_per_km`. Efficiency is not a user input. The existing `/emissions` endpoint retains its historical meaning; the updated annual and comparison interfaces never fall back to it.

The service builds one physical vehicle per duty, aggregating its temperature scenarios. Duplicated duty/scenario pairs, missing scenarios or required immutable capacity, length and passenger inputs fail closed. Distances and fuel come from the same completed prediction-run ledger as energy. Installed capacity is the pack count times pack capacity **before** SOC-window/SOH reductions, not usable operating energy. Snapshot physical parameters take precedence over subsequently edited bus specifications. Snapshot assessment metadata, explicitly attributed model metadata and declared defaults provide chemistry/lifetimes. Defaults are NMC, 12-year electric bus, 8-year battery, 10-year diesel bus, and `CONSUMER_PHYSICAL` electricity.

Lifetime km = annual km × vehicle years. Battery replacements = `max(ceil(vehicle_years / battery_years) - 1, 0)`; the initial battery is not a replacement and none is installed at the exact terminal age. Life-cycle impacts are annualized physically, without financial discounting.

References in `config/lca_catalog.json` pin the configured upstream and data version, never “latest”. Length <=10 m maps to 9 m; >10 and <=14 m maps to 13 m; >14 m maps to 18 m. Chemistry selects compatible inventory, independently of pantograph presence. All input values are forwarded (`vkm=true`), not merely retained as annotations. Values outside advertised API parameter ranges produce explicit warnings, not clipping.

## Decomposition and units

For identical per-vehicle parameters, requests at 0 and 100 kWh/100 km isolate electricity supply. Their difference corresponds to one DC kWh/km. Divide the **operational total difference** by `(1+batteryChargeLoss)*(1+chargerLoss)` to recover impact per purchased kWh, then multiply by ELETTRA grid energy. The zero-consumption inventories remain absolute and are multiplied by annual km. Never allocate vehicle production as a percentage of electricity emissions.

For primary energy, native direct use remains DC-use energy and the adjusted energy-chain term equals adjusted operational total minus direct use. This preserves negative bookkeeping components. The upstream primary-energy unit is kWh; the public LCA contract converts it to MJ (×3.6). Other units are g CO2-eq, mg NO2-eq and mg PM10. Totals reconcile to the seven reported phases.

The full diesel reference is separately parameterized with the shared economic diesel-consumption relationship, same activity and diesel lifetime. Heater fuel has separate documented combustion/upstream factors from `emission_defaults.json`; it is not represented by diesel-bus exhaust inventory. Heater hardware and actual depot construction are not separately inventoried.

`status=complete` means data-complete within the declared representative-inventory scope, not a certified manufacturer/depot LCA. Missing/nonfinite phases, mismatched versions or service errors return incomplete status with reasons and no overall total. Zero passengers is not silently changed to one: the current upstream produces invalid infrastructure in this case. Other energy and cost results remain available.

Responses carry physical inputs, source attribution, catalog/version, native multiplier removed, actual forwarded queries, response hashes, units, warnings and scope limitations. Cache keys include upstream, data version, reference and all request parameters; bounded cache lifetime is 600 s. New analyses save policy identifiers in their existing features JSON. Historical artifacts are never rewritten.

## Verification

- `tests/test_charging_energy_boundary.py`: conversions, AC inputs, station scope, optimizer independence.
- `tests/test_parameterized_lca.py`: frozen upstream linearity, three classes, chemistry/capacity/replacements/lifetime, absolute manufacturing, primary-energy units, incomplete responses and timeouts.
- `tests/test_parameterized_lca_endpoint.py`: access, missing snapshot, mileage reparameterization.
- `scripts/check_grid94_lca.py`: controlled read-only live API probe.
- `scripts/replay_yearly_lca.py`: regenerate report examples from a read-only JSON export without a DB connection or writes.

Tests use isolated PostgreSQL. Do not run the suite against the production database. Release gates also require complete frontend/backend suites, desktop/mobile browser checks, image startup and isolated replay before deploying the same digests.
