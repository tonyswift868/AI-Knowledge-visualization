#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
base_url='postgresql+psycopg://postgres:mentorflow-e1-02b-staging-only@postgres:5432'

run_alembic() {
  local database="$1"
  shift
  docker compose run --rm -T \
    -e "DATABASE_URL=${base_url}/${database}" \
    backend-tools alembic "$@"
}

assert_revision() {
  local database="$1"
  local expected="$2"
  local actual
  actual="$(docker compose exec -T postgres psql -U postgres -d "${database}" -tAc \
    'SELECT version_num FROM alembic_version')"
  [[ "${actual//[[:space:]]/}" == "${expected}" ]]
}

# Fresh path.
run_alembic mentorflow_e1_02b_fresh upgrade head
assert_revision mentorflow_e1_02b_fresh 0031_enterprise_tenancy_completion

# Incremental path with a pre-existing organization row.
run_alembic mentorflow_e1_02b_incremental upgrade 0030_enterprise_jobs_v1
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres \
  -d mentorflow_e1_02b_incremental <<'SQL'
INSERT INTO organizations(id, slug, name, status)
VALUES ('preserved-org', 'preserved-org', 'Preserved Organization', 'active');
SQL
run_alembic mentorflow_e1_02b_incremental upgrade head
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres \
  -d mentorflow_e1_02b_incremental -tAc \
  "SELECT 1 FROM organizations WHERE id = 'preserved-org'" | grep -q 1

# Required downgrade boundary, followed by a replay to the canonical head.
run_alembic mentorflow_e1_02b_incremental downgrade 0030_enterprise_jobs_v1
assert_revision mentorflow_e1_02b_incremental 0030_enterprise_jobs_v1
run_alembic mentorflow_e1_02b_incremental upgrade head
assert_revision mentorflow_e1_02b_incremental 0031_enterprise_tenancy_completion

run_alembic mentorflow_e1_02b upgrade head
assert_revision mentorflow_e1_02b 0031_enterprise_tenancy_completion
echo "MIGRATION_GATE=PASS FRESH=PASS INCREMENTAL=PASS DOWNGRADE_0030=PASS EXISTING_ROW_PRESERVED=PASS"
