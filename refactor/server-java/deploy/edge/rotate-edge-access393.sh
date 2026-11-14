#!/bin/sh
set -eu

action=${1:-rotate}
container=${EDGE_CONTAINER:-mentorflow-e1-preview-edge-1}
docker_bin=${DOCKER_BIN:-docker}
logrotate_bin=${LOGROTATE_BIN:-logrotate}
proc_root=${PROC_ROOT:-/proc}
status_file=${LOGROTATE_STATUS:-/var/lib/logrotate/status}
tmp_dir=${ROTATE_TMPDIR:-/run}
case "$action" in check|rotate) ;; *) echo 'usage: rotate-edge-access393.sh [check|rotate]' >&2; exit 2 ;; esac
command -v "$docker_bin" >/dev/null 2>&1
command -v "$logrotate_bin" >/dev/null 2>&1
pid=$("$docker_bin" inspect --format '{{.State.Pid}}' "$container")
case "$pid" in ''|*[!0-9]*|0) echo 'edge container has no running pid' >&2; exit 1 ;; esac
log_file=$proc_root/$pid/root/var/log/nginx/mentorflow-access.log
test -f "$log_file"
docker_path=$(command -v "$docker_bin")
logrotate_path=$(command -v "$logrotate_bin")
config=$(mktemp "$tmp_dir/mentorflow-edge-access393.XXXXXX")
trap 'rm -f -- "$config"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
cat > "$config" <<EOF
$log_file {
    size 1M
    rotate 7
    missingok
    notifempty
    compress
    delaycompress
    create 0640 root root
    sharedscripts
    postrotate
        $docker_path exec $container nginx -s reopen
    endscript
}
EOF
if [ "$action" = check ]; then
  "$logrotate_path" -d -s "$status_file" "$config" >/dev/null
else
  "$logrotate_path" -s "$status_file" "$config"
fi
