# Backend test suite

The backend suite covers the API and domain services used by the current
application, including authentication/authorization, fleet CRUD, GTFS and
elevation, prediction/optimization, yearly analysis, economic and environmental
calculations, HVAC policy, release configuration, and deployment hardening.

Tests are collected from `tests/test_*.py`, `tests/auth/`, and `tests/gtfs/`.
Shared fixtures and environment isolation live in `tests/conftest.py`.

## Run tests

Use the repository wrapper for a local full run:

```bash
./run_tests.sh
```

It activates `.venv` when available, sets `PYTHONPATH` and `ELETTRA_TESTING`,
loads `tests/test.env` when present, and validates that local integration
targets are the dedicated staging services before pytest collection.

Examples:

```bash
./run_tests.sh -q
./run_tests.sh tests/test_lca_size.py tests/test_environmental_lca_size.py -q
./run_tests.sh -k "hvac or yearly" -q
./run_tests.sh tests/auth -q
```

Direct `pytest` remains useful inside a prepared CI/container environment where
the required configuration and isolated database targets are already set.

## Test boundaries

Most tests are deterministic unit or isolated API regressions. Tests that need
deployment GTFS data, OSRM, MinIO, private model artifacts, or the official
VECTO binary are explicit opt-in integrations and report why they are skipped.
A green run does not turn an unconfigured external integration into covered
behavior.

The official VECTO SSM oracle has its own instructions in
[`vecto_oracle/README.md`](vecto_oracle/README.md).

### Fleet charging regressions

```bash
./run_tests.sh tests/test_fleet_charging_selection.py tests/test_fleet_optimization.py -q
```

These integrations execute the checked-out API and solver workers against the
validated staging database and profile storage. Charging candidates come from
`/api/v1/simulation/charging-stops/candidates` for the test's selected shifts;
they are never expanded by searching all GTFS records with the same stop name.
The candidate list is neither truncated nor allowed to bypass the API limit.

The depot case uses five source duties with their depot movements preserved.
It creates owned temporary copies of auxiliary trips and depot stops, references
existing elevation objects read-only, and asserts positive depot charging energy.
Cleanup removes only fixture-created rows, including after setup failure.
The selection regressions also compare candidates against the actual trip
termini for one shift, five shifts and the full fleet. Full solver cases can
take several minutes each (up to 600 solver seconds per fleet scenario).
The integration assertions require a conclusive outcome: verified feasibility
or established infeasibility. An API job marked `completed` with a solver
timeout or an unverified forecast is not accepted as a successful calculation.

The entire `test_fleet_optimization.py` module is skipped by default, including
release/production verification. They are one-off staging diagnostics, not production
smoke tests. This also skips fleet setup, candidate selection and charging-only
runs, so routine tests do not create or simulate the large fleet.
Opt in with `ELETTRA_RUN_LONG_FLEET=1` to retain normal time limits.
To investigate their final outcomes without an operational time deadline:

```bash
ELETTRA_RUN_LONG_FLEET=1 ELETTRA_FLEET_UNBOUNDED=1 \
ELETTRA_FLEET_DIAGNOSTIC_RESULTS=/tmp/elettra-fleet-unbounded-results \
./run_tests.sh tests/test_fleet_optimization.py -k 'fleet_battery_only or fleet_joint' -s
```

This test-only worker refuses any database except local staging port 9004.
It removes the solver, supervisor and polling deadlines. Its pipeline budget
uses a JSON-compatible `1e100`-second sentinel instead of infinity, only in
temporary test rows. API validation and deployed application limits are unchanged.
Both mean and Q95 results are saved before fixture cleanup. The experiment can
run for hours; solver optimality and physical verification are still required.

The one-off tolerance benchmark uses only routes 401, 404 and 407 (21 shifts),
and is likewise excluded by default. It compares BATTERY_ONLY and JOINT for
Mean and aggregate Q95 at relative MIP gaps `0`, `1e-8`, `1e-6`, `1e-4` and
`1e-3`, with all other inputs fixed:

```bash
ELETTRA_RUN_FLEET_TOLERANCE_BENCHMARK=1 \
ELETTRA_FLEET_TOLERANCE_RESULTS=/tmp/elettra-fleet-tolerance-401-404-407.json \
./run_tests.sh tests/test_fleet_tolerance_benchmark.py -s
```

The JSON artifact is updated after each Mean/Q95 pair, so completed measurements
survive an interrupted experiment. This remains a staging-only diagnostic.

## Environment

The test environment file is selected in this order:

1. `ELETTRA_TEST_ENV_FILE`, when set;
2. `tests/test.env`;
3. `.env.test` in the repository root.

The deployment `.env` is not loaded by pytest. Common variables include:

| Variable | Purpose |
|---|---|
| `ELETTRA_CONFIG_FILE` | application YAML/JSON used during test startup |
| `DATABASE_URL` | dedicated test/staging PostgreSQL URL |
| `ELETTRA_TEST_BACKEND_URL` | dedicated live backend for opt-in integration tests |
| `ELETTRA_TEST_FRONTEND_URL` | dedicated live frontend for cross-service checks |
| `TEST_LOGIN_EMAIL` / `TEST_LOGIN_PASSWORD` | credentials for authenticated live checks |
| `TEST_REPORT_COLOR` | opt-in ANSI colors in generated text reports |

See [`README_TESTING.md`](README_TESTING.md) for the local staging target rules.

## Adding regressions

- Name files `test_*.py` so pytest collects them.
- Prefer isolated fixtures and explicit assertions over shared mutable state.
- Keep network/deployment dependencies opt-in and mark their requirements.
- Add a permanent regression with every behavioral fix.
- Use the `record` fixture only when a module needs the optional structured
  text/JSON report output; ordinary pytest assertions are fully supported.
