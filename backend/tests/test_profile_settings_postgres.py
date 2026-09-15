"""Real app-role PostgreSQL qualification; runner accepts no external DSN."""
from datetime import datetime, timezone
import json

from alembic import command
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import select, text

from test_account_import_ledger_schema import native
from test_enterprise_account_core_api import ISSUER, _principal
from app.enterprise.contracts_v1 import ActorContext, ActorKind, TenantContext, TenantMode
from app.enterprise_tenancy.context import PostgresTenantTransactionManager, require_safe_application_role
from app.enterprise_tenancy.profile_settings_store import LocalProfileSettingsStore
from app.enterprise_tenancy.structured_sync import StructuredSyncService, Write
from app.enterprise_tenancy.sync_schema import require_structured_sync_schema
from app.enterprise_tenancy.workspace_bootstrap import AccountWorkspaceBootstrap


@pytest.fixture
def server(native):
    command.upgrade(native.config, 'head')
    bootstrap = AccountWorkspaceBootstrap(native.app, issuer=ISSUER)
    contexts = []
    for subject in ('profile-fixture-a', 'profile-fixture-b'):
        principal = _principal(subject, 'synthetic')
        workspace = bootstrap.initialize(principal)['workspaces'][0]
        actor = ActorContext(subject_id=subject, actor_kind=ActorKind.HUMAN, issuer=ISSUER,
            token_id='synthetic', session_id='synthetic', request_id='fixture',
            authentication_time=datetime.now(timezone.utc), authentication_strength='mfa',
            organization_id=workspace['organization_id'], workspace_id=workspace['id'])
        tenant = TenantContext(mode=TenantMode.ENTERPRISE_SERVER,
            organization_id=workspace['organization_id'], workspace_id=workspace['id'])
        contexts.append((actor, tenant))
    return native, StructuredSyncService(PostgresTenantTransactionManager(native.app)), contexts


def local_scope(actor, tenant):
    return dict(account_id=actor.subject_id, organization_id=tenant.organization_id, workspace_id=tenant.workspace_id)


def test_canonical_schema_and_app_role(server):
    native, _, _ = server
    assert ScriptDirectory.from_config(native.config).get_heads() == ['0040_admin_overview_aggregate']
    with native.app.connect() as c:
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0040_admin_overview_aggregate'
        require_safe_application_role(c)
        require_structured_sync_schema(c)


def test_real_snapshot_restores_both_profile_and_settings(server, tmp_path):
    native, service, contexts = server
    actor, tenant = contexts[0]
    scope = local_scope(actor, tenant)
    a = LocalProfileSettingsStore(tmp_path / 'a.sqlite', scope=scope)
    values = dict(locale='zh-CN', timezone='Asia/Shanghai', theme='dark', learning_goal='学会统计',
                  daily_minutes=45, notifications_enabled=False, default_workspace_view='plans')
    a.mutate(**scope, profile={'display_name': '本地学习者'}, setting_updates=values)
    def send(intent):
        service.write(actor, tenant, intent['kind'], intent['entity_id'], Write(
            request_id=intent['request_id'], expected_revision=intent['expected_revision'], payload=intent['payload']))
    assert a.relay_pending(send) == 2
    assert not a.pending()
    a.engine.dispose()
    # A is unavailable from here; B only consumes durable PostgreSQL state.
    b = LocalProfileSettingsStore(tmp_path / 'b.sqlite', scope=scope)
    snapshot = service.snapshot(actor, tenant)
    b.apply_snapshot(snapshot['entities'], **scope)
    assert b.read_profile(**scope) == {'display_name': '本地学习者'}
    assert b.read_settings(**scope) == values
    assert not b.pending(), 'Rehydration must not enqueue echo writes'


def test_scope_isolation_retry_idempotency_and_secret_rejection(server, tmp_path):
    _, service, contexts = server
    actor, tenant = contexts[0]
    other_actor, other_tenant = contexts[1]
    scope = local_scope(actor, tenant)
    a = LocalProfileSettingsStore(tmp_path / 'a.sqlite', scope=scope)
    a.mutate(**scope, profile={'display_name': 'Scoped'}, setting_updates={'locale': 'en-US'})
    intent = a.pending()[0]
    send = lambda i: service.write(actor, tenant, i['kind'], i['entity_id'], Write(request_id=i['request_id'], expected_revision=i['expected_revision'], payload=i['payload']))
    send(intent)  # crash window: server accepted before local ACK
    assert a.retry(intent['request_id'])
    assert a.relay_pending(send) == 2  # replay is idempotent, then ACKs both intents
    assert service.snapshot(other_actor, other_tenant)['entities'] == []
    with pytest.raises(Exception):
        a.mutate(**scope, setting_updates={'password': 'never-store'})
