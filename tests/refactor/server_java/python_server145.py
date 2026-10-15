"""Real local Python sync HTTP process for the 145 Java parity suite."""
from __future__ import annotations

import os
import uvicorn

from refactor.server import app as entry
from refactor.server import orm
from refactor.server.enterprise.contracts_v1 import TenantMode
from refactor.server.enterprise.identity.config import EnterpriseIdentityConfig, KeycloakOidcConfig
from refactor.server.enterprise.identity.keycloak import KeycloakOidcClient
from refactor.server.enterprise.identity.runtime import EnterpriseIdentityRuntime
from refactor.server.enterprise.structured_sync_api import router as structured_sync_router
from refactor.server.enterprise_tenancy.structured_sync import StructuredSyncService
from refactor.server.enterprise_tenancy.context import PostgresTenantTransactionManager


class Directory145:
    def __init__(self, issuer):
        self.issuer = issuer

    def ensure_open(self, _issuer, _subject):
        return None

    def resolve_membership(self, *, issuer, subject, organization_id, workspace_id):
        if subject == "user-145-db-failure":
            from sqlalchemy.exc import OperationalError
            raise OperationalError("membership lookup", {}, Exception("connection reset"))
        from refactor.server.enterprise.identity.models import MembershipGrant, MembershipStatus, BusinessRole
        if (issuer == self.issuer and (subject == "user-145" or subject.startswith("user-145-"))
                and organization_id == "org-145" and workspace_id == "ws-145"):
            return MembershipGrant(
                membership_id="membership-145-" + subject,
                issuer=issuer, subject=subject, organization_id=organization_id,
                workspace_id=workspace_id, roles=(BusinessRole.LEARNER,),
                status=MembershipStatus.ACTIVE)
        return None

    def resolve_service_identity(self, **_kwargs):
        return None


issuer = os.environ["MENTORFLOW_KEYCLOAK_ISSUER"]
configuration = EnterpriseIdentityConfig(
    mode=TenantMode.ENTERPRISE_SERVER,
    keycloak=KeycloakOidcConfig(
        issuer=issuer, client_id="mentorflow-desktop", audience="mentorflow-api",
        redirect_uri="http://127.0.0.1/callback", allow_loopback_http=True,
        max_token_lifetime_seconds=3600),
    browser_allowed_origins=("http://127.0.0.1",))
identity = EnterpriseIdentityRuntime(
    configuration, client=KeycloakOidcClient(configuration), directory=Directory145(issuer))
entry.app.state.enterprise_identity_runtime = identity
engine = orm.configure_enterprise_orm()
entry.app.state.structured_sync_service = StructuredSyncService(PostgresTenantTransactionManager(engine))
entry.account_core_health_registry.started = True

if __name__ == "__main__":
    uvicorn.run(entry.app, host="127.0.0.1", port=int(os.environ["CONTRACT145_PORT"]),
                log_level="error", access_log=False, lifespan="off", proxy_headers=False)
