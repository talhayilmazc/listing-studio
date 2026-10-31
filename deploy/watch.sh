#!/usr/bin/env bash
# What the app's containers cannot see about themselves, every minute (cron): each container's memory
# against its limit, its restarts, and whether it was OOM-killed. Left in Redis under "ops:containers"
# for backend/app/monitor/rules.py, which alerts and keeps the day's peaks.
#
# If the API is not healthy the app cannot say anything at all, so this says it, straight to
# ALERT_WEBHOOK_URL: once when it has lasted 3 minutes, again each hour, and once when it is back.
# Numbers only.
set -uo pipefail
cd "$(dirname "$0")/.."

OPS_ENV="${OPS_ENV:-/etc/listyro/ops.env}"
# shellcheck disable=SC1090
[ -r "$OPS_ENV" ] && . "$OPS_ENV"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
STATE_DIR="${WATCH_STATE_DIR:-/var/lib/listyro}"
PROJECT="${COMPOSE_PROJECT:-listyro}"

# "1.2GiB" / "512.4MiB" / "12kB" / "3B" -> MB (docker stats' units).
watch_mb() {
  awk -v s="$1" 'BEGIN {
    if (match(s, /^[0-9.]+/) == 0) { print 0; exit }
    n = substr(s, 1, RLENGTH); u = toupper(substr(s, RLENGTH + 1))
    m = 1 / 1048576
    if (u == "KIB") m = 1 / 1024; else if (u == "MIB") m = 1; else if (u == "GIB") m = 1024
    else if (u == "KB") m = 1e3 / 1048576; else if (u == "MB") m = 1e6 / 1048576; else if (u == "GB") m = 1e9 / 1048576
    printf "%.0f\n", n * m
  }'
}

# Lines "name<TAB>id<TAB>used / limit<TAB>restarts<TAB>oom" on stdin -> the JSON left in Redis.
watch_json() {
  local at="$1" name id usage restarts oom used limit sep=""
  printf '{"at":%s,"containers":[' "$at"
  while IFS=$'\t' read -r name id usage restarts oom; do
    [ -n "$name" ] || continue
    used="$(watch_mb "${usage%% /*}")"
    limit="$(watch_mb "${usage##*/ }")"
    name="${name#"$PROJECT"-}"
    name="${name%-[0-9]*}"
    printf '%s{"name":"%s","id":"%s","mem_mb":%s,"limit_mb":%s,"restarts":%s,"oom":%s}' \
      "$sep" "$name" "$id" "$used" "$limit" "${restarts:-0}" "${oom:-false}"
    sep=","
  done
  printf ']}\n'
}

# How long the API has been unhealthy, from the state file; what to say, if anything.
# watch_down <healthy 0/1> <now> <state file>: prints a line to send, or nothing.
watch_down() {
  local healthy="$1" now="$2" state="$3" since said
  if [ "$healthy" = 1 ]; then
    if [ -s "$state" ]; then
      read -r since said < "$state"
      rm -f "$state"
      [ "${said:-0}" -gt 0 ] && echo "[Listyro] Resolved: the API is healthy again."
    fi
    return 0
  fi
  if [ ! -s "$state" ]; then
    echo "$now 0" > "$state"
    return 0
  fi
  read -r since said < "$state"
  if [ $(( now - since )) -ge 180 ] && [ $(( now - ${said:-0} )) -ge 3600 ]; then
    echo "$since $now" > "$state"
    echo "[Listyro] API not healthy for $(( (now - since) / 60 )) minutes: the site is probably down, and the app cannot send its own alerts."
  fi
}

watch_send() {
  [ -n "$1" ] || return 0
  [ -n "$ALERT_WEBHOOK_URL" ] || { echo "ALERT (no ALERT_WEBHOOK_URL): $1"; return 0; }
  curl -fsS -m 10 -d "$1" "$ALERT_WEBHOOK_URL" >/dev/null || echo "alert could not be delivered: $1"
}

watch_sample() {
  local name id usage restarts oom
  docker stats --no-stream --format '{{.Name}}	{{.ID}}	{{.MemUsage}}' | while IFS=$'\t' read -r name id usage; do
    restarts="$(docker inspect -f '{{.RestartCount}}' "$name" 2>/dev/null || echo 0)"
    oom="$(docker inspect -f '{{.State.OOMKilled}}' "$name" 2>/dev/null || echo false)"
    printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$id" "$usage" "$restarts" "$oom"
  done
}

# Sourced by tests for the functions above; run for real otherwise.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  mkdir -p "$STATE_DIR"
  now="$(date +%s)"
  health="$(docker inspect -f '{{.State.Health.Status}}' "$PROJECT-api-1" 2>/dev/null || echo missing)"
  healthy=0
  [ "$health" = "healthy" ] && healthy=1
  # Starting after a deploy is not down; update.sh watches that itself.
  [ "$health" = "starting" ] && healthy=1
  watch_send "$(watch_down "$healthy" "$now" "$STATE_DIR/api-down")"
  watch_sample | watch_json "$now" | docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli -x SETEX ops:containers 900 >/dev/null \
    || echo "container numbers could not be written"
fi
