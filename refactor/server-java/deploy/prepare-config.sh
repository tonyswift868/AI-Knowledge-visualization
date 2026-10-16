#!/bin/sh
set -eu
umask 077
if [ "$#" -ne 2 ]; then
  echo "usage: $0 BACKEND_CONTAINER TRUSTSTORE_DIR" >&2
  echo "optional: MF_OIDC_PROXY_CONTAINER (default mentorflow-e1-preview-oidc-loopback-proxy-1)" >&2
  exit 2
fi
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$here/prepare-config.py" "$1" "$2"
