BEGIN;

ALTER TABLE public.optimization_runs
    ADD COLUMN IF NOT EXISTS name text;

ALTER TABLE public.optimization_runs
    ALTER COLUMN bus_model_id DROP NOT NULL;

COMMIT;
