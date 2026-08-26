-- Migration 006: durable outbox for auxiliary elevation object deletion.
--
-- The owning trip is intentionally not referenced by a foreign key: this row
-- must survive the transaction that deletes the trip and cascades its profile
-- generation job.  A pyriadne worker claims rows with SKIP LOCKED and removes
-- the listed MinIO objects/prefixes idempotently.

CREATE TABLE public.elevation_profile_cleanup_jobs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    trip_id uuid NOT NULL,
    payload jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_expires_at timestamp with time zone,
    worker_id text,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT elevation_profile_cleanup_jobs_attempts_check CHECK (attempts >= 0),
    CONSTRAINT elevation_profile_cleanup_jobs_status_check CHECK (
        status IN ('pending', 'processing', 'succeeded', 'failed')
    ),
    CONSTRAINT elevation_profile_cleanup_jobs_pkey PRIMARY KEY (id),
    CONSTRAINT elevation_profile_cleanup_jobs_trip_id_key UNIQUE (trip_id)
);

CREATE INDEX elevation_profile_cleanup_jobs_status_available_at_idx
    ON public.elevation_profile_cleanup_jobs USING btree (status, available_at);
