# Scripts Directory

This directory contains utility scripts for the Elettra backend project.

## Available Scripts

### `apply_hybrid_temperature_migration.sh`

Applies and verifies migration 007, which adds hybrid-temperature provenance,
rollback data and yearly-analysis weather revisions. Set
`MIGRATION_DATABASE_URL` (or `DATABASE_URL`) before running it.

### `backfill_hybrid_temperature.py`

Plans, applies, recalculates and rolls back the PVGIS/Open-Meteo temperature
revision. Run it from the repository root with the application virtualenv.

```bash
# Download and validate all current DB coordinates; this does not modify DB data.
.venv/bin/python scripts/backfill_hybrid_temperature.py plan \
  --bundle /secure/path/hybrid-temperature.json.gz

# Apply exactly the reviewed, checksummed bundle, one transaction per coordinate.
.venv/bin/python scripts/backfill_hybrid_temperature.py apply-weather \
  --bundle /secure/path/hybrid-temperature.json.gz --resume

# Recreate prediction runs and atomically switch completed yearly analyses.
.venv/bin/python scripts/backfill_hybrid_temperature.py recalculate-analyses \
  --resume --analysis-map /secure/path/analysis-map.json

# Restore every original temperature, cluster configuration and revised analysis.
.venv/bin/python scripts/backfill_hybrid_temperature.py rollback --all
```

`--analysis-map` is optional. It is a JSON object keyed by yearly-analysis UUID;
each value supplies `latitude`, `longitude`, `k`, `start_time` and `end_time` for
an unresolved case. When the CLI runs on the Docker host, set `MINIO_ENDPOINT`
to the published host endpoint (for the development compose file,
`localhost:9002`) before recalculating analyses. Inside the application network,
the normal `minio:9000` endpoint applies.

### `prepare_init_schema.sh`

**Purpose:** Convert `pg_dump` output to Docker-compatible PostgreSQL initialization script.

**Usage:**
```bash
./scripts/prepare_init_schema.sh
```

**What it does:**
1. Reads `db/elettra_schema.sql` (the raw `pg_dump` output)
2. Removes `pg_dump`-specific metadata commands (`\restrict`, `\unrestrict`)
3. Removes explicit `OWNER TO` statements (ownership will inherit from connection user)
4. Outputs to `db/elettra_schema_init.sql`

**When to run:**
- After updating `db/elettra_schema.sql` via `pg_dump`
- As part of the database schema update workflow (see `docs/database-schema-updates.md`)
- Before committing schema changes

**Integration:**
- The generated `db/elettra_schema_init.sql` is used by docker-compose for automatic database initialization
- This file is listed in `.gitignore` as it's auto-generated from the source schema
- When PostgreSQL container starts with an empty volume, it automatically runs this script

**Example workflow:**
```bash
# 1. Update the database schema
export PGPASSWORD='password' && pg_dump --schema-only -U admin -h localhost -p 5440 -d elettra -f db/elettra_schema.sql

# 2. Prepare the init script
./scripts/prepare_init_schema.sh

# 3. Test with fresh database
docker-compose down -v
docker-compose up
```

## Adding New Scripts

When adding new utility scripts to this directory:
1. Make them executable: `chmod +x scripts/your_script.sh`
2. Add proper error handling: `set -e` at the beginning
3. Document them in this README
4. Use relative paths from project root when possible
