#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
compose_file=${MONITORING_COMPOSE_FILE:-$script_dir/compose.yml}
compose_project=${MONITORING_COMPOSE_PROJECT:-mentorflow-monitoring}
docker_bin=${DOCKER_BIN:-docker}; systemctl_bin=${SYSTEMCTL_BIN:-systemctl}
unit_root=${SYSTEMD_UNIT_ROOT:-/etc/systemd/system}
install_root=${MONITORING_INSTALL_ROOT:-/opt/mentorflow/monitoring}
rollback_state=${MONITORING_ROLLBACK_STATE:-$install_root/.385-preinstall}
die() { printf 'monitoring rollback: %s\n' "$*" >&2; exit 1; }
run() { "$@" || die "command failed: $*"; }
if "$systemctl_bin" is-active --quiet mentorflow-monitor-heartbeat.timer 2>/dev/null || "$systemctl_bin" is-enabled --quiet mentorflow-monitor-heartbeat.timer 2>/dev/null; then
  run "$systemctl_bin" disable --now mentorflow-monitor-heartbeat.timer
fi
run "$systemctl_bin" daemon-reload
run "$docker_bin" compose -p "$compose_project" -f "$compose_file" stop
run "$docker_bin" compose -p "$compose_project" -f "$compose_file" rm -f
if [ -r "$rollback_state/manifest" ]; then
  while IFS='|' read -r state target backup; do
    [ -n "$target" ] || continue
    if [ "$state" = present ]; then
      run mkdir -p "$(dirname -- "$target")"
      run cp -a "$backup" "$target"
    else
      if [ -e "$target" ] || [ -L "$target" ]; then run rm -f "$target"; fi
    fi
  done < "$rollback_state/manifest"
else
  printf '%s\n' 'rollback state absent; leaving existing unit/helper files untouched' >&2
fi
for dir in "$unit_root/mentorflow-db-backup.service.d" "$unit_root/mentorflow-e108-ip-renew.service.d"; do rmdir "$dir" 2>/dev/null || true; done
if [ -r "$rollback_state/manifest" ]; then
  for backup in "$rollback_state/healthchecks-send.sh" "$rollback_state/mentorflow-monitor-heartbeat.service" "$rollback_state/mentorflow-monitor-heartbeat.timer" "$rollback_state/db-backup-healthchecks.conf" "$rollback_state/e108-ip-renew-healthchecks.conf" "$rollback_state/manifest"; do
    [ ! -e "$backup" ] || run rm -f "$backup"
  done
  rmdir "$rollback_state" 2>/dev/null || true
fi
rmdir "$install_root" 2>/dev/null || true
run "$systemctl_bin" daemon-reload
printf '%s\n' 'monitoring rollback complete; data volumes were retained'
