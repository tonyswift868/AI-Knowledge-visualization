#!/bin/sh
# Shared command builder for the existing Python Compose stack. Keep the
# BUG-45 overlay as the final -f argument on every Compose operation.
set -eu

action=${1:-}
case "$action" in
  start|stop) ;;
  *) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
network_override=${BUG45_OVERRIDE:-/opt/mentorflow/state/bug45-20261010/network45.override.yml}
docker_bin=${DOCKER_BIN:-docker}
python_container=${PYTHON_CONTAINER:-mentorflow-e1-preview-backend-1}
project=$("$docker_bin" inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$python_container")
config_files=$("$docker_bin" inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$python_container")
working_dir=$("$docker_bin" inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$python_container")
[ -n "$project" ] && [ "$project" != '<no value>' ] &&
  [ -n "$config_files" ] && [ "$config_files" != '<no value>' ] &&
  [ -n "$working_dir" ] && [ "$working_dir" != '<no value>' ] || {
  echo "cannot read Compose project labels from $python_container" >&2
  exit 1
}

# Compose stores config_files as a comma-separated label. Keep every existing
# layer (including later session/MF overlays), remove duplicate BUG-45 entries,
# then append that override last as required by its IPAM declarations.
old_ifs=$IFS
IFS=,
set -f
set -- $config_files
set +f
IFS=$old_ifs
compose_files=
for file do
  case "$file" in *network45.override.yml) continue ;; esac
  compose_files=${compose_files}${compose_files:+,}$file
done
[ -n "$compose_files" ] || { echo "Compose labels contain no base config files" >&2; exit 1; }

set -- -p "$project" --project-directory "$working_dir"
profiles=${PYTHON_COMPOSE_PROFILES:-}
if [ -n "$profiles" ]; then
  old_ifs=$IFS
  IFS=,
  set -f
  for profile in $profiles; do set -- "$@" --profile "$profile"; done
  set +f
  IFS=$old_ifs
fi
python_env_file=${PYTHON_ENV_FILE:-${BACKEND_ENV:-}}
if [ -n "$python_env_file" ]; then
  set -- --env-file "$python_env_file" "$@"
fi
old_ifs=$IFS
IFS=,
set -f
for file in $compose_files; do set -- "$@" -f "$file"; done
set +f
IFS=$old_ifs
set -- "$@" -f "$network_override"
if [ "$action" = start ]; then
  exec "$docker_bin" compose "$@" start backend oidc-loopback-proxy
fi
exec "$docker_bin" compose "$@" stop oidc-loopback-proxy backend
