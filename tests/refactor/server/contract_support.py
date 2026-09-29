"""Local fixtures; no external identity, mail, model, or database connections."""
import importlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

ISSUER = 'https://identity.example.test/realms/mentorflow'
ORIGIN = 'https://mentorflow.example'


def module(root, name):
    return importlib.import_module(root + '.' + name)


def principal(root, subject='user-a'):
    now = datetime.now(timezone.utc)
    return module(root, 'enterprise.identity.models').AuthenticatedPrincipal(
        issuer=ISSUER, audiences=('mentorflow-api',), token_id='test-token',
        subject=subject, issued_at=now, expires_at=now + timedelta(minutes=5),
        authentication_time=now, authentication_strength='password_verified',
        client_id='mentorflow-desktop')


def runtime(root, engine=None):
    models = module(root, 'enterprise.identity.models')
    config = module(root, 'enterprise.identity.config')
    error = module(root, 'enterprise.identity.oidc').IdentityError

    class Client:
        async def authenticate_access_token(self, token):
            if token not in ('token-a', 'token-b'):
                raise error('OIDC_TOKEN_INVALID', 'Bearer token is invalid')
            return principal(root, 'user-' + token[-1])

        async def aclose(self):
            pass

    class Directory:
        def resolve_membership(self, *, issuer, subject, organization_id, workspace_id):
            if engine is not None:
                from sqlalchemy import text
                with engine.connect() as connection:
                    role = connection.execute(text('''
                        SELECT om.role FROM organization_memberships om
                        JOIN workspace_memberships wm ON wm.organization_id=om.organization_id
                          AND wm.subject_id=om.subject_id
                        WHERE om.organization_id=:org AND wm.workspace_id=:ws
                          AND om.subject_id=:subject AND om.status='active' AND wm.status='active'
                    '''), dict(org=organization_id, ws=workspace_id, subject=subject)).scalar_one_or_none()
                if issuer != ISSUER or role is None:
                    return None
                return models.MembershipGrant(membership_id='member-' + subject, issuer=issuer,
                    subject=subject, organization_id=organization_id, workspace_id=workspace_id,
                    roles=(models.BusinessRole(role),), status=models.MembershipStatus.ACTIVE)
            if issuer != ISSUER or organization_id != 'org' or workspace_id != 'ws':
                return None
            return models.MembershipGrant(membership_id='member-' + subject, issuer=issuer,
                subject=subject, organization_id='org', workspace_id='ws',
                roles=(models.BusinessRole.LEARNER,), status=models.MembershipStatus.ACTIVE)

        def resolve_service_identity(self, **kwargs):
            return None

    value = config.EnterpriseIdentityConfig(
        mode=module(root, 'enterprise.contracts_v1').TenantMode.ENTERPRISE_SERVER,
        keycloak=config.KeycloakOidcConfig(issuer=ISSUER, client_id='mentorflow-web',
            audience='mentorflow-api', redirect_uri=ORIGIN + '/api/enterprise/auth/callback'),
        browser_allowed_origins=(ORIGIN,))
    return module(root, 'enterprise.identity.runtime').EnterpriseIdentityRuntime(
        value, client=Client(), directory=Directory())


def headers():
    return {'Authorization': 'Bearer token-a', 'X-MentorFlow-Organization-ID': 'org',
            'X-MentorFlow-Workspace-ID': 'ws', 'X-Request-ID': 'slim-contract'}


@contextmanager
def client(root, monkeypatch):
    entry = module(root, 'account_core_mvp' if root == 'app' else 'app')
    value = runtime(root)
    monkeypatch.setattr(entry.app.state, 'enterprise_identity_runtime', value, raising=False)
    registry = entry.account_core_health_registry
    monkeypatch.setattr(registry, 'started', True)
    monkeypatch.setattr(registry, '_probes', {})
    connection = TestClient(entry.app, base_url=ORIGIN)
    try:
        yield connection, value
    finally:
        connection.close()
