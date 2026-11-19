"""Opt-in session lifecycle over existing preferences and Windows Locker."""
from __future__ import annotations

import threading
import asyncio
import os
import time
import json
from contextlib import contextmanager

from .account_identity import AccountCredentialVault, AccountPreferenceStore, ProfileMode
from .account_protocol import AccountError

_locks_guard = threading.Lock()
_locks: dict[tuple[str, str], threading.RLock] = {}


class UpdateLoginHandoff:
    """One Windows Locker record, independent of updater restart arguments.

    Consume before attempting refresh, so a failed/crashed attempt cannot reuse
    it. The refreshed session lives in memory unless Remember was already set.
    """
    REFERENCE = 'mfcred_update_login_once_v1'
    TTL_SECONDS = 600

    def __init__(self, *, clock=time.time):
        self.clock = clock

    @staticmethod
    def _log(state):
        from refactor.runtime_logging import event
        event('update', state={'交接成功': 'HANDOFF_SUCCESS', '交接失败': 'HANDOFF_FAILED',
                               '交接过期': 'HANDOFF_EXPIRED'}[state])

    def clear(self):
        from .credential_store import delete_secret
        delete_secret(self.REFERENCE)

    def write(self, profile, refresh_token):
        from .credential_store import store_secret
        if not profile.account_id or not refresh_token:
            raise ValueError('UPDATE_LOGIN_UNAVAILABLE')
        store_secret(json.dumps(dict(account_id=profile.account_id,
            refresh_token=refresh_token, written_at=self.clock())), self.REFERENCE)

    def take(self, profile):
        """Return (record existed, token or None); never alter preferences."""
        from .credential_store import get_secret
        try:
            raw = get_secret(self.REFERENCE)
        except Exception as exc:
            if getattr(exc, 'code', '') == 'CREDENTIAL_NOT_FOUND':
                return False, None
            try:
                self.clear()
            except Exception:
                pass  # Locker unavailable: normal login, never a disk fallback.
            self._log('交接失败')
            return True, None
        try:
            self.clear()
            value = json.loads(raw)
            age = self.clock() - value['written_at']
            if not 0 <= age < self.TTL_SECONDS:
                self._log('交接过期')
                return True, None
            if not profile.account_id or value['account_id'] != profile.account_id:
                raise ValueError('UPDATE_LOGIN_ACCOUNT_MISMATCH')
            token = value['refresh_token']
            if not isinstance(token, str) or not token:
                raise ValueError('UPDATE_LOGIN_UNAVAILABLE')
            return True, token
        except Exception:
            self._log('交接失败')
            return True, None


@contextmanager
def account_session_lock(profile, *, blocking=True):
    key = (profile.issuer_hash, profile.subject_hash)
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    if not lock.acquire(blocking=blocking):
        raise RuntimeError('Account session operation is already in progress')
    mutex = None
    acquired = False
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            mutex = kernel.CreateMutexW(None, False, 'Local\\MentorFlow.Login.' + '.'.join(key))
            if not mutex:
                raise RuntimeError('Secure session lock is unavailable')
            acquired = kernel.WaitForSingleObject(mutex, 15000 if blocking else 0) in {0, 0x80}
            if not acquired:
                raise RuntimeError('Account session operation is already in progress')
        yield
    finally:
        if mutex:
            if acquired:
                kernel.ReleaseMutex(mutex)
            kernel.CloseHandle(mutex)
        lock.release()


class LoginSession:
    def __init__(self, preferences: AccountPreferenceStore, credentials=None, passwords=None):
        from .account_registry import AccountRegistry
        from .saved_password import SavedPasswordVault
        self.preferences = preferences
        self.credentials = credentials if credentials is not None else AccountCredentialVault()
        self.registry = AccountRegistry(preferences)
        self.passwords = passwords if passwords is not None else SavedPasswordVault()
        self.update_handoff = UpdateLoginHandoff()

    def take_update_login(self):
        from types import SimpleNamespace
        try:
            profile = self.preferences.load_unvalidated()
            selected = self.registry.snapshot()['current_account_id']
            if profile.account_id != selected:
                profile = None
        except Exception:
            profile = None
        present, token = self.update_handoff.take(profile or SimpleNamespace(account_id=None))
        return present, profile, token

    def restore_update_login(self, profile, token, cancelled, *, restorer=None):
        # This private in-memory adapter receives rotation from the native
        # client without creating an ordinary remembered credential record.
        from types import SimpleNamespace
        from .account_shell_main import AccountShell
        from .native_auth import restore_native_account_from_env
        memory = AccountShell._EphemeralCredentials(SimpleNamespace(
            identity=SimpleNamespace(profile=lambda: profile), access_token='', refresh_token=token))
        try:
            with account_session_lock(profile):
                if cancelled.is_set() or self.closure_blocks_login(profile) or self.requires_reauthentication(profile):
                    raise ValueError('UPDATE_LOGIN_UNAVAILABLE')
                result = (restorer or restore_native_account_from_env)(profile, memory)
                if cancelled.is_set() or result.identity.profile().account_id != profile.account_id:
                    raise ValueError('UPDATE_LOGIN_ACCOUNT_MISMATCH')
                row = self.registry.get(profile.account_id)
                if row and row['remember_session']:
                    self.credentials.store_oauth_tokens(result.identity, access_token=result.access_token,
                                                       refresh_token=result.refresh_token)
            self.update_handoff._log('交接成功')
            return result
        except Exception:
            self.update_handoff._log('交接失败')
            # Never carry a backend exception/token into UI or exception logs.
            raise AccountError('UPDATE_LOGIN_FAILED', '更新后的登录未能恢复，请重新登录。') from None
        finally:
            memory.logout(profile)

    def requires_reauthentication(self, profile):
        if not profile.account_id:
            return False
        self.preferences.settings.sync()
        return bool(self.preferences.settings.value('account/reauth_required/' + profile.account_id, ''))

    def save_offline_snapshot(self, result):
        """Keep only the last server-validated identity/scope; never persist tokens."""
        profile = result.identity.profile()
        row = self.registry.get(profile.account_id)
        if row is None or not row['remember_session']:
            self.clear_offline_snapshot(profile)
            return
        try:
            self.credentials.load_oauth_tokens(profile)
        except Exception:
            self.clear_offline_snapshot(profile)
            return
        state = getattr(result, 'server_state', {})
        role = None
        if isinstance(state, dict):
            account = state.get('account') if isinstance(state.get('account'), dict) else {}
            role = next((item for item in (state.get('role'), state.get('application_role'),
                account.get('role'), account.get('application_role')) if item in {'SUPER_ADMIN','LEARNER'}), None)
        identity = result.identity
        value = dict(issuer=identity.issuer, subject=identity.subject,
            display_name=identity.display_name, organization_id=identity.organization_id,
            workspace_id=identity.workspace_id, role=role)
        self.credentials.store_offline_identity(
            identity, json.dumps(value, ensure_ascii=False, separators=(',', ':')))

    def clear_offline_snapshot(self, profile):
        if profile is None or not profile.account_id:
            return
        try:
            self.credentials.delete_offline_identity(profile)
        except Exception as exc:
            if getattr(exc, 'code', '') != 'CREDENTIAL_NOT_FOUND':
                raise

    def load_offline_snapshot(self, profile):
        """Build a tokenless local shell result for the selected vaulted account."""
        from .account_identity import LocalOnlyIdentity
        from .account_protocol import AccountIdentity, AccountLoginResult
        if (profile is None or not profile.account_id or self.closure_blocks_login(profile)
                or self.requires_reauthentication(profile)):
            return None
        try:
            registry = self.registry.snapshot()
            row = self.registry.get(profile.account_id)
            if (registry['current_account_id'] != profile.account_id or row is None
                    or not row['remember_session']):
                return None
            self.credentials.load_oauth_tokens(profile)
            local_result = lambda: AccountLoginResult(LocalOnlyIdentity(profile), '', '', {}, restored=True)
            try:
                raw = self.credentials.load_offline_identity(profile)
            except Exception as exc:
                if getattr(exc, 'code', '') == 'CREDENTIAL_NOT_FOUND':
                    return local_result()
                return None
            try:
                if not isinstance(raw, str) or len(raw) > 8192:
                    return local_result()
                value = json.loads(raw)
                if not isinstance(value, dict) or set(value) != {
                    'issuer', 'subject', 'display_name', 'organization_id', 'workspace_id', 'role'
                }:
                    return local_result()
                identity = AccountIdentity(value['issuer'], value['subject'], value['display_name'],
                    value.get('organization_id'), value.get('workspace_id'))
                if identity.profile().account_id != profile.account_id:
                    return local_result()
                state = {'role': value.get('role')} if value.get('role') else {}
                return AccountLoginResult(identity, '', '', state, restored=True)
            except Exception:
                # A legacy install has no scope snapshot.  Keep it local-only;
                # never infer subject or workspace from the one-way hashes.
                return local_result()
        except Exception:
            return None

    def revocation_pending(self, profile):
        self.preferences.settings.sync()
        return str(self.preferences.settings.value('account/revocation_pending/' + profile.account_id, '') or '')

    def set_revocation_pending(self, profile, state):
        settings = self.preferences.settings
        key = 'account/revocation_pending/' + profile.account_id
        if state:
            settings.setValue(key, state)
        else:
            settings.remove(key)
        settings.sync()
        if settings.status().value != 0:
            raise AccountError('LOCAL_REAUTH_STATE_UNAVAILABLE', '无法保存会话处理状态。')

    def closure_blocks_login(self, profile):
        if not profile.account_id:
            return False
        from .account_closure_store import ClosureStore
        record = ClosureStore(self.preferences.settings).load(profile)
        return bool(record and (record['submitted'] or record['state'] == 'COMPLETED'))

    def set_reauthentication_required(self, profile, required=True):
        """Persist before a password mutation, so a crash cannot restore old secrets."""
        settings = self.preferences.settings
        key = 'account/reauth_required/' + profile.account_id
        if required:
            settings.setValue(key, '1')
        else:
            settings.remove(key)
        settings.sync()
        status = settings.status() if hasattr(settings, 'status') else None
        if status is not None and status.value != 0:
            raise AccountError('LOCAL_REAUTH_STATE_UNAVAILABLE', '无法保存重新登录状态，尚未提交修改，请稍后重试。')
        if required:
            try:
                self.clear_offline_snapshot(profile)
            except Exception:
                # The durable reauthentication marker already blocks offline
                # restoration, even if Locker cleanup needs a later retry.
                pass

    def invalidate_saved_login(self, profile):
        """Clear only this account, retaining a restore block if any cleanup fails."""
        with account_session_lock(profile):
            self.set_reauthentication_required(profile)
            failures = []
            actions = [
                lambda: self.registry.set_options(profile.account_id, remember_password=False,
                                                  remember_session=False, auto_login=False),
                lambda: self.credentials.logout(profile),
                lambda: self.passwords.delete(profile),
            ]
            def disable_selected_restore():
                if self.registry.snapshot()['current_account_id'] == profile.account_id:
                    self.preferences.disable_restore()
            actions.append(disable_selected_restore)
            for action in actions:
                try:
                    action()
                except Exception:
                    failures.append('LOCAL_CREDENTIAL_CLEANUP_FAILED')
            return not failures

    def remembered(self):
        profile = self.preferences.load_unvalidated()
        if self.closure_blocks_login(profile) or self.requires_reauthentication(profile):
            return None, False
        remember, automatic = self.preferences.login_options()
        if self.registry.snapshot()['current_account_id'] != profile.account_id:
            return None, False
        row = self.registry.get(profile.account_id) if profile.account_id else None
        if row is not None and row['remember_password']:
            return profile, row['auto_login']
        if profile.mode is ProfileMode.ENTERPRISE_ACCOUNT and remember:
            # Preferences alone are not proof that a restorable session exists.
            try:
                _, refresh = self.credentials.load_oauth_tokens(profile)
                if not refresh:
                    return None, False
            except Exception:
                return None, False
            return profile, automatic
        return None, False

    def accept(self, result, *, remember: bool, automatic: bool, remember_password=None, username=None, password=None):
        with account_session_lock(result.identity.profile(),blocking=False):
            return self._accept_locked(result,remember=remember,automatic=automatic,
                remember_password=remember_password,username=username,password=password)

    def _accept_locked(self, result, *, remember, automatic, remember_password, username, password):
        profile = result.identity.profile()
        if self.closure_blocks_login(profile):
            raise AccountError('CLOSURE_IN_PROGRESS', '此账号有待处理的注销操作，请继续查询原操作。')
        if result.restored and self.requires_reauthentication(profile):
            raise AccountError('ENTERPRISE_SESSION_REAUTH_REQUIRED', '请使用密码重新登录。')
        if not result.restored and self.requires_reauthentication(profile):
            if not self.invalidate_saved_login(profile):
                raise AccountError('LOCAL_CREDENTIAL_CLEANUP_FAILED', '本机旧登录信息未能清理，请重试清理后再登录。')
        previous = self.registry.get(profile.account_id)
        saved = bool(previous and previous['remember_password'])
        # Raw credentials reach this point only after native identity validation.
        if remember_password is True:
            if password is None and not saved:
                raise ValueError('NATIVE_CREDENTIALS_REQUIRED')
            saved = True
        elif remember_password is False:
            if saved: self.passwords.delete(profile)
            saved = False
        automatic = remember and automatic
        # With no consent, keep this session only in the existing in-memory
        # login result. Do not overwrite/delete any earlier Locker credentials.
        if remember and not result.restored:
            with account_session_lock(profile, blocking=False):
                self.credentials.store_oauth_tokens(result.identity, access_token=result.access_token,
                                                   refresh_token=result.refresh_token)
        elif not remember and result.restored:
            # The user explicitly turned off Remember for this restored
            # account. Remove its rotated recovery pair, never another account.
            with account_session_lock(profile, blocking=False):
                self.credentials.logout(profile)
        password_written = remember_password is True and password is not None
        if password_written: self.passwords.store(profile,username,password)
        try:
            # Capture a legacy selected account before replacing that selection.
            self.registry.add(profile,remember_session=remember,remember_password=saved,automatic=automatic,username=username)
            self.preferences.save_target(profile, sync_cursor=result.sync_cursor,
                                         remember_me=remember, auto_login=remember and automatic)
            if not result.restored:
                self.set_reauthentication_required(profile, False)
            self.save_offline_snapshot(result)
        except Exception:
            # A failed metadata write must not leave a newly saved password
            # invisible to the consent controls. Never fall back to a disk store.
            if password_written: self.passwords.delete(profile)
            raise

    def forget_password(self, profile):
        with account_session_lock(profile):
            self.passwords.delete(profile)
            self.credentials.logout(profile)
            self.clear_offline_snapshot(profile)
            self.registry.set_options(profile.account_id,remember_password=False,remember_session=False,auto_login=False)
            if self.registry.snapshot()['current_account_id']==profile.account_id:
                self.preferences.disable_restore()

    def restore(self, profile, cancelled: threading.Event):
        if self.closure_blocks_login(profile) or self.requires_reauthentication(profile):
            raise AccountError('ENTERPRISE_SESSION_REAUTH_REQUIRED', '请使用密码重新登录。')
        try:
            return self._restore_session(profile,cancelled)
        except Exception as exc:
            code=getattr(exc,'code','')
            if not isinstance(exc,KeyError) and code not in {'CREDENTIAL_NOT_FOUND','ENTERPRISE_SESSION_REAUTH_REQUIRED',
                    'ENTERPRISE_OIDC_TOKEN_INVALID','ENTERPRISE_ACCOUNT_RESTORE_MISMATCH'}:
                raise
            row=self.registry.get(profile.account_id)
            if row is None or not row['remember_password']: raise
        with account_session_lock(profile):
            from .native_auth import login_native_account_from_env
            from .account_protocol import AccountConfig
            from .account_identity import issuer_hash
            if cancelled.is_set(): raise AccountError('ENTERPRISE_LOGIN_CANCELLED','登录已取消。')
            if self.closure_blocks_login(profile) or self.requires_reauthentication(profile):
                raise AccountError('ENTERPRISE_SESSION_REAUTH_REQUIRED', '请使用密码重新登录。')
            row=self.registry.get(profile.account_id)
            if row is None or not row['remember_password']:
                raise AccountError('ENTERPRISE_SESSION_REAUTH_REQUIRED','请重新登录。')
            if issuer_hash(AccountConfig.from_env().canonical_identity_issuer)!=profile.issuer_hash:
                raise AccountError('ENTERPRISE_ACCOUNT_ISSUER_CHANGED','登录服务已变更，请重新登录。')
            username,password=self.passwords.load(profile)
            try: result=login_native_account_from_env(username,password)
            except AccountError as exc:
                if exc.code == 'NATIVE_CREDENTIALS_INVALID':
                    self.forget_password(profile)
                    raise AccountError('SAVED_PASSWORD_REJECTED','保存的密码已失效，请重新输入密码；如已重置，请使用新密码。') from None
                raise
            finally: password=None
            if cancelled.is_set(): raise AccountError('ENTERPRISE_LOGIN_CANCELLED','登录已取消。')
            if result.identity.profile().account_id!=profile.account_id:
                raise AccountError('ENTERPRISE_ACCOUNT_RESTORE_MISMATCH','重新认证的账号与所选账号不匹配。')
            return result

    def _restore_session(self, profile, cancelled: threading.Event):
        # Serialize refresh/read/rotation for this app and issuer+sub. A cancelled
        # UI may discard its result, but a completed rotation must still replace
        # the old refresh token. Explicit logout queues behind this transaction.
        with account_session_lock(profile):
            if self.closure_blocks_login(profile) or self.requires_reauthentication(profile):
                raise AccountError('ENTERPRISE_SESSION_REAUTH_REQUIRED', '请使用密码重新登录。')
            if cancelled.is_set():
                raise AccountError('ENTERPRISE_LOGIN_CANCELLED', '登录已取消。')
            try:
                from .native_auth import REFRESH_PREFIX, restore_native_account_from_env
                _, token = self.credentials.load_oauth_tokens(profile)
                if token.startswith(REFRESH_PREFIX):
                    return restore_native_account_from_env(profile, self.credentials)
                raise AccountError('ENTERPRISE_SESSION_REAUTH_REQUIRED', '请使用账号密码重新登录。')
            except AccountError as exc:
                if exc.code in {'ENTERPRISE_SESSION_REAUTH_REQUIRED', 'NATIVE_SESSION_SUPERSEDED'}:
                    self.credentials.logout(profile)
                raise

    def select(self, account_id):
        return self.registry.select(account_id)

    def suspend_automatic(self):
        account_id=self.registry.snapshot()['current_account_id']
        if account_id is not None: self.registry.set_options(account_id,auto_login=False)
        self.preferences.settings.setValue('account/auto_login','0')
        self.preferences.settings.sync()

    def remove_local(self, account_id):
        row=self.registry.get(account_id)
        if row is None: return
        profile=self.registry.profile(row)
        # Called after the current UI/service has drained back to LoginGate.
        with account_session_lock(profile):
            self.credentials.logout(profile)
            self.clear_offline_snapshot(profile)
            self.passwords.delete(profile)
            self.registry.remove(account_id)

    def logout(self, profile, *, remote=False, refresh_token=None, forget_password=False):
        with account_session_lock(profile):
            remote_ended = False
            if remote:
                async def end():
                    from .account_protocol import AccountConfig
                    from .native_auth import NativeCredentialClient, REFRESH_PREFIX
                    from .account_identity import issuer_hash
                    try:
                        if refresh_token is not None:
                            token = refresh_token
                        else:
                            _, token = self.credentials.load_oauth_tokens(profile)
                    except Exception as exc:
                        if getattr(exc, 'code', '') != 'CREDENTIAL_NOT_FOUND' and not isinstance(exc, KeyError):
                            raise
                        token = refresh_token
                    if not token or not token.startswith(REFRESH_PREFIX):
                        return False
                    client = NativeCredentialClient(AccountConfig.from_env())
                    try:
                        if profile.issuer_hash != issuer_hash(client.config.canonical_identity_issuer):
                            return False
                        return await client.end_session(token)
                    finally:
                        await client.aclose()
                try:
                    remote_ended = asyncio.run(end())
                except Exception:
                    remote_ended = False
            self.credentials.logout(profile)
            self.clear_offline_snapshot(profile)
            if forget_password: self.passwords.delete(profile)
            self.registry.set_options(profile.account_id,auto_login=False,remember_session=False,
                                      **({'remember_password':False} if forget_password else {}))
            if self.registry.snapshot()['current_account_id']==profile.account_id:
                self.preferences.disable_restore()
            return remote_ended

    def prepare_explicit_logout(self, profile):
        """Durably deny local restoration before an asynchronous remote logout."""
        # This is the authoritative fence. Even if a refresh owns the account
        # lock, Locker cleanup fails, or the process exits before remote
        # end_session returns, restart cannot restore the old session.
        self.set_reauthentication_required(profile, True)
        self.preferences.disable_restore()
        try:
            with account_session_lock(profile, blocking=False):
                self.registry.set_options(profile.account_id, remember_session=False,
                                          auto_login=False)
                self.credentials.logout(profile)
                return True
        except Exception:
            return False
