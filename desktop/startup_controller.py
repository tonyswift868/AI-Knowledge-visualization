"""Single-window startup barrier. Product attachment requires both real gates."""
from __future__ import annotations
import threading
from PySide6.QtCore import QObject, Signal
from .login_shell import LoginGateState


class StartupController(QObject):
    _finished = Signal(int, str, object)

    def __init__(self, gate, *, verify, connection_factory, attach, record=lambda name: None):
        super().__init__(gate)
        self.gate = gate
        self.verify = verify
        self.connection_factory = connection_factory
        self.attach = attach
        self.record = record
        self.generation = 0
        self.closed = False
        self.attached = False
        self.results = {}
        self.connection = None
        self.startup_policy = None
        self._finished.connect(self._accept)
        self.gate.first_paint_completed.connect(self.start)
        self.gate.retry_requested.connect(self.start)
        self.gate.closed.connect(self.close)

    def start(self):
        if self.closed or self.attached:
            return
        # Retry is available only after both bounded jobs have returned.
        if self.generation and len(self.results) < 2:
            return
        self.generation += 1
        generation = self.generation
        self.results = {}
        if self.connection is not None:
            self.connection.close()
        try:
            self.connection = self.connection_factory()
        except Exception:
            self.results = {"SECURITY_GATE": False, "TUNNEL": False}
            self.gate.set_state(LoginGateState.ERROR, detail="启动配置暂时不可用，请检查安装后重试。", retry_bootstrap=True)
            return
        connection = self.connection
        self.gate.set_state(LoginGateState.SECURE_CONNECTION)
        self.record("LOGIN_SHELL_EVENT_LOOP_READY")

        def work(kind, operation):
            self.record(kind + "_BEGIN")
            try:
                result = operation()
                ok = (result is True if kind == "TUNNEL" else
                      isinstance(result, dict) and result.get("verification") == "PASS")
            except Exception:
                # Never put protocol exceptions, paths or account details in
                # ordinary UI logs. Failure remains failure at the barrier.
                ok = False
            self.record(kind + ("_END" if ok else "_FAILED"))
            try:
                self._finished.emit(generation, kind, result if ok and kind == 'SECURITY_GATE' else ok)
            except RuntimeError:
                pass  # QApplication closed while a bounded worker finished.

        threading.Thread(target=work, args=("SECURITY_GATE", self.verify), daemon=True).start()
        threading.Thread(target=work, args=("TUNNEL", connection.connect), daemon=True).start()

    def _accept(self, generation, kind, ok):
        if self.closed or self.attached or generation != self.generation:
            return
        if kind == 'SECURITY_GATE' and isinstance(ok, dict):
            self.startup_policy = ok.get('startup_update')
            if self.startup_policy:
                self.gate.set_startup_policy(self.startup_policy)
            ok = ok.get('verification') == 'PASS'
        self.results[kind] = ok
        if kind == "SECURITY_GATE" and not ok:
            self.connection.close()
        if len(self.results) != 2:
            return
        if self.startup_policy and not self.startup_policy['login_allowed']:
            self.connection.close()
            self.gate.set_startup_policy(self.startup_policy)
            self.record('STARTUP_' + self.startup_policy['state'])
            return
        if not all(self.results.values()):
            self.connection.close()
            message = ("程序安全检查未通过，请修复安装后重试。" if not self.results["SECURITY_GATE"]
                       else "无法连接到 MentorFlow 服务，请检查网络后重试。")
            from scripts.install_product import configured
            if self.results['SECURITY_GATE'] and configured().uat:
                message='账号服务暂未连接。您可以打开“关于与检查更新”查看软件版本。账号资料未加载。'
            self.gate.set_state(LoginGateState.ERROR, detail=message, retry_bootstrap=True)
            return
        self.gate.set_state(LoginGateState.LOADING_AUTH)
        try:
            self.attach(self.gate)
        except Exception:
            self.connection.close()
            self.gate.set_state(LoginGateState.ERROR, detail="登录界面暂时无法准备，请重试。", retry_bootstrap=True)
            return
        self.attached = True
        # The existing account flow now owns retry semantics on this window.
        self.gate.retry_requested.disconnect(self.start)
        self.record("LOGIN_FLOW_ATTACHED")

    def close(self):
        self.closed = True
        self.generation += 1
        if self.connection is not None:
            self.connection.close()
