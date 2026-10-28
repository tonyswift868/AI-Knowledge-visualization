#!/bin/sh
# Persist the result of an existing deployment command, independently of SSH.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
action=${1:-}
job=${2:-}
[ -n "$job" ] || { echo 'usage: job.sh start JOB_DIR COMMAND [ARG...] | status JOB_DIR' >&2; exit 2; }
status() {
  if [ ! -d "$job" ]; then echo MISSING
  elif [ -f "$job/done" ] && [ -f "$job/exit_code" ]; then
    code=$(cat "$job/exit_code")
    case "$code" in ''|*[!0-9]*) echo UNKNOWN; return ;; esac
    if [ "$code" -eq 0 ]; then echo 'SUCCEEDED 0'; else echo "FAILED $code"; fi
  elif [ -f "$job/pid" ] && kill -0 "$(cat "$job/pid")" 2>/dev/null; then echo RUNNING
  else echo UNKNOWN
  fi
}
case "$action" in
  status) [ "$#" -eq 2 ] || exit 2; status ;;
  start)
    [ "$#" -ge 3 ] || exit 2
    shift 2
    umask 077
    mkdir -p "$(dirname -- "$job")"
    # Atomic claim also makes retrying a lost launch acknowledgement safe.
    if ! mkdir "$job" 2>/dev/null; then status; exit 0; fi
    job=$(CDPATH= cd -- "$job" && pwd)
    nohup sh "$here/job.sh" _run "$job" "$@" </dev/null >"$job/output.log" 2>&1 &
    echo RUNNING
    ;;
  _run)
    shift 2
    printf '%s\n' "$$" > "$job/pid"
    date -u '+%Y-%m-%dT%H:%M:%SZ' > "$job/started"
    # Only an actual command result completes a job. Missing markers after a
    # machine/process failure remain UNKNOWN, never an automatic rollback.
    set +e
    "$@"
    code=$?
    printf '%s\n' "$code" > "$job/exit_code.tmp"
    mv "$job/exit_code.tmp" "$job/exit_code"
    date -u '+%Y-%m-%dT%H:%M:%SZ' > "$job/done.tmp"
    mv "$job/done.tmp" "$job/done"
    exit "$code"
    ;;
  *) echo 'unknown job action' >&2; exit 2 ;;
esac
