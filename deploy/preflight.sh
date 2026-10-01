#!/usr/bin/env bash
# Check the production configuration before starting it (production-spec F3).
#
#   deploy/preflight.sh            # from /opt/listyro
#
# Exits non-zero on any problem that would ship an insecure or broken service.
# Prints which setting is wrong, never its value.
set -uo pipefail

# No `set -e`: every problem is reported, not just the first. So a failed cd
# must stop us explicitly.
cd "$(dirname "$0")/.." || exit 1
ENV_FILE="${ENV_FILE:-.env}"
TUNNEL_CONFIG="${TUNNEL_CONFIG:-deploy/cloudflared/config.yml}"
CREDS_DIR="${CREDS_DIR:-/etc/listyro/cloudflared}"

errors=0
fail() { echo "  FAIL  $*"; errors=$((errors + 1)); }
pass() { echo "  ok    $*"; }
warn() { echo "  warn  $*"; }

get() {
  # Last assignment wins, as in compose. Values are never echoed.
  grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'
}

echo "== $ENV_FILE"
if [ ! -f "$ENV_FILE" ]; then
  fail "$ENV_FILE not found (copy deploy/env.production.example)"
  exit 1
fi
perms="$(stat -c %a "$ENV_FILE" 2>/dev/null || echo "?")"
[ "$perms" = "600" ] && pass "permissions 600" || warn "permissions are $perms; run: chmod 600 $ENV_FILE"

[ "$(get APP_ENV)" = "production" ] && pass "APP_ENV=production" || fail "APP_ENV must be production"

for key in POSTGRES_PASSWORD DATABASE_URL ENCRYPTION_KEY ADMIN_TOKEN SECRET_KEY \
           FRONTEND_URL CORS_ORIGINS ETSY_REDIRECT_URI ETSY_CLIENT_ID ETSY_CLIENT_SECRET \
           LLM_API_KEY SUPPORT_EMAIL OPERATOR_NAME OPERATOR_LOCATION GOVERNING_LAW DISPUTE_VENUE; do
  value="$(get "$key")"
  if [ -z "$value" ]; then
    fail "$key is empty"
  elif printf '%s' "$value" | grep -qiE 'change-me|<[A-Z_]+>|example\.com$'; then
    fail "$key still holds a placeholder"
  fi
done

# Values known from development must never reach production.
for key in ADMIN_TOKEN SECRET_KEY POSTGRES_PASSWORD; do
  case "$(get "$key")" in
    e2e-admin-token|change-me|password|postgres) fail "$key is a development value; generate a new one" ;;
  esac
done

pg="$(get POSTGRES_PASSWORD)"
if [ -n "$pg" ] && ! get DATABASE_URL | grep -qF ":$pg@"; then
  fail "DATABASE_URL does not contain POSTGRES_PASSWORD"
fi
[ "${#pg}" -ge 32 ] && pass "POSTGRES_PASSWORD length" || fail "POSTGRES_PASSWORD shorter than 32 characters"
[ "$(get ADMIN_TOKEN | tr -d '\n' | wc -c)" -ge 32 ] || fail "ADMIN_TOKEN shorter than 32 characters"

key="$(get ENCRYPTION_KEY)"
if [ -n "$key" ]; then
  decoded="$(printf '%s' "$key" | tr -- '-_' '+/' | base64 -d 2>/dev/null | wc -c)"
  [ "$decoded" = "32" ] && pass "ENCRYPTION_KEY is a valid Fernet key" \
    || fail "ENCRYPTION_KEY is not 32 url-safe base64 bytes"
fi

[ "$(get SESSION_COOKIE_SECURE)" = "true" ] && pass "SESSION_COOKIE_SECURE=true" \
  || fail "SESSION_COOKIE_SECURE must be true (HTTPS-only session cookie)"
[ "$(get CLIENT_IP_HEADER)" = "cf-connecting-ip" ] && pass "CLIENT_IP_HEADER=cf-connecting-ip" \
  || fail "CLIENT_IP_HEADER must be cf-connecting-ip behind Cloudflare"

case "$(get CORS_ORIGINS)" in
  *"*"*) fail "CORS_ORIGINS must not contain a wildcard" ;;
  https://*) pass "CORS_ORIGINS is https" ;;
  *) fail "CORS_ORIGINS must be https" ;;
esac
for key in FRONTEND_URL ETSY_REDIRECT_URI; do
  case "$(get "$key")" in https://*) ;; *) fail "$key must be https" ;; esac
done
case "$(get ETSY_REDIRECT_URI)" in
  */api/auth/etsy/callback) pass "ETSY_REDIRECT_URI path" ;;
  *) fail "ETSY_REDIRECT_URI must end in /api/auth/etsy/callback" ;;
esac

limit="$(get GLOBAL_DAILY_LIMIT)"
case "${limit:-5000}" in
  5000) pass "GLOBAL_DAILY_LIMIT is Etsy's ceiling (5000)" ;;
  *[!0-9]*) fail "GLOBAL_DAILY_LIMIT must be a number" ;;
  *) if [ "$limit" -gt 5000 ]; then
       fail "GLOBAL_DAILY_LIMIT above Etsy's 5000/day would send requests Etsy refuses"
     else
       warn "GLOBAL_DAILY_LIMIT is $limit, not 5000; the margin belongs in GLOBAL_PAUSE_PERCENT"
     fi ;;
esac

echo "== tunnel"
if grep -q '<TUNNEL_ID>' "$TUNNEL_CONFIG"; then
  fail "$TUNNEL_CONFIG still contains <TUNNEL_ID>"
else
  tunnel_id="$(grep -E '^tunnel:' "$TUNNEL_CONFIG" | awk '{print $2}')"
  pass "tunnel id set"
  creds="$CREDS_DIR/$tunnel_id.json"
  # The directory is private to the tunnel container's user (65532), so only
  # root can look inside it from the host.
  if [ ! -x "$CREDS_DIR" ]; then
    warn "cannot look inside $CREDS_DIR as $(id -un); run with sudo to check the tunnel credentials"
  elif [ ! -f "$creds" ]; then
    fail "missing $creds (cloudflared tunnel create)"
  elif [ "$(stat -c %u "$creds")" != 65532 ] || [ "$(stat -c %u "$CREDS_DIR")" != 65532 ]; then
    fail "the tunnel container (uid 65532) cannot read its credentials: chown -R 65532:65532 $CREDS_DIR"
  else
    pass "credentials file present and readable by the tunnel container"
  fi
fi

echo "== host"
if command -v ufw >/dev/null 2>&1; then
  ufw status 2>/dev/null | grep -q "Status: active" && pass "ufw active" || warn "ufw is not active (deploy/provision.sh)"
fi
if [ -r /etc/ssh/sshd_config.d/60-listyro.conf ]; then pass "ssh hardening in place"; else warn "ssh hardening not found (deploy/provision.sh)"; fi

echo
if [ "$errors" -gt 0 ]; then
  echo "$errors problem(s). Not ready."
  exit 1
fi
echo "Ready."
