# Database Auto-Initialization Setup

## Overview

The PostgreSQL database is now configured to automatically initialize the schema on first startup, similar to how MinIO bucket creation works in docker-compose.

## How It Works

### 1. PostgreSQL Init Script Feature
PostgreSQL's official Docker image automatically executes `.sql` files found in `/docker-entrypoint-initdb.d/` when the database starts with an empty data volume.

### 2. Schema Preparation Pipeline

```
db/elettra_schema.sql          (source: pg_dump output)
         ↓
scripts/prepare_init_schema.sh (cleaning script)
         ↓
db/elettra_schema_init.sql     (output: Docker-ready init script)
         ↓
docker-compose.yml             (mounted to container)
         ↓
PostgreSQL auto-initialization (on first startup)
```

### 3. What Gets Cleaned

The preparation script removes/modifies:
- `\restrict` and `\unrestrict` commands (pg_dump metadata)
- `OWNER TO admin` statements (ownership inherits from connection user)
- Other pg_dump-specific directives that aren't valid for init scripts

## Usage

### Fresh Database Initialization

When starting with a fresh setup:

```bash
# Run the preparation script (if not already done)
./scripts/prepare_init_schema.sh

# Start the stack - database will auto-initialize
docker-compose up
```

The PostgreSQL container will:
1. Detect empty data volume
2. Find `/docker-entrypoint-initdb.d/01-schema.sql` (mounted from `db/elettra_schema_init.sql`)
3. Execute the schema creation automatically
4. Complete initialization before the app container starts

### Re-initializing from Scratch

To force a complete re-initialization:

```bash
# Stop all services and remove volumes
docker-compose down -v

# Start again - will re-initialize
docker-compose up
```

⚠️ **Warning:** This deletes all data in the database!

### After Schema Updates

When you update the database schema (following `docs/database-schema-updates.md`):

```bash
# 1. Update models and export schema
sqlacodegen "$DB_URL" --outfile app/models.py
python generate_schemas.py --models-module app.models --out app/schemas/database.py --base-class-name Base
export PGPASSWORD='password' && pg_dump --schema-only -U admin -h localhost -p 5440 -d elettra -f db/elettra_schema.sql

# 2. Prepare init script (NEW STEP)
./scripts/prepare_init_schema.sh

# 3. Commit changes (except the generated init file)
git add db/elettra_schema.sql app/models.py app/schemas/database.py
git commit -m "Update database schema"
```

## Files and Configuration

### Docker Compose Configuration

```yaml
db:
  volumes:
    - db-data:/var/lib/postgresql/data
    # Auto-initialize schema on first startup (only runs if db is empty)
    - ./db/elettra_schema_init.sql:/docker-entrypoint-initdb.d/01-schema.sql:ro
```

### File Structure

```
db/
├── elettra_schema.sql          # Source schema (pg_dump output) - COMMITTED
└── elettra_schema_init.sql     # Docker init script - AUTO-GENERATED, NOT COMMITTED

scripts/
└── prepare_init_schema.sh      # Conversion script - COMMITTED
```

### Git Configuration

The generated init file is ignored in `.gitignore`:

```
# db/.gitignore
elettra_schema_init.sql
```

## Benefits

1. **Consistent with MinIO:** Same pattern as `minio-create-bucket` service
2. **No Manual Setup:** New developers can run `docker-compose up` and get a working database
3. **Idempotent:** Safe to restart - only initializes when volume is empty
4. **Automated:** Part of the standard schema update workflow
5. **Flexible:** Different environments can use different credentials via env vars

## Troubleshooting

### Schema Not Loading

If the schema doesn't initialize:

1. Check that the init script exists:
   ```bash
   ls -lh db/elettra_schema_init.sql
   ```

2. Run the preparation script:
   ```bash
   ./scripts/prepare_init_schema.sh
   ```

3. Check docker-compose volume mount:
   ```bash
   docker-compose config | grep -A 3 "docker-entrypoint-initdb.d"
   ```

### Syntax Errors in Init Script

If PostgreSQL fails to initialize:

1. Check container logs:
   ```bash
   docker-compose logs db
   ```

2. Verify the init script is valid SQL:
   ```bash
   grep -E "(\\\\restrict|\\\\unrestrict|OWNER TO admin)" db/elettra_schema_init.sql
   ```
   Should return no results.

3. Re-run the preparation script:
   ```bash
   ./scripts/prepare_init_schema.sh
   ```

### Init Script Not Running

PostgreSQL only runs init scripts on **first startup** with an empty volume. If you have existing data:

```bash
# Option 1: Fresh start (deletes data)
docker-compose down -v
docker-compose up

# Option 2: Manual application (keeps data)
docker-compose exec db psql -U admin -d elettra -f /path/to/schema.sql
```

## Comparison with Alternative Approaches

### Why Not Use Alembic/Migrations?

- **Pro:** Good for incremental updates and production environments
- **Con:** More complex setup, requires migration management
- **Decision:** Current approach is simpler for development and testing

### Why Not Use a Separate Init Service?

- **Pro:** Could check if tables exist before running
- **Con:** Extra container, more complex than native PostgreSQL feature
- **Decision:** Native PostgreSQL init scripts are sufficient and simpler

### Why Not Commit the Init Script?

- **Pro:** One less step in workflow
- **Con:** Redundant with source schema, potential for inconsistency
- **Decision:** Auto-generate to ensure consistency with source schema

## Related Documentation

- [Database Schema Updates Guide](database-schema-updates.md) - Complete workflow for schema changes
- [Scripts README](../scripts/README.md) - Details about preparation script
- [Main README](../README.md) - Overall project documentation

