#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
compose_file=${MONITORING_COMPOSE_FILE:-$script_dir/compose.yml}
compose_project=${MONITORING_COMPOSE_PROJECT:-mentorflow-monitoring}
docker_bin=${DOCKER_BIN:-docker}; systemctl_bin=${SYSTEMCTL_BIN:-systemctl}
ss_bin=${SS_BIN:-ss}; ufw_bin=${UFW_BIN:-ufw}
monitor_script=${MONITOR_SCRIPT:-$script_dir/ensure-monitors.sh}
unit_root=${SYSTEMD_UNIT_ROOT:-/etc/systemd/system}
install_root=${MONITORING_INSTALL_ROOT:-/opt/mentorflow/monitoring}
rollback_state=${MONITORING_ROLLBACK_STATE:-$install_root/.385-preinstall}
agent_env=${BESZEL_AGENT_ENV_FILE:-/etc/mentorflow/monitoring/beszel-agent.env}
beszel_volume=${BESZEL_HUB_VOLUME:-mentorflow_monitoring_beszel_data}
ssh_keygen_bin=${SSH_KEYGEN_BIN:-ssh-keygen}
agent_env_tmp=
agent_env_created=0
die() { printf 'monitoring install: %s\n' "$*" >&2; exit 1; }
run() { "$@" || die "command failed: $*"; }
install_complete=0
up_started=0
cleanup_on_exit() {
  rc=$?
  [ -z "$agent_env_tmp" ] || rm -f "$agent_env_tmp"
  if [ "$install_complete" -eq 0 ] && [ "$up_started" -eq 1 ]; then
    printf '%s\n' 'monitoring install failed; attempting rollback' >&2
    MONITORING_ROLLBACK_STATE="$rollback_state" "$script_dir/rollback-monitoring.sh" || printf '%s\n' 'monitoring rollback also failed; run rollback-monitoring.sh manually' >&2
  fi
  if [ "$install_complete" -eq 0 ] && [ "$agent_env_created" -eq 1 ] && [ -e "$agent_env" ]; then
    rm -f "$agent_env"
  fi
  exit "$rc"
}
trap cleanup_on_exit EXIT
measure() {
  printf '%s\n' "--- $1 ---"
  command -v free >/dev/null 2>&1 && free -h || true
  command -v nproc >/dev/null 2>&1 && nproc || true
  command -v df >/dev/null 2>&1 && df -h / || true
  "$ss_bin" -ltnp 2>/dev/null || true
  "$docker_bin" stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' 2>/dev/null || true
}
listen_ok() {
  output=$($ss_bin -ltnp 2>/dev/null || true)
  for port in 3001 8090; do
    printf '%s\n' "$output" | awk -v p=":$port" '$4 ~ "^127\\.0\\.0\\.1" p "$" { found=1 } END { exit(found ? 0 : 1) }' || die "expected 127.0.0.1:$port listener is absent"
    if printf '%s\n' "$output" | awk -v p=":$port" '$4 ~ "^(0\\.0\\.0\\.0|\\[::\\])" p "$" { found=1 } END { exit(found ? 0 : 1) }'; then die "port $port is exposed beyond loopback"; fi
  done
}
list_listeners() {
  "$ss_bin" -ltnp 2>/dev/null | awk 'NR > 1 { print $4 }' | sort -u
}
check_no_new_listeners() {
  before=$1
  after=$2
  for endpoint in $after; do
    if ! printf '%s\n' "$before" | grep -Fqx "$endpoint"; then
      case "$endpoint" in
        127.0.0.1:3001|127.0.0.1:8090) ;;
        *) die "unexpected new TCP listener: $endpoint" ;;
      esac
    fi
  done
}
[ -r "$compose_file" ] || die "compose file missing: $compose_file"
[ -r "$monitor_script" ] || die "monitor script missing: $monitor_script"
run mkdir -p "$(dirname -- "$agent_env")"
if [ ! -e "$agent_env" ]; then
  umask 077
  run sh -c ': > "$1"' sh "$agent_env"
  agent_env_created=1
fi
run chmod 0600 "$agent_env"
measure before
listeners_before=$(list_listeners)
ufw_before=$($ufw_bin status 2>/dev/null) || die 'ufw status failed; cannot prove firewall rules are unchanged'
run "$docker_bin" compose -p "$compose_project" -f "$compose_file" config --quiet
run "$docker_bin" compose -p "$compose_project" -f "$compose_file" pull
up_started=1
run "$docker_bin" compose -p "$compose_project" -f "$compose_file" up -d uptime-kuma beszel-hub
run mkdir -p "$install_root"
if [ -e "$rollback_state" ]; then
  die "rollback state already exists: $rollback_state (run rollback-monitoring.sh first)"
fi
run mkdir -p "$rollback_state"
run chmod 0700 "$rollback_state"
backup_target() {
  target=$1
  slot=$2
  if [ -e "$target" ] || [ -L "$target" ]; then
    run cp -a "$target" "$rollback_state/$slot"
    printf 'present|%s|%s\n' "$target" "$rollback_state/$slot" >> "$rollback_state/manifest" || die 'cannot record rollback state'
  else
    printf 'absent|%s|\n' "$target" >> "$rollback_state/manifest" || die 'cannot record rollback state'
  fi
}
backup_target "$install_root/healthchecks-send.sh" healthchecks-send.sh
if [ "$agent_env_created" -eq 1 ]; then
  run rm -f "$agent_env"
fi
backup_target "$agent_env" beszel-agent.env
backup_target "$unit_root/mentorflow-monitor-heartbeat.service" mentorflow-monitor-heartbeat.service
backup_target "$unit_root/mentorflow-monitor-heartbeat.timer" mentorflow-monitor-heartbeat.timer
backup_target "$unit_root/mentorflow-db-backup.service.d/healthchecks.conf" db-backup-healthchecks.conf
backup_target "$unit_root/mentorflow-e108-ip-renew.service.d/healthchecks.conf" e108-ip-renew-healthchecks.conf
hub_mount=$("$docker_bin" volume inspect -f '{{.Mountpoint}}' "$beszel_volume" 2>/dev/null) || die "Beszel hub volume missing: $beszel_volume"
[ -n "$hub_mount" ] || die "Beszel hub volume mountpoint is empty: $beszel_volume"
if [ -r "$hub_mount/id_ed25519.pub" ]; then
  hub_key=$(cat "$hub_mount/id_ed25519.pub" 2>/dev/null) || die 'Beszel hub public key is not available in its data volume'
  hub_key_source=public-file
elif [ -r "$hub_mount/id_ed25519" ]; then
  command -v "$ssh_keygen_bin" >/dev/null 2>&1 || die 'ssh-keygen is required to derive the Beszel hub public key'
  hub_key=$("$ssh_keygen_bin" -y -P '' -f "$hub_mount/id_ed25519" 2>/dev/null) || die 'Beszel hub public key could not be derived from its data volume'
  hub_key_source=derived-from-private
else
  die 'Beszel hub public key is not available in its data volume'
fi
[ -n "$hub_key" ] || die 'Beszel hub public key is empty'
printf '%s\n' "$hub_key" | grep -Eq '^ssh-ed25519 [A-Za-z0-9+/=]+( .*)?$' || die 'Beszel hub public key has an unexpected format'
printf 'Beszel hub public key loaded (%s)\n' "$hub_key_source"
if [ ! -e "$agent_env" ]; then
  umask 077
  run sh -c ': > "$1"' sh "$agent_env"
fi
agent_env_tmp=$agent_env.$$
grep -v '^KEY=' "$agent_env" > "$agent_env_tmp" || true
printf 'KEY=%s\n' "$hub_key" >> "$agent_env_tmp"
run chmod 0600 "$agent_env_tmp"
run mv "$agent_env_tmp" "$agent_env"
run "$docker_bin" compose -p "$compose_project" -f "$compose_file" up -d beszel-agent
listen_ok
listeners_after=$(list_listeners)
check_no_new_listeners "$listeners_before" "$listeners_after"
ufw_after=$($ufw_bin status 2>/dev/null) || die 'ufw status failed after install'
[ "$ufw_before" = "$ufw_after" ] || die 'UFW output changed during monitoring install'
run "$monitor_script"
run cp "$script_dir/healthchecks-send.sh" "$install_root/healthchecks-send.sh"
run chmod 0755 "$install_root/healthchecks-send.sh"
run mkdir -p "$unit_root/mentorflow-db-backup.service.d" "$unit_root/mentorflow-e108-ip-renew.service.d"
run cp "$script_dir/systemd/mentorflow-monitor-heartbeat.service" "$unit_root/mentorflow-monitor-heartbeat.service"
run cp "$script_dir/systemd/mentorflow-monitor-heartbeat.timer" "$unit_root/mentorflow-monitor-heartbeat.timer"
run cp "$script_dir/systemd/mentorflow-db-backup.service.d/healthchecks.conf" "$unit_root/mentorflow-db-backup.service.d/healthchecks.conf"
run cp "$script_dir/systemd/mentorflow-e108-ip-renew.service.d/healthchecks.conf" "$unit_root/mentorflow-e108-ip-renew.service.d/healthchecks.conf"
run "$systemctl_bin" daemon-reload
run "$systemctl_bin" enable --now mentorflow-monitor-heartbeat.timer
measure after
install_complete=1
printf '%s\n' 'monitoring install complete'
