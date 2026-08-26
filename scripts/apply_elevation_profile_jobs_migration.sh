#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
queue_migration_file="$repository_root/db/migrations/005_create_elevation_profile_jobs.sql"
cleanup_migration_file="$repository_root/db/migrations/006_create_elevation_profile_cleanup_jobs.sql"
database_url="${MIGRATION_DATABASE_URL:-${DATABASE_URL:-}}"

if [[ -z "$database_url" ]]; then
  echo "Set MIGRATION_DATABASE_URL (or DATABASE_URL) to a PostgreSQL libpq URL" >&2
  exit 2
fi

# Application deployments commonly use SQLAlchemy's asyncpg scheme; psql
# expects the corresponding libpq scheme.
database_url="${database_url/postgresql+asyncpg:\/\//postgresql:\/\/}"
database_url="${database_url/postgresql+psycopg:\/\//postgresql:\/\/}"
database_url="${database_url/postgres:\/\//postgresql:\/\/}"

existing="$(psql "$database_url" -Atqc \
  "SELECT COALESCE(to_regclass('public.elevation_profile_jobs')::text, '')")"

if [[ -z "$existing" ]]; then
  psql "$database_url" -1 -v ON_ERROR_STOP=1 -f "$queue_migration_file"
else
  echo "elevation_profile_jobs already exists; verifying production contract"
fi

cleanup_existing="$(psql "$database_url" -Atqc \
  "SELECT COALESCE(to_regclass('public.elevation_profile_cleanup_jobs')::text, '')")"

if [[ -z "$cleanup_existing" ]]; then
  psql "$database_url" -1 -v ON_ERROR_STOP=1 -f "$cleanup_migration_file"
else
  echo "elevation_profile_cleanup_jobs already exists; verifying production contract"
fi

psql "$database_url" -v ON_ERROR_STOP=1 <<'SQL'
DO $$
DECLARE
    missing_columns text[];
    missing_constraints text[];
BEGIN
    SELECT array_agg(required.name ORDER BY required.name)
    INTO missing_columns
    FROM (
        VALUES
            ('id'), ('trip_id'), ('payload'), ('status'), ('attempts'),
            ('available_at'), ('lease_expires_at'), ('worker_id'),
            ('last_error'), ('algorithm_version'), ('roads_release'),
            ('output_object_name'), ('created_at'), ('updated_at'),
            ('completed_at')
    ) AS required(name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM information_schema.columns AS column_info
        WHERE column_info.table_schema = 'public'
          AND column_info.table_name = 'elevation_profile_jobs'
          AND column_info.column_name = required.name
    );

    IF missing_columns IS NOT NULL THEN
        RAISE EXCEPTION 'elevation_profile_jobs is missing columns: %', missing_columns;
    END IF;
    SELECT array_agg(required.name ORDER BY required.name)
    INTO missing_constraints
    FROM (
        VALUES
            ('elevation_profile_jobs_pkey'),
            ('elevation_profile_jobs_trip_id_key'),
            ('elevation_profile_jobs_trip_id_fkey'),
            ('elevation_profile_jobs_status_check'),
            ('elevation_profile_jobs_attempts_check')
    ) AS required(name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS constraint_info
        WHERE constraint_info.conrelid =
              'public.elevation_profile_jobs'::regclass
          AND constraint_info.conname = required.name
    );
    IF missing_constraints IS NOT NULL THEN
        RAISE EXCEPTION 'elevation_profile_jobs is missing constraints: %', missing_constraints;
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.elevation_profile_jobs'::regclass
          AND conname = 'elevation_profile_jobs_trip_id_fkey'
          AND contype = 'f'
          AND confdeltype = 'c'
    ) THEN
        RAISE EXCEPTION 'elevation_profile_jobs is missing its ON DELETE CASCADE FK';
    END IF;
    IF to_regclass('public.elevation_profile_jobs_status_available_at_idx') IS NULL THEN
        RAISE EXCEPTION 'elevation profile availability index is missing';
    END IF;

    SELECT array_agg(required.name ORDER BY required.name)
    INTO missing_columns
    FROM (
        VALUES
            ('id'), ('trip_id'), ('payload'), ('status'), ('attempts'),
            ('available_at'), ('lease_expires_at'), ('worker_id'),
            ('last_error'), ('created_at'), ('updated_at'), ('completed_at')
    ) AS required(name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM information_schema.columns AS column_info
        WHERE column_info.table_schema = 'public'
          AND column_info.table_name = 'elevation_profile_cleanup_jobs'
          AND column_info.column_name = required.name
    );

    IF missing_columns IS NOT NULL THEN
        RAISE EXCEPTION 'elevation_profile_cleanup_jobs is missing columns: %', missing_columns;
    END IF;
    SELECT array_agg(required.name ORDER BY required.name)
    INTO missing_constraints
    FROM (
        VALUES
            ('elevation_profile_cleanup_jobs_pkey'),
            ('elevation_profile_cleanup_jobs_trip_id_key'),
            ('elevation_profile_cleanup_jobs_status_check'),
            ('elevation_profile_cleanup_jobs_attempts_check')
    ) AS required(name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS constraint_info
        WHERE constraint_info.conrelid =
              'public.elevation_profile_cleanup_jobs'::regclass
          AND constraint_info.conname = required.name
    );
    IF missing_constraints IS NOT NULL THEN
        RAISE EXCEPTION 'elevation_profile_cleanup_jobs is missing constraints: %', missing_constraints;
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.elevation_profile_cleanup_jobs'::regclass
          AND contype = 'f'
    ) THEN
        RAISE EXCEPTION 'elevation_profile_cleanup_jobs must survive trip deletion and cannot have a FK';
    END IF;
    IF to_regclass('public.elevation_profile_cleanup_jobs_status_available_at_idx') IS NULL THEN
        RAISE EXCEPTION 'elevation profile cleanup availability index is missing';
    END IF;
END
$$;
SQL

echo "elevation profile queue and cleanup outbox migrations verified"
