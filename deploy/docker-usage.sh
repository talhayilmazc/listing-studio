#!/usr/bin/env bash
# What Docker itself takes on the disk, the way `docker system df` counts it: images (with the
# containerd image store their layers and snapshots live under /var/lib/containerd, outside
# Docker's root directory, which is why measuring /var/lib/docker said 43 MB while the images
# took gigabytes), the containers' writable layers, the build cache, and the containers' log
# files. Not the volumes: those are the uploads and the database, measured by the app.
#
# Sourced by deploy/disk-check.sh; the parsing is a function of its input so it is tested
# without Docker (backend/tests/test_disk_docker.py).

# "3.31GB" / "512.4MB" / "12kB" / "0B" (docker's decimal units) -> bytes.
docker_size_bytes() {
  awk -v s="$1" 'BEGIN {
    if (match(s, /^[0-9.]+/) == 0) { print 0; exit }
    n = substr(s, 1, RLENGTH); u = toupper(substr(s, RLENGTH + 1))
    m = 1
    if (u == "KB") m = 1e3; else if (u == "MB") m = 1e6; else if (u == "GB") m = 1e9; else if (u == "TB") m = 1e12
    printf "%.0f\n", n * m
  }'
}

# Lines "Type<TAB>Size" (docker system df --format '{{.Type}}\t{{.Size}}') on stdin -> JSON
# {"images":…, "containers":…, "build_cache":…} in bytes (volumes left out).
docker_df_json() {
  local images=0 containers=0 build=0 type size
  while IFS=$'\t' read -r type size; do
    case "$type" in
      Images) images=$(docker_size_bytes "$size") ;;
      Containers) containers=$(docker_size_bytes "$size") ;;
      "Build Cache") build=$(docker_size_bytes "$size") ;;
    esac
  done
  printf '{"images":%s,"containers":%s,"build_cache":%s}' "$images" "$containers" "$build"
}

# The containers' log files, in bytes (json-file logs under each container's own path).
docker_logs_bytes() {
  local total=0 path size
  for path in $(docker ps -aq 2>/dev/null | xargs -r docker inspect --format '{{.LogPath}}' 2>/dev/null); do
    [ -f "$path" ] || continue
    size=$(stat -c %s "$path" 2>/dev/null || echo 0)
    total=$(( total + size ))
  done
  echo "$total"
}

# {"images":…,"containers":…,"build_cache":…} plus the logs -> the same with "logs" added.
docker_parts_json() {
  printf '%s,"logs":%s}' "${1%\}}" "${2:-0}"
}

# Docker's whole use, in bytes: images + containers + build cache + logs (no volumes).
docker_total() {
  local parts="$1" logs="${2:-0}" total=0 n
  for n in $(printf '%s' "$parts" | tr -c '0-9' ' '); do
    total=$(( total + n ))
  done
  echo $(( total + logs ))
}
