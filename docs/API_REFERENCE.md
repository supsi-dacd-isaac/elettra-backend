# Elettra Backend API Reference

> This document reflects the **current** implemented endpoints (routers: `auth`, `api`) as of this revision.
>
> Base URL examples below assume: `http://127.0.0.1:8000`.

---
## Conventions
- All JSON bodies are `application/json`.
- Authenticated endpoints require header: `Authorization: Bearer <JWT>`.
- UUID path parameters use the internal primary key (not the GTFS string IDs except where noted).
- Pagination (where supported) uses `?skip=<int>&limit=<int>`.

Legend:
- `CRE` = Create (POST)
- `RD`  = Read (GET single)
- `LST` = List (GET collection)
- `UPD` = Update (PUT)

---
## 1. Authentication (`/auth`)
| Method | Path         | Purpose                |
|--------|--------------|------------------------|
| POST   | /auth/login  | Obtain JWT access token|
| POST   | /auth/register | Register new user (requires valid company/agency UUID) |
| GET    | /auth/me     | Current authenticated user profile |

### 1.1 Login
```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"user@example.com","password":"Secret123"}'
```
Response:
```json
{"access_token": "<jwt>", "token_type": "bearer"}
```

### 1.2 Register
```bash
curl -X POST http://127.0.0.1:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{
    "company_id": "<agency_uuid>",
    "email": "admin@example.com",
    "full_name": "Admin User",
    "password": "Secret123",
    "role": "admin"
  }'
```

### 1.3 Current User
```bash
curl -H 'Authorization: Bearer $TOKEN' http://127.0.0.1:8000/auth/me
```

---
## 2. Users (Admin Only) (`/api/v1/users`)
| Op  | Method | Path                          |
|-----|--------|-------------------------------|
| LST | GET    | /api/v1/users/?skip=0&limit=50 |
| CRE | POST   | /api/v1/users/                |
| RD  | GET    | /api/v1/users/{user_uuid}     |
| UPD | PUT    | /api/v1/users/{user_uuid}     |

Create:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/users/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "company_id": "<agency_uuid>",
    "email": "analyst@example.com",
    "full_name": "Analyst A",
    "role": "analyst"
  }'
```

Update (example):
```bash
curl -X PUT http://127.0.0.1:8000/api/v1/users/<user_uuid> \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{"full_name": "New Name"}'
```

---
## 3. Agencies (`/api/v1/agencies`)
| Op  | Method | Path                                |
|-----|--------|-------------------------------------|
| LST | GET    | /api/v1/agencies/                   |
| CRE | POST   | /api/v1/agencies/                   |
| RD  | GET    | /api/v1/agencies/{agency_uuid}      |

Create:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/agencies/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "gtfs_agency_id": "801",
    "agency_name": "Sample Transit Co",
    "agency_url": "https://example.com",
    "agency_timezone": "Europe/Zurich"
  }'
```

---
## 4. GTFS Routes (`/api/v1/gtfs-routes`)
| Op  | Method | Path                                             |
|-----|--------|--------------------------------------------------|
| LST | GET    | /api/v1/gtfs-routes/                             |
| CRE | POST   | /api/v1/gtfs-routes/                             |
| RD  | GET    | /api/v1/gtfs-routes/{route_uuid}                 |
| LST | GET    | /api/v1/gtfs-routes/by-agency/{agency_uuid}      |

Create:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/gtfs-routes/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "route_id": "96-990-j25-1",
    "agency_id": "<agency_uuid>",
    "route_short_name": "165",
    "route_type": 700
  }'
```

---
## 5. Trips & Stops Relationships
### 5.1 Trips by Route
```
GET /api/v1/gtfs-trips/by-route/{route_uuid}
```
Example:
```bash
curl -H 'Authorization: Bearer $TOKEN' \
  http://127.0.0.1:8000/api/v1/gtfs-trips/by-route/<route_uuid>
```

### 5.2 Create Depot Trip
```
POST /api/v1/gtfs/depot-trip
```
Body:
```json
{
  "departure_stop_id": "<uuid>",
  "arrival_stop_id": "<uuid>",
  "departure_time": "HH:MM:SS",
  "arrival_time": "HH:MM:SS",
  "route_id": "<uuid>"
}
```
Behavior:
- Computes OSRM route geometry between the two stops
- Uses SwissTopo to compute elevation profile and stores parquet in MinIO bucket `elevation-profiles` as `{shape_id}.parquet`
- Creates a new trip with `status=depot`, `gtfs_service_id=depot`, and `service_id` referencing the `gtfs_calendar` row where `service_id='depot'`
- Inserts two stop_times: departure (seq 1) and arrival (seq 2)
Returns the created trip (`GtfsTripsRead`).

### 5.2 Trips by Stop
```
GET /api/v1/gtfs-trips/by-stop/{stop_uuid}
```

### 5.3 Stops by Trip
```
GET /api/v1/gtfs-stops/by-trip/{trip_uuid}
```

---
## 6. Variants (`/api/v1/variants`)
| Op  | Method | Path                                  |
|-----|--------|---------------------------------------|
| LST | GET    | /api/v1/variants/by-route/{route_uuid} |

---
## 7. Calendar (`/api/v1/gtfs-calendar`)
| Op  | Method | Path                                |
|-----|--------|-------------------------------------|
| LST | GET    | /api/v1/gtfs-calendar/by-trip/{trip_uuid} |

> NOTE: Endpoint currently filters calendar rows by `trip_id`; underlying model link may evolve.

---
## 8. Bus Models (`/api/v1/bus-models`)
| Op  | Method | Path                                   |
|-----|--------|----------------------------------------|
| LST | GET    | /api/v1/bus-models/                    |
| CRE | POST   | /api/v1/bus-models/                    |
| RD  | GET    | /api/v1/bus-models/{model_uuid}        |
| UPD | PUT    | /api/v1/bus-models/{model_uuid}        |

Create:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/bus-models/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "company_id": "<agency_uuid>",
    "name": "test_bus02",
    "specs": {"battery_kwh": 400},
    "manufacturer": "Hess"
  }'
```

Update:
```bash
curl -X PUT http://127.0.0.1:8000/api/v1/bus-models/<model_uuid> \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{"manufacturer": "Hess AG"}'
```

---
## 9. Simulation Runs (`/api/v1/simulation-runs`)
| Op  | Method | Path                                        |
|-----|--------|---------------------------------------------|
| LST | GET    | /api/v1/simulation-runs/                    |
| CRE | POST   | /api/v1/simulation-runs/                    |
| RD  | GET    | /api/v1/simulation-runs/{run_uuid}          |
| UPD | PUT    | /api/v1/simulation-runs/{run_uuid}          |

Create:
```bash
curl -X POST http://127.0.0.1:8000/api/v1/simulation-runs/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "user_id": "<user_uuid>",
    "input_params": {"note": "initial run"},
    "status": "pending",
    "variant_id": "<variant_uuid>"
  }'
```

Update:
```bash
curl -X PUT http://127.0.0.1:8000/api/v1/simulation-runs/<run_uuid> \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{"status": "running"}'
```

---
## 10. Root Health
```
GET /
```
Example:
```bash
curl http://127.0.0.1:8000/
```

---
## 11. Error Handling & Status Codes (Summary)
| Scenario                          | Code |
|----------------------------------|------|
| Unauthorized / Missing token     | 401 / 403 (depending on auth dependency) |
| Not found (entity by UUID)       | 404  |
| Duplicate unique constraint      | 400 / 409 (depends on raised exception mapping) |
| Validation error (Pydantic)      | 422  |

---
## 12. JSON Schemas
For full request/response field definitions consult the Swagger UI or `app/schemas.py` (Pydantic models). All read models use `from_attributes=True` enabling ORM object serialization.

---
## 13. Roadmap Notes
Planned future additions (not yet present here):
- GTFS dataset ingestion & shape enrichment.
- Energy / battery sizing algorithm endpoints.
- Advanced filtering & pagination across large GTFS tables.

---
**Last Updated:** (auto-maintain manually) YYYY-MM-DD

