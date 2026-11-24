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
- Looks up `gtfs_calendar.service_id` using `calendar_service_key` (default `auxiliary`)
- Atomically creates the trip, its two stop-times and a durable elevation job containing the encoded OSRM polyline
- Returns `202 Accepted` with `{ "trip": ..., "elevation_job": ... }`; no synchronous elevation request or provisional MinIO upload is made

Poll `GET /api/v1/gtfs/aux-trip/{trip_id}/elevation-status`. Until the job is
`succeeded`, elevation, statistics, prediction and optimization requests that
need the trip return `409` with code `elevation_profile_not_ready`.

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
    "specs": {
      "bus_length_m": 12,
      "empty_weight_kg": 12000,
      "battery_pack_size_kwh": 40,
      "battery_pack_weight_kg": 274,
      "min_battery_packs": 6,
      "max_battery_packs": 10,
      "max_passengers": 80,
      "max_charging_power_kw": 450
    },
    "manufacturer": "Hess"
  }'
```

The seven physical fields shown above are mandatory on create and whenever a
PUT includes `specs`. Length, empty mass, pack capacity and pack mass must be
finite and greater than zero. Pack bounds and passenger capacity must be
positive integers, with `min_battery_packs <= max_battery_packs`.
`name` is trimmed and must be a non-empty string whenever it is supplied.
`empty_weight_kg` is the vehicle mass **without traction batteries or
passengers**. Extra economic, LCA, charging and auxiliary fields are accepted
and retained.

Existing incomplete records remain readable. To edit only their metadata,
omit `specs` entirely. Explicit `null`, arrays and partial `specs` objects are
rejected with HTTP 422. Supplying a valid `specs` object replaces the complete
stored object.

Metadata-only update:
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

List items include `planner_version`, `service_weekday`, `compatible_weekdays`,
and the number of auxiliary elevation profiles still being prepared
(`pending_elevation_profiles`).
A version 1 programme exposes its recorded weekday as the sole compatible day.
For historical shifts, the list reports the intersection of the recurring
weekly calendars for GTFS line trips and for school or service trips with a
specific calendar. Depot movements, transfers, other auxiliary trips, and the
generic `auxiliary` calendar for school or service trips leave the intersection
unchanged. `null` means the calendar data is insufficient. `[]` means there is
no common recurring day. The weekday flags describe the weekly timetable.

### Sequential programmes

| Method | Path under `/api/v1/user/shift-planner` | Purpose |
|---|---|---|
| GET | `/catalog?route_id=…&weekday=monday` | Scheduled trips with their published times, first and last stop UUIDs, and parent stations |
| GET | `/route?from_stop_id=…&to_stop_id=…` | Directional road distance in metres and driving time in seconds |
| POST | `/programmes` | Compile and save an ordered programme, its auxiliary movements and their elevation jobs atomically |
| GET | `/programmes/{id}` | Reopen the programme with its timetable and revision |
| PUT | `/programmes/{id}` | Validate changes and update the specified revision |

A creation request supplies `name`, `bus_id`, `gtfs_dataset` (year and file date)
and `programme`. Version 1 programmes specify `version`, `weekday`,
`depot_id`, `start_time` and ordered `steps`. All times refer to the same
GTFS service day, including hours after midnight such as `24:43`.

- A `service` step specifies the scheduled `trip_id`. Optional
  `transfer_departure` and `transfer_arrival` describe the operating
  connection. Platforms with a common parent connect directly by default.
  Set `force_transfer` to schedule a timed movement between them.
- A `stay` step specifies the custom stop's `depot_id`, `transfer_departure`,
  `arrival` and the subsequent `departure`.
- The final `return` step specifies `transfer_departure` and `arrival`.
  The server resolves the destination to the starting depot.

Include a client-generated UUID in `request_id` when creating. A retry of
the same request reopens the saved block. Reusing the UUID with different
content returns HTTP 409. An edit requires the current `revision`; a stale
revision also returns HTTP 409. A creation request with `copy_from` can reuse
unchanged auxiliary trips from an owned source programme in the same edition.
Historical blocks use the existing shifts API.


---

## 13. Simulation (`/api/v1/simulation`)

### 13.1 Prediction Runs

| Op  | Method | Path                                                   |
|-----|--------|--------------------------------------------------------|
| CRE | POST   | /api/v1/simulation/prediction-runs/                    |
| LST | GET    | /api/v1/simulation/prediction-runs/                    |
| RD  | GET    | /api/v1/simulation/prediction-runs/{run_id}            |
| RD  | GET    | /api/v1/simulation/prediction-runs/{run_id}/predictions |

Create:
```bash
curl -X POST http://127.0.0.1:8002/api/v1/simulation/prediction-runs/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "shift_ids": ["<shift_uuid>"],
    "bus_model_id": "<bus_model_uuid>",
    "prediction_stack": "legacy",
    "external_temp_celsius": 15.0,
    "occupancy_percent": 50.0,
    "quantiles": [0.05, 0.5, 0.95]
  }'
```

`prediction_stack` accepts `legacy`, `vecto-g2`, and (when explicitly enabled
by the server) `vecto-g0-transfer`. It can be omitted to use the configured
default. `model_name` remains accepted for compatibility; if both selectors
are sent, they must identify the same registered immutable release. VECTO
responses persist the explicit mechanical, residual-QRF, fixed-auxiliary,
electrical-HVAC and diesel components on the prediction run and trip rows.
`occupancy_percent` must be a finite value from 0 through 100. For VECTO
stacks, unsupported bus lengths or incomplete scenario declarations are
rejected synchronously with `422`; no failed prediction row is scheduled.

### 13.2 Optimization Runs

| Op  | Method | Path                                                |
|-----|--------|-----------------------------------------------------|
| CRE | POST   | /api/v1/simulation/optimization-runs/               |
| LST | GET    | /api/v1/simulation/optimization-runs/               |
| RD  | GET    | /api/v1/simulation/optimization-runs/{run_id}       |

Set `service_weekday` to the study's weekday from `monday` through `sunday`.
Each selected shift must support it. The prediction catalogue must refer to the
same shifts. Both study variants save the selected day, the intersected
`compatible_weekdays`, and the verification status under
`service_weekday_provenance` in `input_params`. When an older client omits the
day, the API infers it from a verified singleton intersection. A verified
intersection with several days is stored with a null selected day. A new study requires a verifiable common weekday. Incomplete
calendars and disjoint operating days return HTTP 422. Historical studies
can still be opened with their original inputs.

Create:
```bash
curl -X POST http://127.0.0.1:8002/api/v1/simulation/optimization-runs/ \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{
    "name": "Monday feasibility study",
    "mode": "battery_only",
    "shift_ids": ["<shift_uuid>"],
    "service_weekday": "monday",
    "prediction_run_ids": ["<prediction_run_uuid>"],
    "charging_stations": [
      {
        "stop_id": "<stop_uuid>",
        "num_slots": 2,
        "max_total_power_kw": 450.0
      }
    ],
    "min_soc": 0.4,
    "max_soc": 0.9,
    "solver_name": "highs"
  }'
```

Solver tolerance:

- Omitted or null `mip_rel_gap` uses the backend environment variable
  `ELETTRA_OPTIMIZATION_MIP_REL_GAP`, default `1e-6` (0.0001%).
- An explicit `mip_rel_gap`, including `0`, overrides that default for the run.
  The effective value is saved in `input_params` when execution starts.
- The same value applies to sizing and fixed-design verification, including
  both mean and Q95 runs. It controls the relative optimality gap, not physical
  feasibility tolerances; SOC and charging constraints remain enforced.
- `mip_abs_gap` defaults to zero. Existing explicit per-request solver settings
  remain supported.

Optimization result semantics:
- `solver_status` is the solver termination/result only. It does not by itself mean the requested electrification is physically feasible.
- `electrification_feasible` is `false` whenever any bus requires `excess_packs`.
- `electrification_summary` reports whether the run is physically feasible and lists the buses/shifts that exceeded their configured `max_packs`.
- `battery_results[shift_id].optimized_packs` is the number of physical packs within the bus model bounds.
- In `battery_only` and `joint`, `optimized_packs` will be maxed out to `max_physical_packs` before any `excess_packs` are used.
- `battery_results[shift_id].excess_packs` is infeasibility slack, not a real installable pack count.
- `battery_results[shift_id].required_total_packs` equals `optimized_packs + excess_packs`.
- `total_infeasibility_penalty_chf` is the slack penalty term used to keep the MILP solvable; it is not a real battery capex figure.

Example optimization result fragment:
```json
{
  "solver_status": "optimal",
  "electrification_feasible": false,
  "electrification_summary": {
    "status": "infeasible",
    "num_buses": 1,
    "num_infeasible_buses": 1,
    "infeasible_buses": [
      {
        "shift_id": "<shift_uuid>",
        "required_total_packs": 32,
        "max_physical_packs": 10,
        "excess_packs": 22
      }
    ]
  },
  "battery_results": {
    "<shift_uuid>": {
      "optimized_packs": 10,
      "excess_packs": 22,
      "required_total_packs": 32,
      "physical_feasible": false,
      "feasibility_status": "infeasible_requires_excess_packs"
    }
  },
  "total_battery_cost_chf": 0.0,
  "total_infeasibility_penalty_chf": 880000000.0
}
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

GTFS objects are resolved as
`releases/$ELEVATION_PROFILES_RELEASE/<shape_id>.parquet` when a release is
configured. Startup fails unless that prefix contains a completed compatible
`release.json`; reads never fall back to the legacy root key. With the variable
unset, GTFS reads remain compatible with `<shape_id>.parquet`. Auxiliary trips
always use their job's stable `output_object_name` after successful completion.

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

**Last Updated:** 2026-03-20

## Annual project TCO

`GET /api/v1/yearly-analysis/{id}/costs` returns operating costs by default.
Request `include_capex=true&capex_source=optimization&bus_length_m=10.75`
to price the selected design. The default method, `project_horizon`, values
electric and diesel on the same horizon. Each asset is purchased at year zero
and replaced at its initial real price when its life expires before the horizon.
The remaining straight-line value is credited at the final year. End-of-year
operating costs and all capital cash flows use the same discount rate.
The equivalent annual cost is the present value divided by the annuity factor.
Totals are rounded after aggregation, so displayed line items can differ by one cent.

Saved project assumptions supply the horizon, lifetimes, operating prices,
maintenance budget and mileage per duty. In project TCO the heater maintenance
fraction applies to annual heater fuel expense. In OPEX-only and legacy
asset-lifetime calculations it applies to electric vehicle maintenance expense.
Explicit query parameters take
precedence. `project_horizon_years` accepts 1–60 years. With no saved horizon,
the electric bus lifetime sets the evaluation period. Use
`tco_method=asset_lifetime` to reproduce the previous per-asset annualization
and its operating-cost conventions. The response identifies the method and horizon
in `assumptions`. `annual_saving_chf` is diesel annual cost minus electric annual cost.

The dashboard calls `POST /api/v1/yearly-analysis/{id}/costs/project` with the
current project horizon, discount rate and fleet/group investments and annual
operating line items. This read-only preview returns unrounded discounted cash
flows, residuals, annual equivalent costs and costs per km for each scope.
It uses the same project valuation as the GET endpoint. Authentication and
ownership apply to both endpoints.
