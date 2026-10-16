#!/bin/sh
set -eu

# Match the Python backend adapter: read the mounted password only when no
# explicit application database URL was supplied, then percent-encode it.
if [ -z "${MENTORFLOW_DATABASE_URL:-}" ]; then
  secret=${MENTORFLOW_DATABASE_PASSWORD_FILE:-/run/secrets/postgres_app_password}
  [ -r "$secret" ] || { echo "missing database password secret: $secret" >&2; exit 1; }
  password=$(tr -d '\r\n' < "$secret")
  [ -n "$password" ] || { echo "database password secret is empty" >&2; exit 1; }
  encoded=$(printf %s "$password" | od -An -v -tu1 | awk '{ for (i=1; i<=NF; i++) { n=$i; c=sprintf("%c", n); if (c ~ /[A-Za-z0-9._~-]/) printf "%s", c; else printf "%%%02X", n } }')
  MENTORFLOW_DATABASE_URL="postgresql+psycopg://mentorflow_app:${encoded}@postgres:5432/mentorflow?connect_timeout=2"
  export MENTORFLOW_DATABASE_URL
fi

unset MENTORFLOW_MIGRATION_DATABASE_URL
export MENTORFLOW_DEPLOYMENT_MODE=${MENTORFLOW_DEPLOYMENT_MODE:-ENTERPRISE_SERVER}
export MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS=${MENTORFLOW_IDENTITY_SESSION_TTL_SECONDS:-0}
export MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS=${MENTORFLOW_NATIVE_AUTH_SINGLE_PROCESS:-1}
exec java -jar /app/mentorflow-server.jar
