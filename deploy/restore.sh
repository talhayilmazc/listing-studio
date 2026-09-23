#!/usr/bin/env bash
# Restore production from a backup. DESTROYS the current database (and, with a
# file archive, the current uploads). For rehearsing, use deploy/restore-test.sh,
# which never touches production.
#
#   deploy/restore.sh --dump PATH [--files PATH] --i-understand-this-replaces-production
# -E: the ERR trap must also fire for failures inside functions (compose()),
# otherwise a failed backup exits without ever sending its failure ping.
set -Eeuo pipefail
# Stop Git Bash rewriting container paths like /data/storage into Windows
# paths when these scripts are run from a Windows machine. No effect on Linux.
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

dump="" files="" confirmed=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dump) dump="$2"; shift 2 ;;
    --files) files="$2"; shift 2 ;;
    --i-understand-this-replaces-production) confirmed=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$dump" ] && [ -f "$dump" ] || { echo "--dump must name a backup file" >&2; exit 2; }
[ -z "$files" ] || [ -f "$files" ] || { echo "--files must name an archive" >&2; exit 2; }
[ -n "$confirmed" ] || { echo "refusing without --i-understand-this-replaces-production" >&2; exit 2; }

echo "checking the backup is readable before touching anything"
compose exec -T postgres pg_restore --list < "$dump" > /dev/null
[ -z "$files" ] || gzip -t "$files"

echo "stopping everything that writes"
compose stop cloudflared frontend worker api

echo "replacing the database"
compose exec -T postgres sh -c '
  dropdb -U "$POSTGRES_USER" --if-exists --force "$POSTGRES_DB" &&
  createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' < "$dump"

if [ -n "$files" ]; then
  echo "replacing uploaded files"
  compose run --rm --no-deps -T --entrypoint sh api -c 'find /data/storage -mindepth 1 -delete && tar -xzf - -C /data/storage' < "$files"
fi

echo "starting the service"
compose up -d
echo "restore complete. Run deploy/restore-test.sh to confirm the counts, then check the app."
