"""Regression coverage for explicit login with remembered sessions (BUG-139)."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from refactor.desktop.account_identity import AccountIdentity, AccountPreferenceStore
from refactor.desktop.account_protocol import AccountError, AccountLoginResult
from refactor.desktop.account_shell_main import AccountShell
from refactor.desktop.login_session import LoginSession
from refactor.desktop import credential_store as locker


class _Vault216:
    def __init__(self):
        self.tokens = {}
        self.snapshots = {}

    def store_oauth_tokens(self, identity, *, access_token, refresh_token):
        self.tokens[identity.profile().account_id] = (access_token, refresh_token)

    def load_oauth_tokens(self, profile):
        from refactor.desktop.account_protocol import AccountError
        try:
            return self.tokens[profile.account_id]
        except KeyError:
            raise AccountError("CREDENTIAL_NOT_FOUND", "fixture has no session") from None

    def store_offline_identity(self, identity, value):
        self.snapshots[identity.profile().account_id] = value

    def load_offline_identity(self, profile):
        from refactor.desktop.account_protocol import AccountError
        try:
            return self.snapshots[profile.account_id]
        except KeyError:
            raise AccountError("CREDENTIAL_NOT_FOUND", "fixture has no snapshot") from None

    def delete_offline_identity(self, profile):
        self.snapshots.pop(profile.account_id, None)

    def logout(self, profile):
        self.tokens.pop(profile.account_id, None)
        self.snapshots.pop(profile.account_id, None)


class _Passwords216:
    def delete(self, _profile):
        pass


@pytest.fixture(autouse=True)
def handoff_vault216(monkeypatch):
    class Backend:
        def __init__(self):
            self.values = {}

        def get_password(self, service, user):
            return self.values.get((service, user))

        def set_password(self, service, user, secret):
            self.values[(service, user)] = secret

        def delete_password(self, service, user):
            self.values.pop((service, user), None)

    backend = Backend()
    monkeypatch.setattr(locker, "_get_keyring", lambda: backend)
    return backend


@pytest.fixture(scope="module")
def qapp216():
    return QApplication.instance() or QApplication([])


def _result216(subject="learner216", *, restored=False):
    identity = AccountIdentity(
        "https://login216.invalid/realm", subject, f"测试账号 {subject}",
        "org-login216", "workspace-login216",
    )
    return AccountLoginResult(
        identity, "mfn1_a_" + "A" * 43, "mfn1_r_" + "R" * 43,
        {"role": "LEARNER"}, restored=restored,
    )


def _session216(tmp_path, *, automatic=False):
    settings = QSettings(str(tmp_path / "accounts216.ini"), QSettings.Format.IniFormat)
    settings.setFallbacksEnabled(False)
    vault = _Vault216()
    session = LoginSession(
        AccountPreferenceStore(settings), credentials=vault, passwords=_Passwords216()
    )
    current = _result216()
    session.accept(
        current, remember=True, automatic=automatic, remember_password=False,
        username="learner216", password=None,
    )
    session.save_offline_snapshot(current)
    return session, current, vault


def _shell216(qapp, tmp_path, session, monkeypatch, *, restorer=None, capture=True):
    shell = AccountShell(
        qapp, SimpleNamespace(data_root=tmp_path), login_session=session,
        native_restorer=restorer,
    )
    # Keep these tests on the real login/session path without starting unrelated
    # account capability requests or WebEngine prewarm work.
    shell._load_capabilities = lambda: None
    shell._refresh_closure_recovery = lambda: None
    shell._schedule_account_prewarm = lambda: None
    if capture:
        shell._accept_login = Mock()
    return shell


class _Signal216:
    def connect(self, _callback):
        pass


class _Home216:
    def __init__(self, *_args, **_kwargs):
        self.switch_requested = _Signal216()
        self.logout_requested = _Signal216()
        self.verify_requested = _Signal216()
        self.offline = False
        self.disposed = False
        self.hidden = False
        self.notices = []
        self.recovered = 0

    def bind_account_settings(self, _callback): pass
    def set_session_offline(self, value): self.offline = value
    def set_session_notice(self, message, **_kwargs): self.notices.append(message)
    def clear_session_notice(self): self.notices.clear()
    def set_server_role(self, _role): pass
    def session_recovered(self): self.recovered += 1
    def isMinimized(self): return False
    def showNormal(self): pass
    def raise_(self): pass
    def activateWindow(self): pass
    def hide(self): self.hidden = True
    def dispose(self): self.disposed = True
    def close(self): pass
    def deleteLater(self): pass


def _pump216(qapp, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.002)
    qapp.processEvents()
    assert predicate()


def _dispose216(qapp, shell):
    shell.close()
    shell.gate.hide()
    shell.gate.deleteLater()
    qapp.processEvents()


def test_login216_remembered_session_waits_for_click_then_restores_online(
        qapp216, tmp_path, monkeypatch):
    session, current, _vault = _session216(tmp_path, automatic=False)
    # Model a remembered OAuth session without a cached offline identity. This
    # exercises the online restore path after explicit user consent.
    session.load_offline_snapshot = Mock(return_value=None)
    restore = Mock(return_value=_result216(restored=True))
    session.restore = restore
    shell = _shell216(qapp216, tmp_path, session, monkeypatch)
    try:
        shell.show()
        qapp216.processEvents()
        assert shell.gate.isVisible()
        assert shell.gate.username_field.text() == "learner216"
        assert shell.gate.auto_box.isChecked() is False
        assert shell.gate.primary_button.isEnabled()
        assert not shell._accept_login.called
        restore.assert_not_called()

        shell.gate.primary_button.click()
        _pump216(qapp216, lambda: shell._accept_login.called)
        restore.assert_called_once()
        assert restore.call_args.args[0].account_id == current.identity.profile().account_id
        assert shell._accept_login.call_args.args[0].identity.profile().account_id == current.identity.profile().account_id
        assert shell._accept_login.call_args.kwargs.get("offline", False) is False
    finally:
        _dispose216(qapp216, shell)


def test_login216_remembered_session_waits_for_click_then_uses_offline_snapshot_on_network_error(
        qapp216, tmp_path, monkeypatch):
    session, current, _vault = _session216(tmp_path, automatic=False)
    monkeypatch.setattr("refactor.learning.app.window.LearningWindow", _Home216)
    recover = Mock(side_effect=[
        OSError("fixture network still unavailable"),
        _result216(restored=True),
    ])
    shell = _shell216(
        qapp216, tmp_path, session, monkeypatch, restorer=recover, capture=False,
    )
    shell._bind_learning_features = lambda *_args, **_kwargs: None
    shell._loading_appearance = lambda _account_id: ("default", False)
    restore_native = Mock(side_effect=OSError("fixture network unavailable"))
    monkeypatch.setattr(
        "refactor.desktop.native_auth.restore_native_account_from_env", restore_native,
    )
    try:
        shell.show()
        qapp216.processEvents()
        assert shell.gate.isVisible() and shell.home is None

        shell.gate.primary_button.click()
        _pump216(qapp216, lambda: shell.home is not None and restore_native.call_count == 1
                 and not shell._session_refresh_busy)
        assert shell._offline_mode is True and shell.home.offline is True
        assert shell._login_result.identity.profile().account_id == current.identity.profile().account_id
        assert shell.home.notices

        # Recovery uses the existing session refresh path after connectivity
        # returns; it switches the same local session back to online and syncable.
        shell._refresh_active_session()
        _pump216(qapp216, lambda: not shell._offline_mode)
        assert recover.call_count == 2
        assert shell.home.offline is False and shell.home.recovered == 1
    finally:
        _dispose216(qapp216, shell)


def test_login216_network_error_falls_back_to_offline_snapshot_before_retry(
        qapp216, tmp_path, monkeypatch):
    session, current, _vault = _session216(tmp_path, automatic=False)
    monkeypatch.setattr("refactor.learning.app.window.LearningWindow", _Home216)
    recover = Mock(side_effect=OSError("fixture network still unavailable"))
    shell = _shell216(
        qapp216, tmp_path, session, monkeypatch, restorer=recover, capture=False,
    )
    shell._bind_learning_features = lambda *_args, **_kwargs: None
    shell._loading_appearance = lambda _account_id: ("default", False)
    session.restore = Mock(side_effect=AccountError(
        "NATIVE_NETWORK_UNAVAILABLE", "fixture network unavailable",
    ))
    snapshot = _result216(restored=True)
    session.load_offline_snapshot = Mock(return_value=snapshot)
    try:
        shell.show()
        qapp216.processEvents()
        before_click = session.load_offline_snapshot.call_count
        shell.gate.primary_button.click()
        _pump216(qapp216, lambda: shell.home is not None and not shell._session_refresh_busy)
        assert session.restore.call_count == 1
        assert session.load_offline_snapshot.call_count > before_click
        assert shell._offline_mode is True
        assert shell._login_result.identity.profile().account_id == current.identity.profile().account_id
        assert shell.home.offline is True
    finally:
        _dispose216(qapp216, shell)


def test_login216_network_error_without_snapshot_keeps_retry_state(
        qapp216, tmp_path, monkeypatch):
    session, _current, _vault = _session216(tmp_path, automatic=False)
    shell = _shell216(qapp216, tmp_path, session, monkeypatch, capture=False)
    session.restore = Mock(side_effect=AccountError(
        "NATIVE_NETWORK_UNAVAILABLE", "fixture network unavailable",
    ))
    session.load_offline_snapshot = Mock(return_value=None)
    try:
        shell.show()
        qapp216.processEvents()
        before_click = session.load_offline_snapshot.call_count
        shell.gate.primary_button.click()
        _pump216(qapp216, lambda: shell.gate.state_detail.text() == "正在恢复连接…")
        assert shell.home is None
        assert session.restore.call_count == 1
        assert session.load_offline_snapshot.call_count > before_click
    finally:
        _dispose216(qapp216, shell)


@pytest.mark.parametrize(
    "expired_code",
    [
        "NATIVE_SESSION_SUPERSEDED",
        "ENTERPRISE_SESSION_REAUTH_REQUIRED",
        "SAVED_PASSWORD_REJECTED",
    ],
)
def test_login216_expired_restore_never_uses_offline_snapshot(
        qapp216, tmp_path, monkeypatch, expired_code):
    session, current, _vault = _session216(tmp_path, automatic=False)
    monkeypatch.setattr("refactor.learning.app.window.LearningWindow", _Home216)
    shell = _shell216(qapp216, tmp_path, session, monkeypatch, capture=False)
    shell._bind_learning_features = lambda *_args, **_kwargs: None
    shell._loading_appearance = lambda _account_id: ("default", False)
    session.restore = Mock(side_effect=AccountError(
        expired_code, "fixture expired",
    ))
    session.load_offline_snapshot = Mock(return_value=_result216(restored=True))
    try:
        shell.show()
        qapp216.processEvents()
        before_click = session.load_offline_snapshot.call_count
        shell.gate.primary_button.click()
        _pump216(qapp216, lambda: shell._remembered_profile is None and shell.home is None)
        assert session.restore.call_count == 1
        assert session.load_offline_snapshot.call_count == before_click
        assert shell.gate.notice_label.text()
    finally:
        _dispose216(qapp216, shell)


@pytest.mark.parametrize(
    "terminal_code",
    ["NATIVE_SESSION_SUPERSEDED", "ENTERPRISE_SESSION_REAUTH_REQUIRED"],
)
def test_login216_offline_terminal_refresh_returns_to_login(
        qapp216, tmp_path, monkeypatch, terminal_code):
    session, current, _vault = _session216(tmp_path, automatic=False)
    monkeypatch.setattr("refactor.learning.app.window.LearningWindow", _Home216)
    terminal = Mock(side_effect=AccountError(terminal_code, "fixture terminal session"))
    shell = _shell216(
        qapp216, tmp_path, session, monkeypatch, restorer=terminal, capture=False,
    )
    shell._bind_learning_features = lambda *_args, **_kwargs: None
    shell._loading_appearance = lambda _account_id: ("default", False)
    session.restore = Mock(side_effect=AccountError(
        "NATIVE_NETWORK_UNAVAILABLE", "fixture network unavailable",
    ))
    session.load_offline_snapshot = Mock(return_value=_result216(restored=True))
    try:
        shell.show()
        qapp216.processEvents()
        shell.gate.primary_button.click()
        _pump216(qapp216, lambda: shell.home is not None and shell.home.offline)
        _pump216(qapp216, lambda: shell.home.disposed and shell.gate.isVisible())
        assert terminal.call_count == 1
        assert session.registry.get(current.identity.profile().account_id)["remember_session"] is False
    finally:
        _dispose216(qapp216, shell)


@pytest.mark.parametrize("network_available", [True, False], ids=["online", "offline"])
def test_login216_opted_in_account_still_enters_automatically(
        qapp216, tmp_path, monkeypatch, network_available):
    session, current, _vault = _session216(tmp_path, automatic=True)
    if network_available:
        session.load_offline_snapshot = Mock(return_value=None)
        session.restore = Mock(return_value=_result216(restored=True))
    else:
        session.load_offline_snapshot = Mock(wraps=session.load_offline_snapshot)
        session.restore = Mock(side_effect=AssertionError("cached offline auto-entry should not require network"))
    shell = _shell216(qapp216, tmp_path, session, monkeypatch)
    try:
        shell.show()
        _pump216(qapp216, lambda: shell._accept_login.called)
        if network_available:
            session.restore.assert_called_once()
            assert shell._accept_login.call_args.kwargs.get("offline", False) is False
        else:
            session.restore.assert_not_called()
            assert shell._accept_login.call_args.kwargs.get("offline") is True
        assert shell._accept_login.call_args.args[0].identity.profile().account_id == current.identity.profile().account_id
    finally:
        _dispose216(qapp216, shell)


def test_login216_switching_remembered_account_before_login_restores_selected_account(
        qapp216, tmp_path, monkeypatch):
    session, first, vault = _session216(tmp_path, automatic=False)
    second = _result216("learner216-second")
    session.registry.add(
        second.identity.profile(), remember_session=True, remember_password=False,
        automatic=False, username="second216",
    )
    # `add` makes the new entry current. Restore A so startup shows A and the
    # test actually switches accounts before any login begins.
    session.select(first.identity.profile().account_id)
    vault.store_oauth_tokens(second.identity, access_token=second.access_token,
                             refresh_token=second.refresh_token)
    session.save_offline_snapshot(second)
    # Exercise the selected B account's online restore, not its optional
    # snapshot shortcut; A's saved session remains registered but unentered.
    session.load_offline_snapshot = Mock(return_value=None)
    restore = Mock(side_effect=lambda profile, _cancelled: _result216(
        "learner216-second", restored=True
    ))
    session.restore = restore
    shell = _shell216(qapp216, tmp_path, session, monkeypatch)
    try:
        shell.show()
        qapp216.processEvents()
        assert shell.gate.isVisible() and not shell._accept_login.called
        assert shell.gate.username_field.text() == "learner216"

        shell._select_local_account(second.identity.profile().account_id)
        assert shell.gate.username_field.text() == "second216"
        shell.gate.primary_button.click()
        _pump216(qapp216, lambda: shell._accept_login.called)
        assert restore.call_args.args[0].account_id == second.identity.profile().account_id
        assert shell._accept_login.call_args.args[0].identity.profile().account_id == second.identity.profile().account_id
        assert shell._accept_login.call_args.args[0].identity.profile().account_id != first.identity.profile().account_id
    finally:
        _dispose216(qapp216, shell)


def test_login216_update_handoff_enters_once_even_when_auto_login_is_off(
        qapp216, tmp_path, monkeypatch, handoff_vault216):
    session, current, _vault = _session216(tmp_path, automatic=False)
    session.update_handoff.write(current.identity.profile(), current.refresh_token)
    shell = _shell216(
        qapp216, tmp_path, session, monkeypatch,
        restorer=lambda _profile, _credentials: _result216(restored=True),
    )
    try:
        shell.show()
        assert shell.gate.auto_box.isChecked() is False
        _pump216(qapp216, lambda: shell._accept_login.called)
        assert shell._accept_login.call_args.args[0].identity.profile().account_id == current.identity.profile().account_id
        assert shell._accept_login.call_args.kwargs.get("update_handoff") is True
        assert session.take_update_login()[0] is False
    finally:
        _dispose216(qapp216, shell)


@pytest.mark.parametrize("handoff_state", ["expired", "account_mismatch"], ids=["expired", "invalid"])
def test_login216_expired_or_invalid_update_handoff_stays_at_login_gate(
        qapp216, tmp_path, monkeypatch, handoff_vault216, handoff_state):
    session, current, _vault = _session216(tmp_path, automatic=True)
    session.update_handoff.write(current.identity.profile(), current.refresh_token)
    if handoff_state == "expired":
        session.update_handoff.clock = lambda: time.time() + session.update_handoff.TTL_SECONDS + 1
    else:
        session.update_handoff.write(_result216("other-account").identity.profile(), current.refresh_token)
    restore = Mock(side_effect=AssertionError("invalid handoff must not fall through to normal auto-login"))
    session.restore = restore
    shell = _shell216(qapp216, tmp_path, session, monkeypatch)
    try:
        shell.show()
        qapp216.processEvents()
        assert shell.gate.isVisible()
        assert shell.home is None
        assert not shell._accept_login.called
        restore.assert_not_called()
    finally:
        _dispose216(qapp216, shell)
