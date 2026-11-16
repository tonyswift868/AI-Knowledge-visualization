#!/bin/sh
# Called only after the deployment, end-to-end checks, storage report, and test-data cleanup pass.
set -eu

: "${STATE:?set STATE to this deployment's state directory}"
: "${JAVA_IMAGE:?set JAVA_IMAGE to the Java image used by this deployment}"
docker_bin=${DOCKER_BIN:-docker}
migration_image=${MIGRATION_TOOL_IMAGE:-mentorflow-migration-tool:MF-275}
migration_base=${MIGRATION_IMAGE:-mentorflow-slim-backend:269-python-fallback}
metrics=$STATE/metrics.txt
work=$(mktemp -d "${TMPDIR:-/tmp}/mf-docker-cleanup406.XXXXXX")
trap 'rm -rf "$work"' 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

record() {
  label=$1
  shift
  printf '%s\n' "$label" | tee -a "$metrics"
  if "$@" >"$work/output" 2>&1; then
    cat "$work/output" | tee -a "$metrics"
  else
    code=$?
    cat "$work/output" | tee -a "$metrics" >&2
    return "$code"
  fi
}

snapshot() {
  phase=$1
  record "docker_system_df_${phase}" "$docker_bin" system df
  record "filesystem_root_${phase}" df -h /
}

mkdir -p "$STATE"
snapshot before

# Resolve all retention rules to IDs before pruning/removing anything. Protecting
# IDs (rather than tags) keeps every alias when one retained image has many tags.
"$docker_bin" image ls -a --no-trunc --format '{{.ID}} {{.Repository}}:{{.Tag}}' > "$work/images"
: > "$work/keep-ids"
java_found=0
migration_found=0
base_found=0
rollback_found=0
while IFS=' ' read -r image_id image_ref extra; do
  [ -n "$image_id" ] || continue
  [ -n "$image_ref" ] || { echo 'invalid docker image listing; stopping cleanup' >&2; exit 1; }
  [ -z "${extra:-}" ] || { echo 'invalid docker image listing; stopping cleanup' >&2; exit 1; }
  if [ "$image_ref" = "$JAVA_IMAGE" ]; then
    printf '%s\n' "$image_id" >> "$work/keep-ids"
    java_found=1
  fi
  if [ "$image_ref" = mentorflow-java-server:393 ]; then
    printf '%s\n' "$image_id" >> "$work/keep-ids"
    rollback_found=1
  fi
  if [ "$image_ref" = "$migration_image" ]; then
    printf '%s\n' "$image_id" >> "$work/keep-ids"
    migration_found=1
  fi
  if [ "$image_ref" = "$migration_base" ]; then
    printf '%s\n' "$image_id" >> "$work/keep-ids"
    base_found=1
  fi
done < "$work/images"
[ "$java_found" -eq 1 ] || { echo "deployed Java image not found: $JAVA_IMAGE; stopping cleanup" >&2; exit 1; }
[ "$rollback_found" -eq 1 ] || { echo 'Java rollback image not found: mentorflow-java-server:393; stopping cleanup' >&2; exit 1; }
[ "$migration_found" -eq 1 ] || { echo "migration tool image not found: $migration_image; stopping cleanup" >&2; exit 1; }
[ "$base_found" -eq 1 ] || { echo "migration base image not found: $migration_base; stopping cleanup" >&2; exit 1; }

"$docker_bin" ps -aq > "$work/containers"
while IFS= read -r container_id; do
  [ -n "$container_id" ] || continue
  "$docker_bin" inspect --format '{{.Image}}' "$container_id" >> "$work/keep-ids"
done < "$work/containers"

record 'docker_builder_prune_all' "$docker_bin" builder prune -a -f

sort -u "$work/keep-ids" -o "$work/keep-ids"
: > "$work/attempted-ids"
: > "$work/kept-ids"
removed=0
kept=0
failed=0
while IFS=' ' read -r image_id image_ref extra; do
  [ -n "$image_id" ] || continue
  if grep -Fxq "$image_id" "$work/keep-ids"; then
    printf 'image_keep id=%s ref=%s\n' "$image_id" "$image_ref" | tee -a "$metrics"
    if ! grep -Fxq "$image_id" "$work/kept-ids"; then
      printf '%s\n' "$image_id" >> "$work/kept-ids"
      kept=$((kept + 1))
    fi
  elif ! grep -Fxq "$image_id" "$work/attempted-ids"; then
    printf '%s\n' "$image_id" >> "$work/attempted-ids"
    if record "docker_image_remove id=$image_id ref=$image_ref" "$docker_bin" image rm "$image_id"; then
      removed=$((removed + 1))
    else
      printf 'image_remove_skipped id=%s ref=%s\n' "$image_id" "$image_ref" | tee -a "$metrics"
      failed=$((failed + 1))
    fi
  fi
done < "$work/images"

snapshot after
printf 'docker_cleanup406_removed=%s kept=%s remove_failed=%s\n' "$removed" "$kept" "$failed" | tee -a "$metrics"
