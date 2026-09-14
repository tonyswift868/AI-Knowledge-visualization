"""Own a fresh loopback PostgreSQL fixture for the Profile/Settings slice."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import sys

from schema_0036_native import NativePostgres

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    lab = NativePostgres(args.pg_bin, args.scratch)
    report = {'fixture_type': 'OWNED_NATIVE_LOOPBACK_POSTGRESQL', 'cases': [], 'cleaned_up': False}
    code = 1
    try:
        lab.start()
        report['postgres_version'] = lab.version
        os.environ.update(MENTORFLOW_DEPLOYMENT_MODE='PERSONAL_LOCAL',
                          MENTORFLOW_ORM_DB=str(lab.home / 'collection.sqlite3'),
                          PYTHONDONTWRITEBYTECODE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
        sys.path[:0] = [str(ROOT), str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
        import pytest

        class Evidence:
            def pytest_configure(self, config):
                config._schema_0036_lab = lab

            def pytest_runtest_logreport(self, report):
                if report.when == 'call' or report.failed:
                    row = {'case': report.nodeid, 'phase': report.when, 'outcome': report.outcome}
                    if report.failed:
                        row['failure'] = str(report.longrepr).replace(lab.password, '[REDACTED]')
                    evidence['cases'].append(row)

        evidence = report
        # Only tests in this slice. Migration/RLS fixture owns its new DB.
        with (lab.home / 'private-tests.log').open('w', encoding='utf-8') as log:
            with redirect_stdout(log), redirect_stderr(log):
                code = int(pytest.main([
                    str(ROOT / 'backend/tests/test_profile_settings_postgres.py'), '-q',
                    '-p', 'no:cacheprovider', '--basetemp', str(lab.home / 'pytest')], plugins=[Evidence()]))
        report['pytest_exit_code'] = code
    except Exception as exc:
        report['failure_type'] = type(exc).__name__
    finally:
        try:
            lab.close()
            report['cleaned_up'] = True
        except Exception as exc:
            report['cleanup_failure_type'] = type(exc).__name__
            code = 1
        receipt = args.scratch / (lab.identity + '-profile-settings.json')
        receipt.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'exit_code': code, 'cleaned_up': report['cleaned_up'], 'receipt': str(receipt)}))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
