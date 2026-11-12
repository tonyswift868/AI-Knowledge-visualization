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
# Heartbeat only counts as healthy after the public edge reports HTTP 200.
# The URL is passed through curl config stdin so neither the ping URL nor the
# optional health URL is exposed in curl's argv or output.
heartbeat_health_ok() {
  [ "$event" = heartbeat ] || return 0
  health_url=${MF_HEARTBEAT_HEALTH_URL:-https://89.45.45.28/health/ready}
  connect_to=${MF_HEARTBEAT_CONNECT_TO:-}
  case "$health_url" in
    https://*) ;;
    *) return 1 ;;
  esac
  case "$health_url" in
    *[![:alnum:]/:?\&_=+.%#@~-]*) return 1 ;;
  esac
  if [ -n "$connect_to" ]; then
    case "$connect_to" in
      *[![:alnum:].:_\[\]-]*) return 1 ;;
    esac
  fi
  status=$(
    {
      printf 'url = "%s"\n' "$health_url"
      [ -n "$connect_to" ] && printf 'connect-to = "%s"\n' "$connect_to"
      printf 'fail\nsilent\nshow-error\nmax-time = 10\noutput = /dev/null\nwrite-out = "%%{http_code}"\n'
    } | "$curl_bin" --config - 2>/dev/null
  ) || return 1
  [ "$status" = 200 ]
}
ping_url=$url
if [ "$event" = heartbeat ] && ! heartbeat_health_ok; then
  ping_url="${url%/}/fail"
fi
{
  printf 'url = "%s"\n' "$ping_url"
  printf 'fail\nsilent\nshow-error\nmax-time = 10\nretry = 2\noutput = /dev/null\n'
} | "$curl_bin" --config - >/dev/null 2>&1 || fail_for_heartbeat
exit 0
