#!/bin/sh
# One-command rollback: start the existing Python service, wait for health,
# then switch the edge upstream back. No image or container is removed.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
docker_bin=${DOCKER_BIN:-docker}
python_container=${PYTHON_CONTAINER:-mentorflow-e1-preview-backend-1}
oidc_container=${PYTHON_OIDC_CONTAINER:-mentorflow-e1-preview-oidc-loopback-proxy-1}
health_attempts=${HEALTH_ATTEMPTS:-45}
health_interval=${HEALTH_INTERVAL_SECONDS:-2}
case "$health_attempts" in ''|*[!0-9]*) echo "HEALTH_ATTEMPTS must be a positive integer" >&2; exit 2 ;; esac
case "$health_interval" in ''|*[!0-9]*) echo "HEALTH_INTERVAL_SECONDS must be a non-negative integer" >&2; exit 2 ;; esac
[ "$health_attempts" -gt 0 ] || { echo "HEALTH_ATTEMPTS must be greater than zero" >&2; exit 2; }

sh "$script_dir/python-compose.sh" start

attempt=0
while [ "$attempt" -lt "$health_attempts" ]; do
  state=$("$docker_bin" inspect --format '{{.State.Health.Status}}' "$python_container" 2>/dev/null || true)
  [ "$state" = healthy ] && break
  attempt=$((attempt + 1))
  sleep "$health_interval"
done
if [ "$state" != healthy ]; then
  echo "Python backend did not become healthy; edge remains on its current upstream" >&2
  exit 1
fi

oidc_state=$("$docker_bin" inspect --format '{{.State.Status}}' "$oidc_container" 2>/dev/null || true)
if [ "$oidc_state" != running ]; then
  echo "Python OIDC loopback proxy is not running; edge remains on its current upstream" >&2
  exit 1
fi

sh "$script_dir/cutover.sh" python
echo "Python backend restored and edge switched back"
