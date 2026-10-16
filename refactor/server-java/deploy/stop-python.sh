#!/bin/sh
# Stop the existing Python backend and its network-sharing OIDC proxy.
# Compose stop preserves their containers, images and definitions.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
state=$(sh "$script_dir/cutover.sh" status)
if [ "$state" != java ]; then
  echo "refusing to stop Python while edge upstream is $state; switch to the formal Java upstream first" >&2
  exit 1
fi
exec sh "$script_dir/python-compose.sh" stop
