"""Opt-in session lifecycle over existing preferences and Windows Locker."""
from __future__ import annotations

import threading
import asyncio
import os
import time
from contextlib import contextmanager

from .account_identity import AccountCredentialVault, AccountPreferenceStore, ProfileMode
from .account_protocol import AccountError

_locks_guard = threading.Lock()
_locks: dict[tuple[str, str], threading.RLock] = {}


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

    def requires_reauthentication(self, profile):
        if not profile.account_id:
            return False
        self.preferences.settings.sync()
        return bool(self.preferences.settings.value('account/reauth_required/' + profile.account_id, ''))

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
        except Exception:
            # A failed metadata write must not leave a newly saved password
            # invisible to the consent controls. Never fall back to a disk store.
            if password_written: self.passwords.delete(profile)
            raise

    def forget_password(self, profile):
        with account_session_lock(profile):
            self.passwords.delete(profile)
            self.credentials.logout(profile)
            self.registry.set_options(profile.account_id,remember_password=False,remember_session=False,auto_login=False)
            if self.registry.snapshot()['current_account_id']==profile.account_id:
                self.preferences.disable_restore()

    def restore(self, profile, cancelled: threading.Event):
        if self.closure_blocks_login(profile) or self.requires_reauthentication(profile):
            raise AccountError('ENTERPRISE_SESSION_REAUTH_REQUIRED', '请使用密码重新登录。')
        row=self.registry.get(profile.account_id)
        if row and row['remember_password']:
            age=time.time()-row.get('last_authenticated',0)
            if age < 0 or age >= 30*24*60*60:
                self.forget_password(profile)
                raise AccountError('SAVED_PASSWORD_EXPIRED','已超过30天未登录，请重新输入密码。')
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
                if exc.code in {'ENTERPRISE_SESSION_REAUTH_REQUIRED', 'ENTERPRISE_OIDC_TOKEN_INVALID',
                                'ENTERPRISE_ACCOUNT_RESTORE_MISMATCH'}:
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
            if forget_password: self.passwords.delete(profile)
            self.registry.set_options(profile.account_id,auto_login=False,remember_session=False,
                                      **({'remember_password':False} if forget_password else {}))
            if self.registry.snapshot()['current_account_id']==profile.account_id:
                self.preferences.disable_restore()
            return remote_ended
