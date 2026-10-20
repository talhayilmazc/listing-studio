#!/usr/bin/env bash
# Disk usage alert (production-spec F6). Run hourly by cron. Uploaded designs,
# their derivatives and backups only ever grow; this says so before the disk
# fills and the database stops accepting writes.
#
# Alerts go to ALERT_WEBHOOK_URL (/etc/listyro/ops.env) as a plain-text POST —
# which ntfy.sh, healthchecks.io and most chat webhooks accept.
#
# It also measures what the app's containers cannot see: how much the backups and
# Docker itself take: images (with the containerd image store their snapshots live under
# /var/lib/containerd, not Docker's root directory), build cache, container layers and logs,
# the way `docker system df` counts them (deploy/docker-usage.sh); not the volumes. The sizes
# are left in Redis under "ops:disk" for four hours, where Admin > Usage and the daily summary
# read them (backend/app/core/disk.py). Sizes only.
set -euo pipefail
cd "$(dirname "$0")/.."

OPS_ENV="${OPS_ENV:-/etc/listyro/ops.env}"
# shellcheck disable=SC1090
[ -r "$OPS_ENV" ] && . "$OPS_ENV"
THRESHOLD="${DISK_ALERT_PERCENT:-75}"
# A build needs several GB at once: alert on what is left, not only on the share used.
MIN_FREE_GB="${DISK_ALERT_FREE_GB:-8}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/listyro}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
# Uploads: alert when they grow more than STORAGE_GROWTH_ALERT_GB (1) in a day.
STORAGE_VOLUME="${STORAGE_VOLUME:-listyro_storage_data}"
STORAGE_STATE="${STORAGE_STATE:-/var/lib/listyro/storage-size.log}"
# shellcheck source=deploy/storage-growth.sh
. "$(dirname "$0")/storage-growth.sh"
# shellcheck source=deploy/docker-usage.sh
. "$(dirname "$0")/docker-usage.sh"

report_usage() {
  local backups df parts logs docker_bytes
  df="$(docker system df --format '{{.Type}}	{{.Size}}' 2>/dev/null)" || return 1
  [ -n "$df" ] || return 1
  backups="$(du -sb "$BACKUP_DIR" 2>/dev/null | cut -f1)"
  backups="${backups:-0}"
  parts="$(printf '%s\n' "$df" | docker_df_json)"
  logs="$(docker_logs_bytes)"
  docker_bytes="$(docker_total "$parts" "$logs")"
  printf '{"at":%s,"backups_bytes":%s,"docker_bytes":%s,"docker":%s}' "$(date +%s)" "$backups" "$docker_bytes" \
    "$(docker_parts_json "$parts" "$logs")" \
    | docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli -x SETEX ops:disk 14400 >/dev/null
  echo "in use: backups $(( backups / 1048576 )) MB, Docker $(( docker_bytes / 1048576 )) MB ($parts, logs $logs bytes)"
}
# Before the alert below: a webhook that cannot be reached must not cost the report.
report_usage || echo "disk report could not be written: Admin > Usage will show backups and Docker as unknown" >&2

alerts=""
storage_mount="$(docker volume inspect -f '{{.Mountpoint}}' "$STORAGE_VOLUME" 2>/dev/null || true)"
if [ -n "$storage_mount" ] && [ -d "$storage_mount" ]; then
  storage_bytes="$(du -sb "$storage_mount" 2>/dev/null | cut -f1)"
  echo "uploads: $(( ${storage_bytes:-0} / 1048576 )) MB"
  growth="$(storage_growth "${storage_bytes:-0}" "$STORAGE_STATE")"
  [ -z "$growth" ] || alerts="${alerts}$(hostname): ${growth}"
else
  echo "the storage volume $STORAGE_VOLUME was not found: its daily growth is not checked" >&2
fi
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
