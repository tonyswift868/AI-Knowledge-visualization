"""Opt-in session lifecycle over existing preferences and Windows Locker."""
from __future__ import annotations

import threading
import asyncio
import os
from contextlib import contextmanager

from .account_identity import AccountCredentialVault, AccountPreferenceStore, ProfileMode
from .enterprise_oidc import EnterpriseOidcError, restore_enterprise_account_from_env

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
    def __init__(self, preferences: AccountPreferenceStore, credentials=None):
        self.preferences = preferences
        self.credentials = credentials if credentials is not None else AccountCredentialVault()

    def remembered(self):
        profile = self.preferences.load_unvalidated()
        remember, automatic = self.preferences.login_options()
        if profile.mode is ProfileMode.ENTERPRISE_ACCOUNT and remember:
            return profile, automatic
        return None, False

    def accept(self, result, *, remember: bool, automatic: bool):
        profile = result.identity.profile()
        # With no consent, keep this session only in the existing in-memory
        # login result. Do not overwrite/delete any earlier Locker credentials.
        if remember:
            with account_session_lock(profile, blocking=False):
                self.credentials.store_oauth_tokens(result.identity, access_token=result.access_token,
                                                   refresh_token=result.refresh_token)
        elif result.restored:
            # The user explicitly turned off Remember for this restored
            # account. Remove its rotated recovery pair, never another account.
            with account_session_lock(profile, blocking=False):
                self.credentials.logout(profile)
        self.preferences.save_target(profile, sync_cursor=result.sync_cursor,
                                     remember_me=remember, auto_login=remember and automatic)

    def restore(self, profile, cancelled: threading.Event):
        # Serialize refresh/read/rotation for this app and issuer+sub. A cancelled
        # UI may discard its result, but a completed rotation must still replace
        # the old refresh token. Explicit logout queues behind this transaction.
        with account_session_lock(profile):
            if cancelled.is_set():
                raise EnterpriseOidcError('ENTERPRISE_LOGIN_CANCELLED', '登录已取消。')
            try:
                return restore_enterprise_account_from_env(profile, self.credentials, refresh_session=True)
            except EnterpriseOidcError as exc:
                if exc.code in {'ENTERPRISE_SESSION_REAUTH_REQUIRED', 'ENTERPRISE_OIDC_TOKEN_INVALID',
                                'ENTERPRISE_ACCOUNT_RESTORE_MISMATCH'}:
                    self.credentials.logout(profile)
                raise

    def logout(self, profile, *, remote=False, refresh_token=None):
        with account_session_lock(profile):
            remote_ended = False
            if remote:
                async def end():
                    from .enterprise_oidc import DesktopOidcConfig, EnterpriseOidcClient
                    client = EnterpriseOidcClient(DesktopOidcConfig.from_env())
                    try:
                        token = refresh_token
                        if token is None:
                            _, token = self.credentials.load_oauth_tokens(profile)
                        return await client.end_session(token)
                    finally:
                        await client.aclose()
                try:
                    remote_ended = asyncio.run(end())
                except Exception:
                    remote_ended = False
            self.credentials.logout(profile)
            return remote_ended
