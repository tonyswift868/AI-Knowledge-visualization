#!/bin/sh
# Build a maintenance image from the existing backend image, then run Alembic.
# Secrets/source mounts are read-only. For a no-build, read-only check use preflight.sh.
set -eu

usage() {
  echo "usage: $0 [current|upgrade|status]" >&2
  exit 2
}
action=${1:-upgrade}
[ "$#" -le 1 ] || usage
case "$action" in current|upgrade|status) ;; *) usage ;; esac

deploy_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$deploy_dir/../../.." && pwd)
server_root=$repo_root/refactor/server
docker_bin=${DOCKER_BIN:-docker}
image=${MIGRATION_IMAGE:-mentorflow-slim-backend:269-python-fallback}
tool_image=${MIGRATION_TOOL_IMAGE:-mentorflow-migration-tool:MF-275}
network=${MIGRATION_NETWORK:-mentorflow-e1-preview_internal}
secrets_volume=${MIGRATION_SECRETS_VOLUME:-mentorflow-e1-preview_migrate_secrets}
secret_path=${MF_MIGRATION_PASSWORD_FILE:-/run/secrets/postgres_migrator_password}

case "$image$tool_image$network$secrets_volume" in *[!A-Za-z0-9_./:-]*)
  echo "invalid image, network or secrets volume" >&2
  exit 2
  ;;
esac
[ -f "$server_root/alembic.ini" ] && [ -d "$server_root/migrations/versions" ] || {
  echo "server migration source is incomplete: $server_root" >&2
  exit 1
}

run_migration() {
  migration_action=$1
  shift
  "$docker_bin" run --rm --network "$network" \
    --mount "type=bind,src=$server_root,dst=/app/refactor/server,readonly" \
    --mount "type=volume,src=$secrets_volume,dst=/run/secrets,readonly" \
    --workdir /app \
    --entrypoint /bin/sh "$tool_image" -ec '
      secret=$1
      action=$2
      shift 2
      [ -r "$secret" ] || { echo "missing migration secret: $secret" >&2; exit 1; }
      password=$(tr -d "\r\n" < "$secret")
      [ -n "$password" ] || { echo "migration secret is empty" >&2; exit 1; }
      encoded=$(printf %s "$password" | od -An -v -tu1 | awk '\''{ for (i=1; i<=NF; i++) { n=$i; c=sprintf("%c", n); if (c ~ /[A-Za-z0-9._~-]/) printf "%s", c; else printf "%%%02X", n } }'\'')
      export MENTORFLOW_MIGRATION_DATABASE_URL="postgresql+psycopg://mentorflow_migrator:${encoded}@postgres:5432/mentorflow?connect_timeout=2"
      python -m alembic -c /app/refactor/server/alembic.ini "$action" "$@"
    ' sh "$secret_path" "$migration_action" "$@"
}

build_tool() {
  "$docker_bin" build --build-arg "MF_MIGRATION_BASE=$image" \
    --tag "$tool_image" --file "$deploy_dir/migration.Dockerfile" "$deploy_dir"
}

case "$action" in
  current|status)
    build_tool
    run_migration current
    ;;
  upgrade)
    build_tool
    echo "Alembic revision before upgrade:"
    run_migration current
    echo "Applying migrations to head:"
    run_migration upgrade head
    echo "Alembic revision after upgrade:"
    run_migration current
    ;;
esac
