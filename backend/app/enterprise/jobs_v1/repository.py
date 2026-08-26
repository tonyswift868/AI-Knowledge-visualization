"""SQLAlchemy Core repository for durable workflow state transitions."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Connection

from app.enterprise.contracts_v1 import ActorContext, TenantContext

from .models import (
    DeadLetterRecord,
    DeadLetterStatus,
    FailureClass,
    FailedJobView,
    JobReceipt,
    JobRecord,
    JobRequest,
    JobStatus,
    OutboxRecord,
    OutboxStatus,
    QueueMetrics,
    WorkflowKind,
    require_aware,
)
from .redaction import redact_payload, redact_text
from .retry import RetryDecision, RetryPolicy, WorkflowFailure
from .schema import dead_letters, jobs, outbox


def _id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex}"


def _aware(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json(value: Any, default: Any) -> Any:
    return default if value is None else value


def _job_record(row: Mapping[str, Any]) -> JobRecord:
    return JobRecord(
        job_id=row["job_id"],
        workflow_kind=WorkflowKind(row["workflow_kind"]),
        status=JobStatus(row["status"]),
        payload=_json(row["payload_json"], {}),
        actor_context=ActorContext.model_validate(row["actor_context_json"]),
        tenant_context=TenantContext.model_validate(row["tenant_context_json"]),
        request_id=row["request_id"],
        trace_id=row["trace_id"],
        idempotency_key=row["idempotency_key"],
        tenant_partition=row["tenant_partition"],
        queue=row["queue"],
        priority=row["priority"],
        lock_key=row["lock_key"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        dispatch_generation=row["dispatch_generation"],
        available_at=_aware(row["available_at"]),
        claim_token=row["claim_token"],
        lease_expires_at=_aware(row["lease_expires_at"]),
        cancel_requested_at=_aware(row["cancel_requested_at"]),
        checkpoint=_json(row["checkpoint_json"], {}),
        evidence=tuple(_json(row["evidence_json"], [])),
        external_effects=_json(row["external_effects_json"], {}),
        result=_json(row["result_json"], {}),
        last_error_redacted=row["last_error_redacted"],
        created_at=_aware(row["created_at"]),
        updated_at=_aware(row["updated_at"]),
        started_at=_aware(row["started_at"]),
        finished_at=_aware(row["finished_at"]),
        version=row["version"],
    )


def _outbox_record(row: Mapping[str, Any]) -> OutboxRecord:
    return OutboxRecord(
        outbox_id=row["outbox_id"],
        job_id=row["job_id"],
        dispatch_generation=row["dispatch_generation"],
        status=OutboxStatus(row["status"]),
        event_type=row["event_type"],
        queue=row["queue"],
        lock_key=row["lock_key"],
        payload_redacted=_json(row["payload_redacted_json"], {}),
        available_at=_aware(row["available_at"]),
        attempts=row["attempts"],
        claim_token=row["claim_token"],
        lease_expires_at=_aware(row["lease_expires_at"]),
        last_error_redacted=row["last_error_redacted"],
        created_at=_aware(row["created_at"]),
        dispatched_at=_aware(row["dispatched_at"]),
    )


def _dead_letter_record(row: Mapping[str, Any]) -> DeadLetterRecord:
    return DeadLetterRecord(
        dead_letter_id=row["dead_letter_id"],
        job_id=row["job_id"],
        status=DeadLetterStatus(row["status"]),
        failure_class=FailureClass(row["failure_class"]),
        error_code=row["error_code"],
        error_redacted=row["error_redacted"],
        payload_redacted=_json(row["payload_redacted_json"], {}),
        actor_redacted=_json(row["actor_redacted_json"], {}),
        tenant_snapshot=_json(row["tenant_snapshot_json"], {}),
        attempts=row["attempts"],
        created_at=_aware(row["created_at"]),
        resolved_at=_aware(row["resolved_at"]),
        resolution_actor_id=row["resolution_actor_id"],
        resolution_reason=row["resolution_reason"],
    )


def _insert_do_nothing(
    connection: Connection,
    table: sa.Table,
    values: dict[str, Any],
    *,
    index_elements: Sequence[sa.Column[Any]],
) -> bool:
    if connection.dialect.name == "postgresql":
        statement = postgresql.insert(table).values(**values).on_conflict_do_nothing(
            index_elements=list(index_elements)
        )
    elif connection.dialect.name == "sqlite":
        statement = sqlite.insert(table).values(**values).on_conflict_do_nothing(
            index_elements=list(index_elements)
        )
    else:
        statement = table.insert().values(**values)
    result = connection.execute(statement)
    return result.rowcount == 1


class WorkflowRepository:
    """Transaction-neutral durable state repository.

    Methods never commit.  A business operation can write its own rows and call
    :meth:`submit` on the same connection; committing or rolling back then
    applies to the business row, job ledger, and outbox row atomically.
    """

    def submit(self, connection: Connection, request: JobRequest) -> JobReceipt:
        now = datetime.now(timezone.utc)
        scheduled_at = require_aware(request.scheduled_at, "scheduled_at")
        job_id = _id("job_")
        created = _insert_do_nothing(
            connection,
            jobs,
            {
                "job_id": job_id,
                "workflow_kind": request.workflow_kind.value,
                "status": JobStatus.PENDING.value,
                "payload_json": request.payload,
                "actor_context_json": request.actor_context.model_dump(mode="json"),
                "tenant_context_json": request.tenant_context.model_dump(mode="json"),
                "request_id": request.request_id,
                "trace_id": request.trace_id,
                "idempotency_key": request.idempotency_key,
                "tenant_partition": request.tenant_partition,
                "queue": request.queue,
                "priority": request.priority,
                "lock_key": request.lock_key,
                "attempts": 0,
                "max_attempts": request.max_attempts,
                "dispatch_generation": 0,
                "available_at": scheduled_at,
                "claim_token": None,
                "lease_expires_at": None,
                "cancel_requested_at": None,
                "checkpoint_json": {},
                "evidence_json": [],
                "external_effects_json": {},
                "result_json": {},
                "last_error_redacted": None,
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "finished_at": None,
                "version": 1,
            },
            index_elements=(jobs.c.tenant_partition, jobs.c.workflow_kind, jobs.c.idempotency_key),
        )
        if created:
            self._insert_outbox(
                connection,
                job_id=job_id,
                workflow_kind=request.workflow_kind,
                tenant_partition=request.tenant_partition,
                queue=request.queue,
                lock_key=request.lock_key,
                generation=0,
                available_at=scheduled_at,
                now=now,
            )
            return JobReceipt(
                job_id=job_id,
                status=JobStatus.PENDING,
                duplicate=False,
                attempts=0,
                dispatch_generation=0,
                scheduled_at=scheduled_at,
            )
        existing = connection.execute(
            sa.select(jobs).where(
                jobs.c.tenant_partition == request.tenant_partition,
                jobs.c.workflow_kind == request.workflow_kind.value,
                jobs.c.idempotency_key == request.idempotency_key,
            )
        ).mappings().one()
        return JobReceipt(
            job_id=existing["job_id"],
            status=JobStatus(existing["status"]),
            duplicate=True,
            attempts=existing["attempts"],
            dispatch_generation=existing["dispatch_generation"],
            scheduled_at=_aware(existing["available_at"]),
        )

    def _insert_outbox(
        self,
        connection: Connection,
        *,
        job_id: str,
        workflow_kind: WorkflowKind,
        tenant_partition: str,
        queue: str,
        lock_key: str,
        generation: int,
        available_at: datetime,
        now: datetime,
    ) -> None:
        _insert_do_nothing(
            connection,
            outbox,
            {
                "outbox_id": _id("out_"),
                "job_id": job_id,
                "dispatch_generation": generation,
                "status": OutboxStatus.PENDING.value,
                "event_type": "mentorflow.workflow.dispatch.v1",
                "queue": queue,
                "lock_key": lock_key,
                "payload_redacted_json": {
                    "job_id": job_id,
                    "workflow_kind": workflow_kind.value,
                    "tenant_partition": tenant_partition,
                    "dispatch_generation": generation,
                },
                "available_at": available_at,
                "attempts": 0,
                "claim_token": None,
                "lease_expires_at": None,
                "last_error_redacted": None,
                "created_at": now,
                "dispatched_at": None,
            },
            index_elements=(outbox.c.job_id, outbox.c.dispatch_generation),
        )

    def get(self, connection: Connection, job_id: str) -> JobRecord | None:
        row = connection.execute(
            sa.select(jobs).where(jobs.c.job_id == job_id)
        ).mappings().one_or_none()
        return _job_record(row) if row else None

    def claim_job(
        self,
        connection: Connection,
        job_id: str,
        *,
        now: datetime,
        lease_seconds: int = 60,
    ) -> JobRecord | None:
        now = require_aware(now, "now")
        row = connection.execute(
            sa.select(jobs)
            .where(
                jobs.c.job_id == job_id,
                jobs.c.status.in_(
                    [JobStatus.PENDING.value, JobStatus.FAILED_RETRYABLE.value]
                ),
                jobs.c.available_at <= now,
                jobs.c.cancel_requested_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        ).mappings().one_or_none()
        if row is None:
            return None
        claim_token = uuid.uuid4().hex
        result = connection.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id, jobs.c.version == row["version"])
            .values(
                status=JobStatus.RUNNING.value,
                attempts=row["attempts"] + 1,
                claim_token=claim_token,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=row["started_at"] or now,
                updated_at=now,
                version=row["version"] + 1,
            )
        )
        if result.rowcount != 1:
            return None
        return self.get(connection, job_id)

    def heartbeat(
        self,
        connection: Connection,
        job_id: str,
        claim_token: str,
        *,
        now: datetime,
        lease_seconds: int = 60,
    ) -> bool:
        now = require_aware(now, "now")
        result = connection.execute(
            jobs.update()
            .where(
                jobs.c.job_id == job_id,
                jobs.c.status == JobStatus.RUNNING.value,
                jobs.c.claim_token == claim_token,
            )
            .values(
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
                version=jobs.c.version + 1,
            )
        )
        return result.rowcount == 1

    def record_checkpoint(
        self,
        connection: Connection,
        job_id: str,
        claim_token: str,
        *,
        phase: str,
        checkpoint: dict[str, Any],
        evidence: dict[str, Any] | None,
        now: datetime,
    ) -> JobRecord:
        row = self._locked_running(connection, job_id, claim_token)
        checkpoints = dict(_json(row["checkpoint_json"], {}))
        checkpoints[phase] = checkpoint
        evidence_items = list(_json(row["evidence_json"], []))
        if evidence is not None:
            evidence_items.append(redact_payload(evidence))
        connection.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id, jobs.c.version == row["version"])
            .values(
                checkpoint_json=checkpoints,
                evidence_json=evidence_items,
                updated_at=require_aware(now, "now"),
                version=row["version"] + 1,
            )
        )
        return self.get(connection, job_id)  # type: ignore[return-value]

    def prepare_external_effect(
        self,
        connection: Connection,
        job_id: str,
        claim_token: str,
        *,
        effect_name: str,
        now: datetime,
    ) -> dict[str, Any]:
        row = self._locked_running(connection, job_id, claim_token)
        effects = dict(_json(row["external_effects_json"], {}))
        existing = effects.get(effect_name)
        if existing:
            return dict(existing)
        prepared = {
            "status": "PREPARED",
            "idempotency_key": f"{job_id}:{effect_name}",
            "prepared_at": require_aware(now, "now").isoformat(),
        }
        effects[effect_name] = prepared
        connection.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id, jobs.c.version == row["version"])
            .values(
                external_effects_json=effects,
                updated_at=now,
                version=row["version"] + 1,
            )
        )
        return prepared

    def complete_external_effect(
        self,
        connection: Connection,
        job_id: str,
        claim_token: str,
        *,
        effect_name: str,
        receipt: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        row = self._locked_running(connection, job_id, claim_token)
        effects = dict(_json(row["external_effects_json"], {}))
        existing = dict(effects.get(effect_name) or {})
        if existing.get("status") == "COMPLETED":
            return existing
        if existing.get("status") != "PREPARED":
            raise RuntimeError("external effect must be prepared before completion")
        completed = {
            **existing,
            "status": "COMPLETED",
            "receipt": redact_payload(receipt),
            "completed_at": require_aware(now, "now").isoformat(),
        }
        effects[effect_name] = completed
        connection.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id, jobs.c.version == row["version"])
            .values(
                external_effects_json=effects,
                updated_at=now,
                version=row["version"] + 1,
            )
        )
        return completed

    def mark_succeeded(
        self,
        connection: Connection,
        job_id: str,
        claim_token: str,
        *,
        result: dict[str, Any],
        now: datetime,
    ) -> JobRecord:
        now = require_aware(now, "now")
        row = self._locked_running(connection, job_id, claim_token)
        if row["cancel_requested_at"] is not None:
            return self.finalize_cancelled(connection, job_id, claim_token, now=now)
        updated = connection.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id, jobs.c.version == row["version"])
            .values(
                status=JobStatus.SUCCEEDED.value,
                result_json=redact_payload(result),
                claim_token=None,
                lease_expires_at=None,
                updated_at=now,
                finished_at=now,
                version=row["version"] + 1,
            )
        )
        if updated.rowcount != 1:
            raise RuntimeError("job success transition lost optimistic lock")
        return self.get(connection, job_id)  # type: ignore[return-value]

    def mark_failed(
        self,
        connection: Connection,
        job_id: str,
        claim_token: str,
        *,
        failure: WorkflowFailure,
        retry_policy: RetryPolicy,
        now: datetime,
    ) -> JobRecord:
        now = require_aware(now, "now")
        row = self._locked_running(connection, job_id, claim_token)
        decision = retry_policy.decide(
            job_id=job_id,
            attempt=row["attempts"],
            max_attempts=row["max_attempts"],
            failure=failure,
            now=now,
        )
        if decision.retry:
            self._transition_to_retry(
                connection,
                row,
                failure=failure,
                decision=decision,
                now=now,
            )
        else:
            self._transition_to_dead_letter(
                connection,
                row,
                failure=failure,
                decision=decision,
                now=now,
            )
        return self.get(connection, job_id)  # type: ignore[return-value]

    def _transition_to_retry(
        self,
        connection: Connection,
        row: Mapping[str, Any],
        *,
        failure: WorkflowFailure,
        decision: RetryDecision,
        now: datetime,
    ) -> None:
        generation = row["dispatch_generation"] + 1
        updated = connection.execute(
            jobs.update()
            .where(jobs.c.job_id == row["job_id"], jobs.c.version == row["version"])
            .values(
                status=JobStatus.FAILED_RETRYABLE.value,
                dispatch_generation=generation,
                available_at=decision.retry_at,
                claim_token=None,
                lease_expires_at=None,
                last_error_redacted=decision.error_redacted,
                updated_at=now,
                version=row["version"] + 1,
            )
        )
        if updated.rowcount != 1:
            raise RuntimeError("job retry transition lost optimistic lock")
        self._insert_outbox(
            connection,
            job_id=row["job_id"],
            workflow_kind=WorkflowKind(row["workflow_kind"]),
            tenant_partition=row["tenant_partition"],
            queue=row["queue"],
            lock_key=row["lock_key"],
            generation=generation,
            available_at=decision.retry_at,  # type: ignore[arg-type]
            now=now,
        )

    def _transition_to_dead_letter(
        self,
        connection: Connection,
        row: Mapping[str, Any],
        *,
        failure: WorkflowFailure,
        decision: RetryDecision,
        now: datetime,
    ) -> None:
        updated = connection.execute(
            jobs.update()
            .where(jobs.c.job_id == row["job_id"], jobs.c.version == row["version"])
            .values(
                status=JobStatus.DEAD_LETTER.value,
                claim_token=None,
                lease_expires_at=None,
                last_error_redacted=decision.error_redacted,
                updated_at=now,
                finished_at=now,
                version=row["version"] + 1,
            )
        )
        if updated.rowcount != 1:
            raise RuntimeError("dead-letter transition lost optimistic lock")
        values = {
            "status": DeadLetterStatus.OPEN.value,
            "failure_class": failure.failure_class.value,
            "error_code": redact_text(failure.error_code, limit=160),
            "error_redacted": decision.error_redacted,
            "payload_redacted_json": redact_payload(row["payload_json"]),
            "actor_redacted_json": redact_payload(row["actor_context_json"]),
            "tenant_snapshot_json": redact_payload(row["tenant_context_json"]),
            "attempts": row["attempts"],
            "created_at": now,
            "resolved_at": None,
            "resolution_actor_id": None,
            "resolution_reason": None,
        }
        existing = connection.execute(
            sa.select(dead_letters.c.dead_letter_id).where(
                dead_letters.c.job_id == row["job_id"]
            )
        ).scalar_one_or_none()
        if existing:
            connection.execute(
                dead_letters.update()
                .where(dead_letters.c.dead_letter_id == existing)
                .values(**values)
            )
        else:
            connection.execute(
                dead_letters.insert().values(
                    dead_letter_id=_id("dlq_"),
                    job_id=row["job_id"],
                    **values,
                )
            )

    def request_cancel(
        self,
        connection: Connection,
        job_id: str,
        *,
        requested_at: datetime,
    ) -> JobRecord | None:
        requested_at = require_aware(requested_at, "requested_at")
        row = connection.execute(
            sa.select(jobs).where(jobs.c.job_id == job_id).with_for_update()
        ).mappings().one_or_none()
        if row is None:
            return None
        status = JobStatus(row["status"])
        if status in {JobStatus.SUCCEEDED, JobStatus.CANCELLED}:
            return _job_record(row)
        values: dict[str, Any] = {
            "cancel_requested_at": row["cancel_requested_at"] or requested_at,
            "updated_at": requested_at,
            "version": row["version"] + 1,
        }
        if status in {JobStatus.PENDING, JobStatus.FAILED_RETRYABLE, JobStatus.DEAD_LETTER}:
            values.update(
                status=JobStatus.CANCELLED.value,
                finished_at=requested_at,
                claim_token=None,
                lease_expires_at=None,
            )
        connection.execute(
            jobs.update().where(jobs.c.job_id == job_id, jobs.c.version == row["version"]).values(**values)
        )
        connection.execute(
            outbox.update()
            .where(
                outbox.c.job_id == job_id,
                outbox.c.status.in_(
                    [
                        OutboxStatus.PENDING.value,
                        OutboxStatus.CLAIMED.value,
                        OutboxStatus.FAILED_RETRYABLE.value,
                    ]
                ),
            )
            .values(status=OutboxStatus.CANCELLED.value, claim_token=None, lease_expires_at=None)
        )
        if status is JobStatus.DEAD_LETTER:
            connection.execute(
                dead_letters.update()
                .where(dead_letters.c.job_id == job_id)
                .values(
                    status=DeadLetterStatus.CANCELLED.value,
                    resolved_at=requested_at,
                    resolution_reason="cancelled",
                )
            )
        return self.get(connection, job_id)

    def finalize_cancelled(
        self,
        connection: Connection,
        job_id: str,
        claim_token: str,
        *,
        now: datetime,
    ) -> JobRecord:
        now = require_aware(now, "now")
        row = self._locked_running(connection, job_id, claim_token)
        connection.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id, jobs.c.version == row["version"])
            .values(
                status=JobStatus.CANCELLED.value,
                cancel_requested_at=row["cancel_requested_at"] or now,
                claim_token=None,
                lease_expires_at=None,
                finished_at=now,
                updated_at=now,
                version=row["version"] + 1,
            )
        )
        return self.get(connection, job_id)  # type: ignore[return-value]

    def is_cancel_requested(self, connection: Connection, job_id: str) -> bool:
        value = connection.execute(
            sa.select(jobs.c.cancel_requested_at).where(jobs.c.job_id == job_id)
        ).scalar_one_or_none()
        return value is not None

    def recover_expired_claims(
        self,
        connection: Connection,
        *,
        now: datetime,
        retry_policy: RetryPolicy,
        limit: int = 100,
    ) -> list[JobRecord]:
        now = require_aware(now, "now")
        rows = connection.execute(
            sa.select(jobs)
            .where(
                jobs.c.status == JobStatus.RUNNING.value,
                jobs.c.lease_expires_at.is_not(None),
                jobs.c.lease_expires_at <= now,
            )
            .order_by(jobs.c.lease_expires_at, jobs.c.job_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).mappings().all()
        recovered: list[JobRecord] = []
        for row in rows:
            if row["cancel_requested_at"] is not None:
                recovered.append(
                    self.finalize_cancelled(
                        connection,
                        row["job_id"],
                        row["claim_token"],
                        now=now,
                    )
                )
                continue
            recovered.append(
                self.mark_failed(
                    connection,
                    row["job_id"],
                    row["claim_token"],
                    failure=WorkflowFailure(
                        FailureClass.WORKER_LOST,
                        "WORKER_LEASE_EXPIRED",
                        "Worker lease expired before completion",
                    ),
                    retry_policy=retry_policy,
                    now=now,
                )
            )
        return recovered

    def claim_outbox(
        self,
        connection: Connection,
        *,
        now: datetime,
        lease_seconds: int = 30,
        schedule_horizon_seconds: int = 60 * 60,
        limit: int = 50,
    ) -> list[OutboxRecord]:
        now = require_aware(now, "now")
        schedule_cutoff = now + timedelta(seconds=schedule_horizon_seconds)
        rows = connection.execute(
            sa.select(outbox)
            .where(
                sa.or_(
                    sa.and_(
                        outbox.c.status.in_(
                            [
                                OutboxStatus.PENDING.value,
                                OutboxStatus.FAILED_RETRYABLE.value,
                            ]
                        ),
                        outbox.c.available_at <= schedule_cutoff,
                    ),
                    sa.and_(
                        outbox.c.status == OutboxStatus.CLAIMED.value,
                        outbox.c.lease_expires_at.is_not(None),
                        outbox.c.lease_expires_at <= now,
                    ),
                ),
            )
            .order_by(outbox.c.available_at, outbox.c.outbox_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).mappings().all()
        claimed: list[OutboxRecord] = []
        for row in rows:
            token = uuid.uuid4().hex
            connection.execute(
                outbox.update()
                .where(outbox.c.outbox_id == row["outbox_id"])
                .values(
                    status=OutboxStatus.CLAIMED.value,
                    attempts=row["attempts"] + 1,
                    claim_token=token,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                )
            )
            claimed_row = dict(row)
            claimed_row.update(
                status=OutboxStatus.CLAIMED.value,
                attempts=row["attempts"] + 1,
                claim_token=token,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            claimed.append(_outbox_record(claimed_row))
        return claimed

    def mark_outbox_dispatched(
        self,
        connection: Connection,
        event: OutboxRecord,
        *,
        now: datetime,
    ) -> bool:
        result = connection.execute(
            outbox.update()
            .where(
                outbox.c.outbox_id == event.outbox_id,
                outbox.c.status == OutboxStatus.CLAIMED.value,
                outbox.c.claim_token == event.claim_token,
            )
            .values(
                status=OutboxStatus.DISPATCHED.value,
                claim_token=None,
                lease_expires_at=None,
                dispatched_at=require_aware(now, "now"),
                last_error_redacted=None,
            )
        )
        return result.rowcount == 1

    def mark_outbox_failed(
        self,
        connection: Connection,
        event: OutboxRecord,
        *,
        error: object,
        retry_at: datetime,
    ) -> bool:
        result = connection.execute(
            outbox.update()
            .where(
                outbox.c.outbox_id == event.outbox_id,
                outbox.c.status == OutboxStatus.CLAIMED.value,
                outbox.c.claim_token == event.claim_token,
            )
            .values(
                status=OutboxStatus.FAILED_RETRYABLE.value,
                claim_token=None,
                lease_expires_at=None,
                available_at=require_aware(retry_at, "retry_at"),
                last_error_redacted=redact_text(error),
            )
        )
        return result.rowcount == 1

    def get_outbox_for_job(self, connection: Connection, job_id: str) -> list[OutboxRecord]:
        rows = connection.execute(
            sa.select(outbox)
            .where(outbox.c.job_id == job_id)
            .order_by(outbox.c.dispatch_generation)
        ).mappings().all()
        return [_outbox_record(row) for row in rows]

    def get_dead_letter(self, connection: Connection, job_id: str) -> DeadLetterRecord | None:
        row = connection.execute(
            sa.select(dead_letters).where(dead_letters.c.job_id == job_id)
        ).mappings().one_or_none()
        return _dead_letter_record(row) if row else None

    def manual_retry(
        self,
        connection: Connection,
        job_id: str,
        *,
        actor_id: str,
        reason: str,
        now: datetime,
    ) -> JobRecord:
        now = require_aware(now, "now")
        row = connection.execute(
            sa.select(jobs)
            .where(jobs.c.job_id == job_id, jobs.c.status == JobStatus.DEAD_LETTER.value)
            .with_for_update()
        ).mappings().one_or_none()
        if row is None:
            raise LookupError("dead-letter job not found")
        generation = row["dispatch_generation"] + 1
        connection.execute(
            jobs.update()
            .where(jobs.c.job_id == job_id, jobs.c.version == row["version"])
            .values(
                status=JobStatus.PENDING.value,
                attempts=0,
                dispatch_generation=generation,
                available_at=now,
                cancel_requested_at=None,
                claim_token=None,
                lease_expires_at=None,
                last_error_redacted=None,
                finished_at=None,
                updated_at=now,
                version=row["version"] + 1,
            )
        )
        connection.execute(
            dead_letters.update()
            .where(dead_letters.c.job_id == job_id)
            .values(
                status=DeadLetterStatus.RETRIED.value,
                resolved_at=now,
                resolution_actor_id=actor_id,
                resolution_reason=redact_text(reason, limit=500),
            )
        )
        self._insert_outbox(
            connection,
            job_id=job_id,
            workflow_kind=WorkflowKind(row["workflow_kind"]),
            tenant_partition=row["tenant_partition"],
            queue=row["queue"],
            lock_key=row["lock_key"],
            generation=generation,
            available_at=now,
            now=now,
        )
        return self.get(connection, job_id)  # type: ignore[return-value]

    def queue_metrics(
        self,
        connection: Connection,
        *,
        now: datetime,
        tenant_partition: str | None = None,
    ) -> QueueMetrics:
        now = require_aware(now, "now")
        predicate = [] if tenant_partition is None else [jobs.c.tenant_partition == tenant_partition]
        counts = dict(
            connection.execute(
                sa.select(jobs.c.status, sa.func.count())
                .where(*predicate)
                .group_by(jobs.c.status)
            ).all()
        )
        oldest = connection.execute(
            sa.select(sa.func.min(jobs.c.created_at)).where(
                *predicate,
                jobs.c.status.in_(
                    [JobStatus.PENDING.value, JobStatus.FAILED_RETRYABLE.value]
                ),
            )
        ).scalar_one_or_none()
        oldest_aware = _aware(oldest)
        dead_count = connection.execute(
            sa.select(sa.func.count())
            .select_from(dead_letters.join(jobs, dead_letters.c.job_id == jobs.c.job_id))
            .where(*predicate, dead_letters.c.status == DeadLetterStatus.OPEN.value)
        ).scalar_one()
        return QueueMetrics(
            tenant_partition=tenant_partition,
            queue_depth=counts.get(JobStatus.PENDING.value, 0)
            + counts.get(JobStatus.FAILED_RETRYABLE.value, 0),
            running=counts.get(JobStatus.RUNNING.value, 0),
            failed_retryable=counts.get(JobStatus.FAILED_RETRYABLE.value, 0),
            dead_letters=dead_count,
            oldest_queued_age_seconds=(now - oldest_aware).total_seconds() if oldest_aware else None,
        )

    def list_failed(
        self,
        connection: Connection,
        *,
        now: datetime,
        tenant_partition: str | None = None,
        limit: int = 100,
    ) -> list[FailedJobView]:
        now = require_aware(now, "now")
        predicates = [
            jobs.c.status.in_(
                [JobStatus.FAILED_RETRYABLE.value, JobStatus.DEAD_LETTER.value]
            )
        ]
        if tenant_partition is not None:
            predicates.append(jobs.c.tenant_partition == tenant_partition)
        rows = connection.execute(
            sa.select(jobs)
            .where(*predicates)
            .order_by(jobs.c.updated_at.desc(), jobs.c.job_id)
            .limit(limit)
        ).mappings().all()
        return [
            FailedJobView(
                job_id=row["job_id"],
                workflow_kind=WorkflowKind(row["workflow_kind"]),
                tenant_partition=row["tenant_partition"],
                status=JobStatus(row["status"]),
                attempts=row["attempts"],
                max_attempts=row["max_attempts"],
                age_seconds=(now - _aware(row["created_at"])).total_seconds(),  # type: ignore[operator]
                last_error_redacted=row["last_error_redacted"],
            )
            for row in rows
        ]

    @staticmethod
    def claim_statement_for_postgresql(*, limit: int = 50) -> str:
        statement = (
            sa.select(outbox)
            .where(outbox.c.status == OutboxStatus.PENDING.value)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )

    @staticmethod
    def _locked_running(
        connection: Connection,
        job_id: str,
        claim_token: str,
    ) -> Mapping[str, Any]:
        row = connection.execute(
            sa.select(jobs)
            .where(
                jobs.c.job_id == job_id,
                jobs.c.status == JobStatus.RUNNING.value,
                jobs.c.claim_token == claim_token,
            )
            .with_for_update()
        ).mappings().one_or_none()
        if row is None:
            raise RuntimeError("running job lease is absent or stale")
        return row


__all__ = ["WorkflowRepository"]
