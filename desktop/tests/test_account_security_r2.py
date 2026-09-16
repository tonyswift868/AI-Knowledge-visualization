"""Offscreen callback/refresh checks; no account writes or network."""
from concurrent.futures import Future
from types import SimpleNamespace

from desktop.account_settings_page import AccountSettingsPage
from desktop.admin_overview_page import AdminOverviewPage


def page(monkeypatch, **callbacks):
    monkeypatch.setattr(AccountSettingsPage, 'refresh', lambda self: None)
    return AccountSettingsPage(profile=None, vault_manager=None, credential_vault=None,
        switch_account=lambda _: None, logout_account=lambda: None, **callbacks)


def finish(view):
    view._security_future.result(timeout=2)
    view._finish_security()


def test_email_state_and_in_page_code_callbacks(monkeypatch):
    state = dict(email='fixture@example.invalid', email_verified=False, can_bind=False, can_verify=True)
    requests, codes = [], []
    view = page(monkeypatch, read_account_security=lambda: state,
        request_email_code=lambda address: requests.append(address) or dict(expires_in=600, resend_after=60),
        verify_email_code=lambda code: (codes.append(code), state.update(email_verified=True, can_verify=False)))
    try:
        view.refresh_security(); finish(view)
        assert '未验证' in view.email_status.text() and view.email_address.isReadOnly()
        view._request_email_code(); finish(view)
        assert requests == ['fixture@example.invalid']
        view.email_code.setText('001234'); view._verify_email_code(); finish(view)
        finish(view)  # successful verification re-reads canonical state
        assert codes == ['001234'] and '已验证' in view.email_status.text()
        assert view.email_code.text() == '' and not view.send_email_code_button.isEnabled()
    finally: view.close()


def test_no_email_authority_fails_closed(monkeypatch):
    view = page(monkeypatch)
    try:
        assert not view.send_email_code_button.isEnabled()
        assert not view.verify_email_button.isEnabled()
        assert '暂不可用' in view.security_status.text()
    finally: view.close()


def test_change_password_clears_only_after_confirmed_callback(monkeypatch):
    writes, cleared = [], []
    view = page(monkeypatch, change_password=lambda old, new: writes.append((old, new)),
                password_changed=lambda: cleared.append('captured-account-only'))
    try:
        view.current_password.setText('old-fixture')
        view.new_password.setText('new-fixture'); view.confirm_password.setText('mismatch')
        view._submit_change_password()
        assert not writes and not cleared
        view.confirm_password.setText('new-fixture'); view._submit_change_password(); finish(view)
        assert writes == [('old-fixture', 'new-fixture')] and cleared == ['captured-account-only']
        assert not view.current_password.text() and not view.new_password.text()
    finally: view.close()


def test_admin_hidden_page_has_no_background_timer_and_partial_is_explicit():
    view = AdminOverviewPage(SimpleNamespace(admin_overview=lambda: {}))
    try:
        assert not view.timer.isActive() and view.REFRESH_SECONDS == 60
        future = Future()
        future.set_result(dict(registered_human_users=None, services={'email':dict(status='unavailable', code='EMAIL_AUTHORITY_NOT_CONFIGURED')},
            data_status='unavailable', identity_statistics=dict(code='ADMIN_READER_NOT_CONFIGURED'), observed_at='fixture-time'))
        view.future = future; view._poll()
        assert view.labels['registered_human_users'].text() == '—'
        assert 'EMAIL_AUTHORITY_NOT_CONFIGURED' in view.labels['service_email'].text()
        assert '部分统计暂不可用' in view.status.text()
    finally: view.close()


def test_password_failure_does_not_clear_saved_credentials(monkeypatch):
    cleared = []
    def reject(*_): raise RuntimeError('fixture-rejection')
    view = page(monkeypatch, change_password=reject, password_changed=lambda: cleared.append(True))
    try:
        view.current_password.setText('old-fixture')
        view.new_password.setText('new-fixture'); view.confirm_password.setText('new-fixture')
        view._submit_change_password()
        try: view._security_future.result(timeout=2)
        except RuntimeError: pass
        view._finish_security()
        assert not cleared and '未能确认完成' in view.password_status.text()
    finally: view.close()


def test_admin_entry_refreshes_and_exit_stops_timer():
    from PySide6.QtGui import QShowEvent, QHideEvent
    calls = []
    view = AdminOverviewPage(SimpleNamespace(admin_overview=lambda: calls.append('read') or {}))
    try:
        view.showEvent(QShowEvent())  # deliver lifecycle only; no window is shown
        view.future.result(timeout=2)
        assert calls == ['read'] and view.timer.isActive()
        view.hideEvent(QHideEvent())
        assert not view.timer.isActive()
    finally: view.close()
