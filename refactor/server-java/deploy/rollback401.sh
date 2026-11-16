#!/bin/sh
# Restore the real 393 Java runtime only while database migration 0054 has not started.
set -eu

release_root=${RELEASE_ROOT:-/opt/mentorflow/releases/java-401}
deploy_dir=$release_root/refactor/server-java/deploy
online_root=${ONLINE_RELEASE_ROOT:-/opt/mentorflow/releases/java-393}
online_deploy=${ONLINE_DEPLOY_DIR:-$online_root/refactor/server-java/deploy}
online_preflight=${ONLINE_PREFLIGHT:-$online_deploy/preflight.sh}
state=${STATE:-/opt/mentorflow/state/deploy401}
backend_env=${BACKEND_ENV:-/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env}
docker_bin=${DOCKER_BIN:-docker}
curl_bin=${CURL_BIN:-curl}
sleep_bin=${SLEEP_BIN:-sleep}
java_container=${JAVA_CONTAINER:-mentorflow-java-backend}
oidc_container=${JAVA_OIDC_CONTAINER:-mentorflow-java-oidc}
postgres_container=${POSTGRES_CONTAINER:-mentorflow-e1-preview-postgres-1}
revision_0053=0053_workbench_question_index

test -d "$state"
if [ -f "$state/migration-started" ]; then
  printf '%s\n' 'ROLLBACK401_REFUSED migration_started database_may_be_0054' >&2
  exit 3
fi

# Query the current revision directly; migrate.sh current builds a tool image and is not read-only.
revision=$("$docker_bin" exec "$postgres_container" psql -XqAt -v ON_ERROR_STOP=1 -U postgres -d mentorflow \
  -c 'BEGIN READ ONLY; SELECT version_num FROM alembic_version; COMMIT;')
test "$revision" = "$revision_0053" || {
  printf '%s\n' 'ROLLBACK401_REFUSED database_revision_not_confirmed_0053' >&2
  exit 3
}
test "$(awk -F= '$1 == "MF_JAVA_VERSION" {print $2}' "$online_deploy/.env")" = 393
DOCKER_BIN="$docker_bin" BACKEND_ENV="$backend_env" sh "$online_deploy/java-compose.sh" config
DOCKER_BIN="$docker_bin" BACKEND_ENV="$backend_env" sh "$online_deploy/java-compose.sh" up

healthy=0
attempt=0
while [ "$attempt" -lt 30 ]; do
  healthy=1
  for path in live startup ready; do
    code=$("$curl_bin" -ksS --max-time 15 -o /dev/null -w '%{http_code}' "https://89.45.45.28/health/$path") || code=000
    printf 'rollback_health_%s=%s\n' "$path" "$code"
    [ "$code" = 200 ] || healthy=0
  done
  [ "$healthy" -eq 1 ] && break
  attempt=$((attempt + 1)); "$sleep_bin" 2
done
test "$healthy" -eq 1
test "$("$docker_bin" inspect --format '{{.Config.Image}}' "$java_container")" = mentorflow-java-server:393
backend_id=$("$docker_bin" inspect --format '{{.Id}}' "$java_container")
test "$("$docker_bin" inspect --format '{{.HostConfig.NetworkMode}}' "$oidc_container")" = "container:$backend_id"
env -i PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" sh "$online_preflight"
printf '%s\n' 'ROLLBACK401_OK image=mentorflow-java-server:393 schema_confirmed=0053'
