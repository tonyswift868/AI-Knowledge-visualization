#!/bin/sh
set -eu

action=${1:?usage: install-edge-logging393.sh check|install|rollback EDGE_CONF [BACKUP]}
conf=${2:?missing EDGE_CONF}
backup=${3:-}
container=${EDGE_CONTAINER:-mentorflow-e1-preview-edge-1}
edge_user=${EDGE_NGINX_USER:-101:101}
docker_bin=${DOCKER_BIN:-docker}
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cron_file=${CRON_FILE:-/etc/cron.d/mentorflow-edge-access-393}
rotate_helper=$here/rotate-edge-access393.sh
rollback_failed_marker=$backup.rollback-failed
docker_path=
logrotate_path=
edge_tmpdir=${EDGE_TMPDIR:-${TMPDIR:-/tmp}}
candidate_dir=/tmp/mentorflow-access393-check
candidate_path=$candidate_dir/nginx.conf.check
tmp=
cron_created=0
config_changed=0
backup_created=0
candidate_in_container=0

exec_nginx() { "$docker_bin" exec -u "$edge_user" "$container" nginx "$@"; }
restore_config() { cat -- "$backup" > "$conf"; }
cleanup_candidate() {
  if [ "$candidate_in_container" -eq 1 ]; then
    "$docker_bin" exec -u "$edge_user" "$container" sh -c "rm -f '$candidate_path'; rmdir '$candidate_dir' 2>/dev/null || true" >/dev/null 2>&1 || true
    candidate_in_container=0
  fi
}
check_candidate() {
  test -f "$conf"
  test -d "$edge_tmpdir"
  tmp=$(mktemp "$edge_tmpdir/mentorflow-edge393.XXXXXX")
  python3 "$here/edge-config393.py" apply "$conf" > "$tmp"
  docker_path=$(command -v "$docker_bin")
  logrotate_path=$(command -v logrotate)
  candidate_in_container=1
  "$docker_bin" exec -u "$edge_user" "$container" sh -c "mkdir -p '$candidate_dir' && chmod 0700 '$candidate_dir'"
  "$docker_bin" exec -u "$edge_user" -i "$container" sh -c "umask 077; cat > '$candidate_path'; chmod 0600 '$candidate_path'" < "$tmp"
  "$docker_bin" exec -u "$edge_user" "$container" nginx -t -p /etc/nginx -c "$candidate_path"
  "$docker_bin" exec -u "$edge_user" "$container" sh -c 'set -eu; file=/tmp/mentorflow-access393.log; test -f "$file"; : >> "$file"; chmod 0640 "$file"'
  DOCKER_BIN="$docker_path" LOGROTATE_BIN="$logrotate_path" EDGE_CONTAINER="$container" sh "$rotate_helper" check
  cleanup_candidate
  rm -f -- "$tmp"
  tmp=
}
restore_and_reload() {
  restore_config || return 1
  exec_nginx -t || return 2
  exec_nginx -s reload || return 3
  return 0
}
report_rollback_failure() {
  reason=$1
  printf 'reason=%s\n' "$reason" > "$rollback_failed_marker" || true
  printf 'EDGE393_ROLLBACK_FAILED reason=%s backup=%s\n' "$reason" "$backup" >&2
}
finish() {
  rc=$?
  trap - EXIT HUP INT TERM
  if [ "$action" = install ] && [ "$rc" -ne 0 ]; then
    if [ "$config_changed" -eq 1 ]; then
      if restore_and_reload; then
        [ "$cron_created" -eq 0 ] || rm -f -- "$cron_file"
      else
        recovery_rc=$?
        case "$recovery_rc" in
          1) report_rollback_failure restore-config ;;
          2) report_rollback_failure nginx-test ;;
          *) report_rollback_failure nginx-reload ;;
        esac
        rc=70
      fi
    else
      [ "$cron_created" -eq 0 ] || rm -f -- "$cron_file"
      [ "$backup_created" -eq 0 ] || rm -f -- "$backup"
    fi
  fi
  cleanup_candidate
  [ -z "$tmp" ] || rm -f -- "$tmp"
  exit "$rc"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

case "$action" in
  check)
    test -s "$rotate_helper"
    command -v python3 >/dev/null 2>&1
    command -v logrotate >/dev/null 2>&1
    command -v "$docker_bin" >/dev/null 2>&1
    check_candidate
    ;;
  install)
    test -n "$backup" || { echo 'missing BACKUP' >&2; exit 2; }
    test -f "$conf"
    test ! -e "$backup"
    test ! -e "$rollback_failed_marker"
    test ! -e "$cron_file"
    test -s "$rotate_helper"
    command -v python3 >/dev/null 2>&1
    command -v logrotate >/dev/null 2>&1
    command -v "$docker_bin" >/dev/null 2>&1
    umask 077
    check_candidate
    tmp=$(mktemp "$edge_tmpdir/mentorflow-edge393.XXXXXX")
    python3 "$here/edge-config393.py" apply "$conf" > "$tmp"
    backup_created=1
    cp -p -- "$conf" "$backup"
    cron_created=1
    cat > "$cron_file" <<EOF
* * * * * root DOCKER_BIN=$docker_path LOGROTATE_BIN=$logrotate_path EDGE_CONTAINER=$container /bin/sh $rotate_helper rotate >/dev/null 2>&1
EOF
    chmod 0644 "$cron_file"
    config_changed=1
    cat -- "$tmp" > "$conf"
    exec_nginx -t
    exec_nginx -s reload
    config_changed=0
    ;;
  rollback)
    test -n "$backup" || { echo 'missing BACKUP' >&2; exit 2; }
    test -s "$backup"
    if restore_and_reload; then :; else
      recovery_rc=$?
      case "$recovery_rc" in
        1) report_rollback_failure restore-config ;;
        2) report_rollback_failure nginx-test ;;
        *) report_rollback_failure nginx-reload ;;
      esac
      exit 70
    fi
    rm -f -- "$cron_file"
    ;;
  *) echo 'usage: install-edge-logging393.sh check|install|rollback EDGE_CONF [BACKUP]' >&2; exit 2;;
esac
