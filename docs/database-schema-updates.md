# Database Schema Updates Guide

This document explains how to update the SQLAlchemy models and schemas after making manual changes to the database schema.

## Overview

When you manually modify the database schema (e.g., adding tables, columns, constraints, or foreign keys), you need to regenerate the corresponding SQLAlchemy models and schemas to keep them in sync.

## Prerequisites

- PostgreSQL database running on localhost:5440
- Database credentials configured in environment variables
- Database name: `elettra`
- Python environment with required packages installed

## Step-by-Step Process

### Step 1: Generate SQLAlchemy Models

First, regenerate the SQLAlchemy models from the current database schema:

```bash
export DB_URL="postgresql+psycopg2://[USER]:[PASSWORD]@localhost:5440/elettra"
sqlacodegen "$DB_URL" --outfile app/models.py
```

This command:
- Connects to the database using the provided connection string
- Analyzes the current database schema
- Generates SQLAlchemy model classes
- Outputs them to `app/models.py`

### Step 2: Generate Database Schemas

Next, generate the database schemas from the updated models:

```bash
python generate_schemas.py --models-module app.models --out app/schemas/database.py --base-class-name Base
```

This command:
- Imports the models from `app.models`
- Generates corresponding schema classes
- Outputs them to `app/schemas/database.py`
- Uses `Base` as the base class name

### Step 3: Update SQL Schema File

Export the current database schema to the SQL file:

```bash
export PGPASSWORD='[PASSWORD]' && pg_dump --schema-only -U [USER] -h localhost -p 5440 -d elettra -f db/elettra_schema.sql
```

This command:
- Sets the PostgreSQL password environment variable
- Uses `pg_dump` to export only the schema (no data)
- Connects as user `[USER]` to host `localhost` on port `5440`
- Exports the `elettra` database schema
- Saves it to `db/elettra_schema.sql`

### Step 4: Prepare Initialization Schema

Finally, convert the pg_dump output to a Docker-compatible init script:

```bash
./scripts/prepare_init_schema.sh
```

This command:
- Reads `db/elettra_schema.sql` (the pg_dump output)
- Removes pg_dump-specific metadata (`\restrict`, `\unrestrict`)
- Removes explicit ownership statements (will inherit from connection user)
- Outputs to `db/elettra_schema_init.sql`
- This file is used by docker-compose for automatic database initialization

## Important Notes

### Security Considerations
- **Use environment variables**: Always use environment variables for database credentials
- **Never commit credentials**: Database credentials should never be committed to version control
- **Clean up**: Remove any credential exports from your shell session after running commands

### File Locations
- **Models**: `app/models.py` - SQLAlchemy model definitions
- **Schemas**: `app/schemas/database.py` - Pydantic schema definitions
- **SQL Schema**: `db/elettra_schema.sql` - PostgreSQL schema dump (source)
- **Init Schema**: `db/elettra_schema_init.sql` - Docker-compatible init script (generated, not committed)

### Verification
After running these commands, verify that:
1. The models reflect your database changes
2. The schemas are properly generated
3. The SQL schema file is up to date
4. The initialization schema is generated
5. All foreign key relationships and constraints are preserved

## Common Use Cases

### Adding New Tables
1. Create the table in the database
2. Run all four steps above
3. Commit the updated files (except `elettra_schema_init.sql` which is auto-generated)

### Modifying Existing Tables
1. Alter the table structure in the database
2. Run all four steps above
3. Update any related application code
4. Commit the changes (except `elettra_schema_init.sql`)

### Adding Foreign Key Constraints
1. Add the foreign key constraint to the database
2. Run all four steps above
3. Verify cascade behaviors are correctly reflected
4. Commit the changes (except `elettra_schema_init.sql`)

## Troubleshooting

### Connection Issues
- Verify PostgreSQL is running on port 5440
- Check that the database `elettra` exists
- Confirm credentials are correct

### Generation Errors
- Ensure all required Python packages are installed
- Check that the database schema is valid
- Verify file permissions for output directories

### Schema Mismatches
- Compare generated models with actual database structure
- Check for missing constraints or relationships
- Verify data types are correctly mapped

## Best Practices

1. **Always backup** your database before making schema changes
2. **Test changes** in a development environment first
3. **Review generated code** before committing
4. **Update tests** if schema changes affect test data
5. **Document changes** in commit messages
6. **Coordinate with team** when making breaking changes

## Related Files

- `app/models.py` - SQLAlchemy models
- `app/schemas/database.py` - Pydantic schemas
- `db/elettra_schema.sql` - Database schema dump (committed)
- `db/elettra_schema_init.sql` - Docker init schema (generated, not committed)
- `scripts/prepare_init_schema.sh` - Script to prepare init schema
- `generate_schemas.py` - Schema generation script
- `requirements.txt` - Python dependencies

## How Auto-Initialization Works

When you start the PostgreSQL container for the first time (with an empty volume):

1. PostgreSQL checks `/docker-entrypoint-initdb.d/` for `.sql` files
2. It finds `01-schema.sql` (mounted from `db/elettra_schema_init.sql`)
3. It executes the schema creation automatically
4. This only happens once - subsequent starts skip initialization

To force re-initialization:
```bash
docker-compose down -v  # Remove volumes
docker-compose up       # Will re-initialize from scratch
```
