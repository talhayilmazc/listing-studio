#!/usr/bin/env bash
# Grant and withdraw the admin role (the only way anyone becomes an admin).
#
#   deploy/admin.sh create-admin you@example.com     # new admin, or promote an account
#   deploy/admin.sh create-admin you@example.com --set-password
#   deploy/admin.sh demote-admin someone@example.com # never the last admin
#   deploy/admin.sh list-admins
#
# The password is typed at a prompt inside the container, never passed as an
# argument, so it stays out of shell history and process listings. Everything
# else — invites, temporary passwords, quotas, suspensions — is done signed in,
# at https://listyro.com/admin.
set -Eeuo pipefail
# Stop Git Bash rewriting container paths when run from Windows. No effect on Linux.
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

case "${1:-}" in
  create-admin|demote-admin|list-admins)
    # No -T: the password prompt needs a terminal.
    exec docker compose -f "$COMPOSE_FILE" exec api python -m app.cli "$@"
    ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
