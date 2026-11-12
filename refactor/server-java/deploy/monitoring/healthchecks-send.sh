#!/bin/sh
set -u
event=${1:-}
env_file=${HEALTHCHECKS_ENV_FILE:-/etc/mentorflow/monitoring/healthchecks.env}
curl_bin=${CURL_BIN:-curl}
case "$event" in heartbeat) key=HEALTHCHECKS_HEARTBEAT_URL ;; backup) key=HEALTHCHECKS_BACKUP_URL ;; cert) key=HEALTHCHECKS_CERT_URL ;; *) exit 0 ;; esac
fail_for_heartbeat() {
  [ "$event" = heartbeat ] && exit 1
  exit 0
}
[ -r "$env_file" ] || fail_for_heartbeat
url=$(awk -F= -v k="$key" '$1 == k { sub(/^[^=]*=/, ""); print; exit }' "$env_file" 2>/dev/null) || fail_for_heartbeat
[ -n "$url" ] || fail_for_heartbeat
# Keep the bearer URL out of curl's argv by passing a config on stdin.
case "$url" in
  https://*) ;;
  *) fail_for_heartbeat ;;
esac
case "$url" in
  *[![:alnum:]/:?\&_=+.%#@~-]*) fail_for_heartbeat ;;
esac
{
  printf 'url = "%s"\n' "$url"
  printf 'fail\nsilent\nshow-error\nmax-time = 10\nretry = 2\noutput = /dev/null\n'
} | "$curl_bin" --config - >/dev/null 2>&1 || fail_for_heartbeat
exit 0
