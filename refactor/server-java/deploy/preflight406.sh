#!/bin/sh
# 406's final no-argument preflight targets its source bundle and schema head.
set -eu

release_root=${RELEASE_ROOT:-/opt/mentorflow/releases/java-406}
deploy_dir=${DEPLOY_DIR:-$release_root/refactor/server-java/deploy}
manifest=${SOURCE_SHA256_MANIFEST:-/opt/mentorflow/state/release406/source.sha256}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec env RELEASE_ROOT="$release_root" DEPLOY_DIR="$deploy_dir" \
  SOURCE_SHA256_MANIFEST="$manifest" JAVA_IMAGE="${JAVA_IMAGE:-mentorflow-java-server:406}" \
  EXPECTED_ALEMBIC_REVISION="${EXPECTED_ALEMBIC_REVISION:-0056_closure_session_cleanup}" \
  PREFLIGHT_SOURCE_REVISION="${PREFLIGHT_SOURCE_REVISION:-0056_closure_session_cleanup}" \
  sh "$script_dir/preflight.sh" "$@"
