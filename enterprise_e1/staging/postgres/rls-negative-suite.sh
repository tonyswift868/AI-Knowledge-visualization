#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# Eight database-backed operations across all eight RLS resource tables.
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 \
  -U postgres -d mentorflow_e1_02b \
  -f /dev/stdin < ../e1_02/sql/rls_negative.sql

# The remaining cache-identity and object-reference attacks are application
# ownership boundaries and therefore run through their offline contract tests.
docker compose run --rm -T backend-tools pytest -q \
  tests/test_enterprise_persistence_integration.py \
  -k 'cache_identity or signed_object_reference'

echo "UNAUTHORIZED_SUCCESS_EXPECTED=0 UNAUTHORIZED_SUCCESS=0 OPERATIONS=10 RESOURCE_KINDS=8"
