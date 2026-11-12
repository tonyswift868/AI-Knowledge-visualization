#!/bin/sh
# Read-only deployment preflight. It is intentionally stdin-safe (sh -s): all
# paths and state are external inputs, and this script creates no files.
set -u

docker_bin=${DOCKER_BIN:-docker}
release_root=${RELEASE_ROOT:-/opt/mentorflow/releases/java-1070685b}
deploy_dir=${DEPLOY_DIR:-$release_root/refactor/server-java/deploy}
server_root=${SERVER_ROOT:-$release_root/refactor/server}
backend_env=${BACKEND_ENV:-/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env}
network45_override=${NETWORK45_OVERRIDE:-/opt/mentorflow/state/bug45-20261010/network45.override.yml}
source_manifest=${SOURCE_SHA256_MANIFEST:-/opt/mentorflow/state/release275/source.sha256}
java_container=${JAVA_CONTAINER:-mentorflow-java-backend}
java_oidc_container=${JAVA_OIDC_CONTAINER:-mentorflow-java-oidc}
python_container=${PYTHON_CONTAINER:-mentorflow-e1-preview-backend-1}
python_oidc_container=${PYTHON_OIDC_CONTAINER:-mentorflow-e1-preview-oidc-loopback-proxy-1}
edge_container=${EDGE_CONTAINER:-mentorflow-e1-preview-edge-1}
edge_conf_path=${EDGE_CONF_PATH:-/opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf}
postgres_container=${POSTGRES_CONTAINER:-mentorflow-e1-preview-postgres-1}
project=${COMPOSE_PROJECT:-mentorflow-e1-preview}
java_image=${JAVA_IMAGE:-mentorflow-java-server:269}
python_image=${PYTHON_IMAGE:-mentorflow-slim-backend:269-python-fallback}
migration_image=${MIGRATION_IMAGE:-mentorflow-slim-backend:269-python-fallback}
migration_tool_image=${MIGRATION_TOOL_IMAGE:-mentorflow-migration-tool:MF-275}
migration_network=${MIGRATION_NETWORK:-mentorflow-e1-preview_internal}
migration_secrets_volume=${MIGRATION_SECRETS_VOLUME:-mentorflow-e1-preview_migrate_secrets}
migration_password_file=${MF_MIGRATION_PASSWORD_FILE:-/run/secrets/postgres_migrator_password}
expected_revision=${EXPECTED_ALEMBIC_REVISION:-0047_rag_promoted_manifests}
source_revision=${PREFLIGHT_SOURCE_REVISION:-0047_rag_promoted_manifests}
expected_java_version=${PREFLIGHT_EXPECTED_JAVA_VERSION:-${java_image##*:}}
curl_bin=${CURL_BIN:-curl}
failures=0

# The new source bundle exists before the database is migrated to its head.
required_hash_paths='refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagService.java
refactor/server-java/src/main/java/win/clannad/mentorflow/rag/RagRepository.java
refactor/server-java/src/main/java/win/clannad/mentorflow/rag/PgVectorStore.java
refactor/server-java/Dockerfile
refactor/server/Dockerfile
refactor/server/enterprise_tenancy/schema.py
refactor/server/migrations/versions/0047_rag_promoted_manifests.py
refactor/server-java/deploy/build.sh
refactor/server-java/deploy/migrate.sh
refactor/server-java/deploy/java-compose.sh
refactor/server-java/deploy/python-compose.sh
refactor/server-java/deploy/probe.py
refactor/server-java/deploy/entrypoint.sh
refactor/server-java/deploy/compose.yml'
case "$source_revision" in
  0047_rag_promoted_manifests) ;;
  0048_rag_source_chunks)
    required_hash_paths="$required_hash_paths
refactor/server/migrations/versions/0048_rag_source_chunks.py"
    ;;
  0049_drop_redundant_rag_indexes)
    required_hash_paths="$required_hash_paths
refactor/server/migrations/versions/0048_rag_source_chunks.py
refactor/server/migrations/versions/0049_drop_redundant_rag_indexes.py"
    ;;
  0050_account_daily_activity)
    required_hash_paths="$required_hash_paths
refactor/server/migrations/versions/0048_rag_source_chunks.py
refactor/server/migrations/versions/0049_drop_redundant_rag_indexes.py
refactor/server/migrations/versions/0050_account_daily_activity.py"
    ;;
  0051_sync_closure_fence)
    required_hash_paths="$required_hash_paths
refactor/server/migrations/versions/0048_rag_source_chunks.py
refactor/server/migrations/versions/0049_drop_redundant_rag_indexes.py
refactor/server/migrations/versions/0050_account_daily_activity.py
refactor/server/migrations/versions/0051_sync_closure_fence.py"
    ;;
  *) printf 'unsupported PREFLIGHT_SOURCE_REVISION\n' >&2; exit 2 ;;
esac
required_hash_count=$(printf '%s\n' "$required_hash_paths" | awk 'NF { n++ } END { print n+0 }')

# Emit the same source set for an approved archive; no Docker or file writes.
if [ "$#" -gt 0 ]; then
  if [ "$#" -ne 1 ] || [ "$1" != --source-manifest ]; then
    printf 'usage: preflight.sh [--source-manifest]\n' >&2
    exit 2
  fi
  (
    cd "$release_root" || exit 1
    for required_path in $required_hash_paths; do
      digest_line=$(sha256sum -b "$required_path") || exit 1
      printf '%s  %s\n' "${digest_line%% *}" "$required_path"
    done
  )
  exit $?
fi

ok() {
  if [ "$#" -gt 1 ]; then printf '%s OK: %s\n' "$1" "$2"; else printf '%s OK\n' "$1"; fi
}
fail() { printf '%s FAIL: %s\n' "$1" "$2"; failures=$((failures + 1)); }
check_dir() { [ -d "$1" ] && [ -r "$1" ]; }
check_file() { [ -f "$1" ] && [ -r "$1" ] && [ -s "$1" ]; }

image_exists() {
  "$docker_bin" image inspect --format '{{.Id}}' "$1" >/dev/null 2>&1
}

env_count() {
  awk -F= -v key="$2" '$1 == key { n++ } END { print n+0 }' "$1" 2>/dev/null
}

env_value() {
  awk -F= -v key="$2" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$1" 2>/dev/null
}

check_env_key() {
  env_file=$1
  key=$2
  count=$(env_count "$env_file" "$key")
  [ "$count" = 1 ] || return 1
  value=$(env_value "$env_file" "$key")
  [ -n "$value" ] || return 1
  case "$value" in *[!A-Za-z0-9_./:@+-]*) return 1 ;; esac
  printf '%s' "$value"
}

check_container_labels() {
  container=$1
  label_project=$($docker_bin inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$container" 2>/dev/null) || return 1
  label_workdir=$($docker_bin inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container" 2>/dev/null) || return 1
  label_configs=$($docker_bin inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$container" 2>/dev/null) || return 1
  [ "$label_project" = "$project" ] || return 1
  check_dir "$label_workdir" || return 1
  [ -n "$label_configs" ] || return 1
  old_ifs=$IFS
  IFS=,
  set -f
  config_count=0
  last_config=
  for config_file in $label_configs; do
    config_count=$((config_count + 1))
    last_config=$config_file
    check_file "$config_file" || { set +f; IFS=$old_ifs; return 1; }
  done
  set +f
  IFS=$old_ifs
  [ "$config_count" -gt 0 ] || return 1
  if [ "$container" != "$edge_container" ]; then
    case "$last_config" in */network45.override.yml) ;; *) return 1 ;; esac
  fi

  label_envfiles=$($docker_bin inspect --format '{{ index .Config.Labels "com.docker.compose.project.environment_file" }}' "$container" 2>/dev/null) || return 1
  if [ -n "$label_envfiles" ] && [ "$label_envfiles" != '<no value>' ]; then
    old_ifs=$IFS
    IFS=,
    set -f
    for env_file in $label_envfiles; do check_file "$env_file" || { set +f; IFS=$old_ifs; return 1; }; done
    set +f
    IFS=$old_ifs
    if [ "$container" != "$edge_container" ]; then
      case ",$label_envfiles," in *",$backend_env,"*) ;; *) return 1 ;; esac
    fi
  fi
  return 0
}

check_compose_config() {
  container=$1
  working_dir=$("$docker_bin" inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$container" 2>/dev/null) || return 1
  label_project=$("$docker_bin" inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$container" 2>/dev/null) || return 1
  configs=$("$docker_bin" inspect --format '{{ index .Config.Labels "com.docker.compose.project.config_files" }}' "$container" 2>/dev/null) || return 1
  env_files=$("$docker_bin" inspect --format '{{ index .Config.Labels "com.docker.compose.project.environment_file" }}' "$container" 2>/dev/null) || return 1
  [ -n "$working_dir" ] && [ -n "$configs" ] || return 1
  set -- compose --project-directory "$working_dir" --project-name "$label_project"
  old_ifs=$IFS
  IFS=,
  set -f
  for env_file in $env_files; do
    [ -n "$env_file" ] && [ "$env_file" != '<no value>' ] && set -- "$@" --env-file "$env_file"
  done
  for config_file in $configs; do
    case "$config_file" in */network45.override.yml) continue ;; esac
    [ -n "$config_file" ] && set -- "$@" -f "$config_file"
  done
  set +f
  IFS=$old_ifs
  set -- "$@" -f "$network45_override"
  "$docker_bin" "$@" config --quiet
}

check_container_mounts() {
  container=$1
  mounts=$($docker_bin inspect --format '{{range .Mounts}}{{printf "%s|%s|%s|%t|%s\n" .Source .Destination .Type .RW .Name}}{{end}}' "$container" 2>/dev/null) || return 1
  [ -n "$mounts" ] || return 1
  printf '%s\n' "$mounts" | while IFS='|' read -r source destination kind writable volume_name; do
    [ -n "$source" ] && [ -n "$destination" ] || exit 1
    case "$kind" in
      bind)
        [ -e "$source" ] || exit 1
        ;;
      volume)
        [ -n "$volume_name" ] || exit 1
        volume_path=$($docker_bin volume inspect --format '{{.Mountpoint}}' "$volume_name" 2>/dev/null) || exit 1
        [ -d "$volume_path" ] || exit 1
        ;;
      *) exit 1 ;;
    esac
    case "$destination" in
      /run/secrets|/run/mentorflow/native-mtls|/run/mentorflow/lifecycle|/run/mentorflow/trust|/run/mentorflow/catalog|/run/mentorflow/tls|/etc/nginx/nginx.conf)
        [ "$writable" = false ] || exit 1
        if [ "$destination" = /etc/nginx/nginx.conf ]; then
          [ -f "$source" ] && [ -s "$source" ] || exit 1
          continue
        fi
        [ -d "$source" ] || exit 1
        if [ "$destination" = /run/mentorflow/trust ]; then
          find "$source" -type f -name '*.p12' -size +0c -print -quit 2>/dev/null | grep -q . || exit 1
        else
          find "$source" -type f -size +0c -print -quit 2>/dev/null | grep -q . || exit 1
        fi
        ;;
    esac
  done
}

secret_volume_exists() {
  volume_path=$("$docker_bin" volume inspect --format '{{.Mountpoint}}' "$migration_secrets_volume" 2>/dev/null) || return 1
  [ -n "$volume_path" ] && [ -d "$volume_path" ]
}

run_readonly_current() {
  "$docker_bin" run --pull=never --rm --network "$migration_network" \
    --read-only --tmpfs /tmp:rw,nosuid,nodev,size=32m \
    --mount "type=bind,src=$server_root,dst=/app/refactor/server,readonly" \
    --mount "type=volume,src=$migration_secrets_volume,dst=/run/secrets,readonly" \
    --workdir /app --env PYTHONDONTWRITEBYTECODE=1 --env 'PGOPTIONS=-c default_transaction_read_only=on' \
    --entrypoint /bin/sh "$migration_tool_image" -ec '
      secret=$1
      [ -f "$secret" ] && [ -r "$secret" ] || exit 31
      password=$(tr -d "\r\n" < "$secret")
      [ -n "$password" ] || exit 32
      encoded=$(printf %s "$password" | od -An -v -tu1 | awk '\''{ for (i=1; i<=NF; i++) { n=$i; c=sprintf("%c", n); if (c ~ /[A-Za-z0-9._~-]/) printf "%s", c; else printf "%%%02X", n } }'\'')
      export MENTORFLOW_MIGRATION_DATABASE_URL="postgresql+psycopg://mentorflow_migrator:${encoded}@postgres:5432/mentorflow?connect_timeout=2"
      python -m alembic -c /app/refactor/server/alembic.ini current
    ' sh "$migration_password_file" 2>/dev/null
}

printf 'preflight read-only checks begin\n'

if "$docker_bin" info >/dev/null 2>&1; then ok docker_daemon; else fail docker_daemon 'Docker daemon unavailable'; fi
compose_version=$("$docker_bin" compose version --short 2>/dev/null) || compose_version=
if [ -n "$compose_version" ]; then ok compose_cli "version=$compose_version"; else fail compose_cli 'Docker Compose CLI unavailable'; fi

for tag in "$java_image" "$python_image" "$migration_image" "$migration_tool_image" \
  mentorflow-java-server:226 mentorflow-java-server:rollback272 mentorflow-java-server:rollback274 \
  mentorflow-slim-backend:226-python-fallback; do
  if image_exists "$tag"; then ok "image_$tag"; else fail "image_$tag" 'required image absent (preflight never builds or pulls images)'; fi
done

if "$docker_bin" network inspect --format '{{.Name}}|{{range .IPAM.Config}}{{.Subnet}}{{end}}' "$migration_network" >/dev/null 2>&1; then
  subnet=$($docker_bin network inspect --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "$migration_network" 2>/dev/null)
  case "$subnet" in 172.18.*) ok migration_network ;; *) fail migration_network 'network subnet is not the expected internal range' ;; esac
else fail migration_network 'existing internal network absent'; fi

if check_file "$network45_override"; then ok network45_override; else fail network45_override 'required override absent, unreadable, or empty'; fi
if check_dir "$release_root" && check_dir "$deploy_dir" && check_dir "$server_root"; then ok release_directories; else fail release_directories 'release/deploy/server source directory absent'; fi
if check_file "$backend_env"; then ok backend_runtime_env; else fail backend_runtime_env 'runtime env file absent, unreadable, or empty'; fi

if check_file "$deploy_dir/.env" && check_file "$deploy_dir/.java.env" && \
  check_file "$deploy_dir/compose.generated.yml" && check_file "$deploy_dir/build.sh" && \
  check_file "$deploy_dir/migrate.sh" && check_file "$deploy_dir/java-compose.sh" && \
  check_file "$deploy_dir/python-compose.sh" && check_file "$deploy_dir/probe.py"; then
  ok deployment_inputs
else fail deployment_inputs 'a required deployment file is absent, unreadable, or empty'; fi

java_deploy_dir=$(check_env_key "$deploy_dir/.env" MF_JAVA_DEPLOY_DIR 2>/dev/null) || java_deploy_dir=
if [ -n "$java_deploy_dir" ] && check_dir "$java_deploy_dir"; then ok env_MF_JAVA_DEPLOY_DIR; else fail env_MF_JAVA_DEPLOY_DIR 'missing/duplicate/empty key or path absent'; fi
trust_dir=$(check_env_key "$deploy_dir/.env" MF_JAVA_TRUSTSTORE_DIR 2>/dev/null) || trust_dir=
if [ -n "$trust_dir" ] && check_dir "$trust_dir" && find "$trust_dir" -type f -name '*.p12' -size +0c -print -quit 2>/dev/null | grep -q .; then ok env_MF_JAVA_TRUSTSTORE_DIR; else fail env_MF_JAVA_TRUSTSTORE_DIR 'missing/duplicate/empty key or truststore absent'; fi
proxy_image=$(check_env_key "$deploy_dir/.env" MF_OIDC_PROXY_IMAGE 2>/dev/null) || proxy_image=
if [ -n "$proxy_image" ] && image_exists "$proxy_image"; then ok env_MF_OIDC_PROXY_IMAGE; else fail env_MF_OIDC_PROXY_IMAGE 'missing/duplicate/empty key or referenced image absent'; fi
proxy_config=$(check_env_key "$deploy_dir/.env" MF_OIDC_PROXY_CONFIG 2>/dev/null) || proxy_config=
if [ -n "$proxy_config" ] && check_file "$proxy_config"; then ok env_MF_OIDC_PROXY_CONFIG; else fail env_MF_OIDC_PROXY_CONFIG 'missing/duplicate/empty key or config absent'; fi

version_count=$(env_count "$deploy_dir/.env" MF_JAVA_VERSION)
version_value=$(env_value "$deploy_dir/.env" MF_JAVA_VERSION)
if [ "$version_count" = 1 ] && [ "$version_value" = "$expected_java_version" ]; then
  ok env_MF_JAVA_VERSION "matches expected image tag $expected_java_version"
else
  fail env_MF_JAVA_VERSION 'must occur exactly once and match the expected Java image tag; online and target build versions must not be conflated'
fi

if secret_volume_exists; then ok migration_secrets_volume; else fail migration_secrets_volume 'existing migration secrets volume absent (will not create it)'; fi

if image_exists "$migration_image" && image_exists "$migration_tool_image" && secret_volume_exists; then
  secret_meta=$($docker_bin run --pull=never --rm --network none --read-only \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=1m \
    --mount "type=volume,src=$migration_secrets_volume,dst=/run/secrets,readonly" \
    --entrypoint /bin/sh "$migration_tool_image" -ec \
    'test -f "$1" && test -r "$1" && bytes=$(wc -c < "$1") && test "$bytes" -gt 0 && mode=$(stat -c %a "$1") && printf "present bytes=%s mode=%s\n" "$bytes" "$mode"' \
    sh "$migration_password_file" 2>/dev/null)
  case "$secret_meta" in
    present\ bytes=*[0-9]\ mode=*)
      secret_bytes=${secret_meta#present bytes=}; secret_bytes=${secret_bytes% mode=*}
      case "$secret_bytes" in ''|*[!0-9]*|0) fail migration_secret_metadata 'secret file is empty or unreadable' ;; *) ok migration_secret_metadata "present bytes=$secret_bytes mode=${secret_meta##* mode=}" ;; esac
      ;;
    *) fail migration_secret_metadata 'secret file absent, unreadable, or empty' ;;
  esac
else fail migration_secret_metadata 'required existing image or secrets volume absent; skipped docker run to avoid creating state'; fi

check_state_image() {
  container=$1; expected_image=$2; expected_state=$3; label=$4
  state=$($docker_bin inspect --format '{{.State.Status}}' "$container" 2>/dev/null) || state=
  image=$($docker_bin inspect --format '{{.Config.Image}}' "$container" 2>/dev/null) || image=
  if [ "$state" = "$expected_state" ] && [ "$image" = "$expected_image" ]; then ok "$label"; else fail "$label" 'container state or image differs from expected live/fallback role'; fi
}

check_container_state() {
  container=$1; expected_state=$2; label=$3
  state=$("$docker_bin" inspect --format '{{.State.Status}}' "$container" 2>/dev/null) || state=
  if [ "$state" = "$expected_state" ]; then ok "$label"; else fail "$label" "expected state $expected_state"; fi
}

check_state_image "$java_container" "$java_image" running current_java
check_state_image "$python_container" "$python_image" exited python_fallback_stopped
check_container_state "$java_oidc_container" running java_oidc_running
check_container_state "$python_oidc_container" exited python_oidc_stopped
check_container_state "$edge_container" running edge_running
check_container_state "$postgres_container" running postgres_running
if check_container_labels "$java_container" && check_container_mounts "$java_container"; then ok compose_java_backend; else fail compose_java_backend 'labels, config files, env files, or read-only mounts disagree'; fi
if check_container_labels "$java_oidc_container" && check_container_mounts "$java_oidc_container"; then ok compose_java_oidc; else fail compose_java_oidc 'labels, config files, env files, or read-only mounts disagree'; fi
if check_container_labels "$python_container" && check_container_mounts "$python_container"; then ok compose_python_backend; else fail compose_python_backend 'labels, config files, env files, or read-only mounts disagree'; fi
if check_container_labels "$python_oidc_container" && check_container_mounts "$python_oidc_container"; then ok compose_python_oidc; else fail compose_python_oidc 'labels, config files, env files, or read-only mounts disagree'; fi
if check_container_labels "$edge_container" && check_container_mounts "$edge_container"; then ok compose_edge; else fail compose_edge 'labels, config files, env files, or read-only mounts disagree'; fi
if check_container_labels "$postgres_container" && check_container_mounts "$postgres_container"; then ok compose_postgres; else fail compose_postgres 'labels, config files, env files, or read-only mounts disagree'; fi
for pair in "$java_container:compose_config_java" "$python_container:compose_config_python" \
  "$java_oidc_container:compose_config_java_oidc" "$python_oidc_container:compose_config_python_oidc" \
  "$edge_container:compose_config_edge" "$postgres_container:compose_config_postgres"; do
  container=${pair%%:*}; label=${pair#*:}
  if check_compose_config "$container" >/dev/null 2>&1; then ok "$label"; else fail "$label" 'docker compose config --quiet failed'; fi
done

java_id=$($docker_bin inspect --format '{{.Id}}' "$java_container" 2>/dev/null) || java_id=
oidc_mode=$($docker_bin inspect --format '{{.HostConfig.NetworkMode}}' "$java_oidc_container" 2>/dev/null) || oidc_mode=
if [ -n "$java_id" ] && [ "$oidc_mode" = "container:$java_id" ]; then ok java_oidc_namespace; else fail java_oidc_namespace 'OIDC proxy is not paired with current Java container'; fi

edge_state=unknown
if check_file "$deploy_dir/cutover.sh"; then
  edge_state=$(EDGE_CONTAINER="$edge_container" EDGE_CONF_PATH="$edge_conf_path" sh "$deploy_dir/cutover.sh" status 2>/dev/null) || edge_state=unknown
fi
if [ "$edge_state" = java ]; then ok edge_upstream_java; else fail edge_upstream_java 'edge status is not Java'; fi

disk_available=$(df -Pk "$release_root" 2>/dev/null | awk 'NR==2 {print $4}')
case "$disk_available" in ''|*[!0-9]*) fail disk_space 'cannot read available space' ;; *) if [ "$disk_available" -gt 0 ]; then ok disk_space "available_kb=$disk_available"; else fail disk_space 'no available space'; fi ;; esac

hash_manifest_ok=0
if check_file "$source_manifest"; then
  manifest_count=$(awk 'NF { n++ } END { print n+0 }' "$source_manifest")
  if [ "$manifest_count" = "$required_hash_count" ] && (sed 's/\r$//' "$source_manifest" | (cd "$release_root" && sha256sum -c - >/dev/null 2>&1)); then
    missing_hash_path=0
    for required_path in $required_hash_paths; do
      awk -v expected="$required_path" '{ sub(/\r$/, "", $2) } $2 == expected { found++ } END { if (found != 1) exit 1 }' "$source_manifest" || missing_hash_path=1
    done
    [ "$missing_hash_path" -eq 0 ] && hash_manifest_ok=1
  fi
fi
if [ "$hash_manifest_ok" -eq 1 ]; then ok approved_source_sha256; else fail approved_source_sha256 "approved $required_hash_count-file hash manifest absent, incomplete, duplicate, or mismatched"; fi

if check_file "$deploy_dir/migration.Dockerfile" && image_exists "$migration_tool_image" && \
  image_exists "$migration_image" && secret_volume_exists && \
  "$docker_bin" network inspect "$migration_network" >/dev/null 2>&1; then
  current_output=$(run_readonly_current) || current_output=
  case "$current_output" in
    *"$expected_revision"*) ok database_migration_current ;;
    *) fail database_migration_current 'read-only Alembic current did not report expected revision' ;;
  esac
else fail database_migration_current 'existing migration inputs/image/network/secret volume unavailable; skipped docker run; preflight never builds or creates them'; fi

schema_tables="to_regclass('public.rag_document_upload_manifests') IS NOT NULL"
case "$expected_revision" in
  0048_rag_source_chunks|0049_drop_redundant_rag_indexes|0050_account_daily_activity|0051_sync_closure_fence)
    schema_tables="$schema_tables AND to_regclass('public.rag_source_chunks') IS NOT NULL"
    ;;
esac
case "$expected_revision" in
  0049_drop_redundant_rag_indexes|0050_account_daily_activity|0051_sync_closure_fence)
    schema_tables="$schema_tables AND to_regclass('public.ix_rag_chunks_account_document') IS NULL"
    schema_tables="$schema_tables AND to_regclass('public.ix_rag_source_chunks_logical') IS NULL"
    ;;
esac
case "$expected_revision" in
  0050_account_daily_activity|0051_sync_closure_fence)
    schema_tables="$schema_tables AND to_regclass('public.account_daily_activity') IS NOT NULL"
    ;;
esac
case "$expected_revision" in
  0051_sync_closure_fence)
    schema_tables="$schema_tables AND has_function_privilege('mentorflow_app', 'account_closure.assert_open(text,text)', 'EXECUTE')"
    ;;
esac
schema_query="BEGIN READ ONLY; SELECT current_database()||'|'||version_num||'|'||($schema_tables)::text FROM alembic_version; COMMIT;"
schema_state=$("$docker_bin" exec "$postgres_container" psql -XqAt -v ON_ERROR_STOP=1 -U postgres -d mentorflow -c "$schema_query" 2>/dev/null) || schema_state=
if [ "$schema_state" = "mentorflow|$expected_revision|true" ]; then ok postgres_schema_revision_and_table; else fail postgres_schema_revision_and_table 'read-only PostgreSQL check did not confirm expected revision and required RAG tables'; fi

java_tmpfs=$("$docker_bin" inspect --format '{{index .HostConfig.Tmpfs "/tmp"}}' "$java_container" 2>/dev/null) || java_tmpfs=
case ",$java_tmpfs," in *,exec,*) ok java_tmpfs_exec_tmpfs ;; *) fail java_tmpfs_exec_tmpfs 'Java /tmp is not configured as executable tmpfs' ;; esac

for health_path in live startup ready; do
  health_code=$("$curl_bin" -ksS --max-time 15 -o /dev/null -w '%{http_code}' "https://89.45.45.28/health/$health_path" 2>/dev/null) || health_code=000
  if [ "$health_code" = 200 ]; then ok "public_health_$health_path"; else fail "public_health_$health_path" "HTTP $health_code"; fi
done
capabilities_code=$("$curl_bin" -ksS --max-time 15 -o /dev/null -w '%{http_code}' 'https://89.45.45.28/api/enterprise/auth/lifecycle/capabilities' 2>/dev/null) || capabilities_code=000
if [ "$capabilities_code" = 200 ]; then ok public_capabilities; else fail public_capabilities "HTTP $capabilities_code"; fi

printf 'PREFLIGHT_FAILURES=%s\n' "$failures"
if [ "$failures" -ne 0 ]; then
  printf 'PREFLIGHT_BLOCKED: resolve listed FAIL items before backup, migration, build, or Compose actions\n'
  exit 1
fi
printf 'PREFLIGHT_OK: all checks passed; no deployment state was changed\n'
exit 0
