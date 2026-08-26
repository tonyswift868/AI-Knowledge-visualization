"""Real PostgreSQL single-slot pool reuse gate for Ubuntu staging."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os

from sqlalchemy import text

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    TenantContext,
    TenantMode,
)
from app.enterprise_tenancy.context import (
    PostgresTenantTransactionManager,
    TenantTransactionContext,
    create_enterprise_postgres_engine,
)


def _context(organization: int, workspace: int, subject: int) -> TenantTransactionContext:
    organization_id = f"synthetic-org-{organization}"
    workspace_id = f"{organization_id}-workspace-{workspace}"
    actor = ActorContext(
        subject_id=f"synthetic-user-{subject:02d}",
        actor_kind=ActorKind.HUMAN,
        issuer="e1-02-staging",
        token_id=f"token-{organization}-{subject}",
        session_id=f"session-{organization}-{subject}",
        request_id=f"request-{organization}-{subject}",
        authentication_time=datetime.now(timezone.utc),
        authentication_strength="staging",
        organization_id=organization_id,
        workspace_id=workspace_id,
        membership_id=f"membership-{organization}-{subject}",
        roles=("MEMBER",),
    )
    tenant = TenantContext(
        organization_id=organization_id,
        workspace_id=workspace_id,
        mode=TenantMode.ENTERPRISE_SERVER,
    )
    return TenantTransactionContext(tenant=tenant, actor=actor)


def main() -> None:
    database_url = os.environ["E1_DATABASE_URL"]
    engine = create_enterprise_postgres_engine(
        database_url,
        pool_size=1,
        max_overflow=0,
    )
    manager = PostgresTenantTransactionManager(engine)
    reused_connection_ids: list[int] = []

    with manager.transaction(_context(1, 1, 1)) as session:
        session.connection.execute(text("SET LOCAL ROLE mentorflow_app"))
        reused_connection_ids.append(id(session.connection.connection.driver_connection))
        own_count = int(
            session.connection.execute(text("SELECT count(*) FROM documents")).scalar_one()
        )
        if own_count < 1:
            raise AssertionError("tenant A could not read its own seeded document")

    with manager.transaction(_context(2, 1, 8)) as session:
        session.connection.execute(text("SET LOCAL ROLE mentorflow_app"))
        reused_connection_ids.append(id(session.connection.connection.driver_connection))
        foreign_count = int(
            session.connection.execute(
                text(
                    "SELECT count(*) FROM documents "
                    "WHERE organization_id = 'synthetic-org-1'"
                )
            ).scalar_one()
        )
        if foreign_count != 0:
            raise AssertionError("tenant B observed tenant A after pool reuse")

    if len(set(reused_connection_ids)) != 1:
        raise AssertionError("pool gate did not reuse the same DBAPI connection")
    engine.dispose()
    print(
        json.dumps(
            {
                "gate": "REAL_POSTGRES_POOL_CONTEXT",
                "same_dbapi_connection_reused": True,
                "unauthorized_success": 0,
                "status": "PASS",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
