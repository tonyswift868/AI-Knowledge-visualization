#!/bin/sh
set -eu

action=${1:-}
unit=mentorflow-account-page-proxy
unit_dir=${SYSTEMD_UNIT_DIR:-/etc/systemd/system}
state=${STATE:-}
socket_file=$unit_dir/$unit.socket
service_file=$unit_dir/$unit.service
socket_marker=${state:+$state/account-page-proxy.socket.inode}
service_marker=${state:+$state/account-page-proxy.service.inode}
socket_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/account-page-proxy.socket
service_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/account-page-proxy.service.in

fail() { echo "$1" >&2; exit 1; }
page_ready() {
  attempt=0
  while [ "$attempt" -lt 30 ]; do
    code=$("$CURL_BIN" -fsS --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/admin/accounts) || code=000
    [ "$code" = 200 ] && return 0
    attempt=$((attempt + 1))
    sleep 2
  done
  echo 'account page proxy did not become healthy' >&2
  return 1
}
remove_owned_file() {
  file=$1 marker=$2
  if [ -f "$marker" ]; then
    inode=$(cat "$marker")
    if [ -e "$file" ] && [ "$(stat -c %i "$file")" = "$inode" ]; then rm -f "$file"; fi
    rm -f "$marker"
  fi
}
remove_owned_units() {
  if [ -n "$socket_marker" ] && [ -f "$socket_marker" ]; then
    "$SYSTEMCTL_BIN" disable --now "$unit.socket"
  fi
  if [ -n "$service_marker" ] && [ -f "$service_marker" ]; then
    "$SYSTEMCTL_BIN" stop "$unit.service"
  fi
  remove_owned_file "$socket_file" "$socket_marker"
  remove_owned_file "$service_file" "$service_marker"
  if [ -n "$state" ]; then "$SYSTEMCTL_BIN" daemon-reload; fi
}
create_unit() {
  source=$1 target=$2 marker=$3
  test ! -e "$target" || fail "refusing to overwrite existing unit file: $target"
  test ! -e "$marker" || fail "ownership marker already exists: $marker"
  temp=$(mktemp "$unit_dir/.mentorflow-account-page.XXXXXX")
  if [ "$source" = "$service_source" ]; then
    sed "s|@RELEASE_ROOT@|$release_root|g" "$source" > "$temp"
  else
    cp "$source" "$temp"
  fi
  chmod 0644 "$temp"
  inode=$(stat -c %i "$temp")
  printf '%s\n' "$inode" > "$marker"
  if ! ln "$temp" "$target"; then
    rm -f "$marker" "$temp"
    fail "refusing to replace existing unit file: $target"
  fi
  rm -f "$temp"
}

case "$action" in
  preflight)
    for name in "$unit.socket" "$unit.service"; do
      load_state=$("${SYSTEMCTL_BIN:-systemctl}" show --property=LoadState --value "$name") || fail 'systemd is unavailable'
      [ "$load_state" = not-found ] || fail "existing systemd unit found: $name"
    done
    for path in "${SYSTEMD_UNIT_DIR:-/etc/systemd/system}/$unit.socket" "${SYSTEMD_UNIT_DIR:-/etc/systemd/system}/$unit.service"; do
      [ ! -e "$path" ] || fail "refusing to overwrite existing unit file: $path"
    done
    ;;
  install)
    : "${RELEASE_ROOT:?set RELEASE_ROOT to the deployed Java source directory}"
    : "${STATE:?set STATE to this deployment's fresh state directory}"
    release_root=$RELEASE_ROOT
    case "$RELEASE_ROOT" in *[!A-Za-z0-9_./-]*) fail 'RELEASE_ROOT contains unsupported characters' ;; esac
    SYSTEMCTL_BIN=${SYSTEMCTL_BIN:-systemctl}
    CURL_BIN=${CURL_BIN:-curl}
    command -v "$SYSTEMCTL_BIN" >/dev/null 2>&1 || fail 'systemctl is unavailable'
    command -v "$CURL_BIN" >/dev/null 2>&1 || fail 'curl is unavailable'
    proxyd_bin=${SYSTEMD_SOCKET_PROXYD:-/usr/lib/systemd/systemd-socket-proxyd}
    test -x "$proxyd_bin" || fail 'systemd-socket-proxyd is unavailable'
    test -d "$unit_dir" || fail 'systemd unit directory is unavailable'
    test -f "$socket_source" && test -f "$service_source" && test -f "$(dirname -- "$socket_source")/account-page-proxy.sh" || fail 'proxy source files are missing'
    mkdir -p "$state"
    for marker in "$socket_marker" "$service_marker"; do test ! -e "$marker" || fail 'deployment proxy ownership marker already exists'; done
    sh "$0" preflight
    if create_unit "$socket_source" "$socket_file" "$socket_marker" && create_unit "$service_source" "$service_file" "$service_marker"; then
      if "$SYSTEMCTL_BIN" daemon-reload && "$SYSTEMCTL_BIN" enable --now "$unit.socket" && page_ready && \
        "$SYSTEMCTL_BIN" try-restart "$unit.service" && page_ready; then
        exit 0
      fi
      remove_owned_units
      fail 'account page proxy did not install and pass its health check'
    else
      remove_owned_units
      fail 'could not install account page proxy units'
    fi
    ;;
  refresh)
    : "${STATE:?set STATE to the installation's owner state directory}"
    SYSTEMCTL_BIN=${SYSTEMCTL_BIN:-systemctl}
    CURL_BIN=${CURL_BIN:-curl}
    test -f "$socket_marker" && test -f "$service_marker" || fail 'account page proxy units are not owned by this deployment'
    "$SYSTEMCTL_BIN" try-restart "$unit.service"
    page_ready
    ;;
  remove)
    SYSTEMCTL_BIN=${SYSTEMCTL_BIN:-systemctl}
    remove_owned_units
    ;;
  *) fail 'usage: install-account-page-proxy.sh preflight|install|refresh|remove' ;;
esac
