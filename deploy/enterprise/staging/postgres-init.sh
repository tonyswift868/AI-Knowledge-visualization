#!/usr/bin/env bash
set -Eeuo pipefail

# The application database is created by POSTGRES_DB. Keep the identity store
# separate so application migrations and backups have explicit blast radii.
psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
SELECT 'CREATE DATABASE mentorflow_keycloak'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mentorflow_keycloak')\gexec
SQL
