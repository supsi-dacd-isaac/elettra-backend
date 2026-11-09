-- Content-addressed inputs used by operational HVAC policies. Existing runs
-- and mutable weather tables are not rewritten or backfilled.
CREATE TABLE IF NOT EXISTS public.hvac_weather_snapshots (
    sha256 varchar(64) PRIMARY KEY,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT hvac_weather_sha256_check CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE OR REPLACE FUNCTION public.reject_hvac_weather_snapshot_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'HVAC weather snapshots are immutable';
END;
$$;
DROP TRIGGER IF EXISTS hvac_weather_snapshot_immutable ON public.hvac_weather_snapshots;
CREATE TRIGGER hvac_weather_snapshot_immutable
BEFORE UPDATE ON public.hvac_weather_snapshots
FOR EACH ROW EXECUTE FUNCTION public.reject_hvac_weather_snapshot_update();
