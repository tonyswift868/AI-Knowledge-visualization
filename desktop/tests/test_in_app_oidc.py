from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
pytest.importorskip("PySide6")

from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.account_identity import AccountIdentity  # noqa: E402
from desktop.account_identity import AccountProfile  # noqa: E402
from desktop.account_settings_page import AccountSettingsPage  # noqa: E402
from desktop.account_vault import AccountVaultManager, VaultLayout  # noqa: E402
from desktop.enterprise_oidc import (  # noqa: E402
    DesktopOidcConfig,
    EnterpriseOidcLoginResult,
    OidcDiscovery,
    OidcLoginTransaction,
    PreparedOidcLogin,
)
from desktop.in_app_oidc import (  # noqa: E402
    AUTH_DIALOG_OBJECT_NAME,
    AUTH_VIEW_OBJECT_NAME,
    InAppOidcLoginDialog,
    InAppOidcNavigationPolicy,
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _config() -> DesktopOidcConfig:
    return DesktopOidcConfig(
        issuer="https://identity.example.test/realms/mentorflow",
        client_id="mentorflow-desktop",
        audience="mentorflow-api",
        api_base_url="https://api.example.test",
        organization_id="org-a",
        workspace_id="workspace-a",
        callback_timeout_seconds=15,
    )


def _prepared(*, port: int = 43210) -> PreparedOidcLogin:
    redirect_uri = f"http://127.0.0.1:{port}/oauth/callback"
    transaction = OidcLoginTransaction(
        redirect_uri=redirect_uri,
        expected_issuer="https://identity.example.test/realms/mentorflow",
    )
    authorization_url = transaction.authorization_url(
        endpoint=(
            "https://identity.example.test/realms/mentorflow/"
            "protocol/openid-connect/auth"
        ),
        client_id="mentorflow-desktop",
        scopes=("openid", "profile", "email"),
        force_reauthentication=True,
    )
    return PreparedOidcLogin(
        receiver=object(),  # type: ignore[arg-type]
        discovery=OidcDiscovery(
            issuer="https://identity.example.test/realms/mentorflow",
            authorization_endpoint=(
                "https://identity.example.test/realms/mentorflow/"
                "protocol/openid-connect/auth"
            ),
            token_endpoint=(
                "https://identity.example.test/realms/mentorflow/"
                "protocol/openid-connect/token"
            ),
            jwks_uri=(
                "https://identity.example.test/realms/mentorflow/"
                "protocol/openid-connect/certs"
            ),
        ),
        transaction=transaction,
        authorization_url=authorization_url,
    )


def test_existing_pkce_transaction_can_force_explicit_account_authentication() -> None:
    prepared = _prepared()
    query = parse_qs(urlsplit(prepared.authorization_url).query)

    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["mentorflow-desktop"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) >= 43
    assert query["state"] == [prepared.transaction.state]
    assert query["nonce"] == [prepared.transaction.nonce]
    assert query["prompt"] == ["login"]


def test_navigation_policy_allows_only_keycloak_origin_and_exact_loopback() -> None:
    policy = InAppOidcNavigationPolicy.from_prepared(_prepared())

    assert policy.allows("about:blank")
    assert policy.allows(
        "https://identity.example.test/realms/mentorflow/login-actions/authenticate?x=1"
    )
    assert policy.allows(
        "http://127.0.0.1:43210/oauth/callback?code=opaque&state=opaque"
    )
    assert not policy.allows("http://identity.example.test/realms/mentorflow")
    assert not policy.allows("https://unrelated.example.test/")
    assert not policy.allows("https://identity.example.test.evil.test/")
    assert not policy.allows("https://identity.example.test@evil.test/")
    assert not policy.allows("http://127.0.0.1:43211/oauth/callback?code=opaque")
    assert not policy.allows("http://127.0.0.1:43210/other?code=opaque")


def test_auth_dialog_uses_dedicated_off_the_record_profile() -> None:
    application = _application()
    dialog = InAppOidcLoginDialog(_config())
    try:
        assert dialog.objectName() == AUTH_DIALOG_OBJECT_NAME
        assert dialog.view.objectName() == AUTH_VIEW_OBJECT_NAME
        assert dialog.is_off_the_record is True
        assert dialog.profile.storageName() == ""
        assert (
            dialog.profile.httpCacheType()
            is QWebEngineProfile.HttpCacheType.NoCache
        )
        assert (
            dialog.profile.persistentCookiesPolicy()
            is QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        settings = dialog.view.settings()
        assert settings.testAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows
        ) is False
        assert settings.testAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard
        ) is False
        assert settings.testAttribute(
            QWebEngineSettings.WebAttribute.LocalStorageEnabled
        ) is False
        assert settings.testAttribute(
            QWebEngineSettings.WebAttribute.AllowRunningInsecureContent
        ) is False
    finally:
        dialog._reset_browser()
        dialog.close()
        application.processEvents()


def test_each_auth_dialog_gets_a_fresh_memory_only_profile() -> None:
    application = _application()
    first = InAppOidcLoginDialog(_config())
    second = InAppOidcLoginDialog(_config())
    try:
        assert first.profile is not second.profile
        assert first.profile.storageName() == second.profile.storageName() == ""
        assert first.is_off_the_record is second.is_off_the_record is True
    finally:
        first._reset_browser()
        second._reset_browser()
        first.close()
        second.close()
        application.processEvents()


def test_dialog_reuses_prepare_loopback_and_complete_protocol() -> None:
    application = _application()
    prepared = _prepared(port=43212)
    callbacks: list[str] = []
    calls: list[object] = []
    identity = AccountIdentity(
        issuer="https://identity.example.test/realms/mentorflow",
        subject="subject-a",
        display_name="Account A",
        organization_id="org-a",
        workspace_id="workspace-a",
    )
    result = EnterpriseOidcLoginResult(
        identity=identity,
        access_token="opaque-access",
        refresh_token="opaque-refresh",
        server_state={"authenticated": True},
    )

    class Receiver:
        def wait(self, timeout_seconds: float) -> str:
            assert timeout_seconds == 15
            callback = (
                "http://127.0.0.1:43212/oauth/callback?code=opaque&state=opaque"
            )
            callbacks.append(callback)
            return callback

        def close(self) -> None:
            calls.append("receiver.close")

    prepared = PreparedOidcLogin(
        receiver=Receiver(),  # type: ignore[arg-type]
        discovery=prepared.discovery,
        transaction=prepared.transaction,
        authorization_url=prepared.authorization_url,
    )

    class Client:
        async def prepare_login(
            self,
            *,
            force_reauthentication: bool = False,
        ) -> PreparedOidcLogin:
            calls.append(("prepare", force_reauthentication))
            return prepared

        async def complete_login(
            self,
            value: PreparedOidcLogin,
            callback_url: str,
        ) -> EnterpriseOidcLoginResult:
            assert value is prepared
            calls.append(("complete", callback_url))
            return result

        async def aclose(self) -> None:
            calls.append("client.close")

    class Dialog(InAppOidcLoginDialog):
        def _show_authorization_page(self, value: object) -> None:
            assert value is prepared
            self._prepared = prepared
            self._interceptor.policy = InAppOidcNavigationPolicy.from_prepared(
                prepared
            )
            self._page_ready.set()

    dialog = Dialog(_config(), client_factory=lambda _config: Client())
    returned = dialog.execute_login()
    application.processEvents()

    assert returned is result
    assert calls[0] == ("prepare", True)
    assert callbacks == [
        "http://127.0.0.1:43212/oauth/callback?code=opaque&state=opaque"
    ]
    assert calls.count("receiver.close") == 1
    assert calls[-1] == "client.close"


def test_desktop_login_surface_retains_protocol_shell_without_page_scripting() -> None:
    account_page = Path("desktop/account_settings_page.py").read_text(encoding="utf-8")
    embedded = Path("desktop/in_app_oidc.py").read_text(encoding="utf-8")

    assert "login_enterprise_account_in_app_from_env" not in account_page
    assert "inAppOidcLoginButton" in account_page
    assert "externalBrowserOAuthButton" not in account_page
    assert "login_enterprise_account_from_env" not in account_page
    assert "open_system_browser_oauth" not in embedded
    # Explicit legacy consumers retain the OIDC shell; normal login is native.
    assert "self._config.requires_system_browser" in embedded
    assert "runJavaScript" not in embedded
    assert "setHtml" not in embedded


@pytest.mark.parametrize('native_switch_available', [True, False])
def test_account_page_uses_native_switch_or_fails_closed(tmp_path, monkeypatch, native_switch_available):
    application = _application()
    profile = AccountProfile.personal_local()
    manager = AccountVaultManager(VaultLayout(tmp_path / 'vault'))
    manager.mount(profile)
    called = []
    def forbidden(*args, **kwargs):
        pytest.fail('Default account switching must not open a legacy browser or persist credentials')
    monkeypatch.setattr('PySide6.QtGui.QDesktopServices.openUrl', forbidden)
    monkeypatch.setattr('desktop.in_app_oidc.InAppOidcLoginDialog', forbidden)
    class Credentials:
        store_oauth_tokens = forbidden
        logout = forbidden
    page = AccountSettingsPage(profile=profile, vault_manager=manager,
        credential_vault=Credentials(), switch_account=forbidden, logout_account=forbidden,
        begin_account_switch=(lambda: called.append('native-switch')) if native_switch_available else None)
    try:
        page.oauth_button.click()
        application.processEvents()
        assert called == (['native-switch'] if native_switch_available else [])
        assert page._oidc_thread is None and page.oauth_button.isEnabled()
        if not native_switch_available:
            assert '原生登录窗口' in page.status.text()
    finally:
        page.close()
        manager.unmount()


def test_credential_result_remains_an_opaque_protocol_return_value() -> None:
    identity = AccountIdentity(
        issuer="https://identity.example.test/realms/mentorflow",
        subject="subject-a",
        display_name="Account A",
        organization_id="org-a",
        workspace_id="workspace-a",
    )
    result = EnterpriseOidcLoginResult(
        identity=identity,
        access_token="opaque-access",
        refresh_token="opaque-refresh",
        server_state={"authenticated": True},
    )

    assert repr(result).find("opaque-access") == -1
    assert repr(result).find("opaque-refresh") == -1
