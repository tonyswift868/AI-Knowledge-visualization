#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ -z "${MENTORFLOW_BACKEND_IMAGE:-}" ]]; then
  echo "MENTORFLOW_BACKEND_IMAGE is required; do not add a PostgreSQL driver without its lease." >&2
  exit 2
fi

docker compose up -d --wait postgres
for database in mentorflow_e1_02b mentorflow_e1_02b_fresh mentorflow_e1_02b_incremental; do
  if ! docker compose exec -T postgres psql -U postgres -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '${database}'" | grep -q 1; then
    docker compose exec -T postgres createdb -U postgres "${database}"
  fi
done

echo "BOOTSTRAP=PASS POSTGRES=16 DATABASES=3"
