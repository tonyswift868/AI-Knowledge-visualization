#!/bin/sh
# Restore only 393's configuration changes and the old Java; never migrate DB.
set -eu
release_root=${RELEASE_ROOT:-/opt/mentorflow/releases/java-393}
deploy_dir=$release_root/refactor/server-java/deploy
online_root=${ONLINE_RELEASE_ROOT:-/opt/mentorflow/releases/java-d37112fd}
online_deploy=$online_root/refactor/server-java/deploy
online_preflight=${ONLINE_PREFLIGHT:-/opt/mentorflow/releases/java-391/refactor/server-java/deploy/preflight.sh}
state=${STATE:-/opt/mentorflow/state/deploy393}
backend_env=${BACKEND_ENV:-/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env}
heartbeat=${HEARTBEAT_SCRIPT:-/opt/mentorflow/monitoring/healthchecks-send.sh}
edge_conf=${EDGE_CONF_PATH:-/opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf}
docker_bin=${DOCKER_BIN:-docker}
curl_bin=${CURL_BIN:-curl}
sleep_bin=${SLEEP_BIN:-sleep}
java_container=${JAVA_CONTAINER:-mentorflow-java-backend}
oidc_container=${JAVA_OIDC_CONTAINER:-mentorflow-java-oidc}
test -d "$state"
if [ -f "$state/edge-install-attempted" ] && [ -f "$state/edge.conf.before" ]; then
  DOCKER_BIN="$docker_bin" sh "$deploy_dir/edge/install-edge-logging393.sh" rollback "$edge_conf" "$state/edge.conf.before"
fi
if [ -f "$state/heartbeat-replace-attempted" ]; then
  test -s "$state/heartbeat.before.sh"
  install -m 755 "$state/heartbeat.before.sh" "$heartbeat.393-restore"
  mv "$heartbeat.393-restore" "$heartbeat"
fi
if [ -f "$state/java-switch-attempted" ]; then
  test "$(awk -F= '$1 == "MF_JAVA_VERSION" {print $2}' "$online_deploy/.env")" = d37112fd
  DOCKER_BIN="$docker_bin" BACKEND_ENV="$backend_env" sh "$online_deploy/java-compose.sh" config
  DOCKER_BIN="$docker_bin" BACKEND_ENV="$backend_env" sh "$online_deploy/java-compose.sh" up
  healthy=0;attempt=0
  while [ "$attempt" -lt 30 ]; do
    healthy=1
    for path in live startup ready; do
      code=$("$curl_bin" -ksS --max-time 15 -o /dev/null -w '%{http_code}' "https://89.45.45.28/health/$path") || code=000
      [ "$code" = 200 ] || healthy=0
    done
    [ "$healthy" -eq 1 ] && break
    attempt=$((attempt + 1)); "$sleep_bin" 2
  done
  test "$healthy" -eq 1
  test "$("$docker_bin" inspect --format '{{.Config.Image}}' "$java_container")" = mentorflow-java-server:d37112fd
  backend_id=$("$docker_bin" inspect --format '{{.Id}}' "$java_container")
  test "$("$docker_bin" inspect --format '{{.HostConfig.NetworkMode}}' "$oidc_container")" = "container:$backend_id"
  page_code=$("$curl_bin" -sS --max-time 35 -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/admin/accounts) || page_code=000
  if [ "$page_code" != 200 ]; then
    "$sleep_bin" 35
    page_code=$("$curl_bin" -sS --max-time 35 -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/admin/accounts) || page_code=000
  fi
  test "$page_code" = 200
fi
env -i PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" sh "$online_preflight"
printf '%s\n' 'ROLLBACK393_OK image=mentorflow-java-server:d37112fd schema_untouched=0053'
