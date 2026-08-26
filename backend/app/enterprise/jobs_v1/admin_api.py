"""Unwired FastAPI router for the Worker-03-authorized admin surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.enterprise.contracts_v1 import ActorContext, TenantContext

from .admin import WorkflowAdminService
from .models import FailedJobView, JobRecord, QueueMetrics


@dataclass(frozen=True, slots=True)
class WorkflowAdminRequestContext:
    actor: ActorContext
    tenant: TenantContext


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetryRequest(_ApiModel):
    reason: str = Field(min_length=1, max_length=500)


class AdminActionReceipt(_ApiModel):
    job_id: str
    status: str
    attempts: int
    dispatch_generation: int


def _action_receipt(job: JobRecord) -> AdminActionReceipt:
    return AdminActionReceipt(
        job_id=job.job_id,
        status=job.status.value,
        attempts=job.attempts,
        dispatch_generation=job.dispatch_generation,
    )


def create_workflow_admin_router(
    service: WorkflowAdminService,
    context_provider: Callable[[], WorkflowAdminRequestContext],
) -> APIRouter:
    router = APIRouter(prefix="/api/enterprise/jobs", tags=["enterprise-jobs"])

    def invoke(operation):
        try:
            return operation()
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail="enterprise jobs access denied") from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="enterprise job not found") from exc

    @router.get("/summary", response_model=QueueMetrics)
    def summary(
        context: WorkflowAdminRequestContext = Depends(context_provider),
        tenant_partition: str | None = Query(default=None, max_length=560),
    ) -> QueueMetrics:
        return invoke(
            lambda: service.metrics(
                actor=context.actor,
                tenant=context.tenant,
                requested_partition=tenant_partition,
            )
        )

    @router.get("/failed", response_model=list[FailedJobView])
    def failed(
        context: WorkflowAdminRequestContext = Depends(context_provider),
        tenant_partition: str | None = Query(default=None, max_length=560),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[FailedJobView]:
        return invoke(
            lambda: service.failed_jobs(
                actor=context.actor,
                tenant=context.tenant,
                requested_partition=tenant_partition,
                limit=limit,
            )
        )

    @router.post("/{job_id}/retry", response_model=AdminActionReceipt)
    def retry(
        job_id: str,
        body: RetryRequest,
        context: WorkflowAdminRequestContext = Depends(context_provider),
    ) -> AdminActionReceipt:
        return _action_receipt(invoke(
            lambda: service.retry_dead_letter(
                job_id,
                actor=context.actor,
                tenant=context.tenant,
                reason=body.reason,
            )
        ))

    @router.post("/{job_id}/cancel", response_model=AdminActionReceipt)
    def cancel(
        job_id: str,
        context: WorkflowAdminRequestContext = Depends(context_provider),
    ) -> AdminActionReceipt:
        return _action_receipt(invoke(
            lambda: service.cancel(
                job_id,
                actor=context.actor,
                tenant=context.tenant,
            )
        ))

    return router


__all__ = [
    "AdminActionReceipt",
    "RetryRequest",
    "WorkflowAdminRequestContext",
    "create_workflow_admin_router",
]
