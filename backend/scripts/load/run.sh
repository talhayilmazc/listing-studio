#!/usr/bin/env bash
# The 100-account load test, start to finish (docs/load-test.md). From the repository root:
#
#   bash backend/scripts/load/run.sh [--label 2cpu] [--cpuset 0-1] [--accounts 100] -- [driver arguments]
#
# Starts the stack (docker-compose.load.yml), runs the driver, samples `docker stats`, prints
# the report and stops everything. Its own containers and images only; no volume is made or removed.
set -uo pipefail

LABEL=run; CPUSET=0-1; ACCOUNTS=100
while [ $# -gt 0 ]; do
  case "$1" in
    --label) LABEL=$2; shift 2 ;;
    --cpuset) CPUSET=$2; shift 2 ;;
    --accounts) ACCOUNTS=$2; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done

COMPOSE=(docker compose -p listyro-load -f docker-compose.load.yml)
export LOAD_CPUSET=$CPUSET LOAD_ACCOUNTS=$ACCOUNTS
# Generated for this run, so no key is in any file (the Fernet key's format, without Python here).
LOAD_SECRET_KEY=$(head -c 48 /dev/urandom | base64 | tr -d '\n=' | tr '+/' '-_')
LOAD_ENCRYPTION_KEY=$(head -c 32 /dev/urandom | base64 | tr -d '\n' | tr '+/' '-_')
export LOAD_SECRET_KEY LOAD_ENCRYPTION_KEY

mkdir -p load-output
STATS="load-output/docker-stats-$LABEL.csv"
: > "$STATS"

cleanup() {
  [ -n "${SAMPLER:-}" ] && kill "$SAMPLER" 2>/dev/null
  "${COMPOSE[@]}" --profile driver logs --no-color load-app > "load-output/app-$LABEL.log" 2>&1
  "${COMPOSE[@]}" --profile driver down --remove-orphans > /dev/null 2>&1
}
trap cleanup EXIT

"${COMPOSE[@]}" up -d --build --wait load-app || { "${COMPOSE[@]}" logs --no-color --tail 60; exit 1; }

(
  while :; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' 2>/dev/null | grep '^listyro-load' | sed "s/^/$(date +%s),/" >> "$STATS"
    sleep 3
  done
) &
SAMPLER=$!

LOAD_CPUSET_SHOWN=$CPUSET "${COMPOSE[@]}" --profile driver run --rm --no-deps -e LOAD_CPUSET_SHOWN="$CPUSET" load-driver \
  python -m scripts.load.driver --label "$LABEL" "$@"
STATUS=$?

{
  echo
  echo "===== docker stats peaks (container, peak CPU % of one core, peak memory)"
  awk -F, '{
    name=$2; cpu=$3; sub(/%/,"",cpu); split($4,m," / "); v=m[1]; u=1;
    if (v ~ /GiB/) u=1024; else if (v ~ /KiB/) u=1/1024; gsub(/[A-Za-z]/,"",v); mb=v*u;
    if (cpu+0>c[name]) c[name]=cpu+0; if (mb>p[name]) p[name]=mb; s[$1]+=mb
  } END {
    for (n in c) printf "  %-34s %7.0f%% %8.0f MB\n", n, c[n], p[n];
    for (t in s) if (s[t]>top) top=s[t];
    printf "  %-34s %17.0f MB (all containers, at their busiest moment; the VM has 5,800 MB)\n", "together", top
  }' "$STATS"
} | tee -a "load-output/report-$LABEL.txt"

exit $STATUS
