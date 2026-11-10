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
