-- Migration 005: durable queue for asynchronous elevation profile generation.
-- The worker claims pending rows with FOR UPDATE SKIP LOCKED and writes the
-- final MinIO object before marking a job as succeeded.

CREATE TABLE public.elevation_profile_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    trip_id uuid NOT NULL,
    payload jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_expires_at timestamp with time zone,
    worker_id text,
    last_error text,
    algorithm_version text,
    roads_release text,
    output_object_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT elevation_profile_jobs_attempts_check CHECK (attempts >= 0),
    CONSTRAINT elevation_profile_jobs_status_check CHECK (
        status IN ('pending', 'processing', 'succeeded', 'failed')
    ),
    CONSTRAINT elevation_profile_jobs_pkey PRIMARY KEY (id),
    CONSTRAINT elevation_profile_jobs_trip_id_key UNIQUE (trip_id),
    CONSTRAINT elevation_profile_jobs_trip_id_fkey
        FOREIGN KEY (trip_id)
        REFERENCES public.gtfs_trips(id)
        ON DELETE CASCADE
);

CREATE INDEX elevation_profile_jobs_status_available_at_idx
    ON public.elevation_profile_jobs USING btree (status, available_at);
