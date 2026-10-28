#!/bin/sh
# Start only the Java services over the running Compose stack. Resolve the
# complete source stack from the backend labels so later overlay files remain
# in the merge, then add Java files and BUG-45 last.
set -eu

action=${1:-}
case "$action" in
  config|up) ;;
  *) echo "usage: BACKEND_ENV=/path/to/env $0 config|up" >&2; exit 2 ;;
esac
backend_env=${BACKEND_ENV:-}
[ -n "$backend_env" ] && [ -f "$backend_env" ] || {
  echo "set BACKEND_ENV to the existing Compose --env-file before starting Java" >&2
  exit 1
}

deploy_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -f "$deploy_dir/.env" ] && [ -f "$deploy_dir/compose.generated.yml" ] || {
  echo "run prepare-config.sh first; deploy .env or compose.generated.yml is missing" >&2
  exit 1
}
docker_bin=${DOCKER_BIN:-docker}
python_container=${PYTHON_CONTAINER:-mentorflow-e1-preview-backend-1}
network_override=${BUG45_OVERRIDE:-/opt/mentorflow/state/bug45-20261010/network45.override.yml}
project=$("$docker_bin" inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$python_container")
config_files=$("$docker_bin" inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$python_container")
working_dir=$("$docker_bin" inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$python_container")
[ -n "$project" ] && [ "$project" != '<no value>' ] &&
  [ -n "$config_files" ] && [ "$config_files" != '<no value>' ] &&
  [ -n "$working_dir" ] && [ "$working_dir" != '<no value>' ] || {
  echo "cannot read Compose project labels from $python_container" >&2
  exit 1
}

set -- --env-file "$backend_env" --env-file "$deploy_dir/.env" \
  -p "$project" --project-directory "$working_dir"
if [ "${COMPOSE_DRY_RUN:-0}" = 1 ]; then set -- --dry-run "$@"; fi
profiles=${JAVA_COMPOSE_PROFILES:-${PYTHON_COMPOSE_PROFILES:-}}
if [ -n "$profiles" ]; then
  old_ifs=$IFS
  IFS=,
  set -f
  for profile in $profiles; do set -- "$@" --profile "$profile"; done
  set +f
  IFS=$old_ifs
fi
old_ifs=$IFS
IFS=,
set -f
for file in $config_files; do
  case "$file" in *network45.override.yml) continue ;; esac
  set -- "$@" -f "$file"
done
set +f
IFS=$old_ifs
set -- "$@" -f "$deploy_dir/compose.yml" -f "$deploy_dir/compose.generated.yml" \
  -f "$network_override"
if [ "$action" = config ]; then
  exec "$docker_bin" compose "$@" config --quiet
fi
exec "$docker_bin" compose "$@" up -d --no-deps java-backend java-oidc
