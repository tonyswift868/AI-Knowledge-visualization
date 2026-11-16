#!/bin/sh
set -eu

edge_conf=${EDGE_CONF:-/opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf}
state_dir=${STATE_DIR:-/opt/mentorflow/state/download416}
container=${EDGE_CONTAINER:-mentorflow-e1-preview-edge-1}
docker_bin=${DOCKER_BIN:-docker}
sha256sum_bin=${SHA256SUM_BIN:-sha256sum}
stat_bin=${STAT_BIN:-stat}
backup=$state_dir/edge.conf.before
container_conf=${CONTAINER_EDGE_CONF:-/etc/nginx/nginx.conf}
new_url=https://aiteacher.clannad.win/updates/app/releases/bootstrap/MentorFlow-Setup.exe

hash_file() { "$sha256sum_bin" "$1" | awk '{print $1}'; }
inode_file() { "$stat_bin" -c '%i' "$1"; }
same_inode() {
  [ "$(inode_file "$edge_conf")" = "$before_inode" ] || {
    printf '%s\n' 'DOWNLOAD416_ERROR active_config_inode_changed' >&2
    return 1
  }
}
write_backup_in_place() {
  cat "$backup" > "$edge_conf" || return 1
  same_inode
}
test_nginx() { "$docker_bin" exec "$container" nginx -t; }
reload_nginx() { "$docker_bin" exec "$container" nginx -s reload; }
check_route_to() {
  "$docker_bin" exec "$container" sh -c \
    "awk -v expected='$1' '
      /^[[:space:]]*location[[:space:]]*=[[:space:]]*\/download[[:space:]]*\\{/ { blocks++; inside=1; next }
      inside { line=\$0; sub(/^[[:space:]]*/, \"\", line); sub(/[[:space:]]*$/, \"\", line);
        if (line == \"return 302 \" expected \";\") matches++;
        if (line ~ /^}/) inside=0 }
      END { exit !(blocks == 1 && matches == 1) }
    ' '$container_conf'"
}
rollback_after_failure() {
  printf '%s\n' 'DOWNLOAD416_ROLLBACK starting' >&2
  write_backup_in_place || return 1
  test_nginx || return 1
  reload_nginx || return 1
  printf '%s\n' 'DOWNLOAD416_ROLLBACK_OK' >&2
}

case "${1:-}" in
  switch)
    [ "$#" -eq 3 ] || { printf 'usage: %s switch EXPECTED_SHA256 CANDIDATE\n' "$0" >&2; exit 2; }
    expected_sha=$2
    candidate=$3
    [ -f "$edge_conf" ] && [ -f "$candidate" ] || { printf '%s\n' 'DOWNLOAD416_ERROR missing_config_or_candidate' >&2; exit 2; }
    actual_sha=$(hash_file "$edge_conf")
    [ "$actual_sha" = "$expected_sha" ] || { printf '%s\n' 'DOWNLOAD416_REFUSED active_sha256_mismatch' >&2; exit 3; }
    mkdir -p "$state_dir"
    [ ! -e "$backup" ] && [ ! -L "$backup" ] || { printf '%s\n' 'DOWNLOAD416_REFUSED backup_already_exists' >&2; exit 3; }
    before_inode=$(inode_file "$edge_conf")
    cat "$edge_conf" > "$backup"
    [ "$(hash_file "$backup")" = "$expected_sha" ] || { printf '%s\n' 'DOWNLOAD416_ERROR backup_sha256_mismatch' >&2; exit 1; }
    printf 'DOWNLOAD416_BACKUP=%s\nDOWNLOAD416_INODE_BEFORE=%s\n' "$backup" "$before_inode"
    if ! cat "$candidate" > "$edge_conf" || ! same_inode; then
      rollback_after_failure || printf '%s\n' 'DOWNLOAD416_ROLLBACK_FAILED inspect_server_immediately' >&2
      exit 1
    fi
    after_inode=$(inode_file "$edge_conf")
    printf 'DOWNLOAD416_INODE_AFTER=%s\n' "$after_inode"
    if ! test_nginx; then
      write_backup_in_place
      printf '%s\n' 'DOWNLOAD416_RESTORED_NO_RELOAD' >&2
      exit 1
    fi
    if ! reload_nginx || ! check_route_to "$new_url"; then
      rollback_after_failure || printf '%s\n' 'DOWNLOAD416_ROLLBACK_FAILED inspect_server_immediately' >&2
      exit 1
    fi
    printf 'DOWNLOAD416_SWITCH_OK route=%s\n' "$new_url"
    ;;
  rollback)
    [ "$#" -eq 1 ] || { printf 'usage: %s rollback\n' "$0" >&2; exit 2; }
    [ -f "$edge_conf" ] && [ -f "$backup" ] || { printf '%s\n' 'DOWNLOAD416_ERROR missing_active_or_backup' >&2; exit 2; }
    before_inode=$(inode_file "$edge_conf")
    printf 'DOWNLOAD416_ROLLBACK_INODE_BEFORE=%s\n' "$before_inode"
    write_backup_in_place
    printf 'DOWNLOAD416_ROLLBACK_INODE_AFTER=%s\n' "$(inode_file "$edge_conf")"
    test_nginx
    reload_nginx
    check_route_to /releases/latest/MentorFlow-Setup.exe
    printf '%s\n' 'DOWNLOAD416_MANUAL_ROLLBACK_OK'
    ;;
  *)
    printf 'usage: %s switch EXPECTED_SHA256 CANDIDATE | rollback\n' "$0" >&2
    exit 2
    ;;
esac
