#!/usr/bin/env bash
# Backups (production-spec F5). Run nightly by cron (deploy/provision.sh).
#
#   * database: a pg_dump every run, verified readable before it is kept;
#     kept RETENTION_DAYS (14, the period the Privacy Policy states)
#   * uploaded files: NOT archived by default (STORAGE_BACKUP=off). Uploads are
#     working copies: sellers keep their originals, published images live on
#     Etsy, and the app deletes them days after use (upload retention). An
#     archive of them on the same 30 GB disk was what filled it. With it off,
#     any storage archives still here are removed. STORAGE_BACKUP=on brings back
#     the weekly archive (only the newest STORAGE_KEEP (1) kept; previews left out).
#   * never the thing that fills the disk: an archive is only written when there
#     is room for it; a skipped archive is alerted and exits 2 (the database dump
#     is safe). Only a failed or unreadable database dump is a failure (exit 1,
#     "backup FAILED"); any later step that fails leaves it INCOMPLETE (exit 2).
#
# Disk used at steady state: one storage archive (about the size of the upload
# directory without previews) plus RETENTION_DAYS small database dumps; for a
# few minutes each week, while the new archive is written and checked, two
# archives. The script prints the total at the end of every run.
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
STORAGE_KEEP="${STORAGE_KEEP:-1}"
# off (default): uploads are working copies and are not archived. on: weekly archive.
STORAGE_BACKUP="${STORAGE_BACKUP:-off}"
# Free space that must remain after an archive is written.
BACKUP_FREE_MARGIN_MB="${BACKUP_FREE_MARGIN_MB:-3072}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"

ping_fail() {
  [ -n "$HEALTHCHECK_URL" ] && curl -fsS -m 10 --retry 3 "$HEALTHCHECK_URL/fail" >/dev/null || true
}
# What a failed step means. Only the database dump and its check are a failed
# backup (exit 1); a step after a good dump (an archive, the off-site copy) leaves
# the backup incomplete (exit 2: update.sh goes on, the dump is safe).
# Errors inside $(...) and pipeline parts run in a subshell and are ignored here:
# the command around them fails in this shell if it matters (set -e, pipefail).
# To stderr: the trap can run while the failed command's stdout is redirected.
phase="database"
on_error() {
  local rc=$? line=$1 cmd=$2
  [ "$BASHPID" = "$$" ] || return 0
  trap - ERR
  if [ "$phase" = "database" ]; then
    echo "backup FAILED: the database dump or its check failed (line $line: $cmd)" >&2
    ping_fail
    exit 1
  fi
  echo "backup INCOMPLETE: the database dump is safe, but a later step failed (line $line, exit $rc: $cmd)" >&2
  [ -n "$ALERT_WEBHOOK_URL" ] && curl -fsS -m 10 --retry 3 -d "$(hostname): backup incomplete: $cmd failed" "$ALERT_WEBHOOK_URL" >/dev/null || true
  ping_fail
  exit 2
}
trap 'on_error "$LINENO" "$BASH_COMMAND"' ERR

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
phase="after-database"

# --- uploaded files, weekly ---------------------------------------------------
skipped=""
if [ "$STORAGE_BACKUP" != "on" ]; then
  echo "storage:  not archived (STORAGE_BACKUP=$STORAGE_BACKUP): uploads are working copies"
  # Archives from when it was on: copies of working files, and the biggest thing here.
  find "$BACKUP_DIR/storage" -type f \( -name 'storage-*.tar.gz' -o -name 'storage-*.partial' \) -print -delete \
    | sed 's/^/removed:  /'
elif [ -z "$(find "$BACKUP_DIR/storage" -name 'storage-*.tar.gz' -mmin -$((6 * 1440)) 2>/dev/null)" ]; then
  # Room first. Images do not compress, so the archive is about as large as the
  # directory; without previews it is somewhat smaller, which only adds margin.
  need_mb="$(compose exec -T api du -sm /data/storage | cut -f1)"
  free_mb=$(( $(df --output=avail -k "$BACKUP_DIR" | tail -1 | tr -dc '0-9') / 1024 ))
  if [ "$free_mb" -lt $((need_mb + BACKUP_FREE_MARGIN_MB)) ]; then
    skipped="storage archive skipped: it needs ~${need_mb} MB and ${BACKUP_FREE_MARGIN_MB} MB must stay free, but only ${free_mb} MB is free on $BACKUP_DIR"
    echo "storage:  SKIPPED: $skipped"
  else
    archive="$BACKUP_DIR/storage/storage-$stamp.tar.gz"
    # Previews (<file>.v<N>.w<width>....jpg) are a cache the app rebuilds on demand.
    compose exec -T api tar -czf - -C /data/storage --exclude='*.v[0-9]*.w[0-9]*.jpg' . > "$archive.partial"
    gzip -t "$archive.partial"
    mv "$archive.partial" "$archive"
    echo "storage:  $(du -h "$archive" | cut -f1)  $archive"
    # Only now that the new one is verified do the older ones go.
    ls -1t "$BACKUP_DIR"/storage/storage-*.tar.gz | tail -n +$((STORAGE_KEEP + 1)) | while read -r old; do
      rm -f "$old"
      echo "expired:  $old (keeping the newest $STORAGE_KEEP)"
    done
  fi
else
  echo "storage:  weekly archive is current"
fi

# --- retention: database dumps, and anything left half-written ------------------
find "$BACKUP_DIR" -type f \( -name '*.dump' -o -name '*.tar.gz' -o -name '*.partial' \)   -mmin +$((RETENTION_DAYS * 1440)) -print -delete | sed 's/^/expired:  /'
echo "on disk:  $(du -sh "$BACKUP_DIR" | cut -f1) in $BACKUP_DIR ($(find "$BACKUP_DIR/db" -type f -name '*.dump' | wc -l) dumps, $(find "$BACKUP_DIR/storage" -type f -name '*.tar.gz' | wc -l) storage archive(s)); $(df -h --output=avail "$BACKUP_DIR" | tail -1 | tr -d ' ') free"

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

if [ -n "$skipped" ]; then
  # The database is safe, the uploads are not: say so where someone will see it.
  [ -n "$ALERT_WEBHOOK_URL" ] && curl -fsS -m 10 --retry 3 -d "$(hostname): $skipped" "$ALERT_WEBHOOK_URL" >/dev/null || true
  ping_fail
  echo "backup INCOMPLETE: $skipped"
  exit 2
fi
[ -n "$HEALTHCHECK_URL" ] && curl -fsS -m 10 --retry 3 "$HEALTHCHECK_URL" >/dev/null || true
echo "backup ok"
