#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_ROOT="${APP_ROOT:-/opt/mentorflow/update-static/e108/app}"
STATE_ROOT="${STATE_ROOT:-/opt/mentorflow/state}"
IMAGE="mentorflow-installer:inno7.1.0-py3.12.10-v1"
test -f "$ROOT/scripts/server_installer/trust.json" || { echo 'Missing bundle trust.json; use make_bundle.py' >&2; exit 1; }
test -f "$APP_ROOT/stable/stable.json"
mkdir -p "$STATE_ROOT"
exec 9>"$STATE_ROOT/installer-build.lock"
flock -n 9 || { echo 'Another installer build is running' >&2; exit 1; }
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo 'BUILDING_IMAGE (first run only; official compiler and pinned Python packages)'
  docker build --platform linux/amd64 -t "$IMAGE" -f "$ROOT/scripts/server_installer/Dockerfile" "$ROOT"
fi
echo 'VERIFYING / ASSEMBLING / COMPILING (network disabled)'
docker run --rm --network none --platform linux/amd64 \
  --mount "type=bind,src=$APP_ROOT,dst=/srv/app" \
  --mount "type=bind,src=$STATE_ROOT,dst=/srv/state" \
  "$IMAGE"
