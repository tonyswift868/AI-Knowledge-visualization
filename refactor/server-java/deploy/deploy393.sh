#!/bin/sh
# 393: replace Java once, preserve schema 0053, add only approved diagnostics.
set -eu
release_root=${RELEASE_ROOT:-/opt/mentorflow/releases/java-393}
deploy_dir=$release_root/refactor/server-java/deploy
online_root=${ONLINE_RELEASE_ROOT:-/opt/mentorflow/releases/java-d37112fd}
online_deploy=$online_root/refactor/server-java/deploy
online_preflight=${ONLINE_PREFLIGHT:-/opt/mentorflow/releases/java-391/refactor/server-java/deploy/preflight.sh}
state=${STATE:-/opt/mentorflow/state/deploy393}
manifest=${SOURCE_SHA256_MANIFEST:-/opt/mentorflow/state/release393/source.sha256}
backend_env=${BACKEND_ENV:-/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env}
heartbeat=${HEARTBEAT_SCRIPT:-/opt/mentorflow/monitoring/healthchecks-send.sh}
edge_conf=${EDGE_CONF_PATH:-/opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf}
docker_bin=${DOCKER_BIN:-docker}
curl_bin=${CURL_BIN:-curl}
sleep_bin=${SLEEP_BIN:-sleep}
java_container=${JAVA_CONTAINER:-mentorflow-java-backend}
oidc_container=${JAVA_OIDC_CONTAINER:-mentorflow-java-oidc}
image=mentorflow-java-server:393
test ! -e "$state"
test -s "$manifest" && test -s "$heartbeat"
for file in .env .java.env compose.generated.yml; do test -s "$online_deploy/$file"; done
for command in "$docker_bin" "$curl_bin" awk sh install cmp; do command -v "$command" >/dev/null 2>&1; done

# The existing no-argument entry checks the current deployment independently.
env -i PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" sh "$online_preflight"
# Candidate source is checked against the old running Java before any switch.
RELEASE_ROOT="$release_root" DEPLOY_DIR="$online_deploy" SERVER_ROOT="$release_root/refactor/server" \
 SOURCE_SHA256_MANIFEST="$manifest" BACKEND_ENV="$backend_env" \
 JAVA_IMAGE=mentorflow-java-server:d37112fd EXPECTED_ALEMBIC_REVISION=0053_workbench_question_index \
 PREFLIGHT_SOURCE_REVISION=0053_workbench_question_index sh "$deploy_dir/preflight.sh"
test "$("$docker_bin" inspect --format '{{.Config.Image}}' "$java_container")" = mentorflow-java-server:d37112fd
umask 077
mkdir -p "$state"
cp -p "$heartbeat" "$state/heartbeat.before.sh"
"$docker_bin" inspect --format '{{.Id}}' "$java_container" > "$state/java.before.id"
"$docker_bin" inspect --format '{{.State.StartedAt}}' "$java_container" > "$state/java.before.started"

rollback_on_failure() {
  code=$?
  trap - 0 HUP INT TERM
  if [ -f "$state/edge.conf.before.rollback-failed" ]; then
    printf '%s\n' 'AUTO_ROLLBACK393_FAILED edge_restore_failed stop_and_report' >&2
    exit 70
  fi
  if [ "$code" -ne 0 ] && [ -f "$state/java-switch-attempted" ]; then
    if RELEASE_ROOT="$release_root" ONLINE_RELEASE_ROOT="$online_root" STATE="$state" \
       BACKEND_ENV="$backend_env" HEARTBEAT_SCRIPT="$heartbeat" EDGE_CONF_PATH="$edge_conf" ONLINE_PREFLIGHT="$online_preflight" \
       DOCKER_BIN="$docker_bin" CURL_BIN="$curl_bin" SLEEP_BIN="$sleep_bin" \
       sh "$deploy_dir/rollback393.sh"; then
      printf '%s\n' 'AUTO_ROLLBACK393_OK original_step_failed' >&2
    else
      printf '%s\n' 'AUTO_ROLLBACK393_FAILED stop_and_report' >&2
      exit 70
    fi
  fi
  exit "$code"
}
trap rollback_on_failure 0
trap 'exit 130' HUP INT TERM

# Check the real edge filesystem, rotation and nginx candidate before Java work.
DOCKER_BIN="$docker_bin" sh "$deploy_dir/edge/install-edge-logging393.sh" check "$edge_conf" "$state/edge.conf.before"

# Reuse the existing runtime inputs as bytes, without printing their values.
cp -p "$online_deploy/.java.env" "$deploy_dir/.java.env"
cp -p "$online_deploy/compose.generated.yml" "$deploy_dir/compose.generated.yml"
awk -F= '$1 != "MF_JAVA_DEPLOY_DIR" && $1 != "MF_JAVA_VERSION" { print }' "$online_deploy/.env" > "$deploy_dir/.env"
printf 'MF_JAVA_DEPLOY_DIR=%s\n' "$deploy_dir" >> "$deploy_dir/.env"
sh "$deploy_dir/build.sh" 393
test "$(awk -F= '$1 == "MF_JAVA_VERSION" { print $2 }' "$deploy_dir/.env")" = 393
jvm_options=$(awk '/JAVA_TOOL_OPTIONS:[[:space:]]*>-/{on=1;next} on && /^[[:space:]]+[A-Za-z_][A-Za-z0-9_]*:/{exit} on{sub(/^[[:space:]]+/,"");printf "%s ",$0}' "$deploy_dir/compose.yml")
test -n "$jvm_options"
"$docker_bin" run --rm --pull=never --entrypoint java -e "JAVA_TOOL_OPTIONS=$jvm_options" "$image" -XX:+PrintFlagsFinal -version >/dev/null 2>&1
DOCKER_BIN="$docker_bin" BACKEND_ENV="$backend_env" sh "$deploy_dir/java-compose.sh" config
touch "$state/java-switch-attempted"
DOCKER_BIN="$docker_bin" BACKEND_ENV="$backend_env" sh "$deploy_dir/java-compose.sh" up

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
[ "$healthy" -eq 1 ]
backend_id=$("$docker_bin" inspect --format '{{.Id}}' "$java_container")
test "$("$docker_bin" inspect --format '{{.HostConfig.NetworkMode}}' "$oidc_container")" = "container:$backend_id"
page_code=$("$curl_bin" -sS --max-time 35 -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/admin/accounts) || page_code=000
if [ "$page_code" != 200 ]; then
  # Existing socket-proxyd rediscovers the new Java address after its idle exit.
  # Let it expire naturally; do not modify or restart the proxy service.
  "$sleep_bin" 35
  page_code=$("$curl_bin" -sS --max-time 35 -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/admin/accounts) || page_code=000
fi
test "$page_code" = 200
if ! cmp -s "$deploy_dir/monitoring/healthchecks-send.sh" "$heartbeat"; then
  touch "$state/heartbeat-replace-attempted"
  install -m 755 "$deploy_dir/monitoring/healthchecks-send.sh" "$heartbeat.393-new"
  mv "$heartbeat.393-new" "$heartbeat"
fi
touch "$state/edge-install-attempted"
DOCKER_BIN="$docker_bin" sh "$deploy_dir/edge/install-edge-logging393.sh" install "$edge_conf" "$state/edge.conf.before"
test "$("$docker_bin" inspect --format '{{.Config.Image}}' "$java_container")" = "$image"
# Explicitly test the new release's no-argument defaults with no old overrides.
(cd "$release_root" && env -i PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" sh refactor/server-java/deploy/preflight.sh)
touch "$state/complete"
printf 'DEPLOY393_OK image=%s state=%s\n' "$image" "$state"
