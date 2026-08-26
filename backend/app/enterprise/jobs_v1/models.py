"""Contracts for Enterprise durable workflow execution.

The models in this module are queue-engine neutral.  Tenant and actor values
are the frozen ``enterprise-context.v1`` contracts rather than Worker-local
copies.  Original job payloads are persisted only in the protected jobs
table; outbox and dead-letter projections are redacted separately.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.enterprise.contracts_v1 import ActorContext, TenantContext, TenantMode


WORKFLOW_CONTRACT_VERSION = "enterprise-workflow.v1"
MAX_PAYLOAD_BYTES = 256 * 1024


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return value.astimezone(timezone.utc)


class _WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    DEAD_LETTER = "DEAD_LETTER"
    CANCELLED = "CANCELLED"


class WorkflowKind(str, Enum):
    DOCUMENT_INGESTION = "DOCUMENT_INGESTION"
    KNOWLEDGE_PIPELINE = "KNOWLEDGE_PIPELINE"
    PLAN_CREATION = "PLAN_CREATION"
    EXPORT = "EXPORT"
    ACCOUNT_DELETION = "ACCOUNT_DELETION"


class FailureClass(str, Enum):
    RETRYABLE = "RETRYABLE"
    NON_RETRYABLE = "NON_RETRYABLE"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    INVALID_INPUT = "INVALID_INPUT"
    WORKER_LOST = "WORKER_LOST"
    TENANT_REVALIDATION_FAILED = "TENANT_REVALIDATION_FAILED"


class OutboxStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    DISPATCHED = "DISPATCHED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    CANCELLED = "CANCELLED"


class DeadLetterStatus(str, Enum):
    OPEN = "OPEN"
    RETRIED = "RETRIED"
    CANCELLED = "CANCELLED"


class JobRequest(_WorkflowModel):
    schema_version: Literal["enterprise-workflow.v1"] = WORKFLOW_CONTRACT_VERSION
    workflow_kind: WorkflowKind
    payload: dict[str, Any] = Field(default_factory=dict)
    actor_context: ActorContext
    tenant_context: TenantContext
    request_id: str = Field(min_length=1, max_length=240)
    trace_id: str = Field(min_length=1, max_length=240)
    idempotency_key: str = Field(min_length=8, max_length=240)
    queue: str = Field(default="mentorflow.enterprise", min_length=1, max_length=120)
    priority: int = Field(default=0, ge=-100, le=100)
    scheduled_at: datetime = Field(default_factory=utc_now)
    max_attempts: int = Field(default=5, ge=1, le=25)

    @model_validator(mode="after")
    def validate_context_and_payload(self) -> "JobRequest":
        require_aware(self.scheduled_at, "scheduled_at")
        if self.actor_context.request_id != self.request_id:
            raise ValueError("request_id must match actor_context.request_id")
        if self.tenant_context.mode is TenantMode.ENTERPRISE_SERVER:
            if self.actor_context.organization_id != self.tenant_context.organization_id:
                raise ValueError("actor and tenant organization must match")
            if (
                self.actor_context.workspace_id is not None
                and self.actor_context.workspace_id != self.tenant_context.workspace_id
            ):
                raise ValueError("actor and tenant workspace must match")
        encoded = json.dumps(
            self.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"payload must not exceed {MAX_PAYLOAD_BYTES} bytes")
        return self

    @computed_field
    @property
    def tenant_partition(self) -> str:
        organization = self.tenant_context.organization_id or "personal"
        workspace = self.tenant_context.workspace_id or "global"
        return f"{self.tenant_context.mode.value}:{organization}:{workspace}"

    @computed_field
    @property
    def lock_key(self) -> str:
        return f"{self.tenant_partition}:{self.workflow_kind.value}"


class JobReceipt(_WorkflowModel):
    job_id: str
    status: JobStatus
    duplicate: bool = False
    attempts: int = 0
    dispatch_generation: int = 0
    scheduled_at: datetime


class JobRecord(_WorkflowModel):
    job_id: str
    workflow_kind: WorkflowKind
    status: JobStatus
    payload: dict[str, Any]
    actor_context: ActorContext
    tenant_context: TenantContext
    request_id: str
    trace_id: str
    idempotency_key: str
    tenant_partition: str
    queue: str
    priority: int
    lock_key: str
    attempts: int
    max_attempts: int
    dispatch_generation: int
    available_at: datetime
    claim_token: str | None = None
    lease_expires_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[dict[str, Any], ...] = ()
    external_effects: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    last_error_redacted: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    version: int


class OutboxRecord(_WorkflowModel):
    outbox_id: str
    job_id: str
    dispatch_generation: int
    status: OutboxStatus
    event_type: str
    queue: str
    lock_key: str
    payload_redacted: dict[str, Any]
    available_at: datetime
    attempts: int
    claim_token: str | None = None
    lease_expires_at: datetime | None = None
    last_error_redacted: str | None = None
    created_at: datetime
    dispatched_at: datetime | None = None


class DeadLetterRecord(_WorkflowModel):
    dead_letter_id: str
    job_id: str
    status: DeadLetterStatus
    failure_class: FailureClass
    error_code: str
    error_redacted: str
    payload_redacted: dict[str, Any]
    actor_redacted: dict[str, Any]
    tenant_snapshot: dict[str, Any]
    attempts: int
    created_at: datetime
    resolved_at: datetime | None = None
    resolution_actor_id: str | None = None
    resolution_reason: str | None = None


class QueueMetrics(_WorkflowModel):
    tenant_partition: str | None = None
    queue_depth: int
    running: int
    failed_retryable: int
    dead_letters: int
    oldest_queued_age_seconds: float | None


class FailedJobView(_WorkflowModel):
    job_id: str
    workflow_kind: WorkflowKind
    tenant_partition: str
    status: JobStatus
    attempts: int
    max_attempts: int
    age_seconds: float
    last_error_redacted: str | None


__all__ = [
    "DeadLetterRecord",
    "DeadLetterStatus",
    "FailureClass",
    "FailedJobView",
    "JobReceipt",
    "JobRecord",
    "JobRequest",
    "JobStatus",
    "OutboxRecord",
    "OutboxStatus",
    "QueueMetrics",
    "WorkflowKind",
]
