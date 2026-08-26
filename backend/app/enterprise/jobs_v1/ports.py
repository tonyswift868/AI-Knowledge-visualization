"""Ports owned by the durable workflow application boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Protocol

from sqlalchemy.engine import Connection

from app.enterprise.contracts_v1 import AccessDecision, ActorContext, TenantContext

from .models import JobReceipt, JobRecord, JobRequest, OutboxRecord, WorkflowKind


class WorkflowPort(Protocol):
    def submit(self, connection: Connection, request: JobRequest) -> JobReceipt: ...

    def get(self, connection: Connection, job_id: str) -> JobRecord | None: ...

    def request_cancel(
        self,
        connection: Connection,
        job_id: str,
        *,
        requested_at: datetime,
    ) -> JobRecord | None: ...


class TenantContextRevalidatorPort(Protocol):
    def revalidate(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        workflow_kind: WorkflowKind,
    ) -> AccessDecision: ...


class WorkflowAdminAuthorizationPort(Protocol):
    def authorize(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        action: str,
        resource_id: str,
    ) -> AccessDecision: ...


class OutboxDispatchPort(Protocol):
    def dispatch(self, connection: Connection, event: OutboxRecord) -> str: ...


class ExternalEffectPort(Protocol):
    def execute(
        self,
        *,
        effect_name: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...


class WorkflowHandlerPort(Protocol):
    def execute(
        self,
        *,
        job: JobRecord,
        checkpoint: Callable[[str, dict[str, Any]], None],
        ensure_not_cancelled: Callable[[], None],
    ) -> dict[str, Any]: ...


__all__ = [
    "ExternalEffectPort",
    "OutboxDispatchPort",
    "TenantContextRevalidatorPort",
    "WorkflowAdminAuthorizationPort",
    "WorkflowHandlerPort",
    "WorkflowPort",
]
