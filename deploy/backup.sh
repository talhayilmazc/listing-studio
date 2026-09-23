#!/usr/bin/env bash
# Backups (production-spec F5). Run nightly by cron (deploy/provision.sh).
#
#   * database: a pg_dump every run, verified readable before it is kept
#   * uploaded files: an archive once a week
#   * both kept 14 days — the Privacy Policy states that period
#   * copied off the server when BACKUP_REMOTE (an rclone remote) is set
#   * optional heartbeat to HEALTHCHECK_URL, so a backup that silently stops
#     running gets noticed
#
# ENCRYPTION_KEY is never written here: a backup plus that key would expose
# every seller's Etsy tokens.
# -E: the ERR trap must also fire for failures inside functions (compose()),
# otherwise a failed backup exits without ever sending its failure ping.
set -Eeuo pipefail
# Stop Git Bash rewriting container paths like /data/storage into Windows
# paths when these scripts are run from a Windows machine. No effect on Linux.
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."
# Cron starts with an empty environment; operational settings live here.
OPS_ENV="${OPS_ENV:-/etc/listyro/ops.env}"
# shellcheck disable=SC1090
[ -r "$OPS_ENV" ] && . "$OPS_ENV"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }
BACKUP_DIR="${BACKUP_DIR:-/var/backups/listyro}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"

ping_fail() {
  [ -n "$HEALTHCHECK_URL" ] && curl -fsS -m 10 --retry 3 "$HEALTHCHECK_URL/fail" >/dev/null || true
}
trap 'echo "backup FAILED"; ping_fail' ERR

umask 077
mkdir -p "$BACKUP_DIR/db" "$BACKUP_DIR/storage"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

# --- database ---------------------------------------------------------------
db="$BACKUP_DIR/db/db-$stamp.dump"
compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "$db.partial"
# A dump nobody can read is not a backup: check it parses before keeping it.
compose exec -T postgres pg_restore --list < "$db.partial" > /dev/null
mv "$db.partial" "$db"
echo "database: $(du -h "$db" | cut -f1)  $db"

# --- uploaded files, weekly ---------------------------------------------------
if [ -z "$(find "$BACKUP_DIR/storage" -name 'storage-*.tar.gz' -mmin -$((6 * 1440)) 2>/dev/null)" ]; then
  archive="$BACKUP_DIR/storage/storage-$stamp.tar.gz"
  compose exec -T api tar -czf - -C /data/storage . > "$archive.partial"
  gzip -t "$archive.partial"
  mv "$archive.partial" "$archive"
  echo "storage:  $(du -h "$archive" | cut -f1)  $archive"
else
  echo "storage:  weekly archive is current"
fi

# --- retention: 14 days ---------------------------------------------------------
find "$BACKUP_DIR" -type f \( -name '*.dump' -o -name '*.tar.gz' -o -name '*.partial' \) \
  -mmin +$((RETENTION_DAYS * 1440)) -print -delete | sed 's/^/expired:  /'

# --- off the server -------------------------------------------------------------
# sync, not copy: the remote keeps the same 14 days, so deleted data expires
# there too, as the Privacy Policy promises. Use an rclone "crypt" remote so the
# copy is encrypted at rest wherever it lands.
if [ -n "$BACKUP_REMOTE" ]; then
  rclone sync "$BACKUP_DIR" "$BACKUP_REMOTE" --quiet
  echo "offsite:  synced to $BACKUP_REMOTE"
else
  echo "offsite:  BACKUP_REMOTE not set; pull $BACKUP_DIR from another machine"
fi

[ -n "$HEALTHCHECK_URL" ] && curl -fsS -m 10 --retry 3 "$HEALTHCHECK_URL" >/dev/null || true
echo "backup ok"
