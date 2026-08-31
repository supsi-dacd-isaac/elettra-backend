#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
migration_file="$repository_root/db/migrations/007_add_hybrid_temperature_series.sql"
database_url="${MIGRATION_DATABASE_URL:-${DATABASE_URL:-}}"

if [[ -z "$database_url" ]]; then
  echo "Set MIGRATION_DATABASE_URL (or DATABASE_URL) to a PostgreSQL libpq URL" >&2
  exit 2
fi

database_url="${database_url/postgresql+asyncpg:\/\//postgresql:\/\/}"
database_url="${database_url/postgresql+psycopg:\/\//postgresql:\/\/}"
database_url="${database_url/postgres:\/\//postgresql:\/\/}"

psql "$database_url" -1 -v ON_ERROR_STOP=1 -f "$migration_file"

psql "$database_url" -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
    IF to_regclass('public.weather_temperature_series') IS NULL THEN
        RAISE EXCEPTION 'weather_temperature_series is missing';
    END IF;
    IF to_regclass('public.yearly_analysis_weather_revisions') IS NULL THEN
        RAISE EXCEPTION 'yearly_analysis_weather_revisions is missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'weather_measurements'
          AND column_name = 'temp_air_original'
    ) THEN
        RAISE EXCEPTION 'weather_measurements.temp_air_original is missing';
    END IF;
    IF to_regclass('public.weather_temperature_series_active_coordinate_udx') IS NULL THEN
        RAISE EXCEPTION 'active weather temperature series index is missing';
    END IF;
END
$$;
SQL

echo "hybrid temperature migration verified"
