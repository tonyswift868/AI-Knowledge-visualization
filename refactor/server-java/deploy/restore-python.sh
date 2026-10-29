#!/bin/sh
# Start the existing Python service and wait for real health before cutover.
# --prepare-only leaves the current upstream serving traffic for probe checks.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
prepare_only=0
case "$#:${1:-}" in
  0:) ;;
  1:--prepare-only) prepare_only=1 ;;
  *) echo "usage: $0 [--prepare-only]" >&2; exit 2 ;;
esac
docker_bin=${DOCKER_BIN:-docker}
python_container=${PYTHON_CONTAINER:-mentorflow-e1-preview-backend-1}
oidc_container=${PYTHON_OIDC_CONTAINER:-mentorflow-e1-preview-oidc-loopback-proxy-1}
health_attempts=${HEALTH_ATTEMPTS:-45}
health_interval=${HEALTH_INTERVAL_SECONDS:-2}
case "$health_attempts" in ''|*[!0-9]*) echo "HEALTH_ATTEMPTS must be a positive integer" >&2; exit 2 ;; esac
case "$health_interval" in ''|*[!0-9]*) echo "HEALTH_INTERVAL_SECONDS must be a non-negative integer" >&2; exit 2 ;; esac
[ "$health_attempts" -gt 0 ] || { echo "HEALTH_ATTEMPTS must be greater than zero" >&2; exit 2; }

# Compose dry-run can inspect an old stopped backend while simulating its
# healthy dependency. Only the real start + inspect below establishes readiness.
cleanup_failed_preparation() {
  # Never stop Python if it is currently serving the public upstream.
  current=$(sh "$script_dir/cutover.sh" status 2>/dev/null || true)
  if [ "$current" = java ]; then
    sh "$script_dir/python-compose.sh" stop || echo "could not stop failed Python preparation" >&2
  fi
}
if ! sh "$script_dir/python-compose.sh" start; then
  cleanup_failed_preparation
  exit 1
fi

attempt=0
while [ "$attempt" -lt "$health_attempts" ]; do
  state=$("$docker_bin" inspect --format '{{.State.Health.Status}}' "$python_container" 2>/dev/null || true)
  [ "$state" = healthy ] && break
  attempt=$((attempt + 1))
  sleep "$health_interval"
done
if [ "$state" != healthy ]; then
  echo "Python backend did not become healthy; edge remains on its current upstream" >&2
  cleanup_failed_preparation
  exit 1
fi

oidc_state=$("$docker_bin" inspect --format '{{.State.Status}}' "$oidc_container" 2>/dev/null || true)
if [ "$oidc_state" != running ]; then
  echo "Python OIDC loopback proxy is not running; edge remains on its current upstream" >&2
  cleanup_failed_preparation
  exit 1
fi

if [ "$prepare_only" -eq 1 ]; then
  echo "Python backend prepared and healthy; edge upstream unchanged"
  exit 0
fi
sh "$script_dir/cutover.sh" python
echo "Python backend restored and edge switched back"
