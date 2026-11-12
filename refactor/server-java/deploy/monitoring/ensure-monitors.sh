#!/bin/sh
# Kuma has no supported declarative import API. Validate the reviewed manifest
# before the operator creates/updates the same entries in Kuma's UI.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
validator=${KUMA_MANIFEST_VALIDATOR:-$script_dir/validate-kuma-manifest.py}
manifest=${KUMA_MANIFEST:-$script_dir/kuma-monitors.json}
python_bin=${PYTHON_BIN:-python3}
[ -r "$validator" ] || { printf '%s\n' 'Kuma validator missing' >&2; exit 1; }
[ -r "$manifest" ] || { printf '%s\n' 'Kuma manifest missing' >&2; exit 1; }
exec "$python_bin" "$validator" "$manifest"
