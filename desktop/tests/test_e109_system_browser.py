"""E109 uses the OS browser; every provider response here is synthetic."""
import asyncio
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from app.enterprise.identity.issuer_migration import (
    AUTHORITY_SHA256, IssuerMigration, NEW_ISSUER, OLD_ISSUER,
)
from desktop.enterprise_oidc import (
    DesktopOidcConfig, EnterpriseOidcClient, EnterpriseOidcError, bind_auth_authority,
)
from desktop.in_app_oidc import InAppOidcLoginDialog


def config(*, migrated=False, prepared=False):
    if prepared:
        from scripts import build_e108_user_candidate as build
        from scripts.windows_installed_launch import ISSUER
        manifest = build.version_document(build.BASE_VERSION, {})
        assert 'auth_authority' not in manifest
        return DesktopOidcConfig.from_env(bind_auth_authority({
            'MENTORFLOW_DESKTOP_OIDC_ISSUER': ISSUER,
            'MENTORFLOW_DESKTOP_OIDC_CLIENT_ID': 'mentorflow-desktop',
            'MENTORFLOW_DESKTOP_OIDC_AUDIENCE': 'mentorflow-api',
            'MENTORFLOW_ENTERPRISE_API_BASE_URL': 'https://localhost:18080',
            'MENTORFLOW_DESKTOP_OIDC_CALLBACK_PORT': '0',
        }, manifest.get('auth_authority')))
    return DesktopOidcConfig(
        issuer=NEW_ISSUER, identity_issuer=OLD_ISSUER,
        issuer_migration=None if migrated else IssuerMigration(100, 200, AUTHORITY_SHA256),
        client_id='mentorflow-desktop', audience='mentorflow-api',
        api_base_url='https://localhost:18080',
    )


def prepare(cfg, discovered):
    async def scenario():
        document = {'issuer': discovered, **{
            field: discovered + '/protocol/openid-connect/' + suffix
            for field, suffix in [('authorization_endpoint', 'auth'), ('token_endpoint', 'token'), ('jwks_uri', 'certs')]
        }}
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=document)
        )) as http:
            client = EnterpriseOidcClient(cfg, http_client=http)
            try:
                return await client.prepare_login(force_reauthentication=True)
            finally:
                await client.aclose()
    return asyncio.run(scenario())


@pytest.mark.parametrize('embedded', [False, True])
@pytest.mark.parametrize('discovered,migrated,prepared_build', [
    (OLD_ISSUER, False, True), (OLD_ISSUER, False, False),
    (NEW_ISSUER, False, False), (NEW_ISSUER, True, False),
])
def test_both_desktop_surfaces_open_system_browser_and_preserve_pkce(monkeypatch, embedded, discovered, migrated, prepared_build):
    monkeypatch.setattr(time, 'time', lambda: 150)
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(url.toString()) or True)
    def embedded_browser_forbidden(self):
        pytest.fail('E109 must not instantiate an embedded authentication browser')
    monkeypatch.setattr(InAppOidcLoginDialog, '_ensure_browser', embedded_browser_forbidden)
    cfg = config(migrated=migrated, prepared=prepared_build)
    prepared = prepare(cfg, discovered)
    dialog = None
    try:
        dialog = InAppOidcLoginDialog(cfg, embedded=embedded)
        dialog._show_authorization_page(prepared)
        assert opened == [prepared.authorization_url]
        assert not dialog._browser_created and dialog._page_ready.is_set()
        query = parse_qs(urlsplit(opened[0]).query)
        callback = urlsplit(query['redirect_uri'][0])
        assert cfg.callback_port == 0 and callback.hostname == '127.0.0.1'
        assert callback.port == prepared.receiver.port and callback.port != 18080
        assert query['response_type'] == ['code'] and query['code_challenge_method'] == ['S256']
        assert query['state'] == [prepared.transaction.state]
        assert query['nonce'] == [prepared.transaction.nonce]
        assert query['prompt'] == ['login']
        assert prepared.transaction.expected_issuer == discovered
        callback_url = prepared.receiver.redirect_uri + '?' + urlencode({
            'code': 'synthetic-code', 'state': prepared.transaction.state, 'iss': discovered,
        })
        code = prepared.transaction.consume_callback(callback_url)
        assert code.code == 'synthetic-code' and code.code_verifier
    finally:
        prepared.receiver.close()
        if dialog is not None:
            dialog.dispose()
            QApplication.instance().processEvents()


@pytest.mark.parametrize('raises', [False, True])
@pytest.mark.parametrize('embedded', [False, True])
@pytest.mark.parametrize('prepared_build', [False, True])
def test_failed_os_browser_launch_closes_callback_without_embedded_fallback(monkeypatch, raises, embedded, prepared_build):
    monkeypatch.setattr(time, 'time', lambda: 150)
    monkeypatch.setattr(InAppOidcLoginDialog, '_ensure_browser',
                        lambda _: pytest.fail('OS browser failure must not open embedded authentication'))
    def failed_launch(url):
        if raises:
            raise OSError('synthetic browser failure')
        return False
    monkeypatch.setattr(QDesktopServices, 'openUrl', failed_launch)
    cfg = config(prepared=prepared_build)
    prepared = prepare(cfg, OLD_ISSUER)
    dialog = InAppOidcLoginDialog(cfg, embedded=embedded)
    try:
        dialog._show_authorization_page(prepared)
        assert dialog._error.code == 'ENTERPRISE_OIDC_BROWSER_UNAVAILABLE'
        assert dialog._page_ready.is_set() and dialog._cancelled.is_set()
        assert prepared.receiver._closed.is_set() and not dialog._browser_created
    finally:
        dialog.dispose()
        QApplication.instance().processEvents()


@pytest.mark.parametrize('discovered,prepared_build', [(OLD_ISSUER, True), (OLD_ISSUER, False), (NEW_ISSUER, False)])
def test_callback_cannot_switch_to_other_allowed_issuer_within_transaction(monkeypatch, discovered, prepared_build):
    monkeypatch.setattr(time, 'time', lambda: 150)
    prepared = prepare(config(prepared=prepared_build), discovered)
    try:
        callback = prepared.receiver.redirect_uri + '?' + urlencode({
            'code': 'synthetic-code', 'state': prepared.transaction.state,
            'iss': NEW_ISSUER if discovered == OLD_ISSUER else OLD_ISSUER,
        })
        with pytest.raises(EnterpriseOidcError) as error:
            prepared.transaction.consume_callback(callback)
        assert error.value.code == 'ENTERPRISE_OIDC_ISSUER_INVALID'
        assert not prepared.transaction.verifier_available
    finally:
        prepared.receiver.close()


@pytest.mark.parametrize('prepared_build', [False, True])
def test_cancelled_login_never_launches_browser(monkeypatch, prepared_build):
    monkeypatch.setattr(time, 'time', lambda: 150)
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda _: pytest.fail('cancelled browser launch'))
    cfg = config(prepared=prepared_build)
    prepared = prepare(cfg, OLD_ISSUER)
    dialog = InAppOidcLoginDialog(cfg, embedded=True)
    try:
        dialog.reject()
        dialog._show_authorization_page(prepared)
        assert not dialog._browser_created
    finally:
        prepared.receiver.close()
        dialog.dispose()
        QApplication.instance().processEvents()


def test_prepared_old_browser_does_not_start_migration_or_accept_new_discovery():
    cfg = config(prepared=True)
    assert cfg.requires_system_browser
    assert cfg.issuer == cfg.canonical_identity_issuer == OLD_ISSUER
    assert cfg.issuer_migration is None
    assert cfg.api_base_url == 'https://localhost:18080' and cfg.callback_port == 0
    assert cfg.accepts_issuer(OLD_ISSUER)
    assert not cfg.accepts_issuer(NEW_ISSUER)
    assert not cfg.accepts_issuer('https://unapproved.example/realms/mentorflow')
    with pytest.raises(EnterpriseOidcError) as error:
        prepare(cfg, NEW_ISSUER)
    assert error.value.code == 'ENTERPRISE_OIDC_DISCOVERY_INVALID'
