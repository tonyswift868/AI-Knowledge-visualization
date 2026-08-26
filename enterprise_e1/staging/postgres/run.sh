#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

cleanup() {
  if [[ "${E1_02B_KEEP_STAGING:-0}" != "1" ]]; then
    ./cleanup.sh
  fi
}
trap cleanup EXIT

./bootstrap.sh
./migrate.sh
./seed.sh
./rls-negative-suite.sh
./pool-leak-suite.sh
echo "E1_02B_POSTGRES_STAGING=PASS"
