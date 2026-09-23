#!/usr/bin/env bash
# Operator tasks (production-spec A1, A4). The admin endpoints are refused at
# the Cloudflare edge, so these run on the server, against the API from inside
# its own container. ADMIN_TOKEN is read there from the environment; it is
# never typed, never passed on a command line, never printed.
#
#   deploy/admin.sh invite "cousin"            # one single-use invite code
#   deploy/admin.sh invites 4                  # several at once
#   deploy/admin.sh reset-password seller@example.com
# -E: the ERR trap must also fire for failures inside functions (compose()),
# otherwise a failed backup exits without ever sending its failure ping.
set -Eeuo pipefail
# Stop Git Bash rewriting container paths like /data/storage into Windows
# paths when these scripts are run from a Windows machine. No effect on Linux.
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.."
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"

call() {
  # $1 path, $2 JSON body. Python is the one HTTP client the image certainly has.
  # The path is passed without its leading slash: some shells (Git Bash) rewrite
  # anything that looks like an absolute path before docker ever sees it.
  docker compose -f "$COMPOSE_FILE" exec -T -e ADMIN_PATH="${1#/}" -e ADMIN_BODY="$2" api python - <<'PY'
import json, os, sys, urllib.error, urllib.request

req = urllib.request.Request(
    "http://127.0.0.1:8000/" + os.environ["ADMIN_PATH"],
    data=os.environ["ADMIN_BODY"].encode(),
    method="POST",
    headers={"Content-Type": "application/json", "X-Admin-Token": os.environ.get("ADMIN_TOKEN", "")},
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(resp.read().decode())
except urllib.error.HTTPError as err:
    msg = "admin endpoints are disabled: ADMIN_TOKEN is not set" if err.code == 404 else err.read().decode()
    print(f"error {err.code}: {msg}", file=sys.stderr)
    sys.exit(1)
PY
}

# Host-side JSON helpers. Take the first interpreter that actually runs: some
# systems have a "python3" that is only a stub telling you to install Python.
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import json' >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
[ -n "$PY" ] || { echo "python3 is required" >&2; exit 1; }
json_str() { "$PY" -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"; }

case "${1:-}" in
  invite)
    note="${2:-}"
    out="$(call /api/account/admin/invites "{\"note\": $(json_str "$note")}")"
    code="$(printf '%s' "$out" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print(d["code"], "expires", d["expires_at"])')"
    echo "invite${note:+ for $note}: $code"
    echo "Single use. Hand it over directly; it is not stored anywhere readable."
    ;;
  invites)
    n="${2:-1}"
    for i in $(seq 1 "$n"); do "$0" invite "beta $i"; done
    ;;
  reset-password)
    email="${2:?usage: admin.sh reset-password EMAIL}"
    out="$(call /api/account/admin/reset-password "{\"email\": $(json_str "$email")}")"
    printf '%s' "$out" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); print("temporary password for", d["email"] + ":", d["temporary_password"])'
    echo "Every existing session for that account has been ended. They must choose a new"
    echo "password at their next sign-in."
    ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
