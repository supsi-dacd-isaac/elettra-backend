# Falco monitoring and Slack notifications

Falco observes host/container activity; it does not enforce database permissions,
block SQL connections, or modify database contents.

## Pipeline and notification policy

`Falco 0.41.3 -> Falcosidekick 2.34.1 -> dedicated Alertmanager 0.34.2 -> Slack`

Falco also keeps JSON events in Docker logs and `/var/log/falco/falco.log` and
streams events to `falco-exporter`. The Slack webhook is used only by the dedicated
Alertmanager, so there is no duplicate direct Slack delivery from Falcosidekick.

| Priority | Slack behavior |
| --- | --- |
| Notice / Informational / Debug | Local events only; not forwarded to Alertmanager |
| Warning / Error | One group per host: first summary after 5 minutes, updates no more often than every 30 minutes, unchanged reminders after 4 hours |
| Critical / Alert / Emergency | One group per host and rule: first notification after 10 seconds, updates no more often than every 15 minutes, unchanged reminders after 1 hour |

Alertmanager's `resolve_timeout: 2h` governs event expiry. Do not set
`ALERTMANAGER_EXPIRESAFTER`: the sidekick payload does not supply `startsAt`, and
explicit future `endsAt` timestamps have undesirable start-time semantics with
this Alertmanager version. Resolved notifications are disabled.

Each Slack message shows the number of active events and at most five examples,
with rule, container, process and UID. Full command lines are not rendered in
Slack. The complete events remain in Falco's local logs. Grouping limits repeated
messages; it is not a global limit across distinct critical rules.

## Detection tuning

`rules.d/zz_elettra_tuning.yaml` loads after the original application rules and
preserves site edits to `elettra_rules.yaml`.

- Expected raw PostgreSQL reads require an explicitly listed DB container, an
  expected PostgreSQL/Timescale image, process `postgres`, and a verified executable
  path. Other programs, host accesses and unknown identities remain covered by the
  Critical database-file rule. Opening the mount directory itself is not treated
  as reading a database file.
- Starting an SQL client is an audit event at Notice, not evidence of an intrusion.
- Normal reads by the MinIO server are excluded from its custom data-read rule.
- The exact MinIO loopback TCP readiness probe is excluded from the upstream
  stream-redirection rule using the image, command, ancestry and destination.
- IPv6 loopback is excluded from the external-network rule, like IPv4 loopback.

Review the explicit container/executable lists when adding or upgrading database
services. Changes to these lists affect alerts only, never DB access rights.

The Docker socket must be mounted at `/host/var/run/docker.sock`, matching the
image's `HOST_ROOT=/host`. Events include container identity, executable, PID,
parent process and UID for diagnosis. `FALCO_HOSTNAME=elettra` keeps grouping stable
across Falco container recreations.

## Credentials and service isolation

Alertmanager reads the existing `SLACK_WEBHOOK_URL` through its entrypoint, writes
it with mode 0600 to a tmpfs file, and removes it from the launched process's
environment. Never copy the webhook into tracked YAML or logs. Container metadata
still requires protecting Docker access, as with the previous sidekick deployment.

Alertmanager has no published host port. Falcosidekick's HTTP input is bound only
to `127.0.0.1:2801`. Notification state uses the `falco-alertmanager-data` volume.
The sidekick and Alertmanager images are pinned by version and digest.

## Validation and operations

Validate candidate rules before installing them in the watched rules directory:

```bash
docker exec elettra-falco falco \
  -V /etc/falco/falco_rules.yaml \
  -V /etc/falco/rules.d/elettra_rules.yaml \
  -V /etc/falco/rules.d/zz_elettra_tuning.yaml
docker exec elettra-falco-alertmanager amtool check-config \
  /etc/alertmanager/alertmanager.yml
```

Limit deployments to the monitoring services:

```bash
docker compose up -d --no-deps --no-build falco-alertmanager falcosidekick falco
docker compose restart falco-exporter
```

Do not run an unrestricted Compose update or restart database/application services
for monitoring changes. Falco watches its rule files and reloads when they change;
use a separately mounted candidate directory for validation before replacement.

Check the pipeline without printing container environments or the rendered
Compose configuration, which contain credentials:

```bash
docker logs --since 5m --tail 100 elettra-falcosidekick
docker logs --since 5m --tail 100 elettra-falco-alertmanager
docker exec elettra-falco-alertmanager wget -q -O- http://127.0.0.1:9093/-/ready
```

Before deployment, back up the literal Compose/rule files and record application
container IDs/start times. Keep operational backups outside the repository.
On rollback, restore those files, remove only newly introduced tuning files,
recreate only Falco/sidekick, restart the exporter, and stop the dedicated
Alertmanager if restoring direct Slack delivery.

The notification integration test uses an isolated Docker network and a local
mock Slack receiver. A burst of 30 Warning and 30 Critical events must produce two
summaries; 30 Notice events must be filtered. Any live detection probe must use a
unique disposable file and an exact temporary Alertmanager silence so synthetic
events never notify Slack. Database checks use only `BEGIN READ ONLY; SELECT 1;
ROLLBACK;` from a disposable client, with no DB container restart or schema change.
