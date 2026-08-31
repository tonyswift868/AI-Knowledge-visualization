"""Tenant-scoped durable persistence for Enterprise Provider sessions.

Only credential references cross this boundary.  Resolved credential material
is deliberately absent from both the table definition and repository API.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import math

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql.elements import ColumnElement

from app.enterprise.contracts_v1 import ActorContext, TenantContext
from app.enterprise_tenancy.context import (
    PostgresTenantTransactionManager,
    TenantTransactionContext,
    require_matching_actor_context,
)


metadata = sa.MetaData()

enterprise_provider_sessions = sa.Table(
    "enterprise_provider_sessions",
    metadata,
    sa.Column("provider_session_id", sa.String(96), primary_key=True),
    sa.Column("subject_id", sa.String(128), nullable=False),
    sa.Column("organization_id", sa.String(96), nullable=False),
    sa.Column("workspace_id", sa.String(96), nullable=False),
    sa.Column("provider", sa.String(64), nullable=False),
    sa.Column("base_url", sa.String(2048), nullable=False),
    sa.Column("model", sa.String(256), nullable=False),
    sa.Column("credential_ref", sa.String(160), nullable=False),
    sa.Column("temperature", sa.Float(), nullable=False),
    sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_latency_ms", sa.Integer(), nullable=True),
    sa.Column("last_error_code", sa.String(160), nullable=True),
    sa.Column("model_reported", sa.String(256), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "provider = 'fake' OR credential_ref <> ''",
        name="ck_enterprise_provider_sessions_real_credential_ref",
    ),
    sa.CheckConstraint(
        "last_latency_ms IS NULL OR last_latency_ms >= 0",
        name="ck_enterprise_provider_sessions_latency_nonnegative",
    ),
    sa.Index(
        "ix_enterprise_provider_sessions_tenant_subject_updated",
        "organization_id",
        "workspace_id",
        "subject_id",
        "updated_at",
    ),
)


def _aware(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _required_aware(value: datetime | str | None, field_name: str) -> datetime:
    normalized = _aware(value)
    if normalized is None:
        raise ValueError(f"durable Provider session is missing {field_name}")
    return normalized


@dataclass(frozen=True, slots=True)
class ProviderSessionRecord:
    provider_session_id: str
    subject_id: str
    organization_id: str
    workspace_id: str
    provider: str
    base_url: str
    model: str
    credential_ref: str
    temperature: float
    created_at: datetime
    updated_at: datetime
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_tested_at: datetime | None = None
    last_latency_ms: int | None = None
    last_error_code: str | None = None
    model_reported: str | None = None

    def __post_init__(self) -> None:
        required = {
            "provider_session_id": self.provider_session_id,
            "subject_id": self.subject_id,
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "provider": self.provider,
            "model": self.model,
        }
        if any(not str(value).strip() for value in required.values()):
            raise ValueError("durable Provider session identity is incomplete")
        if self.provider != "fake" and not self.credential_ref.strip():
            raise ValueError("real durable Provider session requires credential_ref")
        if not math.isfinite(self.temperature):
            raise ValueError("Provider session temperature must be finite")
        _required_aware(self.created_at, "created_at")
        _required_aware(self.updated_at, "updated_at")


def _record(row: Mapping[str, object]) -> ProviderSessionRecord:
    return ProviderSessionRecord(
        provider_session_id=str(row["provider_session_id"]),
        subject_id=str(row["subject_id"]),
        organization_id=str(row["organization_id"]),
        workspace_id=str(row["workspace_id"]),
        provider=str(row["provider"]),
        base_url=str(row["base_url"]),
        model=str(row["model"]),
        credential_ref=str(row["credential_ref"]),
        temperature=float(row["temperature"]),
        verified_at=_aware(row.get("verified_at")),
        expires_at=_aware(row.get("expires_at")),
        revoked_at=_aware(row.get("revoked_at")),
        last_tested_at=_aware(row.get("last_tested_at")),
        last_latency_ms=(
            int(row["last_latency_ms"])
            if row.get("last_latency_ms") is not None
            else None
        ),
        last_error_code=(
            str(row["last_error_code"])
            if row.get("last_error_code") is not None
            else None
        ),
        model_reported=(
            str(row["model_reported"])
            if row.get("model_reported") is not None
            else None
        ),
        created_at=_required_aware(row.get("created_at"), "created_at"),
        updated_at=_required_aware(row.get("updated_at"), "updated_at"),
    )


class ProviderSessionRepository:
    """Repository shared by backend and worker processes.

    Production construction is PostgreSQL-only and always uses the integrated
    tenant transaction manager.  ``for_test_engine`` is an explicit offline
    seam used to prove process/restart durability without claiming PostgreSQL
    RLS integration acceptance.
    """

    def __init__(self, engine: Engine, *, _allow_test_dialect: bool = False) -> None:
        if engine.dialect.name != "postgresql" and not _allow_test_dialect:
            raise ValueError("durable Provider sessions require PostgreSQL")
        self.engine = engine
        self._test_dialect = engine.dialect.name != "postgresql"
        self._transactions = (
            None
            if self._test_dialect
            else PostgresTenantTransactionManager(engine)
        )

    @classmethod
    def for_test_engine(cls, engine: Engine) -> "ProviderSessionRepository":
        return cls(engine, _allow_test_dialect=True)

    @contextmanager
    def _connection(
        self,
        actor: ActorContext,
        tenant: TenantContext,
    ) -> Iterator[Connection]:
        require_matching_actor_context(tenant, actor)
        context = TenantTransactionContext(tenant=tenant, actor=actor)
        if self._transactions is not None:
            with self._transactions.transaction(context) as session:
                yield session.connection
            return
        with self.engine.begin() as connection:
            yield connection

    @staticmethod
    def _scope(
        actor: ActorContext,
        tenant: TenantContext,
    ) -> tuple[str, str, str]:
        return require_matching_actor_context(tenant, actor)

    @staticmethod
    def _owned_predicate(
        provider_session_id: str,
        actor: ActorContext,
        tenant: TenantContext,
    ) -> tuple[ColumnElement[bool], ...]:
        organization_id, workspace_id, subject_id = (
            ProviderSessionRepository._scope(actor, tenant)
        )
        return (
            enterprise_provider_sessions.c.provider_session_id
            == provider_session_id,
            enterprise_provider_sessions.c.organization_id == organization_id,
            enterprise_provider_sessions.c.workspace_id == workspace_id,
            enterprise_provider_sessions.c.subject_id == subject_id,
        )

    def create(
        self,
        record: ProviderSessionRecord,
        *,
        actor: ActorContext,
        tenant: TenantContext,
    ) -> ProviderSessionRecord:
        organization_id, workspace_id, subject_id = self._scope(actor, tenant)
        if (
            record.organization_id != organization_id
            or record.workspace_id != workspace_id
            or record.subject_id != subject_id
        ):
            raise PermissionError("Provider session ownership does not match tenant context")
        with self._connection(actor, tenant) as connection:
            connection.execute(
                enterprise_provider_sessions.insert().values(
                    provider_session_id=record.provider_session_id,
                    subject_id=record.subject_id,
                    organization_id=record.organization_id,
                    workspace_id=record.workspace_id,
                    provider=record.provider,
                    base_url=record.base_url,
                    model=record.model,
                    credential_ref=record.credential_ref,
                    temperature=record.temperature,
                    verified_at=record.verified_at,
                    expires_at=record.expires_at,
                    revoked_at=record.revoked_at,
                    last_tested_at=record.last_tested_at,
                    last_latency_ms=record.last_latency_ms,
                    last_error_code=record.last_error_code,
                    model_reported=record.model_reported,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
        stored = self.get(
            record.provider_session_id,
            actor=actor,
            tenant=tenant,
        )
        if stored is None:
            raise RuntimeError("durable Provider session was not persisted")
        return stored

    def get(
        self,
        provider_session_id: str,
        *,
        actor: ActorContext,
        tenant: TenantContext,
    ) -> ProviderSessionRecord | None:
        with self._connection(actor, tenant) as connection:
            row = connection.execute(
                sa.select(enterprise_provider_sessions).where(
                    *self._owned_predicate(
                        provider_session_id,
                        actor,
                        tenant,
                    )
                )
            ).mappings().one_or_none()
        return _record(row) if row is not None else None

    def latest_verified(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        now: datetime,
    ) -> ProviderSessionRecord | None:
        organization_id, workspace_id, subject_id = self._scope(actor, tenant)
        with self._connection(actor, tenant) as connection:
            row = connection.execute(
                sa.select(enterprise_provider_sessions)
                .where(
                    enterprise_provider_sessions.c.organization_id
                    == organization_id,
                    enterprise_provider_sessions.c.workspace_id == workspace_id,
                    enterprise_provider_sessions.c.subject_id == subject_id,
                    enterprise_provider_sessions.c.verified_at.is_not(None),
                    enterprise_provider_sessions.c.revoked_at.is_(None),
                    sa.or_(
                        enterprise_provider_sessions.c.expires_at.is_(None),
                        enterprise_provider_sessions.c.expires_at > now,
                    ),
                )
                .order_by(
                    enterprise_provider_sessions.c.last_tested_at.desc(),
                    enterprise_provider_sessions.c.created_at.desc(),
                )
                .limit(1)
            ).mappings().one_or_none()
        return _record(row) if row is not None else None

    def record_test_result(
        self,
        provider_session_id: str,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        tested_at: datetime,
        verified: bool,
        latency_ms: int | None,
        error_code: str | None,
        model_reported: str | None,
    ) -> bool:
        values = {
            "verified_at": tested_at if verified else None,
            "last_tested_at": tested_at,
            "last_latency_ms": latency_ms,
            "last_error_code": error_code,
            "model_reported": model_reported,
            "updated_at": tested_at,
        }
        with self._connection(actor, tenant) as connection:
            result = connection.execute(
                enterprise_provider_sessions.update()
                .where(
                    *self._owned_predicate(
                        provider_session_id,
                        actor,
                        tenant,
                    ),
                    enterprise_provider_sessions.c.revoked_at.is_(None),
                )
                .values(**values)
            )
        return result.rowcount == 1

    def revoke(
        self,
        provider_session_id: str,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        revoked_at: datetime,
    ) -> bool:
        with self._connection(actor, tenant) as connection:
            result = connection.execute(
                enterprise_provider_sessions.update()
                .where(
                    *self._owned_predicate(
                        provider_session_id,
                        actor,
                        tenant,
                    ),
                    enterprise_provider_sessions.c.revoked_at.is_(None),
                )
                .values(revoked_at=revoked_at, updated_at=revoked_at)
            )
        return result.rowcount == 1

    def delete(
        self,
        provider_session_id: str,
        *,
        actor: ActorContext,
        tenant: TenantContext,
    ) -> bool:
        with self._connection(actor, tenant) as connection:
            result = connection.execute(
                enterprise_provider_sessions.delete().where(
                    *self._owned_predicate(
                        provider_session_id,
                        actor,
                        tenant,
                    )
                )
            )
        return result.rowcount == 1


__all__ = [
    "ProviderSessionRecord",
    "ProviderSessionRepository",
    "enterprise_provider_sessions",
    "metadata",
]
