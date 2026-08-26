"""Application services for durable execution and recovery."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.engine import Connection, Engine

from app.enterprise.contracts_v1 import AccessDecisionKind

from .models import JobRecord, JobRequest, JobStatus, OutboxRecord
from .ports import (
    ExternalEffectPort,
    OutboxDispatchPort,
    TenantContextRevalidatorPort,
)
from .repository import WorkflowRepository
from .retry import RetryPolicy, WorkflowFailure
from .models import FailureClass


class TenantRevalidationRejected(RuntimeError):
    pass


class WorkflowCancellationRequested(RuntimeError):
    pass


class WorkflowService:
    def __init__(
        self,
        repository: WorkflowRepository | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.repository = repository or WorkflowRepository()
        self.retry_policy = retry_policy or RetryPolicy()

    def submit(self, connection: Connection, request: JobRequest):
        return self.repository.submit(connection, request)

    def get(self, connection: Connection, job_id: str) -> JobRecord | None:
        return self.repository.get(connection, job_id)

    def claim_and_revalidate(
        self,
        connection: Connection,
        job_id: str,
        *,
        revalidator: TenantContextRevalidatorPort,
        now: datetime,
        lease_seconds: int = 60,
    ) -> JobRecord | None:
        job = self.repository.claim_job(
            connection,
            job_id,
            now=now,
            lease_seconds=lease_seconds,
        )
        if job is None:
            return None
        decision = revalidator.revalidate(
            actor=job.actor_context,
            tenant=job.tenant_context,
            workflow_kind=job.workflow_kind,
        )
        if decision.decision is not AccessDecisionKind.ALLOW:
            self.repository.mark_failed(
                connection,
                job.job_id,
                job.claim_token or "",
                failure=WorkflowFailure(
                    FailureClass.TENANT_REVALIDATION_FAILED,
                    "TENANT_CONTEXT_REJECTED",
                    "Persisted tenant context is no longer authorized",
                ),
                retry_policy=self.retry_policy,
                now=now,
            )
            raise TenantRevalidationRejected("persisted tenant context was rejected")
        return job

    def fail(
        self,
        connection: Connection,
        job: JobRecord,
        failure: WorkflowFailure,
        *,
        now: datetime,
    ) -> JobRecord:
        return self.repository.mark_failed(
            connection,
            job.job_id,
            job.claim_token or "",
            failure=failure,
            retry_policy=self.retry_policy,
            now=now,
        )

    def recover_expired(
        self,
        connection: Connection,
        *,
        now: datetime,
        limit: int = 100,
    ) -> list[JobRecord]:
        return self.repository.recover_expired_claims(
            connection,
            now=now,
            retry_policy=self.retry_policy,
            limit=limit,
        )


class OutboxPublisher:
    """Publish outbox rows with an atomic Procrastinate INSERT + acknowledgement."""

    def __init__(
        self,
        repository: WorkflowRepository | None = None,
        *,
        failure_delay_seconds: int = 5,
    ) -> None:
        self.repository = repository or WorkflowRepository()
        self.failure_delay_seconds = failure_delay_seconds

    def publish_batch(
        self,
        connection: Connection,
        dispatcher: OutboxDispatchPort,
        *,
        now: datetime,
        limit: int = 50,
        schedule_horizon_seconds: int = 60 * 60,
    ) -> list[tuple[OutboxRecord, str | None]]:
        events = self.repository.claim_outbox(
            connection,
            now=now,
            limit=limit,
            schedule_horizon_seconds=schedule_horizon_seconds,
        )
        results: list[tuple[OutboxRecord, str | None]] = []
        for event in events:
            try:
                # A savepoint prevents one queueing-lock or connector error from
                # poisoning the publisher transaction.  The external connection
                # passed to Procrastinate still makes its INSERT and our ack one
                # atomic unit.
                with connection.begin_nested():
                    engine_job_id = dispatcher.dispatch(connection, event)
                    if not self.repository.mark_outbox_dispatched(
                        connection,
                        event,
                        now=now,
                    ):
                        raise RuntimeError("outbox claim was lost before acknowledgement")
                results.append((event, engine_job_id))
            except Exception as exc:
                self.repository.mark_outbox_failed(
                    connection,
                    event,
                    error=exc,
                    retry_at=now + timedelta(seconds=self.failure_delay_seconds),
                )
                results.append((event, None))
        return results


class DurableEffectCoordinator:
    """Persist effect intent before a provider call and its receipt afterwards.

    Recovery may submit the same stable idempotency key again after an
    after-call/before-receipt crash.  The provider adapter is required to
    deduplicate that key; cancellation before the call never invokes it.
    """

    def __init__(self, engine: Engine, repository: WorkflowRepository | None = None) -> None:
        self.engine = engine
        self.repository = repository or WorkflowRepository()

    def execute_once(
        self,
        *,
        job_id: str,
        claim_token: str,
        effect_name: str,
        payload: dict[str, Any],
        effect_port: ExternalEffectPort,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        after_external_call: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        cancelled_before_call = False
        with self.engine.begin() as connection:
            job = self.repository.get(connection, job_id)
            if job is None or job.status is not JobStatus.RUNNING or job.claim_token != claim_token:
                raise RuntimeError("running job lease is absent or stale")
            if job.cancel_requested_at is not None:
                self.repository.finalize_cancelled(connection, job_id, claim_token, now=now())
                cancelled_before_call = True
                prepared: dict[str, Any] = {}
            else:
                prepared = self.repository.prepare_external_effect(
                    connection,
                    job_id,
                    claim_token,
                    effect_name=effect_name,
                    now=now(),
                )
                if prepared.get("status") == "COMPLETED":
                    return dict(prepared.get("receipt") or {})
        if cancelled_before_call:
            raise WorkflowCancellationRequested(job_id)

        receipt = effect_port.execute(
            effect_name=effect_name,
            idempotency_key=str(prepared["idempotency_key"]),
            payload=payload,
        )
        if after_external_call is not None:
            after_external_call()

        cancelled_after_call = False
        with self.engine.begin() as connection:
            completed = self.repository.complete_external_effect(
                connection,
                job_id,
                claim_token,
                effect_name=effect_name,
                receipt=receipt,
                now=now(),
            )
            if self.repository.is_cancel_requested(connection, job_id):
                self.repository.finalize_cancelled(connection, job_id, claim_token, now=now())
                cancelled_after_call = True
            final_receipt = dict(completed.get("receipt") or {})
        if cancelled_after_call:
            raise WorkflowCancellationRequested(job_id)
        return final_receipt


__all__ = [
    "DurableEffectCoordinator",
    "OutboxPublisher",
    "TenantRevalidationRejected",
    "WorkflowCancellationRequested",
    "WorkflowService",
]
