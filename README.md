# Elettra Backend

FastAPI backend for managing GTFS agencies/routes/trips, electric bus models and simulation run metadata with JWT authentication.

> NOTE: This README reflects the CURRENT exposed API (routers: `auth`, `api`). Historical documentation about pfaedle, GTFS dataset ingestion, energy algorithms, etc. was removed because those endpoints are not presently implemented in `app/routers`. See *Roadmap* for planned re‑introductions.

---
## Table of Contents
1. Features
2. Tech Stack
3. Project Structure
4. Quick Start
5. Configuration
6. Database Setup
7. Running the App
8. Available API Endpoints
9. Authentication Flow
10. Bus Model & Simulation Examples
11. Testing
12. Development Tips
13. Roadmap
14. License & Acknowledgments

---
## 1. Features
Current implemented features:
- JWT authentication (`/auth/login`, `/auth/register`, `/auth/me`).
- User administration (list/create/read/update) – restricted to admins.
- GTFS base entities: Agencies, Routes (as `gtfs-routes`), Trips, Stops (lookup via stop_times relationships), Variants.
- Bus model catalog per agency (company) with JSON specs field.
- Simulation runs metadata (create / update / list / read).
- Pydantic v2 configuration loading from YAML/JSON via `ELETTRA_CONFIG_FILE`.
- Automatic OpenAPI docs with persisted authorization.
- Structured test reporting (see `tests/README.md`).

Out of scope (not currently exposed): route energy algorithms, GTFS dataset ingestion pipeline, pfaedle shape enhancement, battery sizing endpoints.

---
## 2. Tech Stack
- Python 3.12+
- FastAPI / Starlette
- SQLAlchemy 2.0 (async engine + models under `app/models.py` for GTFS tables; legacy models in `app/database.py` kept for compatibility)
- PostgreSQL / asyncpg
- JWT (`python-jose`)
- Pydantic v2 / pydantic-settings
- Pytest (custom reporting)

---
## 3. Project Structure (trimmed)
```
app/
  core/            # auth & config
  routers/         # auth.py, api.py (all endpoints)
  models.py        # ORM models (gtfs_*, variants, bus_models, etc.)
  database.py      # Legacy/alternative ORM models & async session
config/
  elettra-config.yaml
tests/
  auth/            # authentication test module
  reports/         # generated verbose + JSON reports
  README.md        # test suite details
main.py            # FastAPI app factory + router mounting
README.md          # (this file)
```

---
## 4. Quick Start
```bash
git clone <repo-url>
cd elettra-backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export ELETTRA_CONFIG_FILE="$(pwd)/config/elettra-config.yaml"
python main.py  # runs uvicorn via __main__ guard
```
Visit: http://127.0.0.1:8000/docs

---
## 5. Configuration
All required settings live in `config/elettra-config.yaml` (or a JSON equivalent). The loader expects *all* keys to be present; no silent defaults.
Set with env var:
```
export ELETTRA_CONFIG_FILE=/absolute/path/to/elettra-config.yaml
```
See file for adjustable values: database_url, CORS origins, JWT secret, etc.

---
## 6. Database Setup
Minimal required extensions (example PostgreSQL):
```sql
CREATE DATABASE elettra;
\c elettra;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()
```
You must create tables according to the schema your deployment uses (e.g. run migrations or pre‑existing SQL). Current code assumes tables already exist matching models in `app/models.py` (e.g. `public.gtfs_agencies`, `public.gtfs_routes`, ...).

---
## 7. Running the App
Development (auto‑reload controlled by config file):
```bash
python main.py
```
Alternative explicit uvicorn:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
Production (basic example):
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

---
## 8. Available API Endpoints (Current)
(Prefix `/api/v1` for core API; `/auth` for authentication.)

Authentication:
- POST `/auth/login` – obtain JWT
- POST `/auth/register` – create user (email uniqueness enforced)
- GET  `/auth/me` – current user info (requires Bearer token)

Users (admin only):
- POST `/api/v1/users/`
- GET  `/api/v1/users/` (pagination via `skip`, `limit`)
- GET  `/api/v1/users/{user_id}`
- PUT  `/api/v1/users/{user_id}`

Agencies:
- POST `/api/v1/agencies/`
- GET  `/api/v1/agencies/`
- GET  `/api/v1/agencies/{agency_id}`

GTFS Routes (named `gtfs-routes`):
- POST `/api/v1/gtfs-routes/`
- GET  `/api/v1/gtfs-routes/`
- GET  `/api/v1/gtfs-routes/{route_uuid}` (internal UUID key)
- GET  `/api/v1/gtfs-routes/by-agency/{agency_uuid}`

Trips:
- GET `/api/v1/gtfs-trips/by-route/{route_uuid}`
- GET `/api/v1/gtfs-trips/by-stop/{stop_uuid}` (reverse lookup via stop_times)

Stops:
- GET `/api/v1/gtfs-stops/by-trip/{trip_uuid}` (ordered by sequence)

Variants:
- GET `/api/v1/variants/by-route/{route_uuid}`

Calendar:
- GET `/api/v1/gtfs-calendar/by-trip/{trip_uuid}` *(Note: current query uses a direct filter; underlying schema may need refinement to join via trips' service reference.)*

Bus Models:
- POST `/api/v1/bus-models/`
- GET  `/api/v1/bus-models/`
- GET  `/api/v1/bus-models/{model_uuid}`
- PUT  `/api/v1/bus-models/{model_uuid}`

Simulation Runs:
- POST `/api/v1/simulation-runs/`
- GET  `/api/v1/simulation-runs/`
- GET  `/api/v1/simulation-runs/{run_uuid}`
- PUT  `/api/v1/simulation-runs/{run_uuid}`

Root:
- GET `/` – simple status payload

---
## 9. Authentication Flow
1. Register (or pre‑seed) a user: `POST /auth/register` with body:
```json
{
  "company_id": "<agency_uuid>",
  "email": "you@example.com",
  "full_name": "You",
  "password": "Secret123",
  "role": "admin"
}
```
2. Login: `POST /auth/login` with email/password.
3. Retrieve token from response and use: `Authorization: Bearer <token>`.
4. Swagger UI (http://localhost:8000/docs) retains token between requests.

---
## 10. Bus Model & Simulation Examples
Create a bus model:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/bus-models/ \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "company_id": "<agency_uuid>",
    "name": "eCitaro-12m",
    "specs": {"battery_kwh": 350, "length_m": 12},
    "manufacturer": "Mercedes"
  }'
```
Update a simulation run:
```bash
curl -X PUT http://127.0.0.1:8000/api/v1/simulation-runs/<run_uuid> \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"status": "running"}'
```

---
## 11. Testing
Test suite lives under `tests/` with verbose per‑module reports.
Run all tests:
```bash
pytest
```
Auth tests only:
```bash
pytest -k auth
```
Generate colored report (opt‑in):
```bash
TEST_REPORT_COLOR=1 pytest -k auth
```
Reports appear in `tests/reports/` as both `.txt` and `.json`.
See `tests/README.md` for details / adding new suites.

---
## 12. Development Tips
- Configuration: ensure `ELETTRA_CONFIG_FILE` is set *before* starting `main.py`.
- Database schema: keep ORM models (`app/models.py`) aligned with Postgres tables (UUID PKs expected; some fields rely on `gen_random_uuid()`).
- Adding endpoints: extend `app/routers/api.py`; keep request/response models in `app/schemas.py` (Pydantic v2). Use `from_attributes=True` for ORM reads.
- Authentication utilities live in `app/core/auth.py` (`authenticate_user`, `create_access_token`, permission helpers).
- Consider writing integration tests with the synchronous `client` fixture unless true async behavior is under test.

---
## 13. Roadmap
Planned / desirable re‑introductions or improvements:
- GTFS dataset ingestion & normalization pipeline.
- Shape / elevation enrichment via pfaedle & geospatial stack.
- Energy consumption & battery sizing algorithm endpoints.
- Improved calendar/trip service resolution (current calendar by trip filter may need join adjustments).
- Role-based authorization granularity across non‑user endpoints.
- Pagination & filtering for large GTFS tables.
- Dedicated migration system (e.g. Alembic) and seed scripts.
- Distinct test database & data factories.

---
## 14. License & Acknowledgments
Licensed under MIT (see `LICENSE`).

Acknowledgments:
- FastAPI & SQLAlchemy teams
- Open GTFS ecosystem
- (Future) pfaedle integration contributors

---
**Elettra** – Foundation for electric public transport analytics.
