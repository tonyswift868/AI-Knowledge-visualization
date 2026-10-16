#!/bin/sh
# Point the existing edge upstream at Java or Python. The bind-mounted config
# must be rewritten through the existing inode (cat >), never replaced.
set -eu

mode=${1:-}
case "$mode" in
  java)
    to='server java-backend:8080 resolve;'
    ;;
  python)
    to='server backend:8000 resolve;'
    ;;
  status) ;;
  *) echo "usage: $0 java|python" >&2; exit 2 ;;
esac

edge_conf=${EDGE_CONF_PATH:-/opt/mentorflow/artifacts/r1-a-functional-20260917/edge.conf}
edge_container=${EDGE_CONTAINER:-mentorflow-e1-preview-edge-1}
docker_bin=${DOCKER_BIN:-docker}
[ -f "$edge_conf" ] || { echo "edge config not found: $edge_conf" >&2; exit 1; }

dir=$(dirname "$edge_conf")
current=$(awk '
  {
    line=$0
    sub(/^[[:space:]]+/, "", line)
    sub(/[[:space:]]+$/, "", line)
    if (line == "upstream mentorflow_account_core_backend {") {
      blocks++
      active=1
      next
    }
    if (active && line == "}") { active=0; next }
    if (active && line ~ /^server[[:space:]]/) {
      servers++
      if (line == "server backend:8000 resolve;" ||
          line == "server mentorflow-java-shadow:8080 resolve;" ||
          line == "server java-backend:8080 resolve;") {
        matches++
        found=$0
      }
    }
  }
  END {
    if (blocks == 1 && servers == 1 && matches == 1) print found
    else exit 1
  }
' "$edge_conf") || {
  echo "expected one supported server directive in the unique account-core upstream block" >&2
  exit 1
}
normalized=$(printf '%s\n' "$current" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
case "$normalized" in
  'server backend:8000 resolve;') state=python ;;
  'server mentorflow-java-shadow:8080 resolve;') state=legacy-java ;;
  'server java-backend:8080 resolve;') state=java ;;
esac
if [ "$mode" = status ]; then
  printf '%s\n' "$state"
  exit 0
fi
if [ "$normalized" = "$to" ]; then
  echo "edge upstream already points to $mode"
  exit 0
fi
from=$current
backup=$(mktemp "$dir/.edge.conf.rollback.XXXXXX")
candidate=$(mktemp "$dir/.edge.conf.candidate.XXXXXX")
cp "$edge_conf" "$backup"
changed=0
cleanup() { rm -f "$backup" "$candidate"; }
trap cleanup EXIT

rollback() {
  # Preserve the bind-mounted inode. Then ask nginx to retain/reload the old
  # configuration; even if that check fails, the source file is restored.
  if ! cat "$backup" > "$edge_conf"; then
    echo "failed to restore original edge config" >&2
    return 1
  fi
  changed=0
  if "$docker_bin" exec "$edge_container" nginx -t &&
     "$docker_bin" exec "$edge_container" nginx -s reload; then
    echo "cutover failed; original config restored and reloaded" >&2
  else
    echo "cutover failed; original config restored, but nginx rollback reload failed" >&2
  fi
}

on_signal() {
  trap - HUP INT TERM
  if [ "$changed" -eq 1 ]; then rollback; fi
  exit 1
}
trap on_signal HUP INT TERM

if ! awk -v from="$normalized" -v to="$to" '
  {
    line=$0
    sub(/^[[:space:]]+/, "", line)
    sub(/[[:space:]]+$/, "", line)
    if (line == "upstream mentorflow_account_core_backend {") {
      blocks++
      active=1
      print
      next
    }
    if (active && line == "}") { active=0; print; next }
    if (active && line ~ /^server[[:space:]]/) {
      servers++
      if (line == from) {
        matches++
        indent=$0
        sub(/[^[:space:]].*$/, "", indent)
        print indent to
        next
      }
    }
    print
  }
  END { if (blocks != 1 || servers != 1 || matches != 1) exit 1 }
' "$edge_conf" > "$candidate"; then
  echo "could not prepare an update within the unique account-core upstream block" >&2
  exit 1
fi

changed=1
if ! cat "$candidate" > "$edge_conf"; then
  rollback
  exit 1
fi
if ! "$docker_bin" exec "$edge_container" nginx -t; then
  rollback
  exit 1
fi
if ! "$docker_bin" exec "$edge_container" nginx -s reload; then
  rollback
  exit 1
fi
echo "edge upstream switched to $mode"
