#!/usr/bin/env bash
# Update production, safely, in one command:
#
#   cd /opt/listyro && bash deploy/update.sh
#
# Run it as your own user, with `bash`, so it
# does not depend on the file being executable: a `git checkout -- .` once reset
# the scripts' mode bits, and a deploy must not hinge on that. For the same
# reason every script it calls is called through `bash`.
#
# It does the whole sequence in order and stops at the first failure:
#
#   1. disk space: refuses to start without room for a full build
#   2. backup, verified (a database dump that parses), before anything changes
#   3. git pull (fast-forward only; local edits are reported, never discarded)
#   4. prune build cache older than a week
#   5. docker compose up -d --build
#   6. wait until every service reports healthy
#   7. preflight
#   8. reclaim build space: when less than 12 GB is free, remove all build cache
#      and dangling images, and say how much that freed (deploy/reclaim.sh)
#
# The incident this exists for: the disk filled during `up --build`, the build
# failed, Redis was cut off mid-write and would not start, and nothing said so.
# A failure here is posted to ALERT_WEBHOOK_URL when that is set.
#
# Settings (environment, or /etc/listyro/ops.env when it is readable):
#   DEPLOY_MIN_FREE_GB   free space required before building (default 8)
#   DEPLOY_HEALTH_WAIT   seconds to wait for health (default 300)
#   DEPLOY_RECLAIM_BELOW_GB  after the update, prune build space when less than
#                        this is free (default 12)
#   SKIP_BACKUP=1        first deploy only: there is nothing to back up yet
set -Eeuo pipefail
export MSYS_NO_PATHCONV=1

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$SELF")/.."
# The backup directory and the alert settings belong to root, so the script runs
# as root (it asks for sudo once) and does the git steps as you.
if [ "$(id -u)" -ne 0 ]; then
  exec sudo --preserve-env=DEPLOY_MIN_FREE_GB,DEPLOY_HEALTH_WAIT,DEPLOY_RECLAIM_BELOW_GB,SKIP_BACKUP,COMPOSE_FILE,BACKUP_DIR,OPS_ENV -- bash "$SELF" "$@"
fi
OPS_ENV="${OPS_ENV:-/etc/listyro/ops.env}"
# shellcheck disable=SC1090
[ -r "$OPS_ENV" ] && . "$OPS_ENV"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/listyro}"
MIN_FREE_GB="${DEPLOY_MIN_FREE_GB:-8}"
HEALTH_WAIT="${DEPLOY_HEALTH_WAIT:-300}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
SERVICES_WITH_HEALTH="postgres redis api worker frontend"

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }
step() { STEP="$*"; printf '\n== %s\n' "$*"; }
STEP="starting"

notify() {
  [ -n "$ALERT_WEBHOOK_URL" ] || return 0
  curl -fsS -m 10 --retry 2 -d "$1" "$ALERT_WEBHOOK_URL" >/dev/null 2>&1 || true
}
failed() {
  local code=$?
  echo
  echo "UPDATE FAILED at: $STEP (exit $code)"
  echo "Nothing after this step was run. The running services were not touched unless the step was the build itself."
  notify "$(hostname): deploy failed at '$STEP' (exit $code)"
  exit "$code"
}
trap failed ERR

# Root owns the backup directory; everything else is done as the operator.
as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }
as_operator() {
  if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    sudo -u "$SUDO_USER" "$@"
  else
    "$@"
  fi
}

# Free space, in whole GB, on the filesystem holding a path.
free_gb() { echo $(( $(df --output=avail -k "$1" | tail -1 | tr -dc '0-9') / 1048576 )); }
docker_root() { docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker; }

check_disk() {
  # The build writes under Docker's root; the backup under BACKUP_DIR. Often the same disk.
  local low=0 path free
  for path in "$(docker_root)" "$BACKUP_DIR" .; do
    [ -e "$path" ] || continue
    free="$(free_gb "$path")"
    echo "  $path: ${free} GB free"
    [ "$free" -ge "$MIN_FREE_GB" ] || low=1
  done
  return "$low"
}

# --- 1. disk ------------------------------------------------------------------
step "1/8 disk space (need ${MIN_FREE_GB} GB free for a full build)"
if ! check_disk; then
  echo "  Below ${MIN_FREE_GB} GB. Removing build cache older than a week and looking again."
  docker builder prune -f --filter until=168h >/dev/null
  if ! check_disk; then
    echo
    echo "Not enough free disk for a build. Nothing was changed. To free space:"
    echo "  docker builder prune -af          # all build cache"
    echo "  docker image prune -f             # images no container uses"
    echo "  sudo ls -lh $BACKUP_DIR/storage   # old storage archives (deploy/backup.sh keeps STORAGE_KEEP of them)"
    false
  fi
fi

# --- 2. backup ----------------------------------------------------------------
step "2/8 backup, verified"
if [ "${SKIP_BACKUP:-0}" = "1" ]; then
  echo "  SKIP_BACKUP=1: skipped (first deploy only)"
elif [ -z "$(compose ps -q postgres 2>/dev/null)" ]; then
  echo "  the database is not running, so there is nothing to back up (first deploy)"
else
  started="$(date +%s)"
  as_root bash deploy/backup.sh
  # backup.sh already checks that the dump parses; make sure this run produced one.
  newest="$(as_root sh -c "ls -1t '$BACKUP_DIR'/db/db-*.dump 2>/dev/null | head -1")"
  [ -n "$newest" ] || { echo "  no database dump was written"; false; }
  taken="$(as_root stat -c %Y "$newest")"
  size="$(as_root stat -c %s "$newest")"
  if [ "$taken" -lt "$started" ] || [ "$size" -le 0 ]; then
    echo "  the newest dump ($newest) is not from this run, or is empty"
    false
  fi
  echo "  verified: $newest ($((size / 1024)) KB)"
fi

# --- 3. code ------------------------------------------------------------------
step "3/8 git pull"
if [ -n "$(as_operator git status --porcelain --untracked-files=no)" ]; then
  echo "  Tracked files were changed on the server:"
  as_operator git status --short --untracked-files=no | sed 's/^/    /'
  echo "  Mode-only changes are ignored below; real edits stop the update rather than being thrown away."
fi
# A changed executable bit is not a change worth refusing a deploy over.
as_operator git config core.fileMode false
before="$(as_operator git rev-parse --short HEAD)"
as_operator git pull --ff-only
after="$(as_operator git rev-parse --short HEAD)"
echo "  $before -> $after"

# --- 4. build cache -----------------------------------------------------------
step "4/8 prune build cache older than a week"
docker builder prune -f --filter until=168h | tail -1

# --- 5. build and start -------------------------------------------------------
step "5/8 build and start"
compose up -d --build

# --- 6. health ----------------------------------------------------------------
step "6/8 waiting for every service to be healthy (up to ${HEALTH_WAIT}s)"
deadline=$(( $(date +%s) + HEALTH_WAIT ))
while :; do
  pending=""
  for service in $SERVICES_WITH_HEALTH; do
    id="$(compose ps -q "$service" 2>/dev/null | head -1)"
    status="missing"
    [ -n "$id" ] && status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null || echo missing)"
    [ "$status" = "healthy" ] || pending="$pending $service=$status"
  done
  # The tunnel has no health check: it must simply be running.
  id="$(compose ps -q cloudflared 2>/dev/null | head -1)"
  if [ -z "$id" ] || [ "$(docker inspect --format '{{.State.Status}}' "$id" 2>/dev/null)" != "running" ]; then
    pending="$pending cloudflared=not-running"
  fi
  [ -z "$pending" ] && break
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "  still not healthy:$pending"
    compose ps
    for item in $pending; do
      echo "--- last lines of ${item%%=*}"
      compose logs --tail 25 "${item%%=*}" 2>&1 | sed 's/^/    /'
    done
    false
  fi
  sleep 5
done
echo "  all healthy"
compose ps --format 'table {{.Service}}\t{{.Status}}'

# --- 7. preflight -------------------------------------------------------------
step "7/8 preflight"
as_root bash deploy/preflight.sh

# --- 8. reclaim build space ---------------------------------------------------
# The update has succeeded by now: nothing here may turn it into a failure.
step "8/8 reclaim build space"
reclaimed="$(DEPLOY_RECLAIM_BELOW_GB="${DEPLOY_RECLAIM_BELOW_GB:-}" OPS_ENV="$OPS_ENV" bash deploy/reclaim.sh 2>&1)" \
  || reclaimed="reclaim could not run: ${reclaimed:-no output}"
printf '%s\n' "$reclaimed" | sed 's/^/  /'
case "$(printf '%s\n' "$reclaimed" | tail -1)" in
  reclaimed:*) notify "$(hostname): updated to $after; $(printf '%s\n' "$reclaimed" | tail -1)" ;;
esac

echo
echo "Updated to $after. Free disk now:"
check_disk || echo "  (below the ${MIN_FREE_GB} GB a build needs: free space before the next update)"
[ -n "$ALERT_WEBHOOK_URL" ] || echo "Note: ALERT_WEBHOOK_URL is not set, so a failure of this script, a full disk or a failed backup reaches nobody."
