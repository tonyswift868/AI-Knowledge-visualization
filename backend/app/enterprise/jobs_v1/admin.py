"""Authorization-gated admin operations for jobs and dead letters."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.engine import Engine

from app.enterprise.contracts_v1 import AccessDecisionKind, ActorContext, TenantContext

from .models import FailedJobView, JobRecord, QueueMetrics
from .ports import WorkflowAdminAuthorizationPort
from .repository import WorkflowRepository


def tenant_partition(tenant: TenantContext) -> str:
    organization = tenant.organization_id or "personal"
    workspace = tenant.workspace_id or "global"
    return f"{tenant.mode.value}:{organization}:{workspace}"


class WorkflowAdminService:
    def __init__(
        self,
        engine: Engine,
        authorizer: WorkflowAdminAuthorizationPort,
        repository: WorkflowRepository | None = None,
    ) -> None:
        self.engine = engine
        self.authorizer = authorizer
        self.repository = repository or WorkflowRepository()

    def metrics(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        requested_partition: str | None = None,
    ) -> QueueMetrics:
        partition = requested_partition or tenant_partition(tenant)
        self._authorize(actor, tenant, "enterprise_jobs.read", partition)
        with self.engine.begin() as connection:
            return self.repository.queue_metrics(
                connection,
                now=datetime.now(timezone.utc),
                tenant_partition=partition,
            )

    def failed_jobs(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        requested_partition: str | None = None,
        limit: int = 100,
    ) -> list[FailedJobView]:
        partition = requested_partition or tenant_partition(tenant)
        self._authorize(actor, tenant, "enterprise_jobs.read", partition)
        with self.engine.begin() as connection:
            return self.repository.list_failed(
                connection,
                now=datetime.now(timezone.utc),
                tenant_partition=partition,
                limit=limit,
            )

    def retry_dead_letter(
        self,
        job_id: str,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        reason: str,
    ) -> JobRecord:
        self._authorize(actor, tenant, "enterprise_jobs.retry", job_id)
        with self.engine.begin() as connection:
            job = self.repository.get(connection, job_id)
            if job is None or job.tenant_partition != tenant_partition(tenant):
                raise LookupError("job not found")
            return self.repository.manual_retry(
                connection,
                job_id,
                actor_id=actor.subject_id,
                reason=reason,
                now=datetime.now(timezone.utc),
            )

    def cancel(
        self,
        job_id: str,
        *,
        actor: ActorContext,
        tenant: TenantContext,
    ) -> JobRecord:
        self._authorize(actor, tenant, "enterprise_jobs.cancel", job_id)
        with self.engine.begin() as connection:
            job = self.repository.get(connection, job_id)
            if job is None or job.tenant_partition != tenant_partition(tenant):
                raise LookupError("job not found")
            cancelled = self.repository.request_cancel(
                connection,
                job_id,
                requested_at=datetime.now(timezone.utc),
            )
            if cancelled is None:
                raise LookupError("job not found")
            return cancelled

    def _authorize(
        self,
        actor: ActorContext,
        tenant: TenantContext,
        action: str,
        resource_id: str,
    ) -> None:
        decision = self.authorizer.authorize(
            actor=actor,
            tenant=tenant,
            action=action,
            resource_id=resource_id,
        )
        if decision.decision is not AccessDecisionKind.ALLOW:
            raise PermissionError(decision.reason_code)


__all__ = ["WorkflowAdminService", "tenant_partition"]
