# Pending production upgrade

User instruction recorded on 2026-11-10; these operations have **not** been
performed on production as part of the staging tests.

- Before deploying the release that uses operational HVAC weather snapshots,
  apply the existing `db/migrations/010_hvac_weather_snapshots.sql` to the
  production database as part of the planned production upgrade. This creates
  the immutable snapshot table and its update-protection trigger. Verify the
  existing state first; staging already has these objects. This is the specific
  schema change requested by the user, not authorization for unrelated changes.
- Exclude the full-fleet BATTERY_ONLY and JOINT diagnostic tests from production
  verification. They now skip by default; do not enable `ELETTRA_RUN_LONG_FLEET`
  or `ELETTRA_FLEET_UNBOUNDED` in release/production checks.
- All database-writing pytest tests remain restricted to staging (9004) or the
  explicitly validated ephemeral CI database. Never run this suite against
  production (5440). The future SQL migration is a separate deployment action.
