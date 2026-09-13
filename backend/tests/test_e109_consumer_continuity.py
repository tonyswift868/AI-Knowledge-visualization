"""Signed-token consumer rehearsal with mock HTTPS, directory and credential IO."""
import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from app.enterprise.contracts_v1 import TenantMode
from app.enterprise.identity.config import EnterpriseIdentityConfig
from app.enterprise.identity.directory import PostgresIdentityDirectory
from app.enterprise.identity.keycloak import InMemoryEnterpriseSessionStore, KeycloakOidcClient
from app.enterprise.identity.oidc import OidcIdentityAdapter
from test_e109_issuer_migration import config, window, OLD_ISSUER, NEW_ISSUER
from test_enterprise_identity_context import _session
from test_enterprise_identity_directory import _Engine
from test_enterprise_identity_oidc import NOW, _claims, _token, _StaticSigningKey, signing_keys


@pytest.mark.parametrize('refreshed_issuer', [OLD_ISSUER, NEW_ISSUER])
def test_backend_old_session_refresh_keeps_membership_id_and_scope(monkeypatch, signing_keys, refreshed_issuer):
    key, _ = signing_keys
    monkeypatch.setattr('app.enterprise.identity.keycloak._now', lambda: NOW)
    adapter = OidcIdentityAdapter(config(), signing_keys=_StaticSigningKey(key.public_key()), clock=lambda: NOW)
    directory = PostgresIdentityDirectory(_Engine(), issuer=OLD_ISSUER, verify_application_role=False)
    calls = []
    def handler(request):
        calls.append(str(request.url))
        assert parse_qs(request.content.decode()) == {
            'grant_type': ['refresh_token'], 'client_id': ['mentorflow-web'], 'refresh_token': ['old-refresh'],
        }
        return httpx.Response(200, json={
            'access_token': _token(key, claims=_claims(iss=refreshed_issuer)), 'refresh_token': 'rotated-refresh',
        })
    def membership(principal):
        return directory.resolve_membership(issuer=principal.issuer, subject=principal.subject,
            organization_id='org-1', workspace_id='workspace-1')
    async def scenario():
        old = await adapter.validate_access_token(_token(key, claims=_claims(iss=OLD_ISSUER)))
        session = _session().model_copy(update={'principal': old})
        store = InMemoryEnterpriseSessionStore()
        store.put(session, refresh_token='old-refresh')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = KeycloakOidcClient(EnterpriseIdentityConfig(mode=TenantMode.ENTERPRISE_SERVER, keycloak=config()),
                oidc=adapter, store=store, http_client=http)
            try:
                refreshed = await client.refresh_session(session.session_id)
                assert refreshed.principal.issuer == OLD_ISSUER
                assert membership(refreshed.principal) == membership(old)
                assert membership(old) is not None
                assert refreshed.session_id == session.session_id
                assert refreshed.selected_organization_id == session.selected_organization_id
                assert refreshed.selected_workspace_id == session.selected_workspace_id
                assert store.get_for_refresh(session.session_id).refresh_token == 'rotated-refresh'
            finally:
                await client.aclose()
    asyncio.run(scenario())
    assert calls == [OLD_ISSUER + '/protocol/openid-connect/token']


@pytest.mark.parametrize('discovered,refresh,server_anchor', [
    (OLD_ISSUER,False,'correct'), (NEW_ISSUER,False,'correct'),
    (OLD_ISSUER,True,'correct'), (NEW_ISSUER,True,'correct'),
    (NEW_ISSUER,True,None), (NEW_ISSUER,True,'wrong-account')])
def test_desktop_old_credentials_restore_and_rotate_in_same_vault(monkeypatch, tmp_path, signing_keys, discovered, refresh, server_anchor):
    from desktop.account_identity import AccountIdentity, AccountCredentialVault
    from desktop.account_vault import VaultLayout
    from desktop.enterprise_oidc import DesktopOidcConfig, EnterpriseOidcClient, EnterpriseOidcError
    from backend.app import credential_store
    from test_enterprise_identity_oidc import _b64

    key, _ = signing_keys
    monkeypatch.setattr('desktop.enterprise_oidc.time.time', lambda: NOW.timestamp())
    values = {}
    class MemoryLocker:
        def get_password(self, service, username): return values.get((service, username))
        def set_password(self, service, username, value): values[service, username] = value
        def delete_password(self, service, username): values.pop((service, username), None)
    monkeypatch.setattr(credential_store, '_get_keyring', MemoryLocker)
    monkeypatch.delenv('MENTORFLOW_CREDENTIAL_STORE', raising=False)
    cfg = DesktopOidcConfig(issuer=NEW_ISSUER, identity_issuer=OLD_ISSUER, issuer_migration=window(),
        client_id='mentorflow-desktop', audience='mentorflow-api', api_base_url='https://localhost:18080')
    identity = AccountIdentity(OLD_ISSUER, 'account-001', 'Existing Account')
    vault = AccountCredentialVault()
    old_access = _token(key, claims=_claims(iss=OLD_ISSUER, azp=cfg.client_id))
    vault.store_oauth_tokens(identity, access_token=old_access, refresh_token='old-refresh')
    old_refs = set(values)
    old_values = dict(values)
    layout = VaultLayout(tmp_path / 'vault', test_only=True)
    numbers = key.public_key().public_numbers()
    jwk = {'kty': 'RSA', 'use': 'sig', 'alg': 'RS256', 'kid': 'kid-001',
        'n': _b64(numbers.n.to_bytes((numbers.n.bit_length()+7)//8, 'big')),
        'e': _b64(numbers.e.to_bytes((numbers.e.bit_length()+7)//8, 'big'))}
    backend = OidcIdentityAdapter(config(client_id=cfg.client_id),
        signing_keys=_StaticSigningKey(key.public_key()), clock=lambda: NOW)
    directory = PostgresIdentityDirectory(_Engine(), issuer=OLD_ISSUER, verify_application_role=False)
    memberships, calls = [], []
    async def handler(request):
        url = str(request.url)
        calls.append(url)
        if url == NEW_ISSUER + '/.well-known/openid-configuration':
            return httpx.Response(200, json={'issuer': discovered, **{
                field: discovered + '/protocol/openid-connect/' + suffix
                for field, suffix in [('authorization_endpoint', 'auth'), ('token_endpoint', 'token'), ('jwks_uri', 'certs')]
            }})
        if url == discovered + '/protocol/openid-connect/certs':
            return httpx.Response(200, json={'keys': [jwk]})
        if url == discovered + '/protocol/openid-connect/token':
            assert refresh
            assert parse_qs(request.content.decode()) == {
                'grant_type': ['refresh_token'], 'client_id': [cfg.client_id], 'refresh_token': ['old-refresh'],
            }
            return httpx.Response(200, json={'token_type': 'Bearer', 'refresh_token': 'rotated-refresh',
                'access_token': _token(key, claims=_claims(iss=discovered, azp=cfg.client_id))})
        assert url.startswith(cfg.api_base_url + '/api/enterprise/')
        principal = await backend.validate_access_token(request.headers['Authorization'][7:])
        grant = directory.resolve_membership(issuer=principal.issuer, subject=principal.subject,
            organization_id='org-1', workspace_id='workspace-1')
        assert grant is not None
        memberships.append(grant.membership_id)
        if url.endswith('/account/workspaces'):
            return httpx.Response(200, json={'initialization_required': False, 'workspaces': [{
                'id': 'workspace-1', 'organization_id': 'org-1', 'title': 'Existing workspace',
                'status': 'active', 'initialization_status': 'READY',
            }]})
        assert url.endswith('/auth/me')
        from app.account_identity_contract import account_id
        return httpx.Response(200, json={'authenticated': True, 'account': {'subject_id': principal.subject,
            'account_id': account_id(principal.issuer, principal.subject) if server_anchor == 'correct' else server_anchor},
            'context': {'organization_id': 'org-1', 'workspace_id': 'workspace-1'}})

    # The real desktop validator/JWKS resolver still verify generated RSA tokens.
    # Replace only transport and the clock; no signature/claim gate is mocked.
    real_http = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_http(
        **{**kwargs, 'transport': httpx.MockTransport(handler)}))
    monkeypatch.setattr('app.enterprise.identity.oidc.OidcIdentityAdapter',
        lambda cfg, **kwargs: OidcIdentityAdapter(cfg, clock=lambda: NOW, **kwargs))
    async def scenario():
        client = EnterpriseOidcClient(cfg)
        try:
            if server_anchor != 'correct':
                with pytest.raises(EnterpriseOidcError):
                    await client.restore(identity.profile(), vault, refresh_session=refresh)
                assert values == old_values  # No alias credential namespace or unverified rotation is stored.
                return
            result = await client.restore(identity.profile(), vault, refresh_session=refresh)
            assert result.identity.issuer == OLD_ISSUER and result.identity.subject == identity.subject
            assert layout.root_for_profile(result.identity.profile()) == layout.root_for_profile(identity.profile())
            assert result.restored and result.identity.workspace_id == 'workspace-1'
            assert vault.load_oauth_tokens(identity.profile()) == (result.access_token, result.refresh_token)
        finally:
            await client.aclose()
    asyncio.run(scenario())
    assert set(values) == old_refs  # No new credential namespace on rotation.
    assert len(set(memberships)) == 1
    assert calls.count(discovered + '/protocol/openid-connect/token') == int(refresh)
