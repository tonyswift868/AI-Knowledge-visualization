"""BUG-297: a superseded refresh must remain a superseded refresh."""
import asyncio
import threading
from uuid import uuid4

import httpx
import pytest
from PySide6.QtCore import QSettings

from refactor.desktop.account_identity import AccountIdentity, AccountPreferenceStore
from refactor.desktop.account_protocol import AccountConfig, AccountError, AccountLoginResult
from refactor.desktop.login_session import LoginSession
from refactor.desktop.native_auth import NativeCredentialClient


ISSUER = "https://identity.session470.invalid/realms/mentorflow"


def test_refresh_preserves_native_session_superseded_code():
    async def run():
        config = AccountConfig(
            ISSUER, "mentorflow-desktop", "mentorflow-api", "https://api.session470.invalid"
        )
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                401, json={"code": "NATIVE_SESSION_SUPERSEDED"}
            )
        )
        async with httpx.AsyncClient(transport=transport) as http:
            client = NativeCredentialClient(config, http_client=http)
            with pytest.raises(AccountError) as raised:
                await client._session_request("refresh", {"refresh_token": "synthetic"})
        assert raised.value.code == "NATIVE_SESSION_SUPERSEDED"

    asyncio.run(run())


class _Vault:
    def __init__(self):
        self.values = {}
        self.logged_out = False

    def store_oauth_tokens(self, identity, *, access_token, refresh_token):
        self.values[identity.account_id] = (access_token, refresh_token)

    def load_oauth_tokens(self, profile):
        try:
            return self.values[profile.account_id]
        except KeyError:
            raise AccountError("CREDENTIAL_NOT_FOUND", "missing")

    def logout(self, profile):
        self.logged_out = True
        self.values.pop(profile.account_id, None)

    def store_offline_identity(self, identity, value):
        pass

    def delete_offline_identity(self, profile):
        pass


class _Passwords:
    def delete(self, profile):
        pass


def test_superseded_restore_clears_session_without_password_fallback(tmp_path, monkeypatch):
    identity = AccountIdentity(
        "https://identity.session470.invalid/realms/mentorflow",
        f"subject-{uuid4().hex}", "Test user",
    )
    vault = _Vault()
    session = LoginSession(
        AccountPreferenceStore(QSettings(str(tmp_path / "accounts.ini"), QSettings.IniFormat)),
        credentials=vault,
        passwords=_Passwords(),
    )
    session.accept(
        AccountLoginResult(identity, "mfn1_a_" + "a" * 43, "mfn1_r_" + "r" * 43, {"role": "LEARNER"}),
        remember=True,
        automatic=True,
        remember_password=False,
    )

    def superseded(*_args, **_kwargs):
        raise AccountError("NATIVE_SESSION_SUPERSEDED", "new login won")

    import refactor.desktop.native_auth as native_auth
    monkeypatch.setattr(native_auth, "restore_native_account_from_env", superseded)

    with pytest.raises(AccountError) as raised:
        session.restore(identity.profile(), threading.Event())

    assert raised.value.code == "NATIVE_SESSION_SUPERSEDED"
    assert vault.logged_out is True
    assert vault.values == {}
