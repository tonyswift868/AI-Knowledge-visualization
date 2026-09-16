"""Retained exact-source UAT evidence, not current packaged/live qualification."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BASE = '1531c65116063ec606ed1c623acd29fb68e9e8b7'
REGISTRATION = '6505fc82f9efdc71baffc86d818aec3141fe8c76'
PREPARATION = 'a9a75ec6871a2291dca78747653a5afd2d6026f2'
HISTORY = '3259dd6d219bf7dbdd116c93a0c637daf5744566'
INTEGRATED = '89315a1f66a4af4a1522f19e3a93008f37a144ed'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode().strip()


def test_frozen_sources_have_exact_expected_order_and_no_path_overlap():
    assert git('rev-parse', PREPARATION + '^') == BASE
    assert git('rev-parse', HISTORY + '^') == PREPARATION
    assert git('rev-parse', REGISTRATION + '^') == BASE
    desktop = set(git('diff', '--name-only', BASE, HISTORY).splitlines())
    registration = set(git('diff', '--name-only', BASE, REGISTRATION).splitlines())
    assert len(desktop) == 16 and len(registration) == 4
    assert not desktop & registration
    for source, paths in ((HISTORY, desktop), (REGISTRATION, registration)):
        for path in paths:
            if path == 'tests/theme/test_mvp_registration.py':
                continue  # bounded integration-only assertion scope adjustment
            expected = git('rev-parse', source + ':' + path)
            assert git('rev-parse', INTEGRATED + ':' + path) == expected, path


def test_former_failure_is_an_ordinary_mandatory_test():
    import ast
    path = ROOT / 'desktop/tests/test_owner0_nonempty_isolation.py'
    source = ast.parse(path.read_text(encoding='utf-8'))
    node = next(n for n in source.body if isinstance(n, ast.FunctionDef)
                and n.name == 'test_recent_history_must_not_cross_accounts')
    assert not node.decorator_list
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr in {'skip', 'xfail'} for n in ast.walk(node))
    assert sum(isinstance(n, ast.Assert) for n in ast.walk(node)) >= 2


def test_shortcut_still_uses_trusted_launcher_without_installing_it():
    source = (ROOT / 'scripts/enterprise/install_personal_test_shortcut.ps1').read_text(encoding='utf-8')
    for expected in ('Start-MentorFlow.ps1', '-NoProfile', '-WindowStyle Hidden',
                     '$shortcut.WorkingDirectory=$Release', '$shortcut.IconLocation=$icon',
                     "throw 'A different MentorFlow shortcut already exists"):
        assert expected in source
