#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
docker compose run --rm -T pool-gate
echo "CONNECTION_REUSE=PASS SEQUENCE=tenant-a:return:same-connection:tenant-b"
