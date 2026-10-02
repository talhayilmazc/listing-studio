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
THRESHOLD="${DISK_ALERT_PERCENT:-75}"
# A build needs several GB at once: alert on what is left, not only on the share used.
MIN_FREE_GB="${DISK_ALERT_FREE_GB:-8}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"

alerts=""
for mount in / /var/lib/docker /var/backups; do
  [ -d "$mount" ] || continue
  used="$(df --output=pcent "$mount" | tail -1 | tr -dc '0-9')"
  free=$(( $(df --output=avail -k "$mount" | tail -1 | tr -dc '0-9') / 1048576 ))
  echo "$mount: ${used}% used, ${free} GB free"
  if [ "$used" -ge "$THRESHOLD" ] || [ "$free" -lt "$MIN_FREE_GB" ]; then
    alerts="${alerts}$(hostname): $mount is ${used}% full with ${free} GB free (alert at ${THRESHOLD}% or under ${MIN_FREE_GB} GB). "
  fi
done

if [ -n "$alerts" ]; then
  echo "ALERT: $alerts"
  if [ -n "$ALERT_WEBHOOK_URL" ]; then
    curl -fsS -m 10 --retry 3 -d "$alerts" "$ALERT_WEBHOOK_URL" >/dev/null
  else
    echo "ALERT_WEBHOOK_URL IS NOT SET in $OPS_ENV: this alert reached nobody, only syslog" >&2
  fi
fi
