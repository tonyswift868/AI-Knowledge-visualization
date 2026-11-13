#!/bin/sh
set -eu

docker_bin=${DOCKER_BIN:-docker}
java_container=${JAVA_CONTAINER:-mentorflow-java-backend}
proxyd_bin=${SYSTEMD_SOCKET_PROXYD:-/usr/lib/systemd/systemd-socket-proxyd}
format='{{with index .NetworkSettings.Networks "mentorflow-e1-preview_internal"}}{{.IPAddress}}{{end}}'
ip=$($docker_bin inspect --format "$format" "$java_container")

printf '%s\n' "$ip" | awk -F. '
  NF != 4 { exit 1 }
  { for (i = 1; i <= 4; i++) if ($i !~ /^[0-9]+$/ || $i > 255) exit 1 }
  $0 == "0.0.0.0" || $1 == 127 { exit 1 }
' || { echo 'Java container has no valid IPv4 address on the internal network' >&2; exit 1; }

test -x "$proxyd_bin" || { echo 'systemd-socket-proxyd is unavailable' >&2; exit 1; }
exec "$proxyd_bin" --exit-idle-time=30s "$ip:8081"
