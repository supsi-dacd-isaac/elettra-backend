-- Migration 007: provenance and rollback support for PVGIS/Open-Meteo TMYs.

ALTER TABLE public.weather_measurements
    ADD COLUMN IF NOT EXISTS temp_air_original real;

CREATE TABLE IF NOT EXISTS public.weather_temperature_series (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    latitude numeric(8,5) NOT NULL,
    longitude numeric(9,5) NOT NULL,
    requested_latitude numeric(8,5) NOT NULL,
    requested_longitude numeric(9,5) NOT NULL,
    provider text NOT NULL,
    openmeteo_model text NOT NULL,
    processing_version text NOT NULL,
    status text NOT NULL,
    pvgis_months_selected jsonb NOT NULL,
    pvgis_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    openmeteo_metadata jsonb NOT NULL DEFAULT '[]'::jsonb,
    row_count integer NOT NULL,
    generated_at timestamp with time zone NOT NULL DEFAULT now(),
    applied_at timestamp with time zone,
    rolled_back_at timestamp with time zone,
    CONSTRAINT weather_temperature_series_pkey PRIMARY KEY (id),
    CONSTRAINT weather_temperature_series_status_check
        CHECK (status IN ('applied', 'superseded', 'rolled_back')),
    CONSTRAINT weather_temperature_series_row_count_check CHECK (row_count = 8760),
    CONSTRAINT weather_temperature_series_latitude_check
        CHECK (latitude >= -90 AND latitude <= 90),
    CONSTRAINT weather_temperature_series_longitude_check
        CHECK (longitude >= -180 AND longitude <= 180)
);

CREATE UNIQUE INDEX IF NOT EXISTS weather_temperature_series_active_coordinate_udx
    ON public.weather_temperature_series (latitude, longitude)
    WHERE status = 'applied';

CREATE INDEX IF NOT EXISTS weather_temperature_series_coordinate_idx
    ON public.weather_temperature_series (latitude, longitude, generated_at DESC);

ALTER TABLE public.weather_temperature_clusters
    ADD COLUMN IF NOT EXISTS temperature_series_id uuid;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'weather_temperature_clusters_series_id_fkey'
          AND conrelid = 'public.weather_temperature_clusters'::regclass
    ) THEN
        ALTER TABLE public.weather_temperature_clusters
            ADD CONSTRAINT weather_temperature_clusters_series_id_fkey
            FOREIGN KEY (temperature_series_id)
            REFERENCES public.weather_temperature_series(id)
            ON DELETE SET NULL;
    END IF;
END
$$;

ALTER TABLE public.yearly_analysis
    ADD COLUMN IF NOT EXISTS weather_temperature_series_id uuid,
    ADD COLUMN IF NOT EXISTS weather_cluster_k integer,
    ADD COLUMN IF NOT EXISTS weather_cluster_start_time varchar(5),
    ADD COLUMN IF NOT EXISTS weather_cluster_end_time varchar(5);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'yearly_analysis_weather_temperature_series_id_fkey'
          AND conrelid = 'public.yearly_analysis'::regclass
    ) THEN
        ALTER TABLE public.yearly_analysis
            ADD CONSTRAINT yearly_analysis_weather_temperature_series_id_fkey
            FOREIGN KEY (weather_temperature_series_id)
            REFERENCES public.weather_temperature_series(id)
            ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'yearly_analysis_weather_cluster_k_check'
          AND conrelid = 'public.yearly_analysis'::regclass
    ) THEN
        ALTER TABLE public.yearly_analysis
            ADD CONSTRAINT yearly_analysis_weather_cluster_k_check
            CHECK (weather_cluster_k IS NULL OR weather_cluster_k > 0);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS yearly_analysis_weather_temperature_series_idx
    ON public.yearly_analysis (weather_temperature_series_id);

CREATE TABLE IF NOT EXISTS public.yearly_analysis_weather_revisions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    yearly_analysis_id uuid NOT NULL,
    previous_temperature_series_id uuid,
    new_temperature_series_id uuid,
    previous_cluster_k integer,
    previous_cluster_start_time varchar(5),
    previous_cluster_end_time varchar(5),
    previous_features jsonb NOT NULL,
    previous_prediction_run_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    new_prediction_run_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    status text NOT NULL DEFAULT 'pending',
    last_error text,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    completed_at timestamp with time zone,
    rolled_back_at timestamp with time zone,
    CONSTRAINT yearly_analysis_weather_revisions_pkey PRIMARY KEY (id),
    CONSTRAINT yearly_analysis_weather_revisions_analysis_fkey
        FOREIGN KEY (yearly_analysis_id)
        REFERENCES public.yearly_analysis(id)
        ON DELETE CASCADE,
    CONSTRAINT yearly_analysis_weather_revisions_previous_series_fkey
        FOREIGN KEY (previous_temperature_series_id)
        REFERENCES public.weather_temperature_series(id)
        ON DELETE SET NULL,
    CONSTRAINT yearly_analysis_weather_revisions_new_series_fkey
        FOREIGN KEY (new_temperature_series_id)
        REFERENCES public.weather_temperature_series(id)
        ON DELETE SET NULL,
    CONSTRAINT yearly_analysis_weather_revisions_status_check
        CHECK (status IN ('pending', 'completed', 'failed', 'rolled_back'))
);

ALTER TABLE public.yearly_analysis_weather_revisions
    ADD COLUMN IF NOT EXISTS previous_cluster_k integer,
    ADD COLUMN IF NOT EXISTS previous_cluster_start_time varchar(5),
    ADD COLUMN IF NOT EXISTS previous_cluster_end_time varchar(5);

CREATE INDEX IF NOT EXISTS yearly_analysis_weather_revisions_analysis_created_idx
    ON public.yearly_analysis_weather_revisions
    (yearly_analysis_id, created_at DESC);
