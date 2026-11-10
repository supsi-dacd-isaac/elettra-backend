# ELETTRA backend

FastAPI service for ELETTRA's electric-bus planning workflow. It provides
authentication, agency and fleet data, versioned GTFS access, vehicle blocks,
consumption prediction and optimization, yearly analysis, economic assessment,
and environmental lifecycle indicators.

The backend is a separate deployment from the Vite frontend. The current
application version is defined in
[`config/elettra-config.image.yaml`](config/elettra-config.image.yaml).

## Main capabilities

- JWT authentication and user/agency administration.
- Bus-model, bus, depot/custom-location, and vehicle-block persistence.
- Versioned GTFS routes, trips, stops, calendars, variants, auxiliary trips,
  driving distance, elevation profiles, and trip statistics.
- Prediction and optimization runs for battery-only, charging, and joint
  feasibility evaluation.
- Representative-weather yearly analyses with saved configuration,
  assumptions, costs, and LCA results.
- Economic endpoints for investment, annualization, operating costs, and
  electric/diesel comparison.
- Environmental endpoints backed by the Energie Schweiz/Mobitool catalogue,
  including the parameterized annual vehicle LCA.
- Hybrid PVGIS/Open-Meteo weather reference and auditable HVAC assumptions.
- PostgreSQL persistence, MinIO object storage, readiness checks, and generated
  OpenAPI documentation.

## API layout

| Prefix | Scope |
|---|---|
| `/auth` | login, registration, current profile, password, logout |
| `/api/v1/agency` | agencies and agency users |
| `/api/v1/user` | bus models, buses, depots, and vehicle blocks |
| `/api/v1/gtfs` | datasets, routes, trips, stops, elevation, and auxiliary trips |
| `/api/v1/simulation` | prediction, optimization, statistics, weather, and HVAC provenance |
| `/api/v1/yearly-analysis` | yearly CRUD, assumptions, costs, LCA, and previews |
| `/api/v1/economic` | economic defaults and calculations |
| `/api/v1/environmental` | catalogue and environmental calculations |

Runtime discovery is available at `/docs` and `/redoc`. A lightweight public
version endpoint is exposed at `/api/v1/version`; `/health` is the public
liveness endpoint, while `/api/v1/readiness` is an authenticated administrator
check of dependencies.

See [the API reference](docs/API_REFERENCE.md) for request examples and the
complete endpoint catalogue.

## Requirements

- Python 3.12 for the application image
- PostgreSQL
- MinIO for elevation/model artifacts in the full stack
- OSRM and external weather/LCA services for the corresponding integrations

Python dependencies are pinned through `requirements.txt` and
`requirements.lock`. The repository also contains the independently versioned
`elettra_core` feature/VECTO contract package described by `pyproject.toml`.

## Quick start with Docker Compose

Create the ignored local configuration from the non-secret image baseline:

```bash
cp config/elettra-config.image.yaml config/elettra-config.docker.yaml
docker compose up --build db minio minio-create-bucket app
```

The default compose mapping exposes:

- API and Swagger: <http://127.0.0.1:8002/docs>
- PostgreSQL: `127.0.0.1:5440`
- MinIO API: `127.0.0.1:9002`
- MinIO console: <http://127.0.0.1:9003>

The compose defaults are for local development only. Production must provide a
strong `APP_SECRET_KEY`, an explicit database URL, restricted CORS origins, and
least-privilege object-storage credentials. Use
`docker-compose.external.yml` for the external production deployment model.

## Run from source

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
cp config/elettra-config.image.yaml config/elettra-config.docker.yaml
export ELETTRA_CONFIG_FILE="$PWD/config/elettra-config.docker.yaml"
uvicorn main:app --host 0.0.0.0 --port 8000
```

Before starting, edit the copied configuration so `database.url` points to a
reachable PostgreSQL instance. Selected settings can also be overridden by
environment variables, including `DATABASE_URL`, `APP_SECRET_KEY`,
`APP_ALLOWED_ORIGINS`, `APP_DEBUG`, and `APP_LOG_LEVEL`.

Configuration is fail-closed: required keys must be present in the selected
YAML/JSON file. The application does not silently fill a missing deployment
configuration.

## Current HVAC policy

New feasibility and yearly runs record the auxiliary-energy policy used for
reproduction:

- feasibility maps temperature to one engineering GHI value, shared by Mean
  and Q95 evaluation;
- yearly analysis uses hourly PVGIS GHI interpolated at every service minute,
  with the saved temperature clusters, GTFS timezone, daylight saving, and
  PVGIS offset;
- terminal HVAC covers at most the final 10 minutes at the same stop, uses an
  empty bus, excludes depots, and does not model thermal inertia;
- fixed auxiliaries are applied during trips, not during terminal waiting.

The authenticated auxiliary-assumptions endpoints expose the policy summary
and, on request, the complete reproduction inputs. Historical runs retain their
recorded legacy behavior. See
[HVAC operational policy](docs/hvac-operational-policy.md) and
[VECTO HVAC templates](docs/VECTO_HVAC_TEMPLATES.md).

## Bus models and LCA size resolution

New bus models store both the user-selected LCA category in `specs.size` and
the physical length in `specs.bus_length_m`. Environmental calculations use
the explicit `size` when present. For legacy rows only, a validated physical
length is converted to the catalogue category as follows:

- `<= 10 m` → `9m`
- `> 10 m` and `<= 14 m` → `13m`
- `> 14 m` → `18m`

Missing, boolean, non-finite, non-numeric, or non-positive lengths are rejected
instead of guessed. The same resolver is used by vehicle-block and yearly LCA
endpoints. No category is inferred from the model name.

## Database initialization

The local PostgreSQL container initializes an empty volume from
`db/elettra_schema_init.sql`. Existing deployments must apply the required
migrations/schema updates explicitly; mounting the initialization script does
not migrate a populated volume.

See:

- [Automatic database initialization](docs/DB_AUTO_INIT.md)
- [Database schema updates](docs/database-schema-updates.md)
- [GTFS datasets](docs/gtfs-datasets.md)

## Tests

The full release gate is the pytest suite:

```bash
./run_tests.sh
```

The wrapper activates `.venv` when available, sets the test environment,
validates that local database/backend targets are the dedicated staging stack,
and forwards additional pytest arguments. For example:

```bash
./run_tests.sh tests/test_lca_size.py -q
./run_tests.sh -k yearly -q
```

Tests that require deployment GTFS data, OSRM, MinIO, or private model artifacts
remain explicit opt-in integration tests. Do not interpret those skips as unit
coverage of the external service.

## Documentation

- [API reference](docs/API_REFERENCE.md)
- [Aggregate feasibility contract](docs/aggregate-feasibility.md)
- [Grid energy and annual LCA](docs/GRID_BUS_ENERGY_AND_LCA.md)
- [Diesel-heating emissions](docs/DIESEL_HEATING_EMISSIONS.md)
- [Hybrid TMY weather](docs/hybrid-tmy-pvgis-openmeteo.md)
- [HVAC operational policy](docs/hvac-operational-policy.md)
- [Feature extraction contract](docs/FEATURE_EXTRACTION_CONTRACT.md)
- [Threat response guide](docs/THREAT_RESPONSE_GUIDE.md)

## Release

`.github/workflows/container-release.yml` checks that the semantic tag matches
the application version in `config/elettra-config.image.yaml`, initializes a
clean PostgreSQL database, runs pytest, builds native AMD64/ARM64 images, and
publishes the release only after verification succeeds.
