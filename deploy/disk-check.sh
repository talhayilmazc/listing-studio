#!/usr/bin/env bash
# Disk usage alert (production-spec F6). Run hourly by cron. Uploaded designs,
# their derivatives and backups only ever grow; this says so before the disk
# fills and the database stops accepting writes.
#
# Alerts go to ALERT_WEBHOOK_URL (/etc/listyro/ops.env) as a plain-text POST —
# which ntfy.sh, healthchecks.io and most chat webhooks accept.
set -euo pipefail

OPS_ENV="${OPS_ENV:-/etc/listyro/ops.env}"
# shellcheck disable=SC1090
[ -r "$OPS_ENV" ] && . "$OPS_ENV"
THRESHOLD="${DISK_ALERT_PERCENT:-80}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"

alerts=""
for mount in / /var/lib/docker /var/backups; do
  [ -d "$mount" ] || continue
  used="$(df --output=pcent "$mount" | tail -1 | tr -dc '0-9')"
  echo "$mount: ${used}% used"
  if [ "$used" -ge "$THRESHOLD" ]; then
    alerts="${alerts}$(hostname): $mount is ${used}% full (alert at ${THRESHOLD}%). "
  fi
done

if [ -n "$alerts" ]; then
  echo "ALERT: $alerts"
  if [ -n "$ALERT_WEBHOOK_URL" ]; then
    curl -fsS -m 10 --retry 3 -d "$alerts" "$ALERT_WEBHOOK_URL" >/dev/null
  else
    echo "ALERT_WEBHOOK_URL is not set; this alert only reached syslog" >&2
  fi
fi
