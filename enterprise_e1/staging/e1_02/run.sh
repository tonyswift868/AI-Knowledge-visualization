#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ -z "${MENTORFLOW_BACKEND_IMAGE:-}" ]]; then
  echo "MENTORFLOW_BACKEND_IMAGE is required and must include the leased PostgreSQL driver." >&2
  exit 2
fi

cleanup() {
  if [[ "${E1_KEEP_STAGING:-0}" != "1" ]]; then
    docker compose down --volumes --remove-orphans
  fi
}
trap cleanup EXIT

docker compose up --abort-on-container-exit --exit-code-from pool-gate \
  postgres migrate rls-gate pool-gate
