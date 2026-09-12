"""Bounded offline regression runner; never runs email UAT or a live DB gate.

Use a Python environment with the repository's backend/desktop test dependencies.
Only counts and fixed labels are emitted; pytest output stays in process memory.
Real email and native PostgreSQL RLS can never become PASS through this runner.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    'backend': [
        'backend/tests/test_e109_account_email_security_preflight.py',
        'backend/tests/test_account_workspace_bootstrap.py',
        'backend/tests/test_enterprise_account_core_api.py',
        'backend/tests/test_enterprise_authorization_negative_pack.py',
        'backend/tests/test_enterprise_tenant_persistence.py',
    ],
    'desktop': [
        'desktop/tests/test_e109_account_action_boundary.py',
        'desktop/tests/test_e109_capability_config.py',
        'desktop/tests/test_in_app_oidc.py',
    ],
    'policy': ['deploy/enterprise/auth-email-security/test_policy_contract.py'],
}


def run(scratch_root: Path) -> dict:
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='MF-MAIN3-04-preflight-', dir=scratch_root))
    env = os.environ.copy()
    for name in (
        'MENTORFLOW_RLS_DSN', 'E1_WORKSPACE_BOOTSTRAP_TEST_DATABASE_URL',
        'MENTORFLOW_DATABASE_URL', 'MENTORFLOW_MIGRATION_DATABASE_URL',
        'PYTEST_ADDOPTS', 'PYTEST_PLUGINS',
    ):
        env.pop(name, None)
    env.update(PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT / 'backend'))),
               TMP=str(scratch), TEMP=str(scratch), QT_QPA_PLATFORM='offscreen',
               MENTORFLOW_DEPLOYMENT_MODE='PERSONAL_LOCAL',
               PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    results = {}
    for group, selectors in GROUPS.items():
        command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                   '--tb=no', f'--basetemp={scratch / group}', *selectors]
        try:
            completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                       text=True, errors='replace', timeout=240)
            counts = {label: int(count) for count, label in re.findall(
                r'(\d+) (passed|failed|skipped|errors?|deselected|xfailed|xpassed)',
                completed.stdout)}
            expected_skips = 2 if group == 'backend' else 0
            passed = (completed.returncode == 0 and counts.get('passed', 0) > 0
                      and counts.get('skipped', 0) == expected_skips
                      and not any(counts.get(k, 0) for k in
                                  ('failed', 'error', 'errors', 'deselected', 'xfailed', 'xpassed')))
            results[group] = {'status': 'PASS' if passed else 'FAIL',
                              'exit_code': completed.returncode, 'counts': counts}
        except subprocess.TimeoutExpired:
            results[group] = {'status': 'TIMEOUT', 'timeout_seconds': 240}
    report = {
        'schema': 'mentorflow.e109-account-email-preflight.v1',
        'status': 'OFFLINE_PREFLIGHT_PASS' if all(r['status'] == 'PASS' for r in results.values()) else 'PARTIAL',
        'groups': results,
        'native_postgres_rls': 'NOT_RUN_NO_ISOLATED_DATABASE',
        'real_email_acceptance': 'NOT_RUN_POST_CUTOVER_GATE',
        'verification_token_runtime_tests': 'NOT_RUN',
        'reset_token_runtime_tests': 'NOT_RUN',
        'capability_enablement_authorized': False,
        'real_smtp_send': 0,
        'live_keycloak_mutation': 0,
    }
    (scratch / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch-root', type=Path,
                        default=Path('E:/Codex/Temp') if os.name == 'nt' else ROOT / '.tmp')
    args = parser.parse_args()
    try:
        report = run(args.scratch_root)
    except (OSError, ValueError):
        print('PREFLIGHT_RUNNER_FAILED')
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'OFFLINE_PREFLIGHT_PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
