#!/bin/sh
# Run on the server inside job.sh, not in an SSH client's failure handler.
set -u
[ "$#" -eq 2 ] || { echo 'usage: checked-step.sh ACTION_SCRIPT ROLLBACK_SCRIPT' >&2; exit 2; }
sh "$1"
result=$?
if [ "$result" -ne 0 ]; then
  printf 'ACTION_EXIT %s; executing server-side application rollback\n' "$result"
  sh "$2"
  rollback=$?
  printf 'ROLLBACK_EXIT %s\n' "$rollback"
fi
# Keep the failed check visible even when recovery succeeded.
exit "$result"
