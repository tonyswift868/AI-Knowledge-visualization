#!/bin/sh
# 388-MF01: approved-window deployment runner; preparation only until invoked.
set -eu
release_root=${RELEASE_ROOT:?set RELEASE_ROOT to the approved new 0052 release directory}
version=${JAVA_VERSION:-${release_root##*/java-}}
case "$version" in ''|*[!A-Za-z0-9_.-]*) echo 'invalid JAVA_VERSION' >&2; exit 2;; esac
release_root=${RELEASE_ROOT:-/opt/mentorflow/releases/java-$version}
deploy_dir=$release_root/refactor/server-java/deploy
state=${STATE:-/opt/mentorflow/state/deploy388-java-$version}
online_root=${ONLINE_RELEASE_ROOT:-/opt/mentorflow/releases/java-0aefe606}
online_deploy=${ONLINE_DEPLOY_DIR:-$online_root/refactor/server-java/deploy}
backend_env=${BACKEND_ENV:-/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env}
candidate_manifest=${CANDIDATE_SOURCE_MANIFEST:-/opt/mentorflow/state/release388/source.sha256}
postgres_container=${POSTGRES_CONTAINER:-mentorflow-e1-preview-postgres-1}
java_container=${JAVA_CONTAINER:-mentorflow-java-backend}
java_oidc_container=${JAVA_OIDC_CONTAINER:-mentorflow-java-oidc}
edge_container=${EDGE_CONTAINER:-mentorflow-e1-preview-edge-1}
edge_conf_path=${EDGE_CONF_PATH:-/opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf}
java_image=mentorflow-java-server:$version
docker_bin=${DOCKER_BIN:-docker}
job_bin=$deploy_dir/job.sh
checked_bin=$deploy_dir/checked-step.sh
for command in "$docker_bin" sh awk sed grep curl python3 ss systemctl; do command -v "$command" >/dev/null 2>&1 || { echo "missing command: $command" >&2; exit 1; }; done
test -f "$job_bin" && test -f "$checked_bin"
test -n "$candidate_manifest" && test -s "$candidate_manifest"
test ! -e "$state"
umask 077
RELEASE_ROOT="$release_root" sh "$deploy_dir/install-account-page-proxy.sh" preflight

run_job() {
  job=$1; shift
  sh "$job_bin" start "$job" "$@" >/dev/null
  unknowns=0
  while :; do
    status=$(sh "$job_bin" status "$job")
    case "$status" in
      'SUCCEEDED 0') return 0;;
      FAILED\ *|MISSING) echo "job failed: $job ($status)" >&2; return 1;;
      UNKNOWN) unknowns=$((unknowns + 1)); [ "$unknowns" -lt 300 ] || { echo "job unresolved: $job" >&2; return 1; }; sleep 2;;
      RUNNING) sleep 2;;
      *) echo "unexpected job status: $status" >&2; return 1;;
    esac
  done
}
write_action() {
  file=$1; shift
  printf '%s\n' '#!/bin/sh' 'set -eu' > "$file"
  printf 'exec' >> "$file"
  for arg in "$@"; do printf " '%s'" "$(printf '%s' "$arg" | sed "s/'/'\\''/g")" >> "$file"; done
  printf '\n' >> "$file"
  chmod 700 "$file"
}
write_rollback() {
  file=$1
  cat > "$file" <<EOF
#!/bin/sh
set -eu
docker_bin='$docker_bin'
java_container='$java_container'
java_oidc_container='$java_oidc_container'
edge_container='$edge_container'
edge_conf_path='$edge_conf_path'
backend_env='$backend_env'
online_deploy='$online_deploy'
release_root='$release_root'
server_root='$release_root/refactor/server'
candidate_manifest='$candidate_manifest'
proxy_installer='$release_root/refactor/server-java/deploy/install-account-page-proxy.sh'
state='$state'
RELEASE_ROOT='$release_root' STATE="\$state" sh "\$proxy_installer" remove
cd '$online_deploy'
test -s '$backend_env'
test "\$(awk -F= '\$1 == "MF_JAVA_VERSION" {print substr(\$0, index(\$0, "=")+1); exit}' .env)" = 0aefe606
DOCKER_BIN="\$docker_bin" BACKEND_ENV="\$backend_env" sh java-compose.sh config
DOCKER_BIN="\$docker_bin" BACKEND_ENV="\$backend_env" sh java-compose.sh up
ss -ltn | awk '\$4 ~ /:18081$/ { found=1 } END { exit found }'
test "\$( "\$docker_bin" inspect --format '{{.State.Status}}' "\$java_container")" = running
backend_id=\$( "\$docker_bin" inspect --format '{{.Id}}' "\$java_container")
oidc_mode=\$( "\$docker_bin" inspect --format '{{.HostConfig.NetworkMode}}' "\$java_oidc_container")
test "\$oidc_mode" = "container:\$backend_id"
healthy=0
attempt=0
while [ "\$attempt" -lt 30 ]; do
  healthy=1
  for path in live startup ready; do
    code=\$(curl -ksS --max-time 15 -o /dev/null -w '%{http_code}' "https://89.45.45.28/health/\$path") || code=000
    [ "\$code" = 200 ] || healthy=0
  done
  [ "\$healthy" -eq 1 ] && break
  attempt=\$((attempt + 1))
  sleep 2
done
test "\$healthy" -eq 1
test -f "\$online_deploy/cutover.sh"
edge_state=\$(DOCKER_BIN="\$docker_bin" EDGE_CONTAINER="\$edge_container" EDGE_CONF_PATH="\$edge_conf_path" sh "\$online_deploy/cutover.sh" status)
test "\$edge_state" = java
RELEASE_ROOT="\$release_root" DEPLOY_DIR="\$online_deploy" SERVER_ROOT="\$server_root" BACKEND_ENV="\$backend_env" \\
  SOURCE_SHA256_MANIFEST="\$candidate_manifest" PREFLIGHT_SOURCE_REVISION=0052_workbench_account_rows \\
  EXPECTED_ALEMBIC_REVISION=0052_workbench_account_rows JAVA_IMAGE=mentorflow-java-server:0aefe606 \\
  sh "\$release_root/refactor/server-java/deploy/preflight.sh"
EOF
  chmod 700 "$file"
}

RELEASE_ROOT="$release_root" DEPLOY_DIR="$online_deploy" SERVER_ROOT="$release_root/refactor/server" \
  BACKEND_ENV="$backend_env" SOURCE_SHA256_MANIFEST="$candidate_manifest" \
  PREFLIGHT_SOURCE_REVISION=0052_workbench_account_rows EXPECTED_ALEMBIC_REVISION=0051_sync_closure_fence \
  JAVA_IMAGE=mentorflow-java-server:0aefe606 sh "$deploy_dir/preflight.sh"
mkdir -p "$state"
old_image=$($docker_bin inspect --format '{{.Config.Image}}' "$java_container")
test "$old_image" = mentorflow-java-server:0aefe606
printf '%s\n' "$old_image" > "$state/java-before-image"
$docker_bin inspect --format '{{.Image}}' "$java_container" > "$state/java-before-image-id"
$docker_bin inspect --format '{{.Id}}' "$java_container" > "$state/java-before-container-id"
$docker_bin inspect --format '{{.State.StartedAt}}' "$java_container" > "$state/java-before-started-at"
write_rollback "$state/rollback-java.sh"

write_backup_action() {
  file=$1
  cat > "$file" <<EOF
#!/bin/sh
set -eu
docker_bin='$docker_bin'
postgres_container='$postgres_container'
state='$state'
dump_tmp="\$state/mentorflow-before.dump.tmp"
"\$docker_bin" exec "\$postgres_container" pg_dump -U postgres -d mentorflow -Fc > "\$dump_tmp"
chmod 600 "\$dump_tmp"
test -s "\$dump_tmp"
"\$docker_bin" exec -i "\$postgres_container" pg_restore --list < "\$dump_tmp" > "\$state/backup-contents.txt"
test -s "\$state/backup-contents.txt"
mv "\$dump_tmp" "\$state/mentorflow-before.dump"
EOF
  chmod 700 "$file"
}
write_backup_action "$state/backup.sh"
run_job "$state/job-backup" sh "$state/backup.sh"

write_action "$state/migrate-current-before.sh" env "DOCKER_BIN=$docker_bin" sh "$deploy_dir/migrate.sh" current
write_action "$state/migrate-upgrade.sh" env "DOCKER_BIN=$docker_bin" sh "$deploy_dir/migrate.sh" upgrade
write_action "$state/migrate-current-after.sh" env "DOCKER_BIN=$docker_bin" sh "$deploy_dir/migrate.sh" current
run_job "$state/job-migrate-current-before" sh "$state/migrate-current-before.sh"
run_job "$state/job-migrate-upgrade" sh "$state/migrate-upgrade.sh"
run_job "$state/job-migrate-current-after" sh "$state/migrate-current-after.sh"

RELEASE_ROOT="$release_root" DEPLOY_DIR="$online_deploy" SERVER_ROOT="$release_root/refactor/server" BACKEND_ENV="$backend_env" \
  SOURCE_SHA256_MANIFEST="$candidate_manifest" PREFLIGHT_SOURCE_REVISION=0052_workbench_account_rows \
  EXPECTED_ALEMBIC_REVISION=0052_workbench_account_rows JAVA_IMAGE=mentorflow-java-server:0aefe606 \
  sh "$deploy_dir/preflight.sh"
write_action "$state/build.sh" sh "$deploy_dir/build.sh" "$version"
run_job "$state/job-build" sh "$state/build.sh"
test -f "$deploy_dir/.env" && test -f "$deploy_dir/prepare-config.sh"
truststore_dir=$(awk -F= '$1 == "MF_JAVA_TRUSTSTORE_DIR" {print substr($0, index($0, "=")+1); exit}' "$online_deploy/.env")
test -n "$truststore_dir" && test -d "$truststore_dir"
DOCKER_BIN="$docker_bin" MF_OIDC_PROXY_CONTAINER="$java_oidc_container" \
  sh "$deploy_dir/prepare-config.sh" "$java_container" "$truststore_dir" > "$state/prepare-config.log"
test -s "$deploy_dir/.java.env" && test -s "$deploy_dir/compose.generated.yml"

jvm_options=$(awk '/JAVA_TOOL_OPTIONS:[[:space:]]*>-/{on=1;next} on && /^[[:space:]]+[A-Za-z_][A-Za-z0-9_]*:/{exit} on{sub(/^[[:space:]]+/,"");printf "%s ",$0}' "$deploy_dir/compose.yml")
test -n "$jvm_options"
$docker_bin run --rm --pull=never --entrypoint java -e "JAVA_TOOL_OPTIONS=$jvm_options" "$java_image" -XX:+PrintFlagsFinal -version >/dev/null 2>&1
DOCKER_BIN="$docker_bin" BACKEND_ENV="$backend_env" sh "$deploy_dir/java-compose.sh" config
write_action "$state/compose-up.sh" env "DOCKER_BIN=$docker_bin" "BACKEND_ENV=$backend_env" sh "$deploy_dir/java-compose.sh" up
run_job "$state/job-compose-up" sh "$checked_bin" "$state/compose-up.sh" "$state/rollback-java.sh"

write_action "$state/account-page-proxy-install.sh" env "RELEASE_ROOT=$release_root" "STATE=$state" \
  sh "$deploy_dir/install-account-page-proxy.sh" install
run_job "$state/job-account-page-proxy" sh "$checked_bin" "$state/account-page-proxy-install.sh" "$state/rollback-java.sh"

write_health_action() {
  file=$1
  cat > "$file" <<EOF
#!/bin/sh
set -eu
docker_bin='$docker_bin'
java_container='$java_container'
java_oidc_container='$java_oidc_container'
backend_env='$backend_env'
release_root='$release_root'
deploy_dir='$deploy_dir'
server_root='$release_root/refactor/server'
candidate_manifest='$candidate_manifest'
RELEASE_ROOT="\$release_root" STATE='$state' sh "\$release_root/refactor/server-java/deploy/install-account-page-proxy.sh" refresh
test "\$( "\$docker_bin" inspect --format '{{.State.Status}}' "\$java_container")" = running
backend_id=\$( "\$docker_bin" inspect --format '{{.Id}}' "\$java_container")
oidc_mode=\$( "\$docker_bin" inspect --format '{{.HostConfig.NetworkMode}}' "\$java_oidc_container")
test "\$oidc_mode" = "container:\$backend_id"
healthy=0
attempt=0
while [ "\$attempt" -lt 30 ]; do
  healthy=1
  for path in live startup ready; do
    code=\$(curl -ksS --max-time 15 -o /dev/null -w '%{http_code}' "https://89.45.45.28/health/\$path") || code=000
    printf '%s %s\\n' "\$path" "\$code"
    [ "\$code" = 200 ] || healthy=0
  done
  [ "\$healthy" -eq 1 ] && break
  attempt=\$((attempt + 1))
  sleep 2
done
test "\$healthy" -eq 1
RELEASE_ROOT="\$release_root" DEPLOY_DIR="\$deploy_dir" SERVER_ROOT="\$server_root" BACKEND_ENV="\$backend_env" \\
  SOURCE_SHA256_MANIFEST="\$candidate_manifest" PREFLIGHT_SOURCE_REVISION=0052_workbench_account_rows \\
  EXPECTED_ALEMBIC_REVISION=0052_workbench_account_rows JAVA_IMAGE='$java_image' \\
  sh "\$deploy_dir/preflight.sh"
EOF
  chmod 700 "$file"
}
write_health_action "$state/health-and-final.sh"
run_job "$state/job-health-and-final" sh "$checked_bin" "$state/health-and-final.sh" "$state/rollback-java.sh"
printf '%s\n' "DEPLOY388_PREPARED version=$version state=$state"
