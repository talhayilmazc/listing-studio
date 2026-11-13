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
warnings=0
fail() { echo "  FAIL  $*"; errors=$((errors + 1)); }
pass() { echo "  ok    $*"; }
warn() { echo "  warn  $*"; warnings=$((warnings + 1)); }
# For what must not scroll past unread.
loud() {
  warnings=$((warnings + 1))
  echo "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  for line in "$@"; do echo "  !!  $line"; done
  echo "  !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
}

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

# The development switch that refuses every Etsy call before it is sent
# (backend/app/etsy/guard.py). Left on in production the app looks healthy and
# does nothing on Etsy. Compose's environment outranks .env, so both are read.
etsy_blocked="$(get ETSY_CALLS_BLOCKED | tr '[:upper:]' '[:lower:]')"
if grep -qsiE '^[[:space:]]*-?[[:space:]]*ETSY_CALLS_BLOCKED[:=][[:space:]]*"?(true|1|yes|on|t|y)"?[[:space:]]*$' "${COMPOSE_FILE:-docker-compose.prod.yml}"; then
  etsy_blocked=true
fi
case "$etsy_blocked" in
  true|1|yes|on|t|y)
    if [ "$(get APP_ENV)" = "production" ]; then
      fail "ETSY_CALLS_BLOCKED is on in production: no drafts, publishes or syncs will reach Etsy"
    fi ;;
  *) pass "Etsy calls are not blocked" ;;
esac

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

# The support address is the product's own mailbox, never a person's (the app shows support@listyro.com instead).
if printf '%s' "$(get SUPPORT_EMAIL)" | grep -qiE '@(gmail|googlemail|hotmail|outlook|live|yahoo|icloud|me|aol|yandex|proton|protonmail|gmx|mail)\.'; then
  warn "SUPPORT_EMAIL is a personal mailbox; the app shows support@listyro.com instead. Set SUPPORT_EMAIL=support@listyro.com"
fi

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

# Etsy's ceiling under Commercial Access (approved 19 October 2026): 100,000 a day.
ETSY_DAILY_CEILING=100000
limit="$(get GLOBAL_DAILY_LIMIT)"
case "${limit:-$ETSY_DAILY_CEILING}" in
  "$ETSY_DAILY_CEILING") pass "GLOBAL_DAILY_LIMIT is Etsy's ceiling ($ETSY_DAILY_CEILING)" ;;
  *[!0-9]*) fail "GLOBAL_DAILY_LIMIT must be a number" ;;
  *) if [ "$limit" -gt "$ETSY_DAILY_CEILING" ]; then
       warn "GLOBAL_DAILY_LIMIT is $limit, above Etsy's $ETSY_DAILY_CEILING/day: requests past it would be refused"
     else
       warn "GLOBAL_DAILY_LIMIT is $limit, not $ETSY_DAILY_CEILING; the margin belongs in GLOBAL_PAUSE_PERCENT"
     fi ;;
esac
rps="$(get ETSY_REQUESTS_PER_SECOND)"
case "${rps:-10}" in
  *[!0-9.]*) fail "ETSY_REQUESTS_PER_SECOND must be a number" ;;
  *) awk "BEGIN { exit !(${rps:-10} > 150) }" && warn "ETSY_REQUESTS_PER_SECOND above Etsy's 150/s" || pass "ETSY_REQUESTS_PER_SECOND is ${rps:-10} (Etsy allows 150)" ;;
esac

echo "== models"
# Production ran on Haiku for days after Sonnet was chosen: VISION_MODEL and
# CONTENT_MODEL were unset, and each falls back to LLM_MODEL without a word.
fallback="$(get LLM_MODEL)"
fallback="${fallback:-claude-sonnet-5-5}"   # the app's default when unset
# The models the app knows how to call (backend/app/pipeline/llm.py:
# MODEL_CAPABILITIES; each takes a different thinking setting, and the app has
# been run against exactly these). Another model is called too, and if it
# refuses the thinking setting the app drops the setting and alerts, but it is
# untested here: warn.
for key in VISION_MODEL CONTENT_MODEL; do
  model="$(get "$key")"
  if [ -z "$model" ]; then
    warn "$key is not set; it falls back to LLM_MODEL ($fallback)"
    model="$fallback"
  fi
  case "$model" in
    claude-sonnet-5|claude-sonnet-5-5|claude-haiku-5-5) pass "$key is $model" ;;
    *) warn "$key is $model, which is not one of the supported models (claude-sonnet-5-5, claude-sonnet-5, claude-haiku-5-5)" ;;
  esac
done
thinking="$(get LLM_THINKING)"
if [ "${thinking:-off}" != "off" ]; then
  warn "LLM_THINKING is '$thinking'; set LLM_THINKING=off (thinking costs more and is slower for listings)"
elif [ -z "$thinking" ]; then
  warn "LLM_THINKING is not set; the app's default is off, but say so in $ENV_FILE: LLM_THINKING=off"
else
  pass "LLM_THINKING=off"
fi
case "$(get LLM_EFFORT)" in
  ""|low|medium|high) ;;
  *) warn "LLM_EFFORT is '$(get LLM_EFFORT)'; with thinking off the models take low, medium or high (the app sends high instead)" ;;
esac
# The free AI ideas tool stays closed without both Turnstile keys (docs/free-tools.md).
if [ "$(get FREE_TOOL_AI_ENABLED)" = "true" ]; then
  if [ -n "$(get TURNSTILE_SITE_KEY)" ] && [ -n "$(get TURNSTILE_SECRET_KEY)" ]; then
    pass "FREE_TOOL_AI_ENABLED is on, with both Turnstile keys"
  else
    warn "FREE_TOOL_AI_ENABLED is on but a Turnstile key is missing: the tool stays closed"
  fi
fi

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
# The tunnel's transport (docs/deploy.md, "Tunnel protocol"): HTTP/2 over TCP. QUIC streams
# timed out on the server's uplink, the tunnel dropped and visitors saw "SSL handshake failed".
# What cloudflared uses: its --protocol flag, else TUNNEL_TRANSPORT_PROTOCOL, else `protocol:`
# in its config; nothing set means "auto", which is QUIC.
compose_file="${COMPOSE_FILE:-docker-compose.prod.yml}"
tunnel_block="$(awk '/^  cloudflared:/{on=1; next} on && /^  [^ #]/{on=0} on' "$compose_file" 2>/dev/null)"
tunnel_protocol="$(printf '%s\n' "$tunnel_block" | grep -oE '"--protocol",[[:space:]]*"[a-z0-9]+"' | grep -oE '[a-z0-9]+"$' | tr -d '"')"
[ -n "$tunnel_protocol" ] || tunnel_protocol="$(printf '%s\n' "$tunnel_block" | grep -oE 'TUNNEL_TRANSPORT_PROTOCOL:[[:space:]]*"?[a-z0-9]+' | grep -oE '[a-z0-9]+$')"
[ -n "$tunnel_protocol" ] || tunnel_protocol="$(grep -E '^protocol:' "$TUNNEL_CONFIG" 2>/dev/null | awk '{print $2}' | tr -d '"')"
if [ "$tunnel_protocol" = "http2" ]; then
  pass "tunnel protocol http2"
else
  warn "the tunnel protocol is ${tunnel_protocol:-not set (cloudflared then uses QUIC)}, not http2: add \"--protocol\", \"http2\" to the cloudflared command in $compose_file (docs/deploy.md, \"Tunnel protocol\")"
fi
# And what the running tunnel actually registered with, when it is running.
if command -v docker >/dev/null 2>&1; then
  running_protocol="$(docker compose -f "$compose_file" logs --since 24h cloudflared 2>/dev/null | grep -oE 'protocol=[a-z0-9]+' | tail -1 | cut -d= -f2)"
  if [ -n "$running_protocol" ] && [ "$running_protocol" != "http2" ]; then
    warn "the running tunnel last registered with protocol=$running_protocol: recreate it (docker compose -f $compose_file up -d cloudflared)"
  fi
fi

echo "== disk"
# A full disk is what took the site down: a build failed half-way and Redis was
# cut off mid-write. Warn long before that; a build alone needs several GB.
DISK_WARN_PERCENT="${DISK_WARN_PERCENT:-70}"
DISK_FAIL_PERCENT="${DISK_FAIL_PERCENT:-90}"
DISK_WARN_FREE_GB="${DISK_WARN_FREE_GB:-10}"
DISK_FAIL_FREE_GB="${DISK_FAIL_FREE_GB:-4}"
seen_devices=""
for mount in / /var/lib/docker "${BACKUP_DIR:-/var/backups/listyro}"; do
  [ -d "$mount" ] || continue
  device="$(df --output=source "$mount" | tail -1)"
  case "|$seen_devices|" in *"|$device|"*) continue ;; esac
  seen_devices="$seen_devices|$device"
  used="$(df --output=pcent "$mount" | tail -1 | tr -dc '0-9')"
  free=$(( $(df --output=avail -k "$mount" | tail -1 | tr -dc '0-9') / 1048576 ))
  if [ "$used" -ge "$DISK_FAIL_PERCENT" ] || [ "$free" -lt "$DISK_FAIL_FREE_GB" ]; then
    fail "$mount is ${used}% full, ${free} GB free: too little to build or back up. Free space first (docker builder prune -af; old storage archives)"
  elif [ "$used" -ge "$DISK_WARN_PERCENT" ] || [ "$free" -lt "$DISK_WARN_FREE_GB" ]; then
    warn "$mount is ${used}% full, ${free} GB free: a build needs several GB. Free space before it becomes urgent"
  else
    pass "$mount: ${used}% used, ${free} GB free"
  fi
done
if [ -d "${BACKUP_DIR:-/var/backups/listyro}" ] && [ -r "${BACKUP_DIR:-/var/backups/listyro}" ]; then
  pass "backups use $(du -sh "${BACKUP_DIR:-/var/backups/listyro}" 2>/dev/null | cut -f1)"
fi

echo "== alerts"
# The hourly disk check ran for weeks with nowhere to send its alert.
OPS_ENV="${OPS_ENV:-/etc/listyro/ops.env}"
ops() { grep -E "^$1=" "$OPS_ENV" 2>/dev/null | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'; }
if [ -f "$OPS_ENV" ] && [ ! -r "$OPS_ENV" ]; then
  warn "cannot read $OPS_ENV as $(id -un); run with sudo to check where alerts go"
elif [ -z "$(ops ALERT_WEBHOOK_URL)" ]; then
  loud "NO ALERT DESTINATION IS CONFIGURED." \
       "A full disk, a failed backup or a failed deploy will reach nobody." \
       "Set ALERT_WEBHOOK_URL in $OPS_ENV (an ntfy.sh topic or a chat webhook)," \
       "then test it:  curl -d test \"\$(sudo grep ALERT_WEBHOOK_URL $OPS_ENV | cut -d= -f2-)\""
else
  pass "ALERT_WEBHOOK_URL is set"
fi
# The app sends its own alerts too (the AI provider refusing the account pauses
# all listing writing), and reads the address from its env file, not ops.env.
if [ -z "$(get ALERT_WEBHOOK_URL)" ]; then
  loud "THE APP HAS NO ALERT DESTINATION."        "If the AI provider refuses the account (usage limit, no credit), listing writing"        "pauses and sellers see a banner, but nobody is told. Set ALERT_WEBHOOK_URL in $ENV_FILE"        "(the same address as in $OPS_ENV), then restart: bash deploy/update.sh"
else
  pass "the app's ALERT_WEBHOOK_URL is set"
fi
if [ -r "$OPS_ENV" ] || [ ! -f "$OPS_ENV" ]; then
  [ -n "$(ops HEALTHCHECK_URL)" ] && pass "HEALTHCHECK_URL is set" || warn "HEALTHCHECK_URL is not set: a backup that stops running will not be noticed"
  [ -n "$(ops BACKUP_REMOTE)" ] && pass "BACKUP_REMOTE is set" || warn "BACKUP_REMOTE is not set: the only backups are on this server's own disk"
  # Ubuntu 24.04's apt package is v1.60, and Cloudflare R2 answers it "501 NotImplemented".
  RCLONE_MIN=1.65
  if [ -n "$(ops BACKUP_REMOTE)" ]; then
    rclone_version="$(rclone version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)"
    if [ -z "$rclone_version" ]; then
      warn "BACKUP_REMOTE is set but rclone did not answer: no copy can leave this server (deploy/provision.sh installs it)"
    elif [ "$(printf '%s\n%s\n' "$RCLONE_MIN" "$rclone_version" | sort -V | head -1)" = "$RCLONE_MIN" ]; then
      pass "rclone $rclone_version"
    else
      warn "rclone is $rclone_version, older than $RCLONE_MIN: uploads to Cloudflare R2 fail with \"501 NotImplemented\". Install the current one (docs/ops-checklist.md, section 2)"
    fi
  fi
fi
# Two files name the alert address: the host scripts read ops.env, the app reads .env. Change one
# and the other keeps the old address, silently. Never printed: an address can carry a secret.
if [ -r "$OPS_ENV" ] && [ -n "$(ops ALERT_WEBHOOK_URL)" ] && [ -n "$(get ALERT_WEBHOOK_URL)" ]; then
  if [ "$(ops ALERT_WEBHOOK_URL)" = "$(get ALERT_WEBHOOK_URL)" ]; then
    pass "ALERT_WEBHOOK_URL is the same in $ENV_FILE and $OPS_ENV"
  else
    warn "ALERT_WEBHOOK_URL differs between $ENV_FILE and $OPS_ENV: the app and the host scripts (watch, backup, disk check) alert different addresses. Change both together (docs/ops-checklist.md, section 11)"
  fi
fi

echo "== host"
overcommit="$(cat /proc/sys/vm/overcommit_memory 2>/dev/null || echo "?")"
if [ "$overcommit" = "1" ]; then
  pass "vm.overcommit_memory = 1"
else
  warn "vm.overcommit_memory is $overcommit; Redis needs 1 to save in the background (deploy/provision.sh sets it)"
fi
if grep -qs "docker builder prune" /etc/cron.d/listyro; then
  pass "weekly build-cache prune is scheduled"
else
  warn "no weekly 'docker builder prune' in /etc/cron.d/listyro: build cache will fill the disk (deploy/provision.sh adds it)"
fi
if grep -qs "deploy/watch.sh" /etc/cron.d/listyro; then
  pass "the container watch is scheduled"
else
  warn "no deploy/watch.sh in /etc/cron.d/listyro: memory, restarts, OOM kills and a dead API are not seen (deploy/provision.sh)"
fi
if command -v ufw >/dev/null 2>&1; then
  ufw status 2>/dev/null | grep -q "Status: active" && pass "ufw active" || warn "ufw is not active (deploy/provision.sh)"
fi
# What sshd really does, not whether our file exists: sshd takes the FIRST value it reads and reads
# sshd_config.d in name order, so on Ubuntu's cloud images 50-cloud-init.conf ("PasswordAuthentication
# yes") beat our 60-listyro.conf and passwords were still accepted while this line said all was well.
SSHD_BIN="${SSHD_BIN:-$(command -v sshd 2>/dev/null || echo /usr/sbin/sshd)}"
SSHD_CONFIG="${SSHD_CONFIG:-/etc/ssh/sshd_config}"
SSHD_CONFIG_DIR="${SSHD_CONFIG_DIR:-/etc/ssh/sshd_config.d}"
# The first file sshd reads that sets this keyword: the one that wins.
ssh_setter() {
  local file
  for file in "$SSHD_CONFIG_DIR"/*.conf "$SSHD_CONFIG"; do
    [ -r "$file" ] || continue
    if grep -qiE "^[[:space:]]*$1[[:space:]]" "$file"; then
      echo "$file"
      return 0
    fi
  done
}
if [ ! -x "$SSHD_BIN" ]; then
  warn "sshd not found, so the ssh settings could not be checked (deploy/provision.sh)"
else
  # sshd -T needs the host keys, so only root can run it.
  ssh_effective="$("$SSHD_BIN" -T 2>/dev/null)"
  if [ -z "$ssh_effective" ] && command -v sudo >/dev/null 2>&1; then
    ssh_effective="$(sudo -n "$SSHD_BIN" -T 2>/dev/null)"
  fi
  if [ -z "$ssh_effective" ]; then
    warn "cannot run sshd -T as $(id -un), so the ssh settings could not be checked; run: sudo bash deploy/preflight.sh"
  else
    ssh_wrong=0
    for keyword in passwordauthentication kbdinteractiveauthentication permitrootlogin; do
      value="$(printf '%s\n' "$ssh_effective" | grep -iE "^$keyword " | head -1 | cut -d' ' -f2)"
      [ "$value" = "no" ] && continue
      ssh_wrong=$((ssh_wrong + 1))
      setter="$(ssh_setter "$keyword")"
      if [ -n "$setter" ]; then
        warn "ssh: $keyword is ${value:-not reported}, not no: $setter sets it, and sshd takes the first value it reads. Set it to no there, then: sudo systemctl reload ssh"
      else
        warn "ssh: $keyword is ${value:-not reported}, not no: no file sets it, so that is sshd's default. Add it to $SSHD_CONFIG_DIR/60-listyro.conf (deploy/provision.sh)"
      fi
    done
    [ "$ssh_wrong" = 0 ] && pass "ssh: no passwords, no keyboard-interactive, no root login (sshd -T)"
  fi
fi

echo
if [ "$errors" -gt 0 ]; then
  echo "$errors problem(s). Not ready."
  exit 1
fi
if [ "$warnings" -gt 0 ]; then
  echo "Ready, with $warnings warning(s) above."
else
  echo "Ready."
fi
