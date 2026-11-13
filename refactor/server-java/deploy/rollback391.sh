#!/bin/sh
# Remove only the 0053 index; the Java service is not restarted.
set -eu
release_root=${RELEASE_ROOT:?set RELEASE_ROOT to the 0053 source release}
deploy_dir=$release_root/refactor/server-java/deploy
docker_bin=${DOCKER_BIN:-docker}
current=$(DOCKER_BIN="$docker_bin" sh "$deploy_dir/migrate.sh" current)
case "$current" in
  *0053_workbench_question_index*) ;;
  *0052_workbench_account_rows*) printf '%s\n' 'ROLLBACK391_OK already_at_0052'; exit 0 ;;
  *) echo 'rollback refused: current revision is not 0053 or 0052' >&2; exit 1 ;;
esac
DOCKER_BIN="$docker_bin" sh "$deploy_dir/migrate.sh" downgrade 0052_workbench_account_rows
revision=$(DOCKER_BIN="$docker_bin" sh "$deploy_dir/migrate.sh" current | tail -n 1 | tr -d '\r')
case "$revision" in *0052_workbench_account_rows*) printf '%s\n' 'ROLLBACK391_OK revision=0052_workbench_account_rows';; *) echo 'rollback revision verification failed' >&2; exit 1;; esac
