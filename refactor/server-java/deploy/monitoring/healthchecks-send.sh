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
curl_error_class() {
  case "$1" in
    5) printf 'proxy_resolve' ;;
    6) printf 'host_resolve' ;;
    7) printf 'connect' ;;
    22) printf 'http_error' ;;
    28) printf 'timeout' ;;
    35) printf 'tls' ;;
    60) printf 'tls' ;;
    *) printf 'curl_error' ;;
  esac
}
log_curl_failure() {
  phase=$1
  rc=$2
  if [ "$phase" = health ] && [ "${3:-}" = http ]; then
    printf 'healthchecks event=%s phase=health http_status=%s\n' "$event" "$4" >&2
  else
    printf 'healthchecks event=%s phase=%s curl_rc=%s class=%s\n' \
      "$event" "$phase" "$rc" "$(curl_error_class "$rc")" >&2
  fi
}
dns_probe_time_ms() {
  date +%s%3N 2>/dev/null || printf '0'
}
log_dns_probe() {
  name=$1
  shift
  start=$(dns_probe_time_ms)
  if command -v timeout >/dev/null 2>&1; then
    output=$(timeout 2 "$@" 2>/dev/null)
    rc=$?
  else
    output=
    rc=127
  fi
  end=$(dns_probe_time_ms)
  case "$start:$end" in
    *[!0-9:]*|:*) elapsed=0 ;;
    *) elapsed=$((end - start)) ; [ "$elapsed" -ge 0 ] || elapsed=0 ;;
  esac
  case "$name" in
    getent)
      case "$rc" in 0) state=resolved ;; 124|137) state=timeout ;; 127) state=unavailable ;; *) state=error ;; esac
      ;;
    *)
      state=$(printf '%s\n' "$output" | sed -n 's/.*status: \([A-Z0-9]*\).*/\1/p' | head -n 1)
      if [ -z "$state" ]; then
        case "$rc" in 124|137) state=timeout ;; 127) state=unavailable ;; *) state=error ;; esac
      fi
      ;;
  esac
  printf 'healthchecks event=%s phase=dns probe=%s state=%s elapsed_ms=%s\n' \
    "$event" "$name" "$state" "$elapsed" >&2
}
log_dns_diagnostics() {
  log_dns_probe getent getent hosts hc-ping.com & p1=$!
  log_dns_probe udp_1_1_1_1 dig +time=1 +tries=1 @1.1.1.1 hc-ping.com A & p2=$!
  log_dns_probe tcp_1_1_1_1 dig +tcp +time=1 +tries=1 @1.1.1.1 hc-ping.com A & p3=$!
  log_dns_probe udp_8_8_8_8 dig +time=1 +tries=1 @8.8.8.8 hc-ping.com A & p4=$!
  log_dns_probe tcp_8_8_8_8 dig +tcp +time=1 +tries=1 @8.8.8.8 hc-ping.com A & p5=$!
  wait "$p1" || :
  wait "$p2" || :
  wait "$p3" || :
  wait "$p4" || :
  wait "$p5" || :
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
  )
  health_rc=$?
  if [ "$health_rc" -ne 0 ] || [ "$status" != 200 ]; then
    if [ "$health_rc" -eq 22 ]; then
      case "$status" in
        [45][0-9][0-9]) log_curl_failure health "$health_rc" http "$status" ;;
        *) log_curl_failure health "$health_rc" ;;
      esac
    elif [ "$health_rc" -ne 0 ]; then
      log_curl_failure health "$health_rc"
    else
      case "$status" in
        [1-5][0-9][0-9]) log_curl_failure health "$health_rc" http "$status" ;;
        *) log_curl_failure health "$health_rc" ;;
      esac
    fi
    [ "$health_rc" -eq 6 ] && log_dns_diagnostics
    return 1
  fi
  return 0
}
ping_url=$url
if [ "$event" = heartbeat ] && ! heartbeat_health_ok; then
  ping_url="${url%/}/fail"
fi
{
  printf 'url = "%s"\n' "$ping_url"
  printf 'fail\nsilent\nshow-error\nmax-time = 10\nretry = 2\noutput = /dev/null\n'
} | "$curl_bin" --config - >/dev/null 2>&1
ping_rc=$?
if [ "$ping_rc" -ne 0 ]; then
  log_curl_failure ping "$ping_rc"
  [ "$ping_rc" -eq 6 ] && log_dns_diagnostics
  fail_for_heartbeat
fi
exit 0
