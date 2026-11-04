#!/bin/sh
set -eu
# Alertmanager reads the existing Slack webhook from tmpfs. Do not put the
# credential in the YAML, repository, persistent volume, command line or logs.
: "${SLACK_WEBHOOK_URL:?SLACK_WEBHOOK_URL is required}"
umask 077
printf '%s' "$SLACK_WEBHOOK_URL" > /run/alertmanager/slack-webhook
unset SLACK_WEBHOOK_URL
exec /bin/alertmanager "$@"
