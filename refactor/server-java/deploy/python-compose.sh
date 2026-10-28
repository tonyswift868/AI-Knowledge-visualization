#!/bin/sh
# Shared command builder for the existing Python Compose stack. Keep the
# BUG-45 overlay as the final -f argument on every Compose operation.
set -eu

action=${1:-}
case "$action" in
  start|stop|recreate) ;;
  *) echo "usage: $0 start|stop|recreate" >&2; exit 2 ;;
esac
deploy_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
network_override=${BUG45_OVERRIDE:-/opt/mentorflow/state/bug45-20261010/network45.override.yml}
fallback_override=${PYTHON_FALLBACK_OVERRIDE:-$deploy_dir/compose.python-fallback.yml}
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
# The production backend and its readiness helpers share the
# account-core-mvp profile. Activating only the explicitly named backend is
# insufficient: Compose can then omit nested depends_on services. Let an
# operator override this profile list for other deployments.
profiles=${PYTHON_COMPOSE_PROFILES:-account-core-mvp}
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
if [ "$action" = recreate ]; then
  [ -f "$fallback_override" ] || {
    echo "Python fallback override not found: $fallback_override" >&2
    exit 1
  }
  set -- "$@" -f "$fallback_override"
fi
set -- "$@" -f "$network_override"
if [ "$action" = start ]; then
  exec "$docker_bin" compose "$@" start backend oidc-loopback-proxy
fi
if [ "$action" = recreate ]; then
  exec "$docker_bin" compose "$@" up -d --no-deps --force-recreate backend oidc-loopback-proxy
fi
exec "$docker_bin" compose "$@" stop oidc-loopback-proxy backend
