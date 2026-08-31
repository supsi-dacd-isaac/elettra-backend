BEGIN;

ALTER TABLE public.prediction_runs
    ADD COLUMN IF NOT EXISTS prediction_stack text NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS auxiliary_estimator_release text;

ALTER TABLE public.trip_predictions
    ADD COLUMN IF NOT EXISTS component_breakdown jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'prediction_runs_prediction_stack_check'
          AND conrelid = 'public.prediction_runs'::regclass
    ) THEN
        ALTER TABLE public.prediction_runs
            ADD CONSTRAINT prediction_runs_prediction_stack_check
            CHECK (prediction_stack IN ('legacy', 'vecto-g2', 'vecto-g0-transfer'));
    END IF;
END
$$;

COMMIT;
