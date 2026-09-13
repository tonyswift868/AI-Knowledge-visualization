"""Real native/session/membership HTTP boundary, exact 04 route + producer.

Only identity verification and snapshot contents are synthetic. No live database,
Keycloak, SMTP, schema 0037 migration, or PostgreSQL/RLS qualification is claimed.
"""
import asyncio
import subprocess
import sys
import types
from pathlib import Path

import httpx
from sqlalchemy import text

from app import account_core_mvp
from app.enterprise.identity.native import NativeSessions
from desktop.account_identity import AccountPreferenceStore
from desktop.enterprise_oidc import DesktopOidcConfig
from desktop.login_session import LoginSession
from desktop.native_auth import NativeCredentialClient
from desktop.rehydration_transport import invalidate_active
from test_account_workspace_bootstrap import system
from test_native_auth import Verifier, ISSUER

INTEGRATION = '04e2d61e3e39729886b3a2e6c7aff022b65a382a'


def test_native_session_to_exact_sync_route_with_server_membership_and_renewal(system, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    modules = [
        ('app.enterprise_tenancy.structured_sync', 'backend/app/enterprise_tenancy/structured_sync.py'),
        ('app.enterprise.structured_sync_api', 'backend/app/enterprise/structured_sync_api.py'),
        ('desktop.structured_sync', 'desktop/structured_sync.py'),
    ]
    for name, path in modules:
        source = subprocess.check_output(['git', 'show', INTEGRATION + ':' + path], cwd=root)
        module = types.ModuleType(name)
        monkeypatch.setitem(sys.modules, name, module)
        exec(compile(source, INTEGRATION + '/' + path, 'exec'), module.__dict__)
    app = account_core_mvp.app
    previous_routes = list(app.router.routes)
    app.include_router(sys.modules['app.enterprise.structured_sync_api'].router)
    seen = []
    class SnapshotFixture:
        def snapshot(self, actor, tenant):
            seen.append((actor.subject_id, tenant.organization_id, tenant.workspace_id))
            return {'schema':'mentorflow.structured-sync.v1', 'complete':True, 'entities':[]}
    monkeypatch.setattr(app.state, 'structured_sync_service', SnapshotFixture(), raising=False)
    client, _, engine = system
    client.base_url = httpx.URL('https://api.example.test')
    runtime = app.state.enterprise_identity_runtime
    runtime.native_sessions = NativeSessions(Verifier(), ISSUER)
    requests = []
    def bridge(request):
        requests.append(request.url.path)
        response = client.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
        return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))
    config = DesktopOidcConfig(ISSUER, 'mentorflow-desktop', 'mentorflow-api', 'https://api.example.test')
    async def authenticate():
        async with httpx.AsyncClient(transport=httpx.MockTransport(bridge)) as http:
            return await NativeCredentialClient(config, http_client=http).login('known', 'synthetic-test-only')
    class Settings:
        def __init__(self): self.values = {}
        def value(self, key, default=None): return self.values.get(key, default)
        def setValue(self, key, value): self.values[key] = value
        def sync(self): pass
    invalidate_active()
    try:
        result = asyncio.run(authenticate())
        adapter = result.rehydration_session
        adapter._client = httpx.Client(transport=httpx.MockTransport(bridge))
        now = [0]
        adapter._clock = lambda: now[0]
        adapter._deadline = 299
        session = LoginSession(AccountPreferenceStore(Settings()))
        session.accept(result, remember=False, automatic=False)
        first = adapter.bootstrap()
        assert first.provenance == 'SERVER_CONFIRMED'
        assert seen == [('user-a', result.identity.organization_id, result.identity.workspace_id)]
        assert '/api/enterprise/account/workspaces/initialize' in requests
        assert requests.index('/api/enterprise/auth/me') < requests.index('/api/enterprise/sync/v1/snapshot')
        token_headers = {'Authorization':'Bearer '+result.access_token,
                         'X-MentorFlow-Organization-ID':result.identity.organization_id,
                         'X-MentorFlow-Workspace-ID':'tampered-workspace'}
        assert client.get('/api/enterprise/sync/v1/snapshot', headers=token_headers).status_code == 403
        assert client.get('/api/enterprise/sync/v1/snapshot').status_code == 401
        assert len(seen) == 1
        now[0] = 300
        renewed = adapter.bootstrap()
        assert renewed.provenance == 'SERVER_CONFIRMED'
        assert first.provenance == 'SESSION_STALE'
        assert '/api/enterprise/auth/native/refresh' in requests
        # Membership revocation must be enforced on the sync route, not just /me.
        with engine.begin() as connection:
            connection.execute(text("UPDATE workspace_memberships SET status='revoked' WHERE subject_id='user-a'"))
        assert adapter.bootstrap().provenance == 'UNAUTHENTICATED'
        assert len(seen) == 2
        assert renewed.provenance == 'SESSION_STALE'
    finally:
        invalidate_active()
        app.router.routes[:] = previous_routes
