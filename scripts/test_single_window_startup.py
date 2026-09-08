"""Offline startup barrier tests. No account requests, database or real UI input."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication
from desktop.login_shell import LoginGateWindow, LoginGateState
from desktop.startup_controller import StartupController


class Connection:
    def __init__(self, outcome=True):
        self.outcome = outcome
        self.closed = False
    def connect(self):
        return self.outcome
    def close(self):
        self.closed = True


class SingleWindowStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition())

    def test_light_shell_has_no_business_or_webengine_imports(self):
        result = subprocess.run([sys.executable, '-B', '-c',
            'import desktop.login_shell, sys; '
            'assert not any(x in sys.modules for x in '
            '("desktop.main", "desktop.startup_flow", "desktop.account_vault", '
            '"PySide6.QtWebEngineCore", "httpx", "sqlite3"))'], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_product_attaches_without_replacing_shell_or_opening_database(self):
        from pathlib import Path
        import tempfile
        from PySide6.QtCore import QSettings
        from desktop.main import MentorFlowApplication
        scratch = Path(__import__('tempfile').gettempdir())
        with tempfile.TemporaryDirectory(dir=scratch, prefix='attach-') as folder:
            root = Path(folder)
            overrides = {
                'MENTORFLOW_DESKTOP_OIDC_ISSUER':'https://identity.example.test/realms/mentorflow',
                'MENTORFLOW_DESKTOP_OIDC_CLIENT_ID':'mentorflow-desktop',
                'MENTORFLOW_DESKTOP_OIDC_AUDIENCE':'mentorflow-api',
                'MENTORFLOW_ENTERPRISE_API_BASE_URL':'https://api.example.test',
                'MENTORFLOW_USER_DATA_ROOT':str(root), 'MENTORFLOW_DATA_DIR':str(root),
                'MENTORFLOW_ACCOUNT_VAULT_ROOT':str(root), 'MENTORFLOW_DB':str(root/'app.db'),
                'MENTORFLOW_ORM_DB':str(root/'orm.db'), 'MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE':'1',
            }
            gate = LoginGateWindow()
            gate.show()
            original = int(gate.winId())
            with patch.dict(os.environ, overrides), patch('desktop.main.QSettings',
                    side_effect=lambda *args: QSettings(str(root/'preferences.ini'),QSettings.Format.IniFormat)), \
                    patch.object(MentorFlowApplication,'_begin_interactive_login') as begin:
                product = MentorFlowApplication(login_gate=gate)
                self.assertTrue(product._enterprise_login_required)
                product._show_login_gate()
                self.app.processEvents()
                self.assertIs(product.login_gate,gate)
                self.assertEqual(int(gate.winId()),original)
                self.assertIsNone(product.main)
                self.assertIsNone(product.service)
                self.assertFalse(list(root.rglob('*.db')))
                begin.assert_called_once()
                gate.close()
                self.app.aboutToQuit.disconnect(product._shutdown)

    def test_same_window_attaches_only_after_security_and_connection(self):
        gate = LoginGateWindow()
        gate.set_state(LoginGateState.STARTING)
        release = threading.Event()
        called = []
        def verify():
            release.wait(3)
            return {'verification': 'PASS'}
        controller = StartupController(gate, verify=verify, connection_factory=Connection, attach=called.append)
        gate.show()
        original = int(gate.winId())
        self.wait_for(lambda: 'TUNNEL' in controller.results)
        self.assertEqual(called, [])
        self.assertFalse(gate.primary_button.isEnabled())
        release.set()
        self.wait_for(lambda: controller.attached)
        self.assertEqual(called, [gate])
        self.assertEqual(int(gate.winId()), original)
        gate.close()
        self.assertTrue(controller.connection.closed)

    def make_connection(self):
        from desktop.startup_connection import ConnectionPreparation
        from pathlib import Path
        return ConnectionPreparation({'SystemRoot':r'C:\Windows'}, Path('D:/public-ca.crt'))

    def test_endpoint_requires_exact_issuer_and_successful_owned_exit(self):
        import json
        from desktop.startup_connection import ISSUER
        for code, body, expected in ((0, {'issuer':ISSUER}, True), (22, {'issuer':ISSUER}, False),
                (0, {'issuer':'https://wrong.invalid'}, False), (0, {}, False), (0, [], False)):
            process = MagicMock()
            process.returncode = code
            process.poll.return_value = code
            process.communicate.return_value = (json.dumps(body).encode(), b'')
            connection = self.make_connection()
            with patch('desktop.startup_connection.subprocess.Popen', return_value=process) as start:
                self.assertIs(connection.endpoint_ready(), expected)
                args, kw = start.call_args
                self.assertIn('--cacert', args[0])
                self.assertNotIn('--insecure', args[0])
                self.assertEqual(kw['creationflags'], subprocess.CREATE_NO_WINDOW)
                self.assertEqual(kw['stdin'], subprocess.DEVNULL)
            self.assertEqual(connection._owned, [])

    def test_timeout_stops_only_its_owned_probe(self):
        process = MagicMock()
        process.poll.return_value = None
        process.communicate.side_effect = subprocess.TimeoutExpired('curl', 5)
        connection = self.make_connection()
        with patch('desktop.startup_connection.subprocess.Popen', return_value=process):
            self.assertFalse(connection.endpoint_ready())
        process.terminate.assert_called_once()
        self.assertEqual(connection._owned, [])

    def test_closed_connection_cannot_spawn_or_claim_ready(self):
        connection = self.make_connection()
        connection.close()
        with patch('desktop.startup_connection.subprocess.Popen') as start:
            self.assertFalse(connection.endpoint_ready())
            self.assertFalse(connection.connect())
            start.assert_not_called()

    def test_fail_closed_for_missing_network_result_and_failed_security(self):
        for verify_result, network in (({'verification':'PASS'}, None), ({}, True), ({'verification':'FAIL'}, True)):
            gate = LoginGateWindow()
            called = []
            controller = StartupController(gate, verify=lambda: verify_result,
                connection_factory=lambda: Connection(network), attach=called.append)
            gate.show()
            self.wait_for(lambda: len(controller.results) == 2)
            self.assertEqual(called, [])
            self.assertEqual(gate.state, LoginGateState.ERROR)
            self.assertTrue(gate.primary_button.isEnabled())
            gate.close()

    def test_retry_reuses_window_and_close_rejects_late_result(self):
        gate = LoginGateWindow()
        called = []
        outcomes = iter((False, True))
        controller = StartupController(gate, verify=lambda: {'verification':'PASS'},
            connection_factory=lambda: Connection(next(outcomes)), attach=called.append)
        gate.show()
        self.wait_for(lambda: gate.state is LoginGateState.ERROR)
        original = int(gate.winId())
        gate.retry_requested.emit()
        self.wait_for(lambda: controller.attached)
        self.assertEqual(int(gate.winId()), original)
        gate.close()
        controller._accept(controller.generation, 'SECURITY_GATE', True)
        self.assertEqual(called, [gate])


if __name__ == '__main__':
    unittest.main()
