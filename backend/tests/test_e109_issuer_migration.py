"""Synthetic signed tokens and mock HTTPS only; no server, email or user data."""
import asyncio
import json
import ssl
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.enterprise.identity.config import KeycloakOidcConfig, EnterpriseIdentityConfig, IdentityConfigurationError
from app.enterprise.identity.issuer_migration import IssuerMigration, OLD_ISSUER, NEW_ISSUER, AUTHORITY_SHA256
from app.enterprise.identity.oidc import OidcIdentityAdapter, IdentityError, HttpJwksSigningKeyResolver
from test_enterprise_identity_oidc import NOW, _claims, _token, _StaticSigningKey, signing_keys
from identity_continuity_fixture import continuity


def window():
    return IssuerMigration(int(NOW.timestamp()) - 60, int(NOW.timestamp()) + 3600, AUTHORITY_SHA256)


def config(**overrides):
    values = dict(issuer=NEW_ISSUER, transport_issuer=OLD_ISSUER, identity_issuer=OLD_ISSUER,
        issuer_migration=window(), client_id='mentorflow-web', audience='mentorflow-api',
        redirect_uri='https://localhost:18080/api/enterprise/auth/callback', identity_continuity=continuity())
    values.update(overrides)
    return KeycloakOidcConfig(**values)


@pytest.mark.parametrize('issuer', [OLD_ISSUER, NEW_ISSUER])
def test_old_and_new_signed_tokens_preserve_existing_membership_namespace(signing_keys, issuer):
    from test_enterprise_identity_context import _membership, _session, _Directory
    from app.enterprise.identity.context import enterprise_request_context
    key, _ = signing_keys
    adapter = OidcIdentityAdapter(config(), signing_keys=_StaticSigningKey(key.public_key()), clock=lambda: NOW)
    principal = asyncio.run(adapter.validate_access_token(_token(key, claims=_claims(iss=issuer))))
    assert principal.issuer == OLD_ISSUER and principal.subject == 'account-001'
    grant = _membership().model_copy(update={'issuer': OLD_ISSUER, 'subject': 'account-001'})
    context = enterprise_request_context(session=_session().model_copy(update={'principal': principal}),
        directory=_Directory(membership=grant), request_id='migration', now=NOW)
    assert context.tenant.organization_id == 'org-1'
    assert context.tenant.workspace_id == 'workspace-1'


@pytest.mark.parametrize('changes,error', [
    ({'iss':'https://evil.example/realms/mentorflow'}, 'OIDC_ISSUER_INVALID'),
    ({'iss':NEW_ISSUER+'/'}, 'OIDC_ISSUER_INVALID'),
    ({'iss':NEW_ISSUER+' '}, 'OIDC_ISSUER_INVALID'),
    ({'iss':'https://auth.clannad.win/realms/other'}, 'OIDC_ISSUER_INVALID'),
    ({'iss':'https://auth.clannad.win:8443/realms/mentorflow'}, 'OIDC_ISSUER_INVALID'),
    ({'aud':'other-api'}, 'OIDC_AUDIENCE_INVALID'),
    ({'azp':'other-client'}, 'OIDC_AUTHORIZED_PARTY_INVALID'),
    ({'exp':int(NOW.timestamp())-100}, 'OIDC_TOKEN_EXPIRED'),
])
def test_migration_preserves_negative_token_gates(signing_keys, changes, error):
    key, _ = signing_keys
    adapter = OidcIdentityAdapter(config(), signing_keys=_StaticSigningKey(key.public_key()), clock=lambda: NOW)
    with pytest.raises(IdentityError) as caught:
        asyncio.run(adapter.validate_access_token(_token(key, claims=_claims(**{'iss':NEW_ISSUER, **changes}))))
    assert caught.value.code == error


def test_forged_signature_and_wrong_nonce_still_rejected(signing_keys):
    key, forged = signing_keys
    adapter = OidcIdentityAdapter(config(), signing_keys=_StaticSigningKey(key.public_key()), clock=lambda: NOW)
    with pytest.raises(IdentityError, match='signature'):
        asyncio.run(adapter.validate_access_token(_token(forged, claims=_claims(iss=NEW_ISSUER))))
    token = _token(key, claims=_claims(iss=NEW_ISSUER, aud='mentorflow-web', nonce='wrong'))
    with pytest.raises(IdentityError) as caught:
        asyncio.run(adapter.validate_id_token(token, expected_nonce='expected'))
    assert caught.value.code == 'OIDC_NONCE_INVALID'


def test_expired_window_removes_old_trust_without_changing_identity_namespace(signing_keys):
    key, _ = signing_keys
    expired = IssuerMigration(int(NOW.timestamp())-3600, int(NOW.timestamp()), AUTHORITY_SHA256)
    for migration in (expired, None):
        adapter = OidcIdentityAdapter(config(issuer_migration=migration, transport_issuer=None),
            signing_keys=_StaticSigningKey(key.public_key()), clock=lambda: NOW)
        with pytest.raises(IdentityError) as caught:
            asyncio.run(adapter.validate_access_token(_token(key, claims=_claims(iss=OLD_ISSUER))))
        assert caught.value.code == 'OIDC_ISSUER_INVALID'
        principal = asyncio.run(adapter.validate_access_token(_token(key, claims=_claims(iss=NEW_ISSUER))))
        assert principal.issuer == OLD_ISSUER  # Identity key is not token trust.


def test_default_configuration_stays_old_only(signing_keys):
    key, _ = signing_keys
    cfg = config(issuer=OLD_ISSUER, issuer_migration=None, identity_issuer=None)
    adapter = OidcIdentityAdapter(cfg, signing_keys=_StaticSigningKey(key.public_key()), clock=lambda: NOW)
    assert asyncio.run(adapter.validate_access_token(_token(key, claims=_claims(iss=OLD_ISSUER)))).issuer == OLD_ISSUER
    with pytest.raises(IdentityError):
        asyncio.run(adapter.validate_access_token(_token(key, claims=_claims(iss=NEW_ISSUER))))


@pytest.mark.parametrize('bad', [
    {'not_before':1,'not_after':172802,'authority_sha256':AUTHORITY_SHA256},
    {'not_before':1,'not_after':100,'authority_sha256':'wrong'},
    {'not_before':True,'not_after':100,'authority_sha256':AUTHORITY_SHA256},
    {'not_before':1,'not_after':100,'authority_sha256':AUTHORITY_SHA256,'issuers':['*']},
])
def test_unbounded_unapproved_and_wildcard_configs_fail_closed(bad):
    with pytest.raises(ValueError): IssuerMigration.from_json(json.dumps(bad))


def test_transport_override_is_only_the_exact_controlled_realm():
    for url in ('https://evil.example/realms/mentorflow', 'http://localhost:18080/realms/mentorflow',
                OLD_ISSUER+'/other', 'https://localhost:18081/realms/mentorflow'):
        with pytest.raises(IdentityConfigurationError): config(transport_issuer=url)
    for issuer in ('https://*.clannad.win/realms/mentorflow', 'https://auth.clannad.win/realms/other'):
        with pytest.raises(IdentityConfigurationError): config(issuer=issuer)
    ctx = config().tls_verification
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert config().jwks_uri == OLD_ISSUER+'/protocol/openid-connect/certs'


def test_both_issuers_use_only_configured_signing_source(signing_keys):
    from test_enterprise_identity_oidc import _b64
    key, forged = signing_keys
    numbers = key.public_key().public_numbers()
    jwk = {'kty':'RSA', 'use':'sig', 'alg':'RS256', 'kid':'kid-001',
           'n':_b64(numbers.n.to_bytes((numbers.n.bit_length()+7)//8, 'big')),
           'e':_b64(numbers.e.to_bytes((numbers.e.bit_length()+7)//8, 'big'))}
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={'keys':[jwk]})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            resolver = HttpJwksSigningKeyResolver(config(), http_client=http)
            adapter = OidcIdentityAdapter(config(), signing_keys=resolver, clock=lambda:NOW)
            for issuer in (OLD_ISSUER, NEW_ISSUER):
                assert (await adapter.validate_access_token(_token(key, claims=_claims(iss=issuer)))).issuer == OLD_ISSUER
            # A jku hint is ignored, never trusted or fetched (including a valid token).
            principal = await adapter.validate_access_token(_token(key,
                header={'jku':'https://evil.example/keys'}, claims=_claims(iss=NEW_ISSUER)))
            assert principal.issuer == OLD_ISSUER
            with pytest.raises(IdentityError, match='signature'):
                await adapter.validate_access_token(_token(forged,
                    header={'jku':'https://evil.example/keys'}, claims=_claims(iss=NEW_ISSUER)))
    asyncio.run(scenario())
    assert calls == [OLD_ISSUER+'/protocol/openid-connect/certs']


def test_backend_env_binds_only_explicit_window():
    cfg = EnterpriseIdentityConfig.from_env({
        'MENTORFLOW_DEPLOYMENT_MODE':'ENTERPRISE_SERVER', 'MENTORFLOW_KEYCLOAK_ISSUER':NEW_ISSUER,
        'MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER':OLD_ISSUER,
        'MENTORFLOW_KEYCLOAK_CLIENT_ID':'mentorflow-desktop', 'MENTORFLOW_KEYCLOAK_AUDIENCE':'mentorflow-api',
        'MENTORFLOW_KEYCLOAK_REDIRECT_URI':'https://localhost:18080/api/enterprise/auth/callback',
        'MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER':OLD_ISSUER,
        'MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON':json.dumps(window().__dict__)})
    assert cfg.keycloak.accepts_issuer(OLD_ISSUER, NOW.timestamp())
    assert cfg.keycloak.accepts_issuer(NEW_ISSUER, NOW.timestamp())
    assert not cfg.keycloak.accepts_issuer('https://untrusted', NOW.timestamp())


@pytest.mark.parametrize('discovered', [OLD_ISSUER, NEW_ISSUER])
def test_desktop_follows_exact_discovery_before_and_after_cutover_with_same_loopback(monkeypatch, discovered):
    from desktop.enterprise_oidc import DesktopOidcConfig, EnterpriseOidcClient, BackendOidcTokenValidator
    monkeypatch.setattr('desktop.enterprise_oidc.time.time', lambda: NOW.timestamp())
    cfg = DesktopOidcConfig(issuer=NEW_ISSUER, identity_issuer=OLD_ISSUER, issuer_migration=window(),
        client_id='mentorflow-desktop', audience='mentorflow-api', api_base_url='https://localhost:18080')
    document = dict(issuer=discovered, authorization_endpoint=discovered+'/protocol/openid-connect/auth',
        token_endpoint=discovered+'/protocol/openid-connect/token', jwks_uri=discovered+'/protocol/openid-connect/certs')
    requests = []
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, json=document)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = EnterpriseOidcClient(cfg, http_client=http)
            prepared = await client.prepare_login()
            try:
                callback = urlsplit(prepared.receiver.redirect_uri)
                assert callback.hostname == '127.0.0.1' and callback.port > 0 and callback.path == '/oauth/callback'
                assert prepared.authorization_url.startswith(discovered+'/protocol/openid-connect/auth?')
                query = parse_qs(urlsplit(prepared.authorization_url).query)
                assert query['response_type'] == ['code'] and query['code_challenge_method'] == ['S256']
                assert query['state'] and query['nonce'] and query['code_challenge']
                validator = BackendOidcTokenValidator(cfg, redirect_uri=prepared.receiver.redirect_uri, discovery=prepared.discovery)
                try:
                    assert validator._adapter.config.issuer == NEW_ISSUER
                    assert validator._adapter.config.canonical_identity_issuer == OLD_ISSUER
                finally: await validator.aclose()
            finally: prepared.receiver.close(); await client.aclose()
    asyncio.run(scenario())
    assert requests == [NEW_ISSUER+'/.well-known/openid-configuration']


def test_desktop_new_config_does_not_change_api_callback_or_profile_identity(monkeypatch):
    from desktop.enterprise_oidc import bind_auth_authority, DesktopOidcConfig
    from desktop.account_identity import issuer_hash
    env = {'MENTORFLOW_DESKTOP_OIDC_ISSUER':OLD_ISSUER,
        'MENTORFLOW_DESKTOP_OIDC_CLIENT_ID':'mentorflow-desktop', 'MENTORFLOW_DESKTOP_OIDC_AUDIENCE':'mentorflow-api',
        'MENTORFLOW_ENTERPRISE_API_BASE_URL':'https://localhost:18080', 'MENTORFLOW_DESKTOP_OIDC_CALLBACK_PORT':'43210'}
    assert bind_auth_authority(env, None) == env
    bound = bind_auth_authority(env, {'issuer':NEW_ISSUER,'identity_issuer':OLD_ISSUER,'migration':window().__dict__})
    cfg = DesktopOidcConfig.from_env(bound)
    assert cfg.issuer == NEW_ISSUER and cfg.api_base_url == env['MENTORFLOW_ENTERPRISE_API_BASE_URL']
    assert cfg.callback_host == '127.0.0.1' and cfg.callback_port == 43210 and cfg.callback_path == '/oauth/callback'
    assert issuer_hash(cfg.canonical_identity_issuer) == issuer_hash(OLD_ISSUER)


def test_unknown_discovery_and_endpoint_escape_rejected(monkeypatch):
    from desktop.enterprise_oidc import DesktopOidcConfig, EnterpriseOidcClient, EnterpriseOidcError
    monkeypatch.setattr('desktop.enterprise_oidc.time.time', lambda: NOW.timestamp())
    cfg = DesktopOidcConfig(issuer=NEW_ISSUER, identity_issuer=OLD_ISSUER, issuer_migration=window(),
        client_id='mentorflow-desktop', audience='mentorflow-api', api_base_url='https://localhost:18080')
    for discovered, endpoint in [('https://evil.example/realms/mentorflow',NEW_ISSUER), (NEW_ISSUER,'https://evil.example')]:
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={
                'issuer':discovered,'authorization_endpoint':endpoint+'/protocol/openid-connect/auth',
                'token_endpoint':endpoint+'/protocol/openid-connect/token','jwks_uri':endpoint+'/protocol/openid-connect/certs'}))) as http:
                client=EnterpriseOidcClient(cfg,http_client=http)
                with pytest.raises(EnterpriseOidcError): await client._discovery()
                await client.aclose()
        asyncio.run(scenario())


def test_runtime_preflight_supports_cutover_but_not_unknown_or_expired_authority(monkeypatch):
    import importlib.util
    from pathlib import Path
    from io import BytesIO
    from urllib.error import HTTPError
    path = Path(__file__).resolve().parents[2] / 'deploy/enterprise/staging/rp05/runtime-entrypoint.py'
    spec = importlib.util.spec_from_file_location('e109_runtime_preflight', path)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    for name, value in {
        'MENTORFLOW_KEYCLOAK_ISSUER': NEW_ISSUER,
        'MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER': OLD_ISSUER,
        'MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER': OLD_ISSUER,
        'MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON': json.dumps(window().__dict__),
        'MENTORFLOW_KEYCLOAK_CLIENT_ID': 'mentorflow-web',
        'MENTORFLOW_KEYCLOAK_REDIRECT_URI': 'https://localhost:18080/api/enterprise/auth/callback',
    }.items(): monkeypatch.setenv(name, value)
    monkeypatch.setattr(runtime.time, 'time', lambda: NOW.timestamp())
    discovered = OLD_ISSUER
    token_error = 'invalid_grant'
    calls = []
    class Response(BytesIO):
        status = 200
    def open_url(request, timeout):
        url = getattr(request, 'full_url', request)
        calls.append(url)
        assert url.startswith(OLD_ISSUER + '/')
        if url.endswith('/.well-known/openid-configuration'):
            body = {'issuer': discovered, **{name: discovered + '/protocol/openid-connect/' + suffix
                for name, suffix in [('authorization_endpoint','auth'), ('token_endpoint','token'), ('jwks_uri','certs')]}}
        elif url.endswith('/certs'): body = {'keys':[{'kid':'test-only'}]}
        else: raise HTTPError(url, 400, 'Bad Request', None, BytesIO(json.dumps({'error': token_error}).encode()))
        return Response(json.dumps(body).encode())
    monkeypatch.setattr(runtime, 'urlopen', open_url)
    for discovered in (OLD_ISSUER, NEW_ISSUER):
        assert runtime._oidc_ready() == 0
    # A reachable token URL with a broken client/grant contract is not ready.
    for token_error in ('invalid_client', 'unauthorized_client', 'unsupported_grant_type', 'invalid_request', ''):
        with pytest.raises(SystemExit): runtime._oidc_ready()
    token_error = 'invalid_grant'
    discovered = 'https://evil.example/realms/mentorflow'
    with pytest.raises(SystemExit): runtime._oidc_ready()
    discovered = OLD_ISSUER
    monkeypatch.setattr(runtime.time, 'time', lambda: window().not_after)
    with pytest.raises(SystemExit): runtime._oidc_ready()
    discovered = NEW_ISSUER
    assert runtime._oidc_ready() == 0


def test_build_authority_is_prepared_only_and_activation_preserves_api(monkeypatch, tmp_path):
    from scripts import build_e108_user_candidate as build
    from desktop import version
    from desktop.startup_connection import preview_discovery_issuer_allowed
    assert build.load_build_auth_authority() is None
    assert 'auth_authority' not in build.version_document('1.5.0', {})
    payload = json.loads(build.AUTH_AUTHORITY_CONFIG.read_text(encoding='utf-8'))
    payload.update(status='ACTIVE', migration=window().__dict__)
    path = tmp_path / 'auth-authority.json'
    path.write_text(json.dumps(payload), encoding='utf-8')
    monkeypatch.setattr(build, 'AUTH_AUTHORITY_CONFIG', path)
    monkeypatch.setattr('time.time', lambda: NOW.timestamp())
    manifest = build.version_document('1.5.0', {})
    assert manifest['auth_authority']['issuer'] == NEW_ISSUER
    assert manifest['account_capabilities'] == {'register':False, 'forgot_password':False}
    monkeypatch.setattr(version, 'VERSION_MANIFEST', manifest)
    assert preview_discovery_issuer_allowed(OLD_ISSUER)
    assert preview_discovery_issuer_allowed(NEW_ISSUER)
    assert not preview_discovery_issuer_allowed('https://evil.example')
    monkeypatch.setattr('time.time', lambda: window().not_after)
    with pytest.raises(ValueError, match='not active'): build.load_build_auth_authority()
    assert not preview_discovery_issuer_allowed(OLD_ISSUER)
    assert preview_discovery_issuer_allowed(NEW_ISSUER)


def test_backend_overlay_changes_only_auth_binding():
    from pathlib import Path
    import yaml
    path = Path(__file__).resolve().parents[2] / 'deploy/enterprise/staging/rp05/compose.auth-issuer-migration.yml'
    overlay = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert set(overlay) == {'services'} and set(overlay['services']) == {'backend'}
    assert set(overlay['services']['backend']) == {'environment'}
    values = overlay['services']['backend']['environment']
    assert set(values) == {'MENTORFLOW_KEYCLOAK_ISSUER', 'MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER',
                           'MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER', 'MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON'}
    assert values['MENTORFLOW_KEYCLOAK_ISSUER'] == NEW_ISSUER
    assert values['MENTORFLOW_KEYCLOAK_TRANSPORT_ISSUER'] == OLD_ISSUER
    assert values['MENTORFLOW_KEYCLOAK_IDENTITY_ISSUER'] == OLD_ISSUER
