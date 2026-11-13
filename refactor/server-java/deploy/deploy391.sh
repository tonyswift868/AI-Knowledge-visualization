#!/bin/sh
# Apply the 0053 index migration without rebuilding or restarting Java.
set -eu
release_root=${RELEASE_ROOT:?set RELEASE_ROOT to the approved 0053 source release}
online_root=${ONLINE_RELEASE_ROOT:-/opt/mentorflow/releases/java-d37112fd}
online_deploy=${ONLINE_DEPLOY_DIR:-$online_root/refactor/server-java/deploy}
server_root=$release_root/refactor/server
deploy_dir=$release_root/refactor/server-java/deploy
state=${STATE:-/opt/mentorflow/state/deploy391-$(date -u +%Y%m%dT%H%M%SZ)}
manifest=${SOURCE_SHA256_MANIFEST:-/opt/mentorflow/state/release391/source.sha256}
backend_env=${BACKEND_ENV:-/opt/mentorflow/state/mentorflow-e1-preview/account-core-21337c1/runtime.env}
docker_bin=${DOCKER_BIN:-docker}
curl_bin=${CURL_BIN:-curl}
postgres=${POSTGRES_CONTAINER:-mentorflow-e1-preview-postgres-1}
for command in "$docker_bin" sh "$curl_bin" awk; do command -v "$command" >/dev/null 2>&1 || { echo "missing command: $command" >&2; exit 1; }; done
test -s "$manifest" && test ! -e "$state" && test -f "$deploy_dir/migrate.sh"
mkdir -m 700 -p "$state"

# Initial checks use the current database revision while hashing the candidate 0053 source.
RELEASE_ROOT="$release_root" DEPLOY_DIR="$online_deploy" SERVER_ROOT="$server_root" \
 BACKEND_ENV="$backend_env" SOURCE_SHA256_MANIFEST="$manifest" \
 PREFLIGHT_SOURCE_REVISION=0053_workbench_question_index EXPECTED_ALEMBIC_REVISION=0052_workbench_account_rows \
 JAVA_IMAGE=mentorflow-java-server:d37112fd sh "$deploy_dir/preflight.sh"

"$docker_bin" exec "$postgres" pg_dump -U postgres -d mentorflow -Fc > "$state/pre-0053.dump.tmp"
chmod 600 "$state/pre-0053.dump.tmp"
test -s "$state/pre-0053.dump.tmp"
"$docker_bin" exec -i "$postgres" pg_restore --list < "$state/pre-0053.dump.tmp" > "$state/pre-0053.contents"
test -s "$state/pre-0053.contents"
mv "$state/pre-0053.dump.tmp" "$state/pre-0053.dump"

DOCKER_BIN="$docker_bin" sh "$deploy_dir/migrate.sh" current
DOCKER_BIN="$docker_bin" sh "$deploy_dir/migrate.sh" upgrade
DOCKER_BIN="$docker_bin" sh "$deploy_dir/migrate.sh" current

healthy=0
attempt=0
while [ "$attempt" -lt 30 ]; do
  healthy=1
  for path in live startup ready; do
    code=$("$curl_bin" -ksS --max-time 15 -o /dev/null -w '%{http_code}' "https://89.45.45.28/health/$path") || code=000
    [ "$code" = 200 ] || healthy=0
  done
  code=$("$curl_bin" -sS --max-time 15 -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/admin/accounts) || code=000
  [ "$code" = 200 ] || healthy=0
  [ "$healthy" -eq 1 ] && break
  attempt=$((attempt + 1)); sleep 2
done
[ "$healthy" -eq 1 ] || { echo 'health check failed; Java was not restarted, run rollback391.sh' >&2; exit 1; }

# Uses the new release's baked-in 0053 defaults, no overrides.
(cd "$release_root" && env -i PATH="${PATH:-/usr/sbin:/usr/bin:/sbin:/bin}" sh refactor/server-java/deploy/preflight.sh)
printf 'DEPLOY391_OK state=%s\n' "$state"
printf '%s\n' 'Rollback with: RELEASE_ROOT=<candidate release> sh refactor/server-java/deploy/rollback391.sh'
