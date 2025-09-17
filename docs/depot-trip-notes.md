# Depot Trip Endpoint - Working Notes

Owner: AI assistant
Status: Draft / In Progress

## Goal
Implement POST `/api/v1/gtfs/depot-trip` to create a new depot trip between two stops, compute OSRM geometry, generate elevation profile via SwissTopoElevationClient, store parquet in MinIO, create trip and two stop_times.

## Inputs (request body)
- departure_stop_id: UUID
- arrival_stop_id: UUID
- departure_time: string (HH:MM:SS)
- arrival_time: string (HH:MM:SS)
- route_id: UUID

## High-level Steps
1) Validate inputs, fetch `GtfsStops` for departure and arrival (need lat/lon)
2) Query OSRM route between [dep_lon,dep_lat] -> [arr_lon,arr_lat]; request geometry (polyline). Decode to list of [lat, lon]
3) Compute elevation along geometry using `SwissTopoElevationClient.get_elevation_batch`
4) Build elevation profile dataframe and write Parquet to MinIO bucket `elevation-profiles`
   - Decide object name convention: `${shape_id}.parquet` (shape_id unique per depot trip)
   - Propose `shape_id = f"depot-{route_id}-{departure_stop_id}-{arrival_stop_id}-{uuid4().hex[:8]}"`
   - Replace hyphens with nothing or keep? Keep hyphens allowed as object keys; OK.
5) Create `GtfsTrips` row with:
   - route_id = input
   - service_id = pk from `gtfs_calendar` where service_id == 'depot'
   - gtfs_service_id = 'depot'
   - trip_id = generated unique id (e.g., `depot:<route_id_short>:<dep_stop_id_short>:<arr_stop_id_short>:<timestamp>`)
   - status = 'depot'
   - shape_id = same as parquet base name
   - start_stop_name, end_stop_name via stop names
   - departure_time, arrival_time as provided
6) Insert two `GtfsStopsTimes` rows:
   - stop_sequence 1: departure_stop_id, arrival_time = departure_time, departure_time = departure_time
   - stop_sequence 2: arrival_stop_id, arrival_time = arrival_time, departure_time = arrival_time
7) Return created trip (and maybe elevation location?)

## Open Questions
- Should the response include the elevation MinIO object path or just the created `GtfsTripsRead`? Proposed: return `GtfsTripsRead` only; elevation is retrievable via existing `/elevation-profile/by-trip/{trip_id}`.
- Timezone handling for times? Existing columns are strings; we'll accept HH:MM:SS as-is.
- Coordinate reference system: our stops are WGS84, columns `stop_lat`/`stop_lon`. Use as-is.
- OSRM BASE URL: existing env `OSRM_BASE_URL` default `http://osrm:5000`. Use `overview=full`.
- Elevation client dependency: add `map-services` or the specific package name that exposes `SwissTopoElevationClient`. Verify import path.

## Implementation Details
- Endpoint path: `/depot-trip` under `gtfs` router; full path `/api/v1/gtfs/depot-trip`
- Request model `DepotTripCreate` (new Pydantic schema under `app/schemas/database.py` or new `app/schemas/requests.py`)
- Use transactions: create trip and stops_times atomically; commit at end
- Error handling: 404 if stops not found or missing coords; 502 on OSRM error; 500 on elevation/minio errors
- MinIO config: env `MINIO_ENDPOINT` (default `minio:9000`), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `MINIO_SECURE`
- Parquet writing: use pandas and pyarrow

## Test Plan
- Unit test: mock OSRM httpx client, mock SwissTopoElevationClient, mock MinIO; assert created trip and stop_times
- Integration smoke (optional): behind env flags, call real OSRM+MinIO
- Validate that `gtfs_calendar` contains a service with service_id == 'depot` in test data; otherwise seed or monkeypatch query

## TODOs
- [ ] Add dependency for `SwissTopoElevationClient` to `requirements.txt`
- [ ] Add request schema `DepotTripCreate`
- [ ] Implement endpoint in `app/routers/gtfs.py`
- [ ] Helper: OSRM route fetch + polyline decode
- [ ] Helper: elevation batch and parquet upload to MinIO
- [ ] DB: insert trip with `status='depot'`, `gtfs_service_id='depot'`, link `service_id`
- [ ] DB: insert two stop_times
- [ ] Tests: endpoint behavior, DB rows present, MinIO called
