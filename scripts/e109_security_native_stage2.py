"""Run bounded local HTTP/native nginx checks without live services or SMTP.

The nginx executable must be supplied explicitly. This runner does not provision
or connect a PostgreSQL/Keycloak environment and cannot certify their semantics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    'runtime': ['backend/tests/test_e109_security_native_stage2.py'],
    'backend_capability': ['backend/tests/test_e109_account_email_security_preflight.py'],
    'desktop_capability': [
        'desktop/tests/test_e109_account_action_boundary.py',
        'desktop/tests/test_e109_capability_config.py',
        'desktop/tests/test_in_app_oidc.py',
    ],
    'policy_contract': ['deploy/enterprise/auth-email-security/test_policy_contract.py'],
}
# Batch02 covers both native-switch availability and fail-closed absence.
EXPECTED_COUNTS = {'runtime': 7, 'backend_capability': 5, 'desktop_capability': 89,
                   'policy_contract': 9}


def run(nginx: Path, scratch_root: Path) -> dict:
    nginx = nginx.resolve(strict=True)
    if not nginx.is_file() or nginx.name not in {'nginx.exe', 'nginx'}:
        raise ValueError('NGINX_EXECUTABLE_REQUIRED')
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix='MF-MAIN3-04-stage2-', dir=scratch_root))
    env = os.environ.copy()
    db_names = ('MENTORFLOW_RLS_DSN', 'E1_WORKSPACE_BOOTSTRAP_TEST_DATABASE_URL')
    db_config_present = any(bool(env.get(name)) for name in db_names)
    for name in (*db_names, 'MENTORFLOW_DATABASE_URL', 'MENTORFLOW_MIGRATION_DATABASE_URL',
                 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS'):
        env.pop(name, None)
    env.update(PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT / 'backend'))),
               TMP=str(scratch), TEMP=str(scratch), QT_QPA_PLATFORM='offscreen',
               MENTORFLOW_DEPLOYMENT_MODE='PERSONAL_LOCAL', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
               E109_TEST_NGINX_EXE=str(nginx))
    groups = {}
    properties = []
    for group, selectors in GROUPS.items():
        evidence = scratch / f'{group}.xml'
        args = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--tb=no',
                '-o', 'junit_family=legacy', f'--junitxml={evidence}',
                f'--basetemp={scratch / group}', *selectors]
        try:
            completed = subprocess.run(args, cwd=ROOT, env=env, capture_output=True,
                                       timeout=180)
            cases = ElementTree.parse(evidence).findall('.//testcase') if evidence.is_file() else []
            failed = sum(c.find('failure') is not None or c.find('error') is not None for c in cases)
            skipped = sum(c.find('skipped') is not None for c in cases)
            passed = completed.returncode == 0 and len(cases) == EXPECTED_COUNTS[group] and not failed and not skipped
            groups[group] = {'status': 'PASS' if passed else 'FAIL', 'tests': len(cases),
                             'failed': failed, 'skipped': skipped, 'exit_code': completed.returncode}
            for case in cases:
                values = {item.attrib['name']: item.attrib['value']
                          for item in case.findall('./properties/property')}
                if values:
                    properties.append({'case': case.attrib['name'], 'observations': values})
        except subprocess.TimeoutExpired:
            groups[group] = {'status': 'TIMEOUT', 'timeout_seconds': 180}
    result = {
        'schema': 'mentorflow.e109-security-native-stage2.v1',
        'status': 'PARTIAL',
        'local_runtime_checks': 'PASS' if all(g['status'] == 'PASS' for g in groups.values()) else 'FAIL',
        'groups': groups,
        'native_nginx_sha256': hashlib.sha256(nginx.read_bytes()).hexdigest(),
        'nginx_policy_sha256': hashlib.sha256((ROOT / 'deploy/enterprise/auth-email-security/nginx.edge.conf').read_bytes()).hexdigest(),
        'rate_observations': properties,
        'postgres_native': 'PENDING_EXPLICIT_ISOLATION_VALIDATION' if db_config_present else 'BLOCKED_ENVIRONMENT',
        'verification_token_runtime': 'PENDING_SAFE_NATIVE_KEYCLOAK_FIXTURE',
        'reset_token_runtime': 'PENDING_SAFE_NATIVE_KEYCLOAK_FIXTURE',
        'register_capability': False,
        'forgot_password_capability': False,
        'public_new_issuer': 'PENDING_GATE_A',
        'real_smtp_verify': 'PENDING_GATE_A',
        'real_smtp_reset': 'PENDING_GATE_A',
        'existing_or_remote_server_mutation': 0,
        'smtp_send': 0,
        'production_data_touched': False,
    }
    (scratch / 'receipt.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nginx-exe', type=Path, required=True)
    parser.add_argument('--scratch-root', type=Path,
                        default=Path('E:/Codex/Temp') if os.name == 'nt' else ROOT / '.tmp')
    args = parser.parse_args()
    try:
        result = run(args.nginx_exe, args.scratch_root)
    except (OSError, ValueError, ElementTree.ParseError):
        print('STAGE2_RUNNER_FAILED')
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result['local_runtime_checks'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
