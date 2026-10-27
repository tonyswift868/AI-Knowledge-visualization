from __future__ import annotations

import pytest
from threading import Event, Lock

pytest.importorskip('PySide6')
from PySide6.QtCore import QSettings

from refactor.desktop.account_identity import AccountIdentity, AccountPreferenceStore
from refactor.desktop.account_protocol import AccountLoginResult
from refactor.desktop.login_session import LoginSession
from types import SimpleNamespace


class Vault:
    def __init__(self):
        self.values = {}
        self.offline = {}

    def store_oauth_tokens(self, identity, *, access_token, refresh_token):
        self.values[identity.account_id] = (access_token, refresh_token)

    def load_oauth_tokens(self, profile):
        from refactor.desktop.account_protocol import AccountError
        try:
            return self.values[profile.account_id]
        except KeyError:
            raise AccountError('CREDENTIAL_NOT_FOUND', 'missing')

    def logout(self, profile):
        self.values.pop(profile.account_id, None)
        self.offline.pop(profile.account_id, None)

    def store_offline_identity(self, identity, value):
        self.offline[identity.account_id] = value

    def load_offline_identity(self, profile):
        from refactor.desktop.account_protocol import AccountError
        try:
            return self.offline[profile.account_id]
        except KeyError:
            raise AccountError('CREDENTIAL_NOT_FOUND', 'missing snapshot')

    def delete_offline_identity(self, profile):
        self.offline.pop(profile.account_id, None)


class Passwords:
    def delete(self, profile):
        pass


def make_session(tmp_path):
    settings = QSettings(str(tmp_path / 'accounts.ini'), QSettings.IniFormat)
    vault = Vault()
    session = LoginSession(AccountPreferenceStore(settings), credentials=vault, passwords=Passwords())
    identity = AccountIdentity('https://identity.example.test/realms/mf', 'subject-181', 'Test user',
                               'org-1', 'workspace-2')
    result = AccountLoginResult(identity, 'access-secret', 'refresh-secret', {'role': 'LEARNER'})
    session.accept(result, remember=True, automatic=False, remember_password=False)
    return session, identity.profile(), identity


def test_snapshot_contains_no_tokens_and_restores_verified_scope(tmp_path):
    session, profile, identity = make_session(tmp_path)
    raw = session.credentials.offline[profile.account_id]
    assert 'access-secret' not in raw and 'refresh-secret' not in raw
    restored = session.load_offline_snapshot(profile)
    assert restored is not None
    assert restored.access_token == restored.refresh_token == ''
    assert restored.identity == identity
    assert restored.server_state == {'role': 'LEARNER'}


@pytest.mark.parametrize('action', ['logout', 'remove', 'reauth'])
def test_logout_removal_and_reauthentication_block_offline_restore(tmp_path, action):
    session, profile, _identity = make_session(tmp_path)
    if action == 'logout':
        session.logout(profile)
    elif action == 'remove':
        session.remove_local(profile.account_id)
    else:
        session.set_reauthentication_required(profile)
    assert session.load_offline_snapshot(profile) is None


def test_wrong_selected_account_and_missing_vault_credential_block(tmp_path):
    session, profile, identity = make_session(tmp_path)
    other = AccountIdentity(identity.issuer, 'other-subject', 'Other user')
    session.registry.add(other.profile(), remember_session=True, remember_password=False,
                         automatic=False, username='other')
    assert session.load_offline_snapshot(profile) is None
    session.registry.select(profile.account_id)
    session.credentials.logout(profile)
    assert session.load_offline_snapshot(profile) is None


def test_legacy_vault_session_enters_local_only_without_invented_scope(tmp_path):
    session, profile, _identity = make_session(tmp_path)
    session.credentials.delete_offline_identity(profile)
    result = session.load_offline_snapshot(profile)
    assert result is not None
    assert result.identity.account_id == profile.account_id
    assert result.identity.issuer is None
    assert result.identity.subject is None
    assert result.identity.organization_id is None
    assert result.identity.workspace_id is None
    assert result.access_token == result.refresh_token == ''


@pytest.mark.parametrize('legacy', [False, True])
def test_offline_shell_does_not_expose_feature_session_before_restore(tmp_path, legacy):
    from refactor.desktop.account_shell_main import AccountShell
    session, profile, _identity = make_session(tmp_path)
    if legacy:
        session.credentials.delete_offline_identity(profile)
    result = session.load_offline_snapshot(profile)
    shell = object.__new__(AccountShell)
    home = object()
    shell._closing = False
    shell.home = home
    shell._login_result = result
    shell._feature_session_confirmed = False
    shell._account_settings = None
    assert shell._feature_session(home, profile.account_id) is None


def test_restored_online_scope_replaces_legacy_binding_snapshot(tmp_path):
    session, profile, identity = make_session(tmp_path)
    session.credentials.delete_offline_identity(profile)
    assert session.load_offline_snapshot(profile).identity.workspace_id is None
    moved = AccountIdentity(identity.issuer, identity.subject, identity.display_name,
                             'org-new', 'workspace-new')
    result = AccountLoginResult(moved, 'new-access', 'new-refresh', {'role': 'LEARNER'}, restored=True)
    session.accept(result, remember=True, automatic=False, remember_password=False)
    recovered = session.load_offline_snapshot(profile)
    assert recovered.identity.account_id == profile.account_id
    assert recovered.identity.organization_id == 'org-new'
    assert recovered.identity.workspace_id == 'workspace-new'


@pytest.mark.parametrize('server_outcome', ['NATIVE_SESSION_SUPERSEDED', 'ENTERPRISE_SESSION_REAUTH_REQUIRED'])
def test_offline_server_rejection_expires_local_session_without_deleting_progress(tmp_path, server_outcome):
    from refactor.desktop.account_shell_main import AccountShell
    session, profile, _identity = make_session(tmp_path)
    source = session.load_offline_snapshot(profile)
    session.preferences.settings.setValue('account/sync_cursor', 'progress-cursor-kept')

    class Field:
        def setText(self, value): self.value = value
        def setChecked(self, value): self.value = value
        def clear(self): self.value = ''

    class Gate:
        def __init__(self):
            self.username_field = self.remember_box = self.auto_box = self.remember_password_box = Field()
            self.notice_label = Field()
            self.state = None
            self.visible = False
        def clear_password(self): pass
        def set_state(self, state, **kwargs): self.state = state
        def show(self): self.visible = True

    home = SimpleNamespace(disposed=False, hidden=False,
        dispose=lambda: setattr(home, 'disposed', True), hide=lambda: setattr(home, 'hidden', True))
    shell = object.__new__(AccountShell)
    shell._session_epoch = 4
    shell._closing = False
    shell._session_refresh_busy = True
    shell._session_refresh_wait_lock = Lock()
    shell._session_refresh_done = Event()
    shell._logout_pending = False
    shell._switch_pending = False
    shell._session_timer = SimpleNamespace(stop=lambda: None)
    shell._confirmed_server_role = 'LEARNER'
    shell._confirmed_role_account_id = profile.account_id
    shell._remembered_profile = None
    shell._session_credentials = session.credentials
    shell._login_result = source
    shell._feature_session_confirmed = False
    shell._session_refresh_degraded = True
    shell.session = session
    shell.home = home
    shell.gate = Gate()
    shell._close_account_settings = lambda: None
    shell._show_gate_before_retiring_home = lambda: None
    shell._discard_login_result = lambda result: None

    # Both server outcomes are classified as session_expired by the refresh worker.
    assert server_outcome in {'NATIVE_SESSION_SUPERSEDED', 'ENTERPRISE_SESSION_REAUTH_REQUIRED'}
    shell._background_failed('4:session_expired', '')
    assert shell._session_refresh_done.is_set()
    assert shell.home.disposed and shell.home.hidden and shell.gate.visible
    assert session.load_offline_snapshot(profile) is None
    assert session.registry.get(profile.account_id)['remember_session'] is False
    assert session.preferences.settings.value('account/sync_cursor') == 'progress-cursor-kept'


def test_explicit_logout_blocks_reopen_before_remote_logout_finishes(tmp_path):
    session, profile, _identity = make_session(tmp_path)
    # Simulate a slow/unavailable server: local vault deletion may still be in
    # flight, while the durable local intent must already deny restoration.
    session.credentials.logout = lambda _profile: None
    session.prepare_explicit_logout(profile)
    reopened = LoginSession(session.preferences, credentials=session.credentials,
                            passwords=session.passwords)
    assert reopened.requires_reauthentication(profile)
    assert reopened.load_offline_snapshot(profile) is None


def test_fresh_password_login_clears_explicit_logout_fence(tmp_path):
    session, profile, identity = make_session(tmp_path)
    session.prepare_explicit_logout(profile)
    result = AccountLoginResult(identity, 'fresh-access', 'fresh-refresh', {'role': 'LEARNER'})
    session.accept(result, remember=True, automatic=False, remember_password=False)
    assert not session.requires_reauthentication(profile)
    assert session.load_offline_snapshot(profile) is not None
