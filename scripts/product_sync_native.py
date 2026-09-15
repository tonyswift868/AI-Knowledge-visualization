"""Bounded source qualification using the existing owned native PostgreSQL lab.

No external DSN, account login, GUI interaction or deployed service is used.
Pass normal pytest arguments after --; dependencies must already be installed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('pytest_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    scratch = args.scratch.resolve()
    if not scratch.is_relative_to(Path('E:/Codex/Temp').resolve()):
        parser.error('Scratch must be inside E:/Codex/Temp')
    scratch.mkdir(parents=True, exist_ok=True)
    # Leave room for the two 64-character account namespace components on
    # Windows hosts where ordinary path APIs still have MAX_PATH limits.
    run_root = scratch / ('p-' + uuid.uuid4().hex[:10])
    run_root.mkdir()
    sys.path[:0] = [str(ROOT), str(ROOT/'backend'), str(ROOT/'backend/tests')]
    os.environ.update(QT_QPA_PLATFORM='offscreen', TEMP=str(run_root), TMP=str(run_root),
        MENTORFLOW_USER_DATA_ROOT=str(run_root/'bootstrap'),
        MENTORFLOW_DATA_DIR=str(run_root/'bootstrap/data'),
        MENTORFLOW_DB=str(run_root/'bootstrap/data/app.db'),
        MENTORFLOW_ORM_DB=str(run_root/'bootstrap/data/mentorflow_v2.db'))
    from scripts.schema_0036_native import NativePostgres
    import pytest
    lab = NativePostgres(args.pg_bin, run_root)
    class Plugin:
        def pytest_configure(self, config):
            config._schema_0036_lab = lab
    paths = args.pytest_args
    if paths and paths[0] == '--':
        paths = paths[1:]
    if not paths:
        paths = ['backend/tests/test_product_sync.py',
                 'backend/tests/test_product_sync_contract.py',
                 'backend/tests/test_product_sync_regression.py',
                 'backend/tests/test_profile_settings_postgres.py',
                 'desktop/tests/test_product_sync_bridge.py',
                 'desktop/tests/test_rehydration_transport.py', '-q']
    try:
        lab.start()
        return pytest.main(paths + ['--basetemp',str(run_root/'tests')], plugins=[Plugin()])
    finally:
        lab.close()


if __name__ == '__main__':
    raise SystemExit(main())
