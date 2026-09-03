#!/bin/sh
set -eu

read_secret() {
  test -r "$1" || { echo "required PostgreSQL secret is unreadable" >&2; exit 20; }
  value="$(cat "$1")"
  test -n "$value" || { echo "required PostgreSQL secret is empty" >&2; exit 21; }
  printf '%s' "$value"
}

export RP05_MIGRATOR_PASSWORD="$(read_secret /run/secrets/postgres_migrator_password)"
export RP05_APP_PASSWORD="$(read_secret /run/secrets/postgres_app_password)"
export RP05_KEYCLOAK_PASSWORD="$(read_secret /run/secrets/keycloak_db_password)"

psql --no-password --set=ON_ERROR_STOP=1 --host "${PGHOST:?PGHOST is required}" --port "${PGPORT:?PGPORT is required}" --username postgres --dbname postgres <<'SQL'
\getenv migrator_password RP05_MIGRATOR_PASSWORD
\getenv app_password RP05_APP_PASSWORD
\getenv keycloak_password RP05_KEYCLOAK_PASSWORD

SELECT format('CREATE ROLE mentorflow_migrator LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS', :'migrator_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mentorflow_migrator') \gexec
SELECT format('ALTER ROLE mentorflow_migrator PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS', :'migrator_password') \gexec

SELECT format('CREATE ROLE mentorflow_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mentorflow_app') \gexec
SELECT format('ALTER ROLE mentorflow_app PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS', :'app_password') \gexec

SELECT format('CREATE ROLE mentorflow_keycloak LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS', :'keycloak_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mentorflow_keycloak') \gexec
SELECT format('ALTER ROLE mentorflow_keycloak PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS', :'keycloak_password') \gexec

SELECT 'CREATE DATABASE mentorflow OWNER mentorflow_migrator'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'mentorflow') \gexec
SELECT 'CREATE DATABASE mentorflow_keycloak OWNER mentorflow_keycloak'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'mentorflow_keycloak') \gexec

ALTER DATABASE mentorflow OWNER TO mentorflow_migrator;
ALTER DATABASE mentorflow_keycloak OWNER TO mentorflow_keycloak;
SQL

psql --no-password --set=ON_ERROR_STOP=1 --host "${PGHOST:?PGHOST is required}" --port "${PGPORT:?PGPORT is required}" --username postgres --dbname mentorflow <<'SQL'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE mentorflow TO mentorflow_app;
SQL

unset RP05_MIGRATOR_PASSWORD RP05_APP_PASSWORD RP05_KEYCLOAK_PASSWORD
echo '{"schema_version":"mentorflow.rp05-postgres-provision.v1","status":"PASS"}'
