#!/usr/bin/env bash
# Offline runtime smoke test: no Panoptes credentials or ERAS requests.
set -euo pipefail

image="${1:-zooniverse-platform-dashboard:smoke}"
container="zooniverse-dashboard-smoke-$$"
volume="${container}-data"

cleanup() {
  result=$?
  if (( result != 0 )); then
    echo 'Docker smoke test failed; container logs:' >&2
    docker logs "$container" >&2 || true
  fi
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# An empty published host port is dynamically assigned and may change on restart.
# Query Docker again before each attempt rather than reusing an obsolete URL.
resolve_base_url() {
  local port
  port="$(docker port "$container" 8050/tcp | awk -F: 'NR == 1 {print $NF}')"
  if [[ ! "$port" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  base_url="http://127.0.0.1:$port"
}

wait_for_health() {
  local phase="$1"
  local attempt
  for (( attempt=1; attempt<=30; attempt++ )); do
    if resolve_base_url && \
       curl --fail --silent --show-error --connect-timeout 2 --max-time 3 \
       --output /dev/null "$base_url/healthz" 2>/dev/null; then
      return 0
    fi
    if [[ "$(docker inspect --format '{{.State.Running}}' "$container")" != true ]]; then
      break
    fi
    sleep 2
  done
  echo "Dashboard did not become healthy during $phase (last URL: ${base_url:-unavailable}/healthz)" >&2
  if [[ -n "${base_url:-}" ]]; then
    curl --silent --show-error --connect-timeout 2 --max-time 3 \
      --include "$base_url/healthz" >&2 || true
  fi
  return 1
}

docker image inspect "$image" >/dev/null
docker volume create "$volume" >/dev/null
docker run --detach --name "$container" \
  --publish '127.0.0.1::8050' \
  --mount "type=volume,source=${volume},target=/data" \
  "$image" >/dev/null

base_url=""
wait_for_health "initial startup"

for path in / /users /classifications /talks /anomalies /_dash-layout /assets/styles.css; do
  curl --fail --silent --show-error --location --output /dev/null "$base_url$path"
done

docker exec "$container" python -c \
  'import os; assert os.geteuid() == 10001; assert os.getenv("ANALYTICS_DATA_DIR") == "/data"'
docker exec "$container" set-date-limit 7 >/dev/null
docker restart "$container" >/dev/null
wait_for_health "container restart"
docker exec "$container" set-date-limit | grep -q 'Current history limit: 7 (days)'

echo "Docker smoke test passed: routes, non-root user, writable persistent volume, restart ($base_url)"
