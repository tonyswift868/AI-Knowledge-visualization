#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
docker compose run --rm -T backend-tools python - <<'PY'
import json
import os

from sqlalchemy import create_engine

from app.enterprise_tenancy.synthetic import seed_synthetic_tenants

engine = create_engine(os.environ["DATABASE_URL"], future=True)
with engine.begin() as connection:
    seed = seed_synthetic_tenants(connection)
print(json.dumps({
    "gate": "SYNTHETIC_TENANT_SEED",
    "organizations": seed.organization_count,
    "workspaces": seed.workspace_count,
    "users": seed.user_count,
    "resources": seed.resource_count,
    "status": "PASS",
}, sort_keys=True))
PY
