"""Signed disposable authority + real adapters/storage; no live identity or data."""
import asyncio
import hashlib
import json

import httpx
import pytest
from alembic import command
from sqlalchemy import create_engine, text

from app.account_identity_contract import account_id
from app.enterprise.identity.continuity import SignedIdentityContinuity, canonical_principal_issuer, from_env
from app.enterprise.identity.issuer_migration import OLD_ISSUER, NEW_ISSUER
from app.enterprise.identity.native import MtlsIdentityVerifier, NativeSessions
from app.enterprise.identity.oidc import IdentityError, OidcIdentityAdapter
from app.enterprise_tenancy.workspace_bootstrap import AccountWorkspaceBootstrap, personal_scope
from identity_continuity_fixture import signed_mapping, continuity
from test_enterprise_identity_oidc import NOW, _claims, _token, _StaticSigningKey, signing_keys
from test_e109_issuer_migration import config


def test_resident_bootstrap_uses_canonical_identity_after_public_hostname_change(monkeypatch):
    from types import SimpleNamespace
    from app import account_core_mvp as module
    from app.enterprise.contracts_v1 import TenantMode, ActorKind
    from app.enterprise_tenancy.models import EnterprisePersistenceRequestError

    async def close():
        pass

    engine = SimpleNamespace(dispose=lambda: None)
    runtime = SimpleNamespace(aclose=close, native_sessions=None)
    monkeypatch.setattr(module.EnterpriseIdentityConfig, 'from_env', lambda: SimpleNamespace(
        mode=TenantMode.ENTERPRISE_SERVER,
        keycloak=config(issuer_migration=None, transport_issuer=None)))
    monkeypatch.setattr(module.orm_runtime, 'configure_enterprise_orm', lambda: engine)
    monkeypatch.setattr(module.orm_runtime, 'verify_enterprise_runtime_schema', lambda: None)
    monkeypatch.setattr(module, 'build_enterprise_identity_runtime', lambda **kw: runtime)
    monkeypatch.setattr(module, 'overview_from_env', lambda *args, **kwargs: None)
    for name in ('EnterpriseWorkspaceService', 'StructuredSyncService',
                 'PostgresTenantTransactionManager', 'EnterpriseDocumentImportService'):
        monkeypatch.setattr(module, name, lambda *args: None)
    app = SimpleNamespace(state=SimpleNamespace())

    async def run():
        async with module.lifespan(app):
            bootstrap = app.state.account_workspace_bootstrap
            bootstrap._validate(SimpleNamespace(issuer=OLD_ISSUER,
                subject='existing-subject', actor_kind=ActorKind.HUMAN))
            with pytest.raises(EnterprisePersistenceRequestError):
                bootstrap._validate(SimpleNamespace(issuer=NEW_ISSUER,
                    subject='existing-subject', actor_kind=ActorKind.HUMAN))

    asyncio.run(run())


def test_public_hostname_without_canonical_anchor_fails_at_configuration():
    from app.enterprise.identity.config import IdentityConfigurationError
    from desktop.enterprise_oidc import DesktopOidcConfig, EnterpriseOidcError
    with pytest.raises(IdentityConfigurationError):
        config(identity_issuer=None, issuer_migration=None, transport_issuer=None)
    with pytest.raises(EnterpriseOidcError):
        DesktopOidcConfig(issuer=NEW_ISSUER, client_id='mentorflow-desktop',
            audience='mentorflow-api', api_base_url='https://api.example.test')


@pytest.mark.parametrize('change', ['signature', 'key', 'digest', 'bytes', 'duplicate', 'unknown', 'rename', 'schema'])
def test_mapping_tamper_duplicates_and_unapproved_issuers_rejected(change):
    args = list(signed_mapping())
    if change == 'signature': args[1] = b'\0' * 64
    if change == 'key': args[2] = b'\0' * 32
    if change == 'digest': args[3] = '0' * 64
    if change == 'bytes': args[0] += b' '
    if change == 'duplicate': args = signed_mapping(('account-001', 'account-001'))
    if change == 'unknown': args = signed_mapping(new_issuer='https://unknown.test/realms/mentorflow')
    if change == 'rename': args = signed_mapping(aliases={'account-001':'other-user'})
    if change == 'schema': args = signed_mapping(schema='v2')
    with pytest.raises(ValueError): SignedIdentityContinuity(*args)


@pytest.mark.parametrize('encoding', ['utf-16', 'duplicate-field'])
def test_even_signed_noncanonical_documents_are_rejected(encoding):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = signed_mapping()[0]
    raw = raw.decode().encode('utf-16') if encoding == 'utf-16' else b'{"audit_id":"extra",' + raw[1:]
    key = Ed25519PrivateKey.generate()
    with pytest.raises(ValueError):
        SignedIdentityContinuity(raw, key.sign(raw), key.public_key().public_bytes_raw(), hashlib.sha256(raw).hexdigest())


def test_trust_anchors_all_or_none_and_pinned_file(tmp_path):
    assert from_env({}) is None
    raw, signature, key, digest = signed_mapping()
    path = tmp_path/'map.json'; path.write_bytes(raw)
    env = dict(MENTORFLOW_IDENTITY_CONTINUITY_FILE=str(path),
        MENTORFLOW_IDENTITY_CONTINUITY_SIGNATURE_HEX=signature.hex(),
        MENTORFLOW_IDENTITY_CONTINUITY_PUBLIC_KEY_HEX=key.hex(),
        MENTORFLOW_IDENTITY_CONTINUITY_SHA256=digest)
    assert from_env(env).resolve(NEW_ISSUER, 'account-001', OLD_ISSUER) == OLD_ISSUER
    for name in env:
        with pytest.raises(ValueError): from_env({k:v for k,v in env.items() if k != name})
    path.write_bytes(raw+b' ')
    with pytest.raises(ValueError): from_env(env)


@pytest.mark.parametrize('claims', [
    {'sub':'different-sub', 'preferred_username':'same-name'},
    {'sub':'different-sub', 'email':'same@example.test'},
    {'iss':'https://unknown.test/realms/mentorflow'},
])
def test_authenticated_wrong_subject_name_email_and_unknown_issuer_fail_closed(signing_keys, claims):
    key, _ = signing_keys
    adapter = OidcIdentityAdapter(config(), signing_keys=_StaticSigningKey(key.public_key()), clock=lambda:NOW)
    with pytest.raises(IdentityError):
        asyncio.run(adapter.validate_access_token(_token(key, claims=_claims(**{'iss':NEW_ISSUER, **claims}))))


def test_no_mapping_cannot_alias_a_new_subject(signing_keys):
    key, _ = signing_keys
    adapter = OidcIdentityAdapter(config(identity_continuity=None),
        signing_keys=_StaticSigningKey(key.public_key()), clock=lambda:NOW)
    with pytest.raises(IdentityError):
        asyncio.run(adapter.validate_access_token(_token(key, claims=_claims(iss=NEW_ISSUER))))


def test_account_key_contract_vector_and_external_desktop_evidence(signing_keys):
    assert account_id(OLD_ISSUER, 'account-001') == 'mfa1_943f4dcc6c0a8e6dce869bb560ceec1db44ce5221e34cb238c0587ebb9202cd5'
    key, _ = signing_keys
    adapter = OidcIdentityAdapter(config(identity_continuity=None), external_identity_only=True,
        signing_keys=_StaticSigningKey(key.public_key()), clock=lambda:NOW)
    principal = asyncio.run(adapter.validate_access_token(_token(key, claims=_claims(iss=NEW_ISSUER))))
    assert principal.issuer == NEW_ISSUER  # Local JWT validation cannot establish the OLD account anchor.
    assert account_id(principal.issuer, principal.subject) != account_id(OLD_ISSUER, principal.subject)


@pytest.mark.parametrize('subject,allowed', [('account-001',True), ('other-account',False), (None,False)])
def test_cross_issuer_logout_requires_mapped_subject(signing_keys, subject, allowed):
    key, _ = signing_keys
    adapter = OidcIdentityAdapter(config(), signing_keys=_StaticSigningKey(key.public_key()), clock=lambda:NOW)
    claims = dict(iss=NEW_ISSUER, aud='mentorflow-web', iat=int(NOW.timestamp()), jti='logout-fixture',
                  sid='fixture-session', events={'http://schemas.openid.net/event/backchannel-logout':{}})
    if subject is not None: claims['sub']=subject
    token = _token(key, header={'typ':'logout+jwt'}, claims=claims)
    if allowed: assert asyncio.run(adapter.validate_logout_token(token))['sub'] == subject
    else:
        with pytest.raises(IdentityError): asyncio.run(adapter.validate_logout_token(token))


def test_native_revalidation_uses_external_authority_and_retains_canonical_anchor():
    calls = []
    def respond(request):
        value = json.loads(request.content); calls.append(value.get('issuer'))
        return httpx.Response(200, json=dict(schema='mentorflow.native-identity.v1',outcome='authenticated',
            issuer=NEW_ISSUER, subject='account-001', display_name='Renamed display',
            authentication_strength='password_verified', verification_handle='a'*43))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            verifier = MtlsIdentityVerifier(NEW_ISSUER+'/mentorflow-native-identity/v1', OLD_ISSUER,
                external_issuer=NEW_ISSUER, continuity=continuity(), http_client=http)
            sessions = NativeSessions(verifier, OLD_ISSUER)
            pair = await sessions.login('display-only', 'synthetic-fixture-value', 'fixture')
            old = sessions.authenticate(pair['access_token'])
            rotated = await sessions.refresh(pair['refresh_token'])
            new = sessions.authenticate(rotated['access_token'])
            assert (old.issuer, old.subject) == (new.issuer, new.subject) == (OLD_ISSUER,'account-001')
            verifier.continuity = continuity(('another-account',))
            with pytest.raises(IdentityError): await sessions.refresh(rotated['refresh_token'])
            with pytest.raises(IdentityError): sessions.authenticate(rotated['access_token'])
    asyncio.run(run())
    assert calls == [None, NEW_ISSUER, NEW_ISSUER]


def test_old_new_keep_membership_workspace_resources_and_vault(tmp_path, monkeypatch, signing_keys):
    from test_enterprise_workspace_persistence import _config
    from desktop.account_identity import AccountIdentity
    from desktop.account_vault import AccountVaultManager, VaultLayout, AssetAvailability
    key, _ = signing_keys
    adapter = OidcIdentityAdapter(config(), signing_keys=_StaticSigningKey(key.public_key()), clock=lambda:NOW)
    principals = [asyncio.run(adapter.validate_access_token(_token(key, claims=_claims(iss=issuer))))
                  for issuer in (OLD_ISSUER, NEW_ISSUER)]
    database = tmp_path/'fixture.sqlite'
    command.upgrade(_config(database), 'head')
    engine = create_engine('sqlite:///'+database.as_posix())
    service = AccountWorkspaceBootstrap(engine, issuer=OLD_ISSUER)
    try:
        before = service.initialize(principals[0])
        workspace = before['workspaces'][0]
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO workspace_goals(goal_id, organization_id, workspace_id, payload_json, confirmed) "
                "VALUES ('kept-resource', :org, :ws, '{}', false)"),
                dict(org=workspace['organization_id'], ws=workspace['id']))
        assert service.initialize(principals[1]) == before
        with engine.connect() as connection:
            assert connection.execute(text('SELECT count(*) FROM organization_memberships')).scalar_one() == 1
            assert connection.execute(text('SELECT count(*) FROM workspace_memberships')).scalar_one() == 1
            assert connection.execute(text('SELECT goal_id FROM workspace_goals')).scalar_one() == 'kept-resource'
        identities = [AccountIdentity(p.issuer,p.subject,'Changed label') for p in principals]
        assert identities[0].account_id == identities[1].account_id == account_id(OLD_ISSUER,'account-001')
        layout = VaultLayout(tmp_path/'fixture-vault', test_only=True)
        manager = AccountVaultManager(layout)
        first = manager.mount(identities[0].profile())
        marker = first.root/'data'/'continuity.txt'; marker.write_text('disposable resource binding')
        binding = first.upsert_resource_binding(resource_id='kept-resource', local_path=marker,
            fingerprint_sha256=hashlib.sha256(marker.read_bytes()).hexdigest(),
            availability=AssetAvailability.AVAILABLE_LOCAL)
        second = manager.mount(identities[1].profile())
        assert second.root == first.root and marker.read_text() == 'disposable resource binding'
        assert second.get_resource_binding('kept-resource') == binding
        assert not layout.resolve(identities[1].profile()).target_storage_stable
        manager.unmount()
        assert personal_scope(OLD_ISSUER,'account-001') != personal_scope(NEW_ISSUER,'account-001')
        # A raw external principal cannot skip the continuity adapter.
        with pytest.raises(Exception): service.initialize(principals[1].model_copy(update={'issuer':NEW_ISSUER}))
    finally:
        engine.dispose()
