#!/bin/bash
# Script to convert pg_dump output to PostgreSQL init script
# This removes pg_dump metadata and makes ownership statements dynamic

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

echo "✓ Created $OUTPUT_SCHEMA"
echo "  - Removed pg_dump metadata"
echo "  - Removed explicit ownership statements (will inherit from connection user)"
echo ""
echo "This file can be used for database initialization in docker-compose"

