"""Transaction-scoped PostgreSQL tenant context and pool cleanup."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine

from refactor.server.enterprise.contracts_v1 import ActorContext, TenantContext, TenantMode

from .models import TenantContextLeakError, TenantContextRequiredError
from .schema import (
    APPLICATION_DATABASE_ROLE,
    FORCE_RLS_TABLES,
    TENANT_CONTEXT_SETTINGS,
)


_SET_CONTEXT = text(
    """
    SELECT
      set_config('mentorflow.organization_id', :organization_id, true),
      set_config('mentorflow.workspace_id', :workspace_id, true),
      set_config('mentorflow.actor_subject', :actor_subject, true)
    """
)
_READ_CONTEXT = text(
    """
    SELECT
      NULLIF(current_setting('mentorflow.organization_id', true), ''),
      NULLIF(current_setting('mentorflow.workspace_id', true), ''),
      NULLIF(current_setting('mentorflow.actor_subject', true), '')
    """
)


@dataclass(frozen=True, slots=True)
class TenantTransactionContext:
    """Application-local binding of the two frozen shared context contracts."""

    tenant: TenantContext
    actor: ActorContext

    def __post_init__(self) -> None:
        require_matching_actor_context(self.tenant, self.actor)

    @property
    def database_values(self) -> tuple[str, str, str]:
        return require_matching_actor_context(self.tenant, self.actor)


@dataclass(frozen=True, slots=True)
class TenantDbSession:
    connection: Connection
    transaction_context: TenantTransactionContext

    @property
    def tenant(self) -> TenantContext:
        return self.transaction_context.tenant

    @property
    def actor(self) -> ActorContext:
        return self.transaction_context.actor


def TenantConnection(
    connection: Connection,
    context: TenantContext,
    actor: ActorContext,
) -> TenantDbSession:
    """Construct the already integrated E1-02 connection shape compatibly."""

    return TenantDbSession(
        connection=connection,
        transaction_context=TenantTransactionContext(
            tenant=context,
            actor=actor,
        ),
    )


@dataclass(frozen=True, slots=True)
class ApplicationRoleSecurity:
    current_user: str
    session_user: str
    is_superuser: bool
    bypasses_rls: bool
    owned_tenant_table_count: int
    forced_rls_table_count: int

    @property
    def safe(self) -> bool:
        return (
            self.current_user == APPLICATION_DATABASE_ROLE
            and not self.is_superuser
            and not self.bypasses_rls
            and self.owned_tenant_table_count == 0
            and self.forced_rls_table_count == len(FORCE_RLS_TABLES)
        )


def _require_postgresql(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise ValueError("enterprise tenant persistence requires PostgreSQL")


def require_enterprise_workspace_context(
    context: TenantContext,
) -> tuple[str, str]:
    """Return the non-null tenant ids required by workspace persistence."""

    if context.mode is not TenantMode.ENTERPRISE_SERVER:
        raise TenantContextRequiredError(
            "enterprise tenant persistence requires ENTERPRISE_SERVER mode"
        )
    if context.organization_id is None or context.workspace_id is None:
        raise TenantContextRequiredError(
            "enterprise tenant persistence requires organization_id and workspace_id"
        )
    return context.organization_id, context.workspace_id


def require_matching_actor_context(
    context: TenantContext,
    actor: ActorContext,
) -> tuple[str, str, str]:
    """Bind the frozen actor and tenant contracts to one persistence boundary."""

    organization_id, workspace_id = require_enterprise_workspace_context(context)
    if actor.organization_id != organization_id or actor.workspace_id != workspace_id:
        raise TenantContextRequiredError(
            "actor organization/workspace must match the tenant context"
        )
    return organization_id, workspace_id, actor.subject_id


def target_workspace_transaction_context(
    tenant: TenantContext,
    actor: ActorContext,
    workspace_id: str,
) -> TenantTransactionContext:
    """Bind a server-selected workspace id to the frozen shared contracts."""

    organization_id, _current_workspace_id, _subject_id = (
        require_matching_actor_context(tenant, actor)
    )
    target_workspace_id = str(workspace_id or "").strip()
    if not target_workspace_id or len(target_workspace_id) > 96:
        raise TenantContextRequiredError("target workspace_id is invalid")
    return TenantTransactionContext(
        tenant=tenant.model_copy(update={"workspace_id": target_workspace_id}),
        actor=actor.model_copy(
            update={
                "organization_id": organization_id,
                "workspace_id": target_workspace_id,
            }
        ),
    )


def _dbapi_context_values(dbapi_connection: Any) -> tuple[Any, Any, Any]:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(
            """
            SELECT
              NULLIF(current_setting('mentorflow.organization_id', true), ''),
              NULLIF(current_setting('mentorflow.workspace_id', true), ''),
              NULLIF(current_setting('mentorflow.actor_subject', true), '')
            """
        )
        values = tuple(cursor.fetchone())
    finally:
        cursor.close()
        dbapi_connection.rollback()
    if len(values) != len(TENANT_CONTEXT_SETTINGS):
        raise TenantContextLeakError("unexpected tenant context projection")
    return values  # type: ignore[return-value]


def _reset_dbapi_context(dbapi_connection: Any) -> None:
    """Clear session state outside a transaction before pool reuse."""

    dbapi_connection.rollback()
    previous_autocommit = getattr(dbapi_connection, "autocommit", None)
    if previous_autocommit is not None:
        dbapi_connection.autocommit = True
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("RESET ALL")
    finally:
        cursor.close()
        if previous_autocommit is not None:
            dbapi_connection.autocommit = previous_autocommit


def require_clean_dbapi_checkout(dbapi_connection: Any) -> None:
    """Reject and scrub a pooled connection that still carries tenant state."""

    values = _dbapi_context_values(dbapi_connection)
    if any(value is not None for value in values):
        _reset_dbapi_context(dbapi_connection)
        raise TenantContextLeakError(
            "pooled PostgreSQL connection retained tenant context"
        )


def install_postgres_pool_context_safety(engine: Engine) -> None:
    """Install fail-closed checkout validation and explicit pool reset."""

    _require_postgresql(engine)
    if getattr(engine, "_mentorflow_tenant_pool_safety", False):
        return

    @event.listens_for(engine.pool, "reset")
    def _reset_on_return(
        dbapi_connection: Any,
        _connection_record: Any,
        reset_state: Any,
    ) -> None:
        if not reset_state.terminate_only:
            _reset_dbapi_context(dbapi_connection)

    @event.listens_for(engine.pool, "checkout")
    def _deny_leaked_checkout(
        dbapi_connection: Any,
        _connection_record: Any,
        _connection_proxy: Any,
    ) -> None:
        require_clean_dbapi_checkout(dbapi_connection)

    setattr(engine, "_mentorflow_tenant_pool_safety", True)


def create_enterprise_postgres_engine(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 5,
) -> Engine:
    if not database_url.startswith(("postgresql://", "postgresql+")):
        raise ValueError("database_url must use a PostgreSQL SQLAlchemy dialect")
    engine = create_engine(
        database_url,
        future=True,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_reset_on_return="rollback",
    )
    install_postgres_pool_context_safety(engine)
    return engine


class PostgresTenantTransactionManager:
    """Only exposes connections after setting all tenant GUCs locally."""

    def __init__(self, engine: Engine) -> None:
        _require_postgresql(engine)
        install_postgres_pool_context_safety(engine)
        self.engine = engine

    @contextmanager
    def transaction(
        self,
        context: TenantTransactionContext,
    ) -> Iterator[TenantDbSession]:
        expected = context.database_values
        with self.engine.connect() as connection:
            with connection.begin():
                before = tuple(connection.execute(_READ_CONTEXT).one())
                if any(value is not None for value in before):
                    raise TenantContextLeakError(
                        "connection entered transaction with tenant context"
                    )
                connection.execute(
                    _SET_CONTEXT,
                    {
                        "organization_id": expected[0],
                        "workspace_id": expected[1],
                        "actor_subject": expected[2],
                    },
                )
                applied = tuple(connection.execute(_READ_CONTEXT).one())
                if applied != expected:
                    raise TenantContextLeakError(
                        "PostgreSQL rejected transaction tenant context"
                    )
                yield TenantDbSession(
                    connection=connection,
                    transaction_context=context,
                )


def inspect_application_role_security(
    connection: Connection,
    *,
    expected_role: str = APPLICATION_DATABASE_ROLE,
) -> ApplicationRoleSecurity:
    row = connection.execute(
        text(
            """
            SELECT
              current_user,
              session_user,
              role.rolsuper,
              role.rolbypassrls,
              (
                SELECT count(*)
                FROM pg_class AS class
                JOIN pg_roles AS owner ON owner.oid = class.relowner
                WHERE class.relname = ANY(:tenant_tables)
                  AND owner.rolname = :expected_role
              ),
              (
                SELECT count(*)
                FROM pg_class AS class
                WHERE class.relname = ANY(:forced_tables)
                  AND class.relrowsecurity
                  AND class.relforcerowsecurity
              )
            FROM pg_roles AS role
            WHERE role.rolname = current_user
            """
        ),
        {
            "expected_role": expected_role,
            "tenant_tables": list(FORCE_RLS_TABLES),
            "forced_tables": list(FORCE_RLS_TABLES),
        },
    ).one()
    return ApplicationRoleSecurity(
        current_user=str(row[0]),
        session_user=str(row[1]),
        is_superuser=bool(row[2]),
        bypasses_rls=bool(row[3]),
        owned_tenant_table_count=int(row[4]),
        forced_rls_table_count=int(row[5]),
    )


def require_safe_application_role(connection: Connection) -> None:
    security = inspect_application_role_security(connection)
    if not security.safe:
        raise TenantContextLeakError(
            "enterprise application role must be mentorflow_app, non-owner, "
            "non-superuser, NOBYPASSRLS, with FORCE RLS on every tenant table"
        )


__all__ = [
    "ApplicationRoleSecurity",
    "PostgresTenantTransactionManager",
    "TenantDbSession",
    "TenantConnection",
    "TenantTransactionContext",
    "create_enterprise_postgres_engine",
    "inspect_application_role_security",
    "install_postgres_pool_context_safety",
    "require_clean_dbapi_checkout",
    "require_enterprise_workspace_context",
    "require_matching_actor_context",
    "require_safe_application_role",
    "target_workspace_transaction_context",
]
