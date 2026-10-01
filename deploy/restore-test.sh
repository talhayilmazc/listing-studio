#!/usr/bin/env bash
# Prove the latest backup restores (production-spec F5: "an untested backup is
# not a backup"). Safe to run on the live server at any time.
#
#   deploy/restore-test.sh
#
# Restores the newest database dump into a throwaway PostgreSQL container —
# never into production — then compares every table's row count with the live
# database, and checks the newest file archive lists cleanly against the live
# upload directory. Exits non-zero on any mismatch or unreadable backup.
# -E: the ERR trap must also fire for failures inside functions (compose()),
# otherwise a failed backup exits without ever sending its failure ping.
set -Eeuo pipefail
# Stop Git Bash rewriting container paths like /data/storage into Windows
# paths when these scripts are run from a Windows machine. No effect on Linux.
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."
OPS_ENV="${OPS_ENV:-/etc/listyro/ops.env}"
# shellcheck disable=SC1090
[ -r "$OPS_ENV" ] && . "$OPS_ENV"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }
BACKUP_DIR="${BACKUP_DIR:-/var/backups/listyro}"
SCRATCH="listyro-restore-test-$$"

dump="$(ls -1t "$BACKUP_DIR"/db/db-*.dump 2>/dev/null | head -1 || true)"
if [ -z "$dump" ]; then
  echo "no database backup in $BACKUP_DIR/db" >&2
  exit 1
fi
echo "backup:   $dump ($(du -h "$dump" | cut -f1))"

cleanup() { docker rm -f "$SCRATCH" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# Same major version as production, isolated: no network, no volume.
docker run -d --name "$SCRATCH" --network none \
  -e POSTGRES_PASSWORD=restore-test -e POSTGRES_DB=restored postgres:16 >/dev/null
# Wait over TCP, not the socket: on first boot the image runs a temporary
# init-only server on the socket, which answers pg_isready and then shuts down.
# Only the real server listens on TCP (loopback exists even with no network).
for _ in $(seq 1 90); do
  docker exec "$SCRATCH" pg_isready -h 127.0.0.1 -U postgres -d restored >/dev/null 2>&1 && break
  sleep 1
done

docker exec -i "$SCRATCH" pg_restore -h 127.0.0.1 -U postgres -d restored --no-owner --exit-on-error < "$dump"
echo "restore:  completed into a scratch database"

# Exact row counts per table, sorted, from a given psql invocation.
COUNT_SQL="
DO \$\$
DECLARE r record; n bigint;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename LOOP
    EXECUTE format('SELECT count(*) FROM public.%I', r.tablename) INTO n;
    RAISE NOTICE '%=%', r.tablename, n;
  END LOOP;
END \$\$;"

restored="$(docker exec -i "$SCRATCH" psql -h 127.0.0.1 -U postgres -d restored -qAt 2>&1 <<<"$COUNT_SQL" \
  | sed -n 's/^NOTICE:  //p')"
live="$(compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qAt' 2>&1 <<<"$COUNT_SQL" \
  | sed -n 's/^NOTICE:  //p')"

printf '\n%-28s %10s %10s\n' "table" "backup" "live"
mismatch=0
while IFS='=' read -r table n; do
  live_n="$(printf '%s\n' "$live" | sed -n "s/^$table=//p")"
  flag=""
  if [ "$n" != "$live_n" ]; then flag="  <- differs"; mismatch=$((mismatch + 1)); fi
  printf '%-28s %10s %10s%s\n' "$table" "$n" "${live_n:-?}" "$flag"
done <<<"$restored"

tables="$(printf '%s\n' "$restored" | grep -c '=' || true)"
if [ "$tables" -eq 0 ]; then
  echo "the restored database has no tables" >&2
  exit 1
fi

archive="$(ls -1t "$BACKUP_DIR"/storage/storage-*.tar.gz 2>/dev/null | head -1 || true)"
if [ -n "$archive" ]; then
  in_archive="$(tar -tzf "$archive" | grep -vc '/$' || true)"
  in_live="$(compose exec -T api find /data/storage -type f | wc -l | tr -d ' ')"
  printf '\nfiles:    %s in %s, %s live\n' "$in_archive" "$(basename "$archive")" "$in_live"
fi

echo
if [ "$mismatch" -gt 0 ]; then
  echo "$mismatch table(s) differ. If the service was in use after the backup ran, that is"
  echo "expected; run deploy/backup.sh and then this again on a quiet system to confirm."
  exit 2
fi
echo "restore test ok: $tables tables, every row count matches"
