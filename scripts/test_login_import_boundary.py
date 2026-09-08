"""Fresh-process source import gate. No backend pytest conftest or live auth."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROGRAM = r'''
import importlib.abc
import json
import os
import sys
import time
blocked = ('desktop.main_window', 'desktop.api', 'desktop.runtime',
           'backend.app.main', 'backend.app.orm', 'torch', 'docling',
           'transformers', 'sentence_transformers', 'uvicorn')
class Fence(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + '.') for name in blocked):
            raise AssertionError('Pre-login import forbidden: ' + fullname)
sys.meta_path.insert(0, Fence())
def audit(event, args):
    if event in ('sqlite3.connect', 'socket.connect'):
        raise AssertionError('Pre-login I/O forbidden: ' + event)
sys.addaudithook(audit)
from PySide6.QtCore import QSettings
import desktop.main as entry
settings = QSettings(os.environ['E107_SETTINGS_FILE'], QSettings.Format.IniFormat)
entry.QSettings = lambda *a, **kw: settings
attempts = []
entry.MentorFlowApplication._begin_interactive_login = lambda self: attempts.append('interactive')
def forbidden_restore(self):
    raise AssertionError('Isolated test must not restore a remembered account')
entry.MentorFlowApplication._begin_remembered_login = forbidden_restore
start = time.perf_counter()
target = entry.MentorFlowApplication()
assert target._enterprise_login_required
target._show_login_gate()
target.app.processEvents()
assert target.login_gate.isVisible()
assert target.main is None and target.api is None and target.service is None
assert attempts == ['interactive']
assert not any(any(name == prefix or name.startswith(prefix + '.') for prefix in blocked) for name in sys.modules)
target.login_gate.close()
target.app.processEvents()
target._shutdown()
print(json.dumps({'source_gate_visible': True, 'main_created': False,
                  'forbidden_imports': [], 'sqlite_connections': 0,
                  'network_connections': 0,
                  'construction_to_event_seconds': time.perf_counter() - start}))
'''


class LoginImportBoundaryTests(unittest.TestCase):
    def test_fresh_login_gate_has_no_business_runtime_or_database_import(self):
        with tempfile.TemporaryDirectory(prefix='e107-import-gate-', dir='E:/Codex/Temp') as folder:
            root = Path(folder)
            env = os.environ.copy()
            for name in list(env):
                if name.startswith('MENTORFLOW_'):
                    del env[name]
            env.update(QT_QPA_PLATFORM='offscreen', E107_SETTINGS_FILE=str(root / 'settings.ini'),
                       MENTORFLOW_USER_DATA_ROOT=folder, MENTORFLOW_DATA_DIR=folder,
                       MENTORFLOW_ACCOUNT_VAULT_ROOT=folder,
                       MENTORFLOW_DB=str(root / 'app.db'), MENTORFLOW_ORM_DB=str(root / 'orm.db'),
                       MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE='1',
                       MENTORFLOW_DESKTOP_OIDC_ISSUER='https://identity.example.test/realms/mentorflow',
                       MENTORFLOW_DESKTOP_OIDC_CLIENT_ID='mentorflow-desktop',
                       MENTORFLOW_DESKTOP_OIDC_AUDIENCE='mentorflow-api',
                       MENTORFLOW_ENTERPRISE_API_BASE_URL='https://api.example.test')
            result = subprocess.run([sys.executable, '-c', PROGRAM], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            evidence = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(evidence['source_gate_visible'])
            self.assertEqual(evidence['forbidden_imports'], [])
            self.assertFalse(list(root.rglob('*.db')))


if __name__ == '__main__':
    unittest.main()
