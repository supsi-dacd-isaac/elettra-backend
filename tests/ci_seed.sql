-- Minimal deterministic identity required by API/CRUD tests on GitHub Actions.
-- Dataset-specific GTFS, OSRM, MinIO and model tests remain opt-in and are
-- skipped when their explicit TEST_* variables are absent.

INSERT INTO public.gtfs_agencies (
    id,
    gtfs_agency_id,
    agency_name,
    agency_url,
    agency_timezone
) VALUES (
    '11111111-1111-4111-8111-111111111111',
    'CI',
    'CI Test Agency',
    'https://example.invalid',
    'Europe/Zurich'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.users (
    id,
    company_id,
    email,
    full_name,
    password_hash,
    role
) VALUES (
    '22222222-2222-4222-8222-222222222222',
    '11111111-1111-4111-8111-111111111111',
    'ci-release@example.com',
    'CI Release User',
    crypt('CI-release-gate_Str0ng!', gen_salt('bf')),
    'admin'
)
ON CONFLICT (id) DO NOTHING;
