"""Schema 0042 SQLite fixture. It proves HTTP/transactions, never PostgreSQL RLS."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from argparse import Namespace

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text

from refactor.server import app as account_core_mvp
from refactor.server.enterprise.contracts_v1 import ActorContext, ActorKind, TenantContext, TenantMode
from refactor.server.enterprise.identity.models import MembershipGrant, MembershipStatus, BusinessRole
from refactor.server.enterprise_tenancy.context import TenantDbSession, TenantTransactionContext
from refactor.server.enterprise_tenancy.workspace_bootstrap import AccountWorkspaceBootstrap
from contract_support import ISSUER, runtime, principal

ROOT = Path(__file__).resolve().parents[3]


def _runtime():
    return runtime('refactor.server')


def _principal(subject='user-a', token='fixture'):
    return principal('refactor.server', subject)


def _config(database):
    config = Config(str(ROOT / 'refactor/server/alembic.ini'))
    config.set_main_option('sqlalchemy.url', f'sqlite:///{database.as_posix()}')
    config.cmd_opts = Namespace(x=['rag_vector_backend=local-test'])
    config.attributes['configure_logger'] = False
    return config


class _SQLiteTransactions:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def transaction(self, context):
        with self.engine.begin() as connection:
            yield TenantDbSession(connection=connection, transaction_context=context)


@pytest.fixture(scope='module')
def schema_template(tmp_path_factory):
    path = tmp_path_factory.mktemp('slim-schema') / 'template.db'
    command.upgrade(_config(path), 'head')
    return path


@pytest.fixture
def system(schema_template, tmp_path, monkeypatch):
    from shutil import copyfile
    path = tmp_path / 'account.db'
    copyfile(schema_template, path)
    engine = create_engine(f'sqlite:///{path.as_posix()}', connect_args={'timeout': 15})
    service = AccountWorkspaceBootstrap(engine, issuer=ISSUER)
    identity = _runtime()
    identity.directory = Directory(engine)
    monkeypatch.setattr(account_core_mvp.app.state, 'enterprise_identity_runtime', identity, raising=False)
    monkeypatch.setattr(account_core_mvp.app.state, 'account_workspace_bootstrap', service, raising=False)
    connection = TestClient(account_core_mvp.app)
    try:
        yield connection, service, engine
    finally:
        connection.close()
        engine.dispose()


def headers(who='a', workspace=None):
    result = {'Authorization': f'Bearer token-{who}'}
    if workspace:
        result.update({'X-MentorFlow-Organization-ID': workspace['organization_id'],
                       'X-MentorFlow-Workspace-ID': workspace['id']})
    return result


class Directory:
    def __init__(self, engine):
        self.engine = engine

    def resolve_service_identity(self, **kwargs):
        return None

    def resolve_membership(self, *, issuer, subject, organization_id, workspace_id):
        if issuer != ISSUER:
            return None
        with self.engine.connect() as connection:
            row = connection.execute(text("""
                SELECT om.role FROM organization_memberships om
                JOIN organizations o ON o.id = om.organization_id
                JOIN workspace_memberships wm ON wm.organization_id = om.organization_id
                  AND wm.subject_id = om.subject_id
                JOIN workspaces w ON w.id = wm.workspace_id AND w.organization_id = wm.organization_id
                WHERE om.subject_id = :subject AND om.organization_id = :org
                  AND wm.workspace_id = :ws AND om.status = 'active'
                  AND wm.status = 'active' AND o.status = 'active' AND w.status = 'active'
            """), dict(subject=subject, org=organization_id, ws=workspace_id)).first()
        if row is None:
            return None
        return MembershipGrant(membership_id=f"membership-{subject}", issuer=issuer,
                               subject=subject, organization_id=organization_id,
                               workspace_id=workspace_id, roles=(BusinessRole(row[0]),),
                               status=MembershipStatus.ACTIVE)

def _context(
    *,
    organization: str = "org-a",
    workspace: str = "workspace-a",
    subject: str = "owner-a",
) -> tuple[ActorContext, TenantContext]:
    tenant = TenantContext(
        organization_id=organization,
        workspace_id=workspace,
        mode=TenantMode.ENTERPRISE_SERVER,
    )
    actor = ActorContext(
        subject_id=subject,
        actor_kind=ActorKind.HUMAN,
        issuer="offline-enterprise-test",
        token_id=f"token-{subject}",
        session_id=f"session-{subject}",
        request_id=f"request-{subject}",
        authentication_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
        authentication_strength="test",
        organization_id=organization,
        workspace_id=workspace,
        membership_id=f"membership-{organization}-{subject}",
        roles=("OWNER",),
    )
    return actor, tenant
