-- Create yearly_analysis table
CREATE TABLE IF NOT EXISTS public.yearly_analysis (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    optimization_run_id uuid NULL
        REFERENCES public.optimization_runs (id) ON DELETE SET NULL,
    name        text        NOT NULL,
    features    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS yearly_analysis_optimization_run_id_idx
    ON public.yearly_analysis (optimization_run_id);
