#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

for required in GITHUB_ACTIONS GITHUB_RUN_ID GITHUB_RUN_ATTEMPT GITHUB_SHA RUNNER_NAME RUNNER_OS RUNNER_ARCH; do
  if [[ -z "${!required:-}" ]]; then
    echo "$required is required for the real gate." >&2
    exit 2
  fi
done
[[ "$GITHUB_ACTIONS" == "true" ]]
[[ "$GITHUB_RUN_ID" =~ ^[0-9]+$ ]]
[[ "$GITHUB_RUN_ATTEMPT" =~ ^[0-9]+$ ]]
[[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$RUNNER_NAME" == "mentorflow-e1-turkey-01" ]]
[[ "$RUNNER_OS" == "Linux" ]]
[[ "$RUNNER_ARCH" == "X64" ]]

compose_project="mf-e102-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
export E1_RLS_DATABASE="e102_${GITHUB_RUN_ID}_${GITHUB_RUN_ATTEMPT}_rls"
export E1_WORKER_DATABASE="e102_${GITHUB_RUN_ID}_${GITHUB_RUN_ATTEMPT}_worker"
export E1_POSTGRES_PASSWORD="e102-ci-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-only"
export E1_APP_PASSWORD="e102-app-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-only"

cleanup() {
  exit_status=$?
  trap - EXIT
  set +e
  docker compose -p "$compose_project" down --volumes --remove-orphans
  down_status=$?
  containers="$(docker ps -aq --filter "label=com.docker.compose.project=$compose_project")"
  containers_status=$?
  volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$compose_project")"
  volumes_status=$?
  networks="$(docker network ls -q --filter "label=com.docker.compose.project=$compose_project")"
  networks_status=$?
  set -e
  if [[ $down_status -ne 0 || $containers_status -ne 0 || $volumes_status -ne 0 || $networks_status -ne 0 || -n "$containers" || -n "$volumes" || -n "$networks" ]]; then
    echo "Exact compose project cleanup failed." >&2
    exit 1
  fi
  printf '{"compose_project":"%s","post_cleanup_containers":0,"post_cleanup_networks":0,"post_cleanup_volumes":0}\n' "$compose_project"
  exit "$exit_status"
}
trap cleanup EXIT

pre_containers="$(docker ps -aq --filter "label=com.docker.compose.project=$compose_project")"
pre_volumes="$(docker volume ls -q --filter "label=com.docker.compose.project=$compose_project")"
pre_networks="$(docker network ls -q --filter "label=com.docker.compose.project=$compose_project")"
[[ -z "$pre_containers" && -z "$pre_volumes" && -z "$pre_networks" ]]

mapfile -t services < <(docker compose -p "$compose_project" config --services | sort)
[[ "${services[*]}" == "one-shot-test-runner postgres" ]]

docker compose -p "$compose_project" up --build --abort-on-container-exit \
  --exit-code-from one-shot-test-runner postgres one-shot-test-runner
