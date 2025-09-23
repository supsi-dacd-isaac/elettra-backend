# Elettra Backend API Reference

> This document reflects the **current** implemented endpoints as of this revision.
>
> Base URL examples below assume: `http://127.0.0.1:8002` (mapped from container port 8000).

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
- `DEL` = Delete (DELETE)

---

## 1. Authentication (`/auth`)

| Method | Path                    | Purpose                           |
|--------|-------------------------|-----------------------------------|
| POST   | /auth/login             | Obtain JWT access token           |
| POST   | /auth/register          | Register new user                 |
| GET    | /auth/me                | Current authenticated user profile |
| PUT    | /auth/me                | Update user profile                |
| PUT    | /auth/me/password       | Update user password               |
| DELETE | /auth/me                | Delete user account                |
| POST   | /auth/logout            | Logout user                       |
| GET    | /auth/check-email/{email} | Check email availability         |

### 1.1 Login
```bash
curl -X POST http://127.0.0.1:8002/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"user@example.com","password":"Secret123"}'
```
Response:
```json
{"access_token": "<jwt>", "token_type": "bearer"}
```

### 1.2 Register
```bash
curl -X POST http://127.0.0.1:8002/auth/register \
  -H 'Content-Type: application/json' \
  -d '{
    "company_id": "<agency_uuid>",
    "email": "admin@example.com",
    "full_name": "Admin User",
    "password": "Secret123",
    "role": "admin"
  }'
```

### 1.3 Current User Profile
```bash
curl -H 'Authorization: Bearer $TOKEN' http://127.0.0.1:8002/auth/me
```

### 1.4 Update User Profile
```bash
curl -X PUT http://127.0.0.1:8002/auth/me \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{"full_name": "New Name", "role": "analyst"}'
```

### 1.5 Update Password
```bash
curl -X PUT http://127.0.0.1:8002/auth/me/password \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{"current_password": "OldPass123", "new_password": "NewPass123"}'
```

### 1.6 Logout
```bash
curl -X POST http://127.0.0.1:8002/auth/logout \
  -H 'Authorization: Bearer $TOKEN'
```

### 1.7 Check Email Availability
```bash
curl http://127.0.0.1:8002/auth/check-email/user@example.com
```

---

## 2. Users Management (Admin Only) (`/api/v1/agency/users`)

| Op  | Method | Path                          |
|-----|--------|-------------------------------|
| LST | GET    | /api/v1/agency/users/?skip=0&limit=50 |
| CRE | POST   | /api/v1/agency/users/        |
| RD  | GET    | /api/v1/agency/users/{user_id} |
| UPD | PUT    | /api/v1/agency/users/{user_id} |

Create:
```bash
curl -X POST http://127.0.0.1:8002/api/v1/agency/users/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "company_id": "<agency_uuid>",
    "email": "analyst@example.com",
    "full_name": "Analyst A",
    "role": "analyst",
    "password": "Secret123"
  }'
```

Update:
```bash
curl -X PUT http://127.0.0.1:8002/api/v1/agency/users/<user_id> \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{"full_name": "New Name"}'
```

---

## 3. Agencies (`/api/v1/agency/agencies`)

| Op  | Method | Path                                |
|-----|--------|-------------------------------------|
| LST | GET    | /api/v1/agency/agencies/            |
| CRE | POST   | /api/v1/agency/agencies/            |
| RD  | GET    | /api/v1/agency/agencies/{agency_id} |

Create:
```bash
curl -X POST http://127.0.0.1:8002/api/v1/agency/agencies/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "gtfs_agency_id": "801",
    "agency_name": "Sample Transit Co",
    "agency_url": "https://example.com",
    "agency_timezone": "Europe/Zurich"
  }'
```

---

## 4. GTFS Routes (`/api/v1/gtfs/gtfs-routes`)

| Op  | Method | Path                                             |
|-----|--------|--------------------------------------------------|
| LST | GET    | /api/v1/gtfs/gtfs-routes/                        |
| CRE | POST   | /api/v1/gtfs/gtfs-routes/                        |
| RD  | GET    | /api/v1/gtfs/gtfs-routes/{route_id}              |
| LST | GET    | /api/v1/gtfs/gtfs-routes/by-agency/{agency_id}   |
| LST | GET    | /api/v1/gtfs/gtfs-routes/by-agency/{agency_id}/with-variant-1 |
| LST | GET    | /api/v1/gtfs/gtfs-routes/by-agency/{agency_id}/with-largest-variant |
| LST | GET    | /api/v1/gtfs/gtfs-routes/by-stop/{stop_id}       |

Create:
```bash
curl -X POST http://127.0.0.1:8002/api/v1/gtfs/gtfs-routes/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "route_id": "96-990-j25-1",
    "agency_id": "<agency_uuid>",
    "route_short_name": "165",
    "route_type": 700
  }'
```

---

## 5. GTFS Trips (`/api/v1/gtfs/gtfs-trips`)

| Op  | Method | Path                                             |
|-----|--------|--------------------------------------------------|
| LST | GET    | /api/v1/gtfs/gtfs-trips/by-route/{route_id}     |
| LST | GET    | /api/v1/gtfs/gtfs-trips/by-stop/{stop_id}       |
| CRE | POST   | /api/v1/gtfs/gtfs-trips/                         |
| UPD | PUT    | /api/v1/gtfs/gtfs-trips/{trip_pk}                |
| DEL | DELETE | /api/v1/gtfs/gtfs-trips/{trip_pk}                |

### 5.1 Create Auxiliary Trip (Depot or Transfer)
```
POST /api/v1/gtfs/aux-trip
```
Body:
```json
{
  "departure_stop_id": "<uuid>",
  "arrival_stop_id": "<uuid>",
  "departure_time": "HH:MM:SS",
  "arrival_time": "HH:MM:SS",
  "route_id": "<uuid>",
  "status": "depot | transfer | service | school | other",
  "calendar_service_key": "auxiliary"
}
```
Behavior:
- Computes OSRM route geometry between the two stops
- Uses SwissTopo to compute elevation profile and stores parquet in MinIO bucket `elevation-profiles` as `{shape_id}.parquet`
- Looks up `gtfs_calendar.service_id` using `calendar_service_key` (default `auxiliary`)
- Creates a new trip with `status` set from the request, `gtfs_service_id` set to the calendar key, and `trip_id`/`shape_id` prefixed by the status
- Inserts two stop_times: departure (seq 1) and arrival (seq 2)
Returns the created trip (`GtfsTripsRead`).

---

## 6. GTFS Stops (`/api/v1/gtfs/gtfs-stops`)

| Op  | Method | Path                                             |
|-----|--------|--------------------------------------------------|
| LST | GET    | /api/v1/gtfs/gtfs-stops/                         |
| CRE | POST   | /api/v1/gtfs/gtfs-stops/                         |
| RD  | GET    | /api/v1/gtfs/gtfs-stops/{stop_pk}                |
| UPD | PUT    | /api/v1/gtfs/gtfs-stops/{stop_pk}                |
| DEL | DELETE | /api/v1/gtfs/gtfs-stops/{stop_pk}                |
| LST | GET    | /api/v1/gtfs/gtfs-stops/by-trip/{trip_id}        |

---

## 7. Variants (`/api/v1/gtfs/variants`)

| Op  | Method | Path                                             |
|-----|--------|--------------------------------------------------|
| LST | GET    | /api/v1/gtfs/variants/by-route/{route_id}       |
| RD  | GET    | /api/v1/gtfs/variants/{route_id}/{variant_num}  |

---

## 8. Calendar (`/api/v1/gtfs/gtfs-calendar`)

| Op  | Method | Path                                             |
|-----|--------|--------------------------------------------------|
| LST | GET    | /api/v1/gtfs/gtfs-calendar/by-trip/{trip_id}    |

---

## 9. Bus Models (`/api/v1/user/bus-models`)

| Op  | Method | Path                                   |
|-----|--------|----------------------------------------|
| LST | GET    | /api/v1/user/bus-models/               |
| CRE | POST   | /api/v1/user/bus-models/               |
| RD  | GET    | /api/v1/user/bus-models/{model_id}     |
| UPD | PUT    | /api/v1/user/bus-models/{model_id}     |
| DEL | DELETE | /api/v1/user/bus-models/{model_id}     |

Create:
```bash
curl -X POST http://127.0.0.1:8002/api/v1/user/bus-models/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "user_id": "<user_uuid>",
    "name": "test_bus02",
    "specs": {"battery_kwh": 400},
    "manufacturer": "Hess"
  }'
```

Update:
```bash
curl -X PUT http://127.0.0.1:8002/api/v1/user/bus-models/<model_id> \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{"manufacturer": "Hess AG"}'
```

---

## 10. Buses (`/api/v1/user/buses`)

| Op  | Method | Path                                   |
|-----|--------|----------------------------------------|
| LST | GET    | /api/v1/user/buses/                    |
| CRE | POST   | /api/v1/user/buses/                    |
| RD  | GET    | /api/v1/user/buses/{bus_id}            |
| UPD | PUT    | /api/v1/user/buses/{bus_id}            |
| DEL | DELETE | /api/v1/user/buses/{bus_id}            |

Create:
```bash
curl -X POST http://127.0.0.1:8002/api/v1/user/buses/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "user_id": "<user_uuid>",
    "model_id": "<bus_model_uuid>",
    "depot_id": "<depot_uuid>",
    "license_plate": "ZH-123456",
    "status": "active"
  }'
```

---

## 11. Depots (`/api/v1/user/depots`)

| Op  | Method | Path                                   |
|-----|--------|----------------------------------------|
| LST | GET    | /api/v1/user/depots/                   |
| CRE | POST   | /api/v1/user/depots/                   |
| RD  | GET    | /api/v1/user/depots/{depot_id}         |
| UPD | PUT    | /api/v1/user/depots/{depot_id}         |
| DEL | DELETE | /api/v1/user/depots/{depot_id}         |

Create:
```bash
curl -X POST http://127.0.0.1:8002/api/v1/user/depots/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "user_id": "<user_uuid>",
    "name": "Main Depot",
    "address": "123 Depot Street",
    "latitude": 46.5197,
    "longitude": 6.6323,
    "capacity": 50
  }'
```

---

## 12. Shifts (`/api/v1/user/shifts`)

| Op  | Method | Path                                   |
|-----|--------|----------------------------------------|
| LST | GET    | /api/v1/user/shifts/                   |
| CRE | POST   | /api/v1/user/shifts/                   |
| RD  | GET    | /api/v1/user/shifts/{shift_id}         |
| UPD | PUT    | /api/v1/user/shifts/{shift_id}         |
| DEL | DELETE | /api/v1/user/shifts/{shift_id}         |

Create:
```bash
curl -X POST http://127.0.0.1:8002/api/v1/user/shifts/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "user_id": "<user_uuid>",
    "bus_id": "<bus_uuid>",
    "depot_id": "<depot_uuid>",
    "shift_name": "Morning Shift",
    "start_time": "06:00:00",
    "end_time": "14:00:00",
    "day_of_week": "monday"
  }'
```

---

## 13. Simulation Runs (`/api/v1/simulation/simulation-runs`)

| Op  | Method | Path                                        |
|-----|--------|---------------------------------------------|
| LST | GET    | /api/v1/simulation/simulation-runs/         |
| CRE | POST   | /api/v1/simulation/simulation-runs/         |
| RD  | GET    | /api/v1/simulation/simulation-runs/{run_id} |
| UPD | PUT    | /api/v1/simulation/simulation-runs/{run_id} |
| RD  | GET    | /api/v1/simulation/simulation-runs/{run_id}/results |

Create:
```bash
curl -X POST http://127.0.0.1:8002/api/v1/simulation/simulation-runs/ \
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
curl -X PUT http://127.0.0.1:8002/api/v1/simulation/simulation-runs/<run_id> \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{"status": "running"}'
```

---

## 14. External Services Integration

### 14.1 OSRM Driving Distance (`/api/v1/gtfs/osrm/driving-distance`)
```bash
curl -H 'Authorization: Bearer $TOKEN' \
  "http://127.0.0.1:8002/api/v1/gtfs/osrm/driving-distance?start_lat=46.5197&start_lon=6.6323&end_lat=46.5200&end_lon=6.6325"
```

### 14.2 Elevation Profile (`/api/v1/gtfs/elevation-profile/by-trip/{trip_id}`)
```bash
curl -H 'Authorization: Bearer $TOKEN' \
  http://127.0.0.1:8002/api/v1/gtfs/elevation-profile/by-trip/<trip_id>
```

### 14.3 PVGIS TMY Data (`/api/v1/simulation/pvgis-tmy/`)
```bash
curl -H 'Authorization: Bearer $TOKEN' \
  "http://127.0.0.1:8002/api/v1/simulation/pvgis-tmy/?lat=46.5197&lon=6.6323"
```

---

## 15. Health & Status

### 15.1 Root Health
```
GET /
```
Example:
```bash
curl http://127.0.0.1:8002/
```
Response:
```json
{
  "message": "Elettra Backend",
  "status": "running",
  "version": "1.0.0"
}
```

### 15.2 Detailed Health Check
```
GET /health
```
Example:
```bash
curl http://127.0.0.1:8002/health
```
Response:
```json
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:30:00Z",
  "version": "1.0.0",
  "services": {
    "database": {
      "status": "healthy",
      "message": "Database connection successful",
      "response_time_ms": 15.23,
      "last_checked": "2024-01-15T10:30:00Z"
    },
    "application": {
      "status": "healthy",
      "message": "Application is running",
      "last_checked": "2024-01-15T10:30:00Z"
    }
  },
  "uptime_seconds": 3600.5
}
```

---

## 16. Error Handling & Status Codes

| Scenario                          | Code |
|----------------------------------|------|
| Unauthorized / Missing token     | 401 / 403 (depending on auth dependency) |
| Not found (entity by UUID)       | 404  |
| Duplicate unique constraint      | 400 / 409 (depends on raised exception mapping) |
| Validation error (Pydantic)      | 422  |
| Server error                     | 500  |

---

## 17. JSON Schemas
For full request/response field definitions consult the Swagger UI at `http://127.0.0.1:8002/docs` or `app/schemas/` (Pydantic models). All read models use `from_attributes=True` enabling ORM object serialization.

---

## 18. Service Ports (Docker Compose)
- **Backend API**: 8002 (mapped from container port 8000)
- **Frontend**: 55557 (mapped from container port 80)
- **Database**: 5440 (mapped from container port 5432)
- **OSRM**: 5001 (mapped from container port 5000)
- **MinIO**: 9002 (API), 9003 (Console)

---

## 19. Roadmap Notes
Planned future additions (not yet present here):
- GTFS dataset ingestion & shape enrichment.
- Energy / battery sizing algorithm endpoints.
- Advanced filtering & pagination across large GTFS tables.

---

**Last Updated:** 2024-01-15