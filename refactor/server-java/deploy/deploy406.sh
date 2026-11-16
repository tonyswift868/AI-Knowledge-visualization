#!/bin/sh
# Switch to Java 406 once, verify it against schema 0053, then run migrations 0054-0056.
set -eu

release_root=${RELEASE_ROOT:-/opt/mentorflow/releases/java-406}
deploy_dir=$release_root/refactor/server-java/deploy
server_root=$release_root/refactor/server
online_root=${ONLINE_RELEASE_ROOT:-/opt/mentorflow/releases/java-393}
online_deploy=${ONLINE_DEPLOY_DIR:-$online_root/refactor/server-java/deploy}
online_preflight=${ONLINE_PREFLIGHT:-$online_deploy/preflight.sh}
state=${STATE:-/opt/mentorflow/state/deploy406}
manifest=${SOURCE_SHA256_MANIFEST:-/opt/mentorflow/state/release406/source.sha256}
backup_verification=${BACKUP_RESTORE_VERIFICATION:-/opt/mentorflow/state/release406/backup-restore-verification.txt}
backend_env=${BACKEND_ENV:-/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env}
docker_bin=${DOCKER_BIN:-docker}
curl_bin=${CURL_BIN:-curl}
sleep_bin=${SLEEP_BIN:-sleep}
java_container=${JAVA_CONTAINER:-mentorflow-java-backend}
oidc_container=${JAVA_OIDC_CONTAINER:-mentorflow-java-oidc}
postgres_container=${POSTGRES_CONTAINER:-mentorflow-e1-preview-postgres-1}
python_container=${PYTHON_CONTAINER:-mentorflow-e1-preview-backend-1}
network=${MIGRATION_NETWORK:-mentorflow-e1-preview_internal}
image=mentorflow-java-server:406
revision_0053=0053_workbench_question_index
revision_0054=0054_sync_receipts_without_payload
revision_0055=0055_purge_deleted_plan_progress
revision_0056=0056_closure_session_cleanup

test ! -e "$state"
test -s "$manifest"
test -s "$backup_verification"
grep -Fxq 'backup-restore=PASS' "$backup_verification"
grep -Fxq 'copy-migration=PASS' "$backup_verification"
grep -Fxq 'copy-revision=0056_closure_session_cleanup' "$backup_verification"
for file in .env .java.env compose.generated.yml; do test -s "$online_deploy/$file"; done
for command in "$docker_bin" "$curl_bin" awk sh install cmp grep; do command -v "$command" >/dev/null 2>&1; done

# Verify both the live 393/0053 deployment and the candidate source before any switch.
env -i PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" sh "$online_preflight"
RELEASE_ROOT="$release_root" DEPLOY_DIR="$online_deploy" SERVER_ROOT="$server_root" \
 SOURCE_SHA256_MANIFEST="$manifest" BACKEND_ENV="$backend_env" \
 JAVA_IMAGE=mentorflow-java-server:393 EXPECTED_ALEMBIC_REVISION="$revision_0053" \
 PREFLIGHT_SOURCE_REVISION="$revision_0056" sh "$deploy_dir/preflight406.sh"
test "$("$docker_bin" inspect --format '{{.Config.Image}}' "$java_container")" = mentorflow-java-server:393
umask 077
mkdir -m 700 -p "$state"
"$docker_bin" inspect --format '{{.Id}}' "$java_container" > "$state/java.before.id"

rollback_on_failure() {
  code=$?
  trap - 0 HUP INT TERM
  if [ "$code" -ne 0 ] && [ -f "$state/java-switch-attempted" ]; then
    if [ -f "$state/migration-started" ]; then
      printf '%s\n' 'AUTO_ROLLBACK406_FORBIDDEN migration_started database_state_unknown_stop_and_report' >&2
    elif RELEASE_ROOT="$release_root" ONLINE_RELEASE_ROOT="$online_root" ONLINE_DEPLOY_DIR="$online_deploy" \
      ONLINE_PREFLIGHT="$online_preflight" STATE="$state" BACKEND_ENV="$backend_env" \
      POSTGRES_CONTAINER="$postgres_container" MIGRATION_NETWORK="$network" \
      DOCKER_BIN="$docker_bin" CURL_BIN="$curl_bin" SLEEP_BIN="$sleep_bin" \
      sh "$deploy_dir/rollback406.sh"; then
      printf '%s\n' 'AUTO_ROLLBACK406_OK original_step_failed schema_confirmed_0053' >&2
    else
      printf '%s\n' 'AUTO_ROLLBACK406_FAILED stop_and_report' >&2
      exit 70
    fi
  fi
  exit "$code"
}
trap rollback_on_failure 0
trap 'exit 130' HUP INT TERM

# Reuse 393's runtime settings as bytes; never print their contents.
cp -p "$online_deploy/.java.env" "$deploy_dir/.java.env"
cp -p "$online_deploy/compose.generated.yml" "$deploy_dir/compose.generated.yml"
awk -F= '$1 != "MF_JAVA_DEPLOY_DIR" && $1 != "MF_JAVA_VERSION" { print }' "$online_deploy/.env" > "$deploy_dir/.env"
printf 'MF_JAVA_DEPLOY_DIR=%s\n' "$deploy_dir" >> "$deploy_dir/.env"
sh "$deploy_dir/build.sh" 406
test "$(awk -F= '$1 == "MF_JAVA_VERSION" { print $2 }' "$deploy_dir/.env")" = 406
jvm_options=$(awk '/JAVA_TOOL_OPTIONS:[[:space:]]*>-/{on=1;next} on && /^[[:space:]]+[A-Za-z_][A-Za-z0-9_]*:/{exit} on{sub(/^[[:space:]]+/,"");printf "%s ",$0}' "$deploy_dir/compose.yml")
test -n "$jvm_options"
"$docker_bin" run --rm --pull=never --entrypoint java -e "JAVA_TOOL_OPTIONS=$jvm_options" "$image" -XX:+PrintFlagsFinal -version >/dev/null 2>&1
DOCKER_BIN="$docker_bin" BACKEND_ENV="$backend_env" sh "$deploy_dir/java-compose.sh" config
touch "$state/java-switch-attempted"
DOCKER_BIN="$docker_bin" BACKEND_ENV="$backend_env" sh "$deploy_dir/java-compose.sh" up

# Require all health endpoints, the OIDC pairing and the Java-only HTTP contract probe.
healthy=0
attempt=0
while [ "$attempt" -lt 30 ]; do
  healthy=1
  for path in live startup ready; do
    code=$("$curl_bin" -ksS --max-time 15 -o /dev/null -w '%{http_code}' "https://89.45.45.28/health/$path") || code=000
    printf 'health_%s=%s\n' "$path" "$code"
    [ "$code" = 200 ] || healthy=0
  done
  [ "$healthy" -eq 1 ] && break
  attempt=$((attempt + 1)); "$sleep_bin" 2
done
test "$healthy" -eq 1
backend_id=$("$docker_bin" inspect --format '{{.Id}}' "$java_container")
test "$("$docker_bin" inspect --format '{{.HostConfig.NetworkMode}}' "$oidc_container")" = "container:$backend_id"
"$docker_bin" run --rm --pull=never --network "$network" -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$deploy_dir/probe.py:/tmp/probe.py:ro" --entrypoint python \
  "$("$docker_bin" inspect --format '{{.Config.Image}}' "$python_container")" \
  /tmp/probe.py --java-only --timeout 15

# No database migration is allowed before this durable marker. Once present, rollback406 refuses 393.
requests_before_bytes=$("$docker_bin" exec "$postgres_container" psql -U postgres -d mentorflow -v ON_ERROR_STOP=1 -Atqc \
  "SELECT pg_total_relation_size('public.structured_sync_requests')")
entities_before_bytes=$("$docker_bin" exec "$postgres_container" psql -U postgres -d mentorflow -v ON_ERROR_STOP=1 -Atqc \
  "SELECT pg_total_relation_size('public.structured_sync_entities')")
for size in "$requests_before_bytes" "$entities_before_bytes"; do
  case "$size" in ''|*[!0-9]*) printf '%s\n' 'invalid pre-migration table size result' >&2; exit 1 ;; esac
done
touch "$state/migration-started"
 migration_started_ns=$(date +%s%N)
DOCKER_BIN="$docker_bin" sh "$deploy_dir/migrate.sh" upgrade > "$state/migration-upgrade.log" 2>&1
migration_finished_ns=$(date +%s%N)
for migration_label in '0054 sync receipt payload scrub UPDATE' '0055 deleted plan progress scrub UPDATE' 'closure session cleanup'; do
  elapsed=$(sed -n "s/.*$migration_label elapsed \\([0-9.][0-9.]*\\) ms.*/\\1/p" \
    "$state/migration-upgrade.log" | tail -n 1)
  test -n "$elapsed"
  case "$migration_label" in
    '0054 sync receipt payload scrub UPDATE') elapsed_0054=$elapsed ;;
    '0055 deleted plan progress scrub UPDATE') elapsed_0055=$elapsed ;;
    *) elapsed_0056=$elapsed ;;
  esac
done
current_revision=$("$docker_bin" exec "$postgres_container" psql -XqAt -v ON_ERROR_STOP=1 -U postgres -d mentorflow \
  -c 'BEGIN READ ONLY; SELECT version_num FROM alembic_version; COMMIT;')
test "$current_revision" = "$revision_0056"
vacuum_started_ns=$(date +%s%N)
"$docker_bin" exec "$postgres_container" psql -U postgres -d mentorflow -v ON_ERROR_STOP=1 \
  -c 'VACUUM (ANALYZE) public.structured_sync_requests' >/dev/null
"$docker_bin" exec "$postgres_container" psql -U postgres -d mentorflow -v ON_ERROR_STOP=1 \
  -c 'VACUUM (ANALYZE) public.structured_sync_entities' >/dev/null
vacuum_finished_ns=$(date +%s%N)
requests_after_bytes=$("$docker_bin" exec "$postgres_container" psql -U postgres -d mentorflow -v ON_ERROR_STOP=1 -Atqc \
  "SELECT pg_total_relation_size('public.structured_sync_requests')")
entities_after_bytes=$("$docker_bin" exec "$postgres_container" psql -U postgres -d mentorflow -v ON_ERROR_STOP=1 -Atqc \
  "SELECT pg_total_relation_size('public.structured_sync_entities')")
for size in "$requests_after_bytes" "$entities_after_bytes"; do
  case "$size" in ''|*[!0-9]*) printf '%s\n' 'invalid post-vacuum table size result' >&2; exit 1 ;; esac
done
printf 'migration_0054_update_elapsed_ms=%s\nmigration_0055_update_elapsed_ms=%s\nmigration_0056_elapsed_ms=%s\nmigration_command_elapsed_ms=%s\nstructured_sync_requests_before_bytes=%s\nstructured_sync_entities_before_bytes=%s\nvacuum_elapsed_ms=%s\nstructured_sync_requests_after_vacuum_bytes=%s\nstructured_sync_entities_after_vacuum_bytes=%s\n' \
  "$elapsed_0054" "$elapsed_0055" "$elapsed_0056" "$(((migration_finished_ns - migration_started_ns) / 1000000))" \
  "$requests_before_bytes" "$entities_before_bytes" "$(((vacuum_finished_ns - vacuum_started_ns) / 1000000))" \
  "$requests_after_bytes" "$entities_after_bytes" | tee "$state/metrics.txt"

# Run the 406 no-argument preflight after the database is at 0056. Docker cleanup
# is deferred to finalize406.py until E2E and post-deployment storage checks pass.
(cd "$release_root" && env -i PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" sh refactor/server-java/deploy/preflight406.sh)
touch "$state/complete"
printf 'DEPLOY406_OK image=%s schema=%s state=%s\n' "$image" "$revision_0056" "$state"
