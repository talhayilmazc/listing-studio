#!/usr/bin/env bash
# Storage growth check, sourced by disk-check.sh (and its test).
#
#   storage_growth <bytes now> <state file> [<epoch now>]
#
# Appends "<epoch> <bytes>" to the state file (three days kept) and compares with
# the newest reading at least 23 hours old. Prints an alert line when storage grew
# more than STORAGE_GROWTH_ALERT_GB (default 1) since then, at most once a calendar
# day (a marker beside the state file), and nothing otherwise. Sizes only.
storage_growth() {
  local bytes="$1" state="$2" now="${3:-$(date +%s)}"
  local limit_gb="${STORAGE_GROWTH_ALERT_GB:-1}" old grew day marker
  mkdir -p "$(dirname "$state")"
  touch "$state"
  echo "$now $bytes" >> "$state"
  awk -v cutoff=$(( now - 3 * 86400 )) '$1 >= cutoff' "$state" > "$state.tmp" && mv "$state.tmp" "$state"
  old="$(awk -v t=$(( now - 23 * 3600 )) '$1 <= t { last = $2 } END { print last }' "$state")"
  [ -n "$old" ] || return 0
  grew=$(( bytes - old ))
  # Compare in bytes: limit_gb may be fractional.
  if awk -v g="$grew" -v l="$limit_gb" 'BEGIN { exit !(g > l * 1073741824) }'; then
    day="$(date -u -d "@$now" +%F 2>/dev/null || date -u +%F)"
    marker="$state.alerted"
    [ "$(cat "$marker" 2>/dev/null)" = "$day" ] && return 0
    echo "$day" > "$marker"
    awk -v g="$grew" -v n="$bytes" -v l="$limit_gb" 'BEGIN {
      printf "storage grew %.1f GB in the last day (now %.1f GB; alert above %s GB a day). ", g / 1073741824, n / 1073741824, l }'
  fi
}
