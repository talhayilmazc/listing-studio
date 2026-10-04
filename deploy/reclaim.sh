#!/usr/bin/env bash
# Give build space back after a deploy, when the disk is short of it.
#
#   bash deploy/reclaim.sh          # what update.sh runs as its last step
#   RECLAIM_FORCE=1 bash deploy/reclaim.sh   # prune whatever the free space is
#
# The server's disk is small and cannot grow. A build leaves its cache and the
# images it replaced behind; a full disk stops the database. So when less than
# DEPLOY_RECLAIM_BELOW_GB (12) is free, all build cache and all dangling images
# are removed. The next build then starts cold and takes longer: running out of
# disk costs more than that.
#
# Nothing in use is touched: not running containers, not their images, not the
# volumes (uploads, database, Redis).
#
# The last line printed is the result, in one sentence; update.sh sends it to
# ALERT_WEBHOOK_URL when something was removed.
set -uo pipefail

OPS_ENV="${OPS_ENV:-/etc/listyro/ops.env}"
# shellcheck disable=SC1090
[ -r "$OPS_ENV" ] && . "$OPS_ENV"
BELOW_GB="${DEPLOY_RECLAIM_BELOW_GB:-12}"  # ":-": empty counts as unset

root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
[ -n "$root" ] && [ -d "$root" ] || root=/var/lib/docker
free_mb() { echo $(( $(df --output=avail -k "$root" | tail -1 | tr -dc '0-9') / 1024 )); }
gb() { awk -v mb="$1" 'BEGIN { printf "%.1f", mb / 1024 }'; }

before="$(free_mb)"
if [ "${RECLAIM_FORCE:-0}" != "1" ] && [ "$before" -ge $(( BELOW_GB * 1024 )) ]; then
  echo "kept: $(gb "$before") GB free is not below ${BELOW_GB} GB, so the build cache stays and the next build is quicker"
  exit 0
fi

echo "$(gb "$before") GB free is below ${BELOW_GB} GB: removing all build cache and dangling images"
cache="$(docker builder prune -af 2>&1 | tail -1)" || cache="could not be pruned ($cache)"
images="$(docker image prune -f 2>&1 | tail -1)" || images="could not be pruned ($images)"
echo "  build cache:     ${cache:-nothing to remove}"
echo "  dangling images: ${images:-nothing to remove}"

after="$(free_mb)"
freed=$(( after - before ))
[ "$freed" -ge 0 ] || freed=0
echo "reclaimed: freed $(gb "$freed") GB of build space; $(gb "$before") GB free before, $(gb "$after") GB free now"
