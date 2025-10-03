# Scripts Directory

This directory contains utility scripts for the Elettra backend project.

## Available Scripts

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

