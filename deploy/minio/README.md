# MinIO identities for elevation profiles

Create four non-root identities and attach the policies in this directory:

- `elevation-backend-readonly.json` for the FastAPI service;
- `elevation-worker.json` for the auxiliary worker;
- `elevation-release-publisher.json` only for the offline release command.
- `consumption-model-publisher-v2.json` only for the approved trainer upload.

Example with an administrative `mc` alias named `production`:

```bash
mc admin policy create production elevation-backend-readonly \
  deploy/minio/elevation-backend-readonly.json
mc admin policy create production elevation-worker \
  deploy/minio/elevation-worker.json
mc admin policy create production elevation-release-publisher \
  deploy/minio/elevation-release-publisher.json
mc admin policy create production consumption-model-publisher-v2 \
  deploy/minio/consumption-model-publisher-v2.json
```

Attach each policy to its dedicated user/service account using the MinIO
administrative workflow for the deployed server version. Store generated keys
in the secret manager, not `.env` or Compose. The worker policy has an explicit
deny for every object operation under `releases/`; only the offline publisher
can upload profiles and commit `release.json`. The publisher cannot delete
release objects. Worker and publisher include `AbortMultipartUpload` so a
failed large upload can be cleaned up.

The backend is explicitly denied access to worker-only `backups/`,
`._staging/` and `._health/` objects. It reads immutable GTFS releases from
`elevation-profiles-gtfs`, root-level auxiliary profiles from
`elevation-profiles`, and models from `consumption-models`. The worker has no
permissions on the GTFS bucket. The publisher has no permissions on the aux
bucket and cannot delete release objects. Its list and object permissions are
pinned to `releases/roaddeck-v3.3-20260828-db39527/`; publishing a later release
requires a reviewed policy with that release's exact prefix, not a wildcard.

The templates use the default bucket names. If either
`ELEVATION_PROFILES_BUCKET` or `GTFS_ELEVATION_PROFILES_BUCKET` differs, change
every corresponding ARN before creating the policies. Enable versioning on the
aux bucket before replacing the v1 profiles. Create the GTFS bucket with object
locking enabled, then apply governance retention to the completed release;
these JSON policies do not substitute for storage-level retention.

The model publisher can list/read only the approved
`models/greybox_qrf_production_core_v2_roaddeck_v3_3_20260828/` prefix and can
get/put objects only there; it cannot delete. A later model release requires a
new policy pinned to its exact prefix. Give its credentials only to the trainer
`--upload-minio` invocation and revoke or disable the identity after the model
gate. The trainer uploads the joblib, metadata, feature-importance and accepted
gate report first, then writes `{model}_release.json` last. Backend startup
treats only that release manifest as the commit marker. It verifies the
declared SHA-256 and size of all four artifacts, checks metadata/manifest
provenance, and pins the manifest digest plus every object identity for the
process lifetime. Never use MinIO root credentials for model training or
publication.

Before enabling a release in production, verify all of the following:

```bash
mc version info production/elevation-profiles
mc version info production/elevation-profiles-gtfs
mc retention info production/elevation-profiles-gtfs
mc anonymous get production/elevation-profiles
mc anonymous get production/elevation-profiles-gtfs
mc admin policy entities production --policy elevation-backend-readonly
mc admin policy entities production --policy elevation-worker
mc admin policy entities production --policy elevation-release-publisher
mc admin policy entities production --policy consumption-model-publisher-v2
```

`release.json` is written last by the publisher, but IAM cannot express write
ordering or prevent overwriting an existing key. Object locking/retention is
therefore the storage-level immutability control. If the existing bucket does
not support locking, publish into a bucket created with object locking enabled
or treat disabling the publisher identity immediately after a successful
release as a temporary operational control.
