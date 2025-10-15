#!/bin/bash
# Script to convert pg_dump output to PostgreSQL init script
# This removes pg_dump metadata and makes ownership statements dynamic
# and adds essential seed data for the application

set -e

SOURCE_SCHEMA="db/elettra_schema.sql"
OUTPUT_SCHEMA="db/elettra_schema_init.sql"

echo "Preparing initialization schema from pg_dump output..."

# Create the cleaned schema file
cat "$SOURCE_SCHEMA" | \
  # Remove pg_dump restrict/unrestrict commands
  grep -v '^\\\restrict' | \
  grep -v '^\\\unrestrict' | \
  # Replace hardcoded admin owner with current_user
  sed 's/OWNER TO admin;/OWNER TO CURRENT_USER;/g' | \
  sed 's/ALTER TYPE public\.\([^ ]*\) OWNER TO CURRENT_USER;/-- Owner will be set automatically for type \1/g' | \
  sed 's/ALTER TABLE public\.\([^ ]*\) OWNER TO CURRENT_USER;/-- Owner will be set automatically for table \1/g' \
  > "$OUTPUT_SCHEMA"

# Add seed data for essential application functionality
cat >> "$OUTPUT_SCHEMA" << 'EOF'

--
-- Essential seed data for Elettra Backend
--

-- Insert 'auxiliary' service into gtfs_calendar
-- This service is used for depot trips, transfer trips, and other non-GTFS trips
INSERT INTO public.gtfs_calendar (service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date)
SELECT 'auxiliary', 1, 1, 1, 1, 1, 1, 1, '2020-01-01'::date, '2099-12-31'::date
WHERE NOT EXISTS (
    SELECT 1 FROM public.gtfs_calendar WHERE service_id = 'auxiliary'
);

-- Per-day auxiliary services
INSERT INTO public.gtfs_calendar (service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date)
SELECT 'auxiliary_mon', 1, 0, 0, 0, 0, 0, 0, '2020-01-01'::date, '2099-12-31'::date
WHERE NOT EXISTS (SELECT 1 FROM public.gtfs_calendar WHERE service_id = 'auxiliary_mon');

INSERT INTO public.gtfs_calendar (service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date)
SELECT 'auxiliary_tue', 0, 1, 0, 0, 0, 0, 0, '2020-01-01'::date, '2099-12-31'::date
WHERE NOT EXISTS (SELECT 1 FROM public.gtfs_calendar WHERE service_id = 'auxiliary_tue');

INSERT INTO public.gtfs_calendar (service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date)
SELECT 'auxiliary_wed', 0, 0, 1, 0, 0, 0, 0, '2020-01-01'::date, '2099-12-31'::date
WHERE NOT EXISTS (SELECT 1 FROM public.gtfs_calendar WHERE service_id = 'auxiliary_wed');

INSERT INTO public.gtfs_calendar (service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date)
SELECT 'auxiliary_thu', 0, 0, 0, 1, 0, 0, 0, '2020-01-01'::date, '2099-12-31'::date
WHERE NOT EXISTS (SELECT 1 FROM public.gtfs_calendar WHERE service_id = 'auxiliary_thu');

INSERT INTO public.gtfs_calendar (service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date)
SELECT 'auxiliary_fri', 0, 0, 0, 0, 1, 0, 0, '2020-01-01'::date, '2099-12-31'::date
WHERE NOT EXISTS (SELECT 1 FROM public.gtfs_calendar WHERE service_id = 'auxiliary_fri');

INSERT INTO public.gtfs_calendar (service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date)
SELECT 'auxiliary_sat', 0, 0, 0, 0, 0, 1, 0, '2020-01-01'::date, '2099-12-31'::date
WHERE NOT EXISTS (SELECT 1 FROM public.gtfs_calendar WHERE service_id = 'auxiliary_sat');

INSERT INTO public.gtfs_calendar (service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date)
SELECT 'auxiliary_sun', 0, 0, 0, 0, 0, 0, 1, '2020-01-01'::date, '2099-12-31'::date
WHERE NOT EXISTS (SELECT 1 FROM public.gtfs_calendar WHERE service_id = 'auxiliary_sun');

EOF

echo "✓ Created $OUTPUT_SCHEMA"
echo "  - Removed pg_dump metadata"
echo "  - Removed explicit ownership statements (will inherit from connection user)"
echo "  - Added seed data for 'auxiliary' GTFS calendar service"
echo ""
echo "This file can be used for database initialization in docker-compose"

