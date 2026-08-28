# Production feature-contract v2 configuration

The road-deck/profile and consumption-model switch is intentionally atomic.
Compatibility mode has both `ELEVATION_PROFILES_RELEASE` and
`CONSUMPTION_MODEL_RELEASE` unset. Production v2 requires all of:

```text
GTFS_ELEVATION_PROFILES_BUCKET=elevation-profiles-gtfs
ELEVATION_PROFILES_BUCKET=elevation-profiles
ELEVATION_PROFILES_RELEASE=roaddeck-v3.3-20260828-db39527
CONSUMPTION_MODEL_RELEASE=greybox_qrf_production_core_v2_roaddeck_v3_3_20260828
ELEVATION_AUX_PROFILE_ALGORITHM=road-snap-v3.3-topology
ELEVATION_AUX_ROADS_RELEASE=swisstlm3d_2026-02-24
```

Startup fails if only the profile or model release is set, if the auxiliary
pins are missing, or if the GTFS manifest is not road-snap v3.3 with profile
contract 2. While the switch is active, prediction requests cannot select a
different model and a succeeded auxiliary job is still rejected unless its
algorithm and road release match both pins.

GTFS release objects are read only from the GTFS bucket and versioned release
prefix; there is no root-key fallback. Auxiliary objects retain their stable
names in the mutable auxiliary bucket. `/health` reports the buckets, release,
manifest digest, feature contract, model pin and auxiliary job compatibility
counts without exposing credentials.

## Frozen auxiliary-energy behaviour

This deployment changes elevation and feature extraction only. It does not
change auxiliary-energy estimation:

- inference continues to interpolate `buses_models.specs["auxiliary_consumption_kw"]`;
- diesel-heating splitting and default-curve fallback remain unchanged;
- training must pin `hvac_exploration_results.json` with SHA-256
  `8ae333170a856adcd938b5a259f21cc5a216743a8eb0c34c5542fb0e6532cfb9`;
- VECTO is neither called nor imported by the prediction path.

Training currently uses vehicle-specific learned curves while inference uses
the selected bus model's configured curve. That pre-existing distinction is a
documented debt and is deliberately outside this release.
