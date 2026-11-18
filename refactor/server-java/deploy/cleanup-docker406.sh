#!/bin/sh
# Called only after the deployment, end-to-end checks, storage report, and test-data cleanup pass.
set -eu

: "${STATE:?set STATE to this deployment state directory}"
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

# Images that MentorFlow systemd units and the scripts they start pass to
# docker run/create stay, even when no container uses them (e.g. --rm certbot).
# An unreadable unit or script stops cleanup before anything is removed.
unit_root=${SYSTEMD_UNIT_ROOT:-/etc/systemd/system}
script_root=${MF_SCRIPT_ROOT:-/opt/mentorflow}
: > "$work/unit-files"
for unit in "$unit_root"/mentorflow-*.service "$unit_root"/mentorflow-*.service.d/*.conf; do
  [ -e "$unit" ] || continue
  [ -f "$unit" ] && [ -r "$unit" ] || { echo "systemd unit unreadable: $unit; stopping cleanup" >&2; exit 1; }
  printf '%s\n' "$unit" >> "$work/unit-files"
done
[ -s "$work/unit-files" ] || { echo "no mentorflow systemd units under $unit_root; stopping cleanup" >&2; exit 1; }
: > "$work/exec-lines"
while IFS= read -r unit; do
  grep -h '^Exec' "$unit" >> "$work/exec-lines" || true
done < "$work/unit-files"
sed -e 's/^Exec[A-Za-z]*=[-@+!:]*//' "$work/exec-lines" | tr -s ' \t"\047' '\n' \
  | sed -n "\|^$script_root/|p" | sort -u > "$work/scripts"
: > "$work/script-files"
while IFS= read -r script; do
  [ -d "$script" ] && continue
  [ -f "$script" ] && [ -r "$script" ] || { echo "script unreadable or missing: $script; stopping cleanup" >&2; exit 1; }
  printf '%s\n' "$script" >> "$work/script-files"
done < "$work/scripts"
{ cat "$work/unit-files" "$work/script-files"; } | while IFS= read -r file; do
  sed -e ':a' -e '/\\$/N; s/\\\n//; ta' "$file"
done | grep -E '(^|[^A-Za-z0-9._-])docker([[:space:]]+container)?[[:space:]]+(run|create)([[:space:]]|$)' \
  > "$work/docker-lines" || true
tr -s ' \t"\047' '\n' < "$work/docker-lines" | sort -u > "$work/tokens"
while IFS= read -r ref; do
  case $ref in ''|-*|*[!A-Za-z0-9._/:@-]*|docker|run|create|container) continue ;; esac
  if image_id=$("$docker_bin" image inspect --format '{{.Id}}' "$ref" 2>/dev/null); then
    printf '%s\n' "$image_id" >> "$work/keep-ids"
    printf 'image_ref_keep ref=%s id=%s\n' "$ref" "$image_id" | tee -a "$metrics"
  else
    printf 'image_ref_not_local ref=%s ignored\n' "$ref" | tee -a "$metrics"
  fi
done < "$work/tokens"

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
