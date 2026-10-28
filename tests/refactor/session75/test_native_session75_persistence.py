"""Real Alembic/SQLite persistence; identity and network are isolated fakes."""
import asyncio
from argparse import Namespace
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from refactor.server.enterprise.identity.native import NativeSessions, NATIVE_FAMILIES, PREFIX, router, expired
from refactor.server.enterprise.identity.models import AuthenticatedPrincipal
from refactor.server.enterprise.identity.oidc import IdentityError
from refactor.server.enterprise.identity.account_lifecycle import KeycloakAccountAdmin, LifecycleError

ISSUER = 'https://identity.example.test/realms/mentorflow'


class Clock:
    value = 1800000000
    def __call__(self): return self.value


class Verifier:
    def __init__(self, clock): self.clock, self.calls = clock, 0
    async def authenticate(self, username, password):
        self.calls += 1
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        return AuthenticatedPrincipal(issuer=ISSUER, subject=username, audiences=('mentorflow-api',),
            token_id='test', issued_at=now, expires_at=now + timedelta(minutes=5),
            authentication_time=now, authentication_strength='password_verified', client_id='mentorflow-desktop')
    async def revalidate(self, principal): raise AssertionError('SPI lease must never be consulted on refresh')
    async def aclose(self): pass


class Accounts:
    binding = 'a' * 64
    revoked = False
    fault = False
    async def __call__(self, principal):
        if self.fault: raise TimeoutError('private identity fault')
        if self.revoked: raise expired()
        return self.binding


@pytest.fixture
def setup(tmp_path):
    db = tmp_path / 'native.db'
    config = Config(str(Path(__file__).resolve().parents[3] / 'refactor/server/alembic.ini'))
    config.set_main_option('sqlalchemy.url', 'sqlite:///' + db.as_posix())
    config.cmd_opts = Namespace(x=['rag_vector_backend=local-test'])
    config.attributes['configure_logger'] = False
    command.upgrade(config, 'head')
    engine = sa.create_engine('sqlite:///' + db.as_posix())
    clock, accounts = Clock(), Accounts()
    def service(): return NativeSessions(Verifier(clock), ISSUER, clock=clock, engine=engine, account_check=accounts)
    yield engine, clock, accounts, service
    engine.dispose()


def test_ten_years_thousand_rotations_restart_and_replay(setup):
    engine, clock, accounts, factory = setup
    async def run():
        service = factory()
        pair = original = await service.login('user-a', 'synthetic', 'peer')
        clock.value += 10 * 365 * 86400
        for _ in range(1000): pair = await service.refresh(pair['refresh_token'])
        assert service.verifier.calls == 1
        await service.aclose()
        restarted = factory()
        pair = await restarted.refresh(pair['refresh_token'])
        assert restarted.authenticate(pair['access_token']).subject == 'user-a'
        with engine.connect() as conn:
            row = conn.execute(sa.select(NATIVE_FAMILIES)).mappings().one()
        assert row['deadline'] is None and len(row['used_digests']) == 1001
        stored = json.dumps(dict(row))
        assert original['refresh_token'] not in stored and pair['refresh_token'] not in stored
        assert pair['access_token'] not in stored
        with pytest.raises(IdentityError) as error: await restarted.refresh(original['refresh_token'])
        assert error.value.status == 401
        with pytest.raises(IdentityError): await factory().refresh(pair['refresh_token'])
    asyncio.run(run())


@pytest.mark.parametrize('event', ['password', 'closure', 'disabled', 'logout', 'superseded'])
def test_explicit_revocations_survive_restart(setup, event):
    _, _, accounts, factory = setup
    async def run():
        service = factory()
        pair = await service.login('user-a', 'synthetic', 'peer')
        if event == 'password':
            from refactor.server.account.password_sessions import _revoke_local
            oidc = SimpleNamespace(store=SimpleNamespace(revoke_by_subject_or_sid=lambda **kw: None))
            assert _revoke_local(SimpleNamespace(native_sessions=service, client=oidc), 'user-a')
        elif event == 'closure': service.revoke_subject('user-a')
        elif event == 'disabled': accounts.revoked = True
        elif event == 'logout': service.logout(pair['refresh_token'])
        else: await service.login('user-a', 'synthetic', 'other-peer')
        with pytest.raises(IdentityError) as error: await factory().refresh(pair['refresh_token'])
        assert error.value.status == 401
        if event == 'superseded': assert error.value.code == 'NATIVE_SESSION_SUPERSEDED'
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['identity', 'database-read', 'database-write'])
def test_transient_failure_http503_preserves_same_refresh(setup, monkeypatch, fault):
    engine, _, accounts, factory = setup
    service = factory()
    pair = asyncio.run(service.login('user-a', 'synthetic', 'peer'))
    app = FastAPI()
    app.include_router(router)
    app.state.enterprise_identity_runtime = SimpleNamespace(native_sessions=service)
    client = TestClient(app, base_url='https://api.example.test')
    with monkeypatch.context() as patch:
        if fault == 'identity': accounts.fault = True
        else:
            def broken(*args, **kwargs): raise RuntimeError('private database detail')
            patch.setattr(engine, 'connect' if fault == 'database-read' else 'begin', broken)
        result = client.post(PREFIX + '/refresh', json={'refresh_token': pair['refresh_token']})
        assert result.status_code == 503 and 'private' not in result.text
    accounts.fault = False
    assert client.post(PREFIX + '/refresh', json={'refresh_token': pair['refresh_token']}).status_code == 200
    client.close()


def test_password_metadata_change_revokes_and_closed_during_check_cannot_resurrect(setup):
    _, _, accounts, factory = setup
    async def run():
        service = factory()
        pair = await service.login('user-a', 'synthetic', 'peer')
        accounts.binding = 'b' * 64
        with pytest.raises(IdentityError): await service.refresh(pair['refresh_token'])
        pair = await service.login('user-a', 'synthetic', 'peer')
        async def check(principal):
            service.revoke_subject(principal.subject)
            return accounts.binding
        service.account_check = check
        with pytest.raises(IdentityError): await service.refresh(pair['refresh_token'])
    asyncio.run(run())


@pytest.mark.parametrize('status,enabled,expected', [(404, True, 'NATIVE_ACCOUNT_REVOKED'),
    (200, False, 'NATIVE_ACCOUNT_REVOKED'), (500, True, 'KEYCLOAK_OPERATION_REJECTED'),
    (401, True, 'KEYCLOAK_OPERATION_REJECTED')])
def test_account_admin_distinguishes_user_revocation_from_authority_failure(status, enabled, expected):
    async def run():
        async def token(): return 'synthetic-admin'
        def handler(request): return httpx.Response(status, json={'id': 'user-a', 'enabled': enabled})
        admin = KeycloakAccountAdmin('https://identity.example.test', 'mentorflow', token,
                                     transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(LifecycleError) as error: await admin.native_session_state('user-a')
            assert error.value.code == expected
        finally: await admin.aclose()
    asyncio.run(run())


def test_account_admin_reads_metadata_without_password_or_lease():
    async def run():
        async def token(): return 'synthetic-admin'
        timestamp = [1]
        calls = []
        def handler(request):
            calls.append(request.url.path)
            assert request.method == 'GET'
            value = [{'type': 'password', 'id': 'credential-a', 'createdDate': timestamp[0]}] if request.url.path.endswith('/credentials') else {'id': 'user-a', 'enabled': True, 'notBefore': 0}
            return httpx.Response(200, json=value)
        admin = KeycloakAccountAdmin('https://identity.example.test', 'mentorflow', token, transport=httpx.MockTransport(handler))
        first = await admin.native_session_state('user-a')
        assert first == await admin.native_session_state('user-a')
        timestamp[0] = 2
        assert first != await admin.native_session_state('user-a')
        assert all('/admin/realms/mentorflow/users/user-a' in path for path in calls)
        await admin.aclose()
    asyncio.run(run())


def test_migration_downgrade_reupgrade(tmp_path):
    # This test covers the reversible session/RAG migration chain, so exercise
    # it in an isolated database pinned at 0045 instead of downgrading head 0046.
    legacy_db = tmp_path / 'native-legacy-0045.db'
    engine = sa.create_engine('sqlite:///' + legacy_db.as_posix())
    config = Config(str(Path(__file__).resolve().parents[3] / 'refactor/server/alembic.ini'))
    config.set_main_option('sqlalchemy.url', str(engine.url))
    config.cmd_opts = Namespace(x=['rag_vector_backend=local-test'])
    config.attributes['configure_logger'] = False
    command.upgrade(config, '0045_account_closure_rag')
    assert 'native_session_families' in sa.inspect(engine).get_table_names()
    command.downgrade(config, '0042_structured_sync_changes')
    assert 'native_session_families' not in sa.inspect(engine).get_table_names()
    command.upgrade(config, '0045_account_closure_rag')
    assert 'native_session_families' in sa.inspect(engine).get_table_names()
    engine.dispose()
