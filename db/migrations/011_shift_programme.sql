ALTER TABLE public.shifts
    ADD COLUMN IF NOT EXISTS service_weekday text,
    ADD COLUMN IF NOT EXISTS programme jsonb;
