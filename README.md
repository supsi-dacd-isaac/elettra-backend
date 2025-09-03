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
15. Dockerization

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
## 15. Dockerization
Two supported compose modes are provided:

### 15.0 Quick Docker Run (External Host DB)
1. Build image:
   ```bash
   docker build -t elettra-backend .
   ```
2. Run container with host networking (recommended for external DB on host):
   ```bash
   docker run -d \
     --network host \
     -v "$(pwd)/../elettra-backend-data/elevation_profiles:/app/data/elevation_profiles:ro" \
     -e ELETTRA_CONFIG_FILE=/app/config/elettra-config.docker.yaml \
     -e DATABASE_URL="postgresql+asyncpg://admin:admin@localhost:5440/elettra" \
     --name elettra-api \
     elettra-backend
   ```
   
   > **Note**: The path `$(pwd)/../elettra-backend-data/elevation_profiles` is just an example. Replace it with the actual path to your elevation profiles directory.
3. Verify:
   ```bash
   curl http://localhost:8000/    # should return status JSON
   docker logs -f elettra-api     # watch startup logs
   ```

> **Note**: Using `--network host` allows the container to access services running on the host (like your PostgreSQL database) directly via `localhost`. The `-v` flag mounts the elevation profiles directory as read-only so the API can access elevation data files.

**Alternative for cross-platform compatibility** (if host networking is not available):
```bash
docker run -d \
  --add-host=host.docker.internal:host-gateway \
  -p 8000:8000 \
  -v "$(pwd)/../elettra-backend-data/elevation_profiles:/app/data/elevation_profiles:ro" \
  -e DATABASE_URL="postgresql+asyncpg://admin:admin@host.docker.internal:5440/elettra" \
  --name elettra-api \
  elettra-backend
```

Optional (env file to shorten command):
```bash
cp .env.docker.example .env.docker
# edit credentials in .env.docker
docker run -d \
  --network host \
  -v "$(pwd)/../elettra-backend-data/elevation_profiles:/app/data/elevation_profiles:ro" \
  --env-file .env.docker \
  --name elettra-api \
  elettra-backend
```

### 15.1 Internal Ephemeral Database (Full Stack)
Uses `docker-compose.yml` to spin up both the API (`app`) and a Postgres service (`db`). Data persists in the named volume `db-data`.

Run:
```bash
docker compose up --build -d
# View logs
docker compose logs -f app
```
Tear down (preserving data volume):
```bash
docker compose down
```
Remove volumes (DESTROYS db data):
```bash
docker compose down -v
```

### 15.2 External / Managed Database
If you already run PostgreSQL elsewhere, use `docker-compose.external.yml` which only launches the API container and connects to your external DB via the `DATABASE_URL` environment variable.

Example run:
```bash
export DATABASE_URL="postgresql+asyncpg://$user:$password@$host:5432/elettra"
docker compose -f docker-compose.external.yml up --build -d
```

### 15.3 Configuration Files inside Containers
By default the image expects: `ELETTRA_CONFIG_FILE=/app/config/elettra-config.docker.yaml`.
Mount (read‑only) your tailored config:
```bash
# In compose service definition (already present):
volumes:
  - ./config/elettra-config.docker.yaml:/app/config/elettra-config.docker.yaml:ro
```
Only a *placeholder* `database_url` inside the file is required because `DATABASE_URL` env will override it if provided (see overrides in `app/core/config.py`).

### 15.4 Environment Overrides
The loader allows these direct env overrides (case sensitive):
- `DATABASE_URL` → replaces `database_url`
- `APP_LOG_LEVEL` → replaces `log_level`
- `APP_DEBUG` (`true/false/1/0`) → replaces `debug`
- `APP_ALLOWED_ORIGINS` (comma separated)
- `APP_SECRET_KEY`

### 15.5 Development (Live Reload) in Docker
For rapid iteration you can:
1. Uncomment the source bind mount in `docker-compose.yml` or `docker-compose.external.yml`:
   ```yaml
   volumes:
     - ./:/app
   ```
2. Override the command to enable reload:
   ```yaml
   command: ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
   ```
Reload is **not** recommended for production.

### 15.6 Manual Image Build, Run & other commands
(See 15.0 for preferred external DB run.)
```bash
docker build -t elettra-backend .
# Preferred: use host networking with elevation data
docker run -d \
  --network host \
  -v "$(pwd)/../elettra-backend-data/elevation_profiles:/app/data/elevation_profiles:ro" \
  -e DATABASE_URL="postgresql+asyncpg://$user:$password@localhost:5440/elettra" \
  --name elettra-api elettra-backend

# Alternative: explicit port mapping with host gateway
docker run -d \
  --add-host=host.docker.internal:host-gateway \
  -p 8000:8000 \
      -v "$(pwd)/../elettra-backend-data/elevation_profiles:/app/data/elevation_profiles:ro" \
  -e DATABASE_URL="postgresql+asyncpg://$user:$password@host.docker.internal:5440/elettra" \
  --name elettra-api elettra-backend

# Container management
docker start elettra-api    # Start if stopped
docker stop elettra-api     # Stop the container
docker rm elettra-api       # Remove the container
```

### 15.7 Health & Logs
- Basic health: `curl http://localhost:8000/`
- Container logs: `docker logs -f elettra-api`

### 15.8 Security Notes
- Always set a strong `APP_SECRET_KEY` (override value in config file).
- Restrict exposed ports in production (e.g. run behind a reverse proxy / API gateway).
- Use separate, least‑privilege DB credentials for production.
- When using `--network host`, the container shares the host's network stack entirely.

### 15.9 Upgrading
```bash
docker compose pull && docker compose up -d --build
# or for external setup
docker compose -f docker-compose.external.yml up -d --build
```

### 15.10 Troubleshooting
| Symptom | Action |
|---------|--------|
| App cannot reach DB | Verify `DATABASE_URL` host/port, firewalls. Use `--network host` for localhost DB access |
| Connection refused | Ensure database is running and accessible. Check if using correct host (localhost vs host.docker.internal) |
| `psycopg2` / build errors | Ensure base image has `libpq-dev` & build-essential (already in Dockerfile) |
| Config file not found | Confirm `ELETTRA_CONFIG_FILE` path inside container and mounted volume path |
| 403/401 on endpoints | Ensure Authorization header contains fresh JWT (re-login if expired) |
| Changes not reflected | Use `--reload` + bind mount (dev only) |
| Port already in use | Stop existing containers or use different port mapping |

### 15.11 Network Modes Explained
- **Host networking** (`--network host`): Container shares host's network stack. Simplest for local development.
- **Bridge networking** (default): Container gets its own IP. Requires port mapping (`-p`) and special host access.
- **Custom networks**: For complex multi-container setups with internal communication.

---
**Elettra** – Foundation for electric public transport analytics.
