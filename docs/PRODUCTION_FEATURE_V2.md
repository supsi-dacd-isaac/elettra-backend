# Production feature-contract v2 configuration

The road-deck/profile and default consumption stack switch is intentionally
atomic. Compatibility mode has both `ELEVATION_PROFILES_RELEASE` and
`CONSUMPTION_MODEL_RELEASE` unset. The singleton production-v2 configuration
remains supported during migration. Once the prediction registry is enabled,
`CONSUMPTION_MODEL_RELEASE` must be unset and the configuration is instead:

```text
GTFS_ELEVATION_PROFILES_BUCKET=elevation-profiles-gtfs
ELEVATION_PROFILES_BUCKET=elevation-profiles
ELEVATION_PROFILES_RELEASE=roaddeck-v3.3-20260828-db39527
LEGACY_CONSUMPTION_MODEL_RELEASE=greybox_qrf_production_core_v2_roaddeck_v3_3_physical_mass_20260831
VECTO_G2_CONSUMPTION_MODEL_RELEASE=greybox_qrf_g2_vecto_hess_v3_hbefa_slope_20260902-r4
VECTO_G0_TRANSFER_MODEL_RELEASE=greybox_qrf_g0_datadriven_to_vecto_complete_hess_v3_hbefa_slope_20260902-r4
DEFAULT_PREDICTION_STACK=vecto-g2
ENABLE_EXPERIMENTAL_PREDICTION_STACKS=true
ELETTRA_CORE_SOURCE_COMMIT=269aa8d73ab2bd06e17eab9ed7df5d74e8b2a871
ELEVATION_AUX_PROFILE_ALGORITHM=road-snap-v3.3-topology
ELEVATION_AUX_ROADS_RELEASE=swisstlm3d_2026-02-24
```

Startup fails for a partial profile/model configuration, mixed singleton and
registry variables, a missing legacy release, a default stack without a model,
an experimental default, missing auxiliary pins, or an incompatible GTFS
manifest. Registering any VECTO stack also requires the exact 40-character
commit named by `elettra-core-v2.2.1`; model provenance must match it.
The same commit is embedded at image build time in
`/etc/elettra-core-image-commit`. Startup requires the deployment pin, model
metadata and image-owned revision to be identical; build with:

```bash
docker build \
  --build-arg ELETTRA_CORE_SOURCE_COMMIT="$(git rev-parse 'elettra-core-v2.2.1^{}')" \
  --label org.opencontainers.image.source-backend-commit="$(git rev-parse HEAD)" \
  -t elettra-backend:"$(git rev-parse HEAD)" .
```
The image also computes `/etc/elettra-core-image-tree-sha256` from the Python
and JSON files it actually ships. Startup compares that digest with the
installed package and with model metadata, so a caller-supplied Git SHA cannot
attest unrelated bytes.

The constrained G2 release is intentionally the default after its controlled
VBZ holdout regression and HBEFA slope constraint have been reviewed. The
legacy release remains registered for an environment-only rollback.

`vecto-g0-transfer` is selectable only when the experimental flag is
true and is never allowed as the default. A request may provide
`prediction_stack`, `model_name`, or both; if both are present they must select
the same registered release.

The configured model is publishable only after
`models/<model>/<model>_release.json` exists. Schema 1 must declare the pinned
release ID, a passed acceptance decision (or an immutable, explicitly
documented controlled-regression approval), manifest-last immutability, and the
exact SHA-256 and size of the joblib, metadata, feature-importance CSV and
acceptance report. Startup hashes every artifact and checks that metadata,
feature release, auxiliary estimator and training software agree with the
manifest. Every configured stack is validated at startup. Readiness re-hashes
all manifests and rejects any changed object identity; `/health` exposes the
registry, tier, auxiliary estimator, fixed-load owner and validated digests.

GTFS release objects are read only from the GTFS bucket and versioned release
prefix; there is no root-key fallback. Auxiliary objects retain their stable
names in the mutable auxiliary bucket. `/health` reports the buckets, release,
manifest digest, feature contract, model pin and auxiliary job compatibility
counts without exposing credentials.

## Auxiliary-energy stacks

The legacy stack preserves the existing auxiliary-energy estimation exactly:

- inference continues to interpolate `buses_models.specs["auxiliary_consumption_kw"]`;
- diesel-heating splitting and default-curve fallback remain unchanged;
- training must pin `hvac_exploration_results.json` with SHA-256
  `8ae333170a856adcd938b5a259f21cc5a216743a8eb0c34c5542fb0e6532cfb9`;
- no VECTO component is mixed into a legacy prediction.

Training currently uses vehicle-specific learned curves while inference uses
the selected bus model's configured curve. That pre-existing distinction is a
documented debt and is deliberately outside this release.

`vecto-g2` uses the shared VECTO HVAC-only contract and obtains the fixed
electrical load from the G2 grey box. `vecto-g0-transfer` uses VECTO HVAC plus
the declared fixed baseline and remains experimental. Stack/model manifests
pin both contracts and the immutable template checksum, preventing silent
double counting or omission. See `VECTO_HVAC_TEMPLATES.md` for generation,
input assumptions, energy identities and licensing.

## VECTO model manifest contract

Both the metadata JSON and release JSON must carry the same core provenance:

```json
{
  "elettra_core": {
    "package_version": "2.2.1",
    "tag": "elettra-core-v2.2.1",
    "source_commit": "<40-lowercase-hex>",
    "source_tree_sha256": "<64-lowercase-hex>"
  }
}
```

The G2 metadata and release manifest both require this exact block:

```json
{
  "prediction_stack_contract": {
    "stack": "vecto-g2",
    "deployment_tier": "production",
    "training_auxiliary_estimator": "<versioned-training-estimator>",
    "inference_auxiliary_estimator": "vecto-hvac-5.1.3-r744-templates-v2",
    "fixed_auxiliary_owner": "model",
    "auxiliary_contract": "vecto-hvac-only",
    "transfer_policy": "fleet-setpoints-to-vecto-default-v1",
    "training_comfort_policy": {
      "release_id": "<versioned-comfort-policy>",
      "sha256": "<64-lowercase-hex>",
      "scope": "training-only"
    },
    "vecto_template_release": "vecto-hvac-5.1.3-r744-templates-v2",
    "vecto_template_sha256": "68dae71d01f93f372d04471f0604b483ab629aa606edb7ac2dcf75cca0541c51"
  }
}
```

The G0 transfer metadata and release manifest require:

```json
{
  "prediction_stack_contract": {
    "stack": "vecto-g0-transfer",
    "deployment_tier": "experimental",
    "training_auxiliary_estimator": "data-driven-by-bus",
    "training_auxiliary_estimator_sha256": "8ae333170a856adcd938b5a259f21cc5a216743a8eb0c34c5542fb0e6532cfb9",
    "inference_auxiliary_estimator": "vecto-complete-5.1.3-r744-templates-v2",
    "fixed_auxiliary_owner": "template",
    "auxiliary_contract": "vecto-complete",
    "vecto_template_release": "vecto-hvac-5.1.3-r744-templates-v2",
    "vecto_template_sha256": "68dae71d01f93f372d04471f0604b483ab629aa606edb7ac2dcf75cca0541c51"
  }
}
```

Metadata additionally requires `greybox_params` exactly equal to
`model.greybox.get_params_dict()` and a unique ordered `selected_features`
list servable by feature contract v2. Manufacturer/bus identifiers and
`bus_battery_kwh` are forbidden QRF inputs. The fitted QRF
`feature_names_in_` must equal that list in the same order.

G2 metadata and release JSON also carry the same `passenger_prior` object:

```json
{
  "passenger_prior": {
    "source": "vbz-ogd",
    "release_id": "<versioned-prior>",
    "sha256": "<64-lowercase-hex>",
    "correction_factor_s": 1.0,
    "qrf_reference_occupancy_percent": 20.0,
    "mass_weighting": "distance",
    "hvac_weighting": "duration",
    "matching_policy": "vbz-ogd-gtfs-v1",
    "primary_secondary_distance_coverage": 0.8,
    "passenger_mass_kg": 68.0
  }
}
```

The correction is fixed to `s=1`, matching coverage must be at least 80%, and
the serialized artifact reference occupancy must exactly equal the manifest.
The backend never reads OGD rows: it reconstructs the reference mass from the
selected bus model, this scalar occupancy and the shared 68 kg passenger-mass
contract. Historical legacy artifacts remain on their serialized mass
convention (70 kg when it was not explicitly recorded).

The corresponding `auxiliary_estimator` blocks are:

```json
{
  "vecto-g2": {
    "training": "<versioned-training-estimator>",
    "inference": "vecto-hvac-5.1.3-r744-templates-v2",
    "fixed_auxiliary_owner": "model",
    "auxiliary_contract": "vecto-hvac-only",
    "transfer_policy": "fleet-setpoints-to-vecto-default-v1",
    "training_comfort_policy": {
      "release_id": "<versioned-comfort-policy>",
      "sha256": "<64-lowercase-hex>",
      "scope": "training-only"
    },
    "vecto_template_release": "vecto-hvac-5.1.3-r744-templates-v2",
    "vecto_template_sha256": "68dae71d01f93f372d04471f0604b483ab629aa606edb7ac2dcf75cca0541c51"
  },
  "vecto-g0-transfer": {
    "training": "data-driven-by-bus",
    "training_sha256": "8ae333170a856adcd938b5a259f21cc5a216743a8eb0c34c5542fb0e6532cfb9",
    "inference": "vecto-complete-5.1.3-r744-templates-v2",
    "fixed_auxiliary_owner": "template",
    "auxiliary_contract": "vecto-complete",
    "vecto_template_release": "vecto-hvac-5.1.3-r744-templates-v2",
    "vecto_template_sha256": "68dae71d01f93f372d04471f0604b483ab629aa606edb7ac2dcf75cca0541c51"
  }
}
```

The remaining release schema is: schema version 1; exact release
ID; feature and categorical contracts; matching `feature_release`,
`prediction_stack_contract`, `auxiliary_estimator`, `training_software` and
`elettra_core` blocks (plus matching `passenger_prior` for G2); exactly
four hashed artifacts (joblib, metadata, feature importance and acceptance);
and an immutable manifest-last publication block. Acceptance is either
`passed` or `approved_with_documented_regression`. The latter also requires
non-empty approver, UTC timestamp, reason and immutable evaluation SHA-256.

For VECTO releases the hashed acceptance JSON must use schema 1 and contain:

```json
{
  "schema_version": 1,
  "evaluation_manifest": {"sha256": "<64-lowercase-hex>"},
  "test_set": {"source_row_identity_sha256": "<feature row_identity_sha256>"},
  "candidate": {
    "model_name": "<release-id>",
    "feature_contract_version": "2.0.0",
    "feature_release_manifest_sha256": "<feature manifest_sha256>"
  }
}
```

The release manifest is authoritative for `acceptance.decision`; for a
controlled regression, `documented_approval.evaluation_sha256` must equal the
acceptance report's `evaluation_manifest.sha256`. Historical legacy acceptance
JSON remains accepted so the code-first rollout can keep the current legacy
release registered.

Startup hashes and deserializes every configured joblib. VECTO artifacts must
be a fitted `elettra_core.HybridGreyboxQRF` with the registered stack, the
correct G0/G2 grey-box class, finite fitted parameters, fitted QRF and exact
selected-feature order. Readiness probes every registered manifest and object
identity, not only the default model.

## Static and per-prediction diagnostics

`/health` exposes static runtime information: available stacks and tiers,
model releases, auxiliary contract, template release/checksum, validated model
digests, passenger mass and training/transfer comfort policies.
Request-dependent inputs cannot truthfully be represented by a
single process health value. Actual bus length, selected template length,
passengers, GHI, heating mode, climatic row, powers and unmatched thermal
demand are therefore persisted in each prediction run under
`summary.vecto_auxiliary`; total unmatched thermal energy is part of the
component breakdown.
