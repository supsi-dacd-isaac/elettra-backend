-- Migration: add weather_temperature_clusters table
-- Idempotent: safe to run multiple times on the same database.
-- Handles upgrade from the old single-column schema to the new configurable one.

-- Drop old table if it has the legacy column layout
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'weather_temperature_clusters' AND column_name = 'cluster_index'
    ) THEN
        DROP TABLE public.weather_temperature_clusters CASCADE;
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS public.weather_temperature_clusters (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    latitude numeric(8,5) NOT NULL,
    longitude numeric(9,5) NOT NULL,
    k integer NOT NULL,
    start_time character varying(5) NOT NULL,
    end_time character varying(5) NOT NULL,
    cluster_id integer NOT NULL,
    centroid_daily_avg_temp real NOT NULL,
    occurrences integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_wtc_lat_range CHECK (((latitude >= ('-90'::integer)::numeric) AND (latitude <= (90)::numeric))),
    CONSTRAINT ck_wtc_lon_range CHECK (((longitude >= ('-180'::integer)::numeric) AND (longitude <= (180)::numeric)))
);

-- Primary key (idempotent via DO block)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'weather_temperature_clusters_pkey'
    ) THEN
        ALTER TABLE ONLY public.weather_temperature_clusters
            ADD CONSTRAINT weather_temperature_clusters_pkey PRIMARY KEY (id);
    END IF;
END$$;

-- Unique constraint on config + cluster_id
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_weather_temp_clusters_config_cluster'
    ) THEN
        ALTER TABLE ONLY public.weather_temperature_clusters
            ADD CONSTRAINT uq_weather_temp_clusters_config_cluster
            UNIQUE (latitude, longitude, k, start_time, end_time, cluster_id);
    END IF;
END$$;

-- Composite index for lookup queries
CREATE INDEX IF NOT EXISTS ix_weather_temp_clusters_config
    ON public.weather_temperature_clusters USING btree (latitude, longitude, k, start_time, end_time);
