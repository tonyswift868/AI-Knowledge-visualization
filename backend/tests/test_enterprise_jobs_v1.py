from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from app.enterprise.contracts_v1 import (
    AccessDecision,
    AccessDecisionKind,
    ActorContext,
    ActorKind,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.enterprise.jobs_v1.admin import WorkflowAdminService
from app.enterprise.jobs_v1.admin_api import (
    WorkflowAdminRequestContext,
    create_workflow_admin_router,
)
from app.enterprise.jobs_v1.models import (
    DeadLetterStatus,
    FailureClass,
    JobRequest,
    JobStatus,
    OutboxStatus,
    WorkflowKind,
)
from app.enterprise.jobs_v1.procrastinate_adapter import (
    PROCRASTINATE_EVALUATED_VERSION,
    PROCRASTINATE_MINIMUM_VERSION,
    ProcrastinateOutboxDispatcher,
    ProcrastinateWorkerPolicy,
)
from app.enterprise.jobs_v1.repository import WorkflowRepository
from app.enterprise.jobs_v1.retry import RetryPolicy, WorkflowFailure
from app.enterprise.jobs_v1.schema import ENTERPRISE_JOBS_SCHEMA, metadata, outbox
from app.enterprise.jobs_v1.service import OutboxPublisher, TenantRevalidationRejected, WorkflowService


NOW = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)


def _engine(path: Path | None = None) -> sa.Engine:
    if path is None:
        engine = sa.create_engine(
            "sqlite+pysqlite://",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        engine = sa.create_engine(f"sqlite+pysqlite:///{path.as_posix()}", future=True)
    engine = engine.execution_options(
        schema_translate_map={ENTERPRISE_JOBS_SCHEMA: None}
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
    return engine


def _contexts(
    *,
    mode: TenantMode = TenantMode.ENTERPRISE_SERVER,
    request_id: str = "req-enterprise-jobs-1",
) -> tuple[ActorContext, TenantContext]:
    organization_id = "org-1" if mode is TenantMode.ENTERPRISE_SERVER else None
    workspace_id = "ws-1"
    actor = ActorContext(
        subject_id="subject-1",
        actor_kind=ActorKind.HUMAN,
        issuer="https://identity.example.test/realms/mentorflow",
        token_id="token-id-1",
        session_id="session-1",
        request_id=request_id,
        authentication_time=NOW,
        authentication_strength="mfa",
        organization_id=organization_id,
        workspace_id=workspace_id if mode is TenantMode.ENTERPRISE_SERVER else None,
        membership_id="membership-1" if mode is TenantMode.ENTERPRISE_SERVER else None,
        roles=("WORKSPACE_ADMIN",),
    )
    tenant = TenantContext(
        organization_id=organization_id,
        workspace_id=workspace_id,
        mode=mode,
    )
    return actor, tenant


def _request(
    kind: WorkflowKind = WorkflowKind.DOCUMENT_INGESTION,
    *,
    idempotency_key: str = "idem-enterprise-jobs-0001",
    mode: TenantMode = TenantMode.ENTERPRISE_SERVER,
    max_attempts: int = 3,
    scheduled_at: datetime = NOW,
) -> JobRequest:
    actor, tenant = _contexts(mode=mode)
    return JobRequest(
        workflow_kind=kind,
        payload={
            "document_id": "document-1",
            "authorization": "Bearer super-secret-value",
            "email": "student@example.test",
        },
        actor_context=actor,
        tenant_context=tenant,
        request_id=actor.request_id,
        trace_id="trace-1",
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        scheduled_at=scheduled_at,
    )


def _decision(actor: ActorContext, allow: bool) -> AccessDecision:
    return AccessDecision(
        decision=AccessDecisionKind.ALLOW if allow else AccessDecisionKind.DENY,
        policy_id="policy-enterprise-jobs",
        reason_code="AUTHORIZED" if allow else "DENIED",
        actor=actor,
        resource=ResourceScope(
            scope_type=ResourceScopeType.WORKSPACE,
            organization_id="org-1",
            workspace_id="ws-1",
            classification=ResourceClassification.INTERNAL,
        ),
        action="enterprise_jobs.execute",
        evaluated_at=NOW,
    )


class _Revalidator:
    def __init__(self, allow: bool) -> None:
        self.allow = allow
        self.calls = 0

    def revalidate(self, *, actor, tenant, workflow_kind):
        self.calls += 1
        return _decision(actor, self.allow)


class _Authorizer:
    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.calls: list[tuple[str, str]] = []

    def authorize(self, *, actor, tenant, action, resource_id):
        self.calls.append((action, resource_id))
        return _decision(actor, self.allow).model_copy(update={"action": action})


class _ConfiguredTask:
    def __init__(self, owner: "_Task") -> None:
        self.owner = owner

    def defer(self, **task_kwargs: Any) -> int:
        self.owner.kwargs = task_kwargs
        if self.owner.queue_table is not None:
            self.owner.options["connection"].execute(
                self.owner.queue_table.insert().values(job_id=task_kwargs["job_id"])
            )
        return 401


class _Task:
    def __init__(self, queue_table: sa.Table | None = None) -> None:
        self.options: dict[str, Any] = {}
        self.kwargs: dict[str, Any] = {}
        self.queue_table = queue_table

    def configure(self, **options: Any) -> _ConfiguredTask:
        self.options = options
        return _ConfiguredTask(self)


def _make_dead_letter(engine: sa.Engine, repository: WorkflowRepository) -> str:
    with engine.begin() as connection:
        receipt = repository.submit(connection, _request(max_attempts=1))
        job = repository.claim_job(connection, receipt.job_id, now=NOW)
        assert job is not None
        dead = repository.mark_failed(
            connection,
            job.job_id,
            job.claim_token or "",
            failure=WorkflowFailure(
                FailureClass.INVALID_INPUT,
                "INVALID_DOCUMENT",
                "invalid input for student@example.test token=secret",
            ),
            retry_policy=RetryPolicy(),
            now=NOW,
        )
        assert dead.status is JobStatus.DEAD_LETTER
        return dead.job_id


def test_frozen_context_is_reused_and_personal_local_remains_optional() -> None:
    personal = _request(mode=TenantMode.PERSONAL_LOCAL)
    assert type(personal.actor_context) is ActorContext
    assert type(personal.tenant_context) is TenantContext
    assert personal.tenant_context.organization_id is None
    assert personal.tenant_partition == "PERSONAL_LOCAL:personal:ws-1"


def test_business_write_job_and_outbox_are_one_transaction() -> None:
    engine = _engine()
    repository = WorkflowRepository()
    business = sa.Table(
        "business_documents",
        sa.MetaData(),
        sa.Column("document_id", sa.String(64), primary_key=True),
    )
    with engine.begin() as connection:
        business.create(connection)

    with pytest.raises(RuntimeError, match="rollback"):
        with engine.begin() as connection:
            connection.execute(business.insert().values(document_id="rolled-back"))
            repository.submit(connection, _request(idempotency_key="idem-rollback-0001"))
            raise RuntimeError("rollback")
    with engine.begin() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(business)) == 0
        from app.enterprise.jobs_v1.schema import jobs

        assert connection.scalar(sa.select(sa.func.count()).select_from(jobs)) == 0
        assert connection.scalar(sa.select(sa.func.count()).select_from(outbox)) == 0

    with engine.begin() as connection:
        connection.execute(business.insert().values(document_id="committed"))
        receipt = repository.submit(connection, _request(idempotency_key="idem-commit-0001"))
    with engine.begin() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(business)) == 1
        assert repository.get(connection, receipt.job_id) is not None
        assert len(repository.get_outbox_for_job(connection, receipt.job_id)) == 1


def test_all_workflow_kinds_are_idempotent_without_duplicate_business_commit() -> None:
    engine = _engine()
    repository = WorkflowRepository()
    for index, kind in enumerate(WorkflowKind):
        request = _request(kind, idempotency_key=f"idem-kind-{index:04d}")
        with engine.begin() as connection:
            first = repository.submit(connection, request)
        with engine.begin() as connection:
            duplicate = repository.submit(connection, request)
            events = repository.get_outbox_for_job(connection, first.job_id)
        assert duplicate.job_id == first.job_id
        assert duplicate.duplicate is True
        assert len(events) == 1


def test_procrastinate_adapter_uses_external_connection_queue_locks_and_schedule() -> None:
    engine = _engine()
    repository = WorkflowRepository()
    task = _Task()
    dispatcher = ProcrastinateOutboxDispatcher(
        task,
        clock=lambda: NOW,
        connection_unwrapper=lambda connection: connection,
    )
    publisher = OutboxPublisher(repository)
    scheduled = NOW + timedelta(minutes=5)
    with engine.begin() as connection:
        receipt = repository.submit(
            connection,
            _request(idempotency_key="idem-scheduled-0001", scheduled_at=scheduled),
        )
    with engine.begin() as connection:
        results = publisher.publish_batch(connection, dispatcher, now=NOW)
        assert results[0][1] == "401"
        events = repository.get_outbox_for_job(connection, receipt.job_id)
        assert events[0].status is OutboxStatus.DISPATCHED
        assert task.options["connection"] is connection
    assert task.options["queue"] == "mentorflow.enterprise"
    assert task.options["lock"].endswith(WorkflowKind.DOCUMENT_INGESTION.value)
    assert task.options["queueing_lock"] == f"mentorflow:{receipt.job_id}:0"
    assert task.options["schedule_at"] == scheduled
    assert task.kwargs == {"job_id": receipt.job_id, "dispatch_generation": 0}
    assert "FOR UPDATE SKIP LOCKED" in repository.claim_statement_for_postgresql().upper()
    assert ProcrastinateWorkerPolicy(concurrency=3).concurrency == 3
    assert PROCRASTINATE_MINIMUM_VERSION == "3.8.0"
    assert PROCRASTINATE_EVALUATED_VERSION == "3.9.0"


def test_expired_outbox_claim_is_recovered_after_publisher_crash() -> None:
    engine = _engine()
    repository = WorkflowRepository()
    with engine.begin() as connection:
        receipt = repository.submit(connection, _request(idempotency_key="idem-outbox-crash-0001"))
        first = repository.claim_outbox(connection, now=NOW, lease_seconds=10)
        assert first[0].status is OutboxStatus.CLAIMED
    with engine.begin() as connection:
        recovered = repository.claim_outbox(connection, now=NOW + timedelta(seconds=11))
        assert len(recovered) == 1
        assert recovered[0].outbox_id == first[0].outbox_id
        assert recovered[0].attempts == 2
        assert recovered[0].claim_token != first[0].claim_token
        assert recovered[0].job_id == receipt.job_id


def test_procrastinate_insert_and_outbox_ack_commit_or_rollback_together() -> None:
    engine = _engine()
    repository = WorkflowRepository()
    queue_table = sa.Table(
        "fake_procrastinate_jobs",
        sa.MetaData(),
        sa.Column("job_id", sa.String(36), primary_key=True),
    )
    with engine.begin() as connection:
        queue_table.create(connection)
        receipt = repository.submit(connection, _request(idempotency_key="idem-atomic-defer-0001"))
    dispatcher = ProcrastinateOutboxDispatcher(
        _Task(queue_table),
        clock=lambda: NOW,
        connection_unwrapper=lambda connection: connection,
    )
    publisher = OutboxPublisher(repository)

    with pytest.raises(RuntimeError, match="publisher crash"):
        with engine.begin() as connection:
            publisher.publish_batch(connection, dispatcher, now=NOW)
            raise RuntimeError("publisher crash before commit")
    with engine.begin() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(queue_table)) == 0
        assert repository.get_outbox_for_job(connection, receipt.job_id)[0].status is OutboxStatus.PENDING

    with engine.begin() as connection:
        publisher.publish_batch(connection, dispatcher, now=NOW)
    with engine.begin() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(queue_table)) == 1
        assert repository.get_outbox_for_job(connection, receipt.job_id)[0].status is OutboxStatus.DISPATCHED


@pytest.mark.parametrize(
    ("failure_class", "retryable"),
    [
        (FailureClass.RETRYABLE, True),
        (FailureClass.RATE_LIMITED, True),
        (FailureClass.PROVIDER_FAILURE, True),
        (FailureClass.WORKER_LOST, True),
        (FailureClass.NON_RETRYABLE, False),
        (FailureClass.INVALID_INPUT, False),
        (FailureClass.TENANT_REVALIDATION_FAILED, False),
    ],
)
def test_retry_taxonomy_is_deterministic_and_bounded(
    failure_class: FailureClass,
    retryable: bool,
) -> None:
    policy = RetryPolicy(base_seconds=2, maximum_seconds=30, jitter_ratio=0.2)
    failure = WorkflowFailure(failure_class, "FAILURE", "safe failure")
    first = policy.decide(
        job_id="job-retry-taxonomy",
        attempt=1,
        max_attempts=3,
        failure=failure,
        now=NOW,
    )
    repeated = policy.decide(
        job_id="job-retry-taxonomy",
        attempt=1,
        max_attempts=3,
        failure=failure,
        now=NOW,
    )
    assert first == repeated
    assert first.retry is retryable
    exhausted = policy.decide(
        job_id="job-retry-taxonomy",
        attempt=3,
        max_attempts=3,
        failure=failure,
        now=NOW,
    )
    assert exhausted.retry is False


def test_retry_taxonomy_backoff_dead_letter_redaction_and_manual_replay() -> None:
    engine = _engine()
    repository = WorkflowRepository()
    policy = RetryPolicy(base_seconds=10, maximum_seconds=60, jitter_ratio=0)
    with engine.begin() as connection:
        receipt = repository.submit(connection, _request(idempotency_key="idem-retry-0001"))
        running = repository.claim_job(connection, receipt.job_id, now=NOW)
        assert running is not None
        retry = repository.mark_failed(
            connection,
            running.job_id,
            running.claim_token or "",
            failure=WorkflowFailure(
                FailureClass.RATE_LIMITED,
                "RATE_LIMITED",
                "provider rate limited",
                retry_after_seconds=20,
            ),
            retry_policy=policy,
            now=NOW,
        )
        assert retry.status is JobStatus.FAILED_RETRYABLE
        assert retry.available_at == NOW + timedelta(seconds=20)
        assert retry.dispatch_generation == 1

    with engine.begin() as connection:
        running = repository.claim_job(connection, receipt.job_id, now=NOW + timedelta(seconds=20))
        assert running is not None
        dead = repository.mark_failed(
            connection,
            running.job_id,
            running.claim_token or "",
            failure=WorkflowFailure(
                FailureClass.INVALID_INPUT,
                "INVALID_INPUT",
                "student@example.test sent Bearer abcdefghijklmnop",
            ),
            retry_policy=policy,
            now=NOW + timedelta(seconds=20),
        )
        assert dead.status is JobStatus.DEAD_LETTER
        letter = repository.get_dead_letter(connection, dead.job_id)
        assert letter is not None
        assert letter.status is DeadLetterStatus.OPEN
        assert "student@example.test" not in letter.error_redacted
        assert "token=secret" not in letter.error_redacted
        assert "super-secret-value" not in str(letter.payload_redacted)
        replayed = repository.manual_retry(
            connection,
            dead.job_id,
            actor_id="admin-1",
            reason="validated corrected input",
            now=NOW + timedelta(minutes=1),
        )
        assert replayed.status is JobStatus.PENDING
        assert replayed.attempts == 0
        assert replayed.dispatch_generation == 2
        assert repository.get_dead_letter(connection, dead.job_id).status is DeadLetterStatus.RETRIED  # type: ignore[union-attr]


def test_recovery_revalidates_frozen_tenant_context_and_fails_closed() -> None:
    engine = _engine()
    service = WorkflowService()
    with engine.begin() as connection:
        receipt = service.submit(connection, _request(idempotency_key="idem-revalidate-0001"))
    revalidator = _Revalidator(allow=False)
    with engine.begin() as connection:
        with pytest.raises(TenantRevalidationRejected):
            service.claim_and_revalidate(
                connection,
                receipt.job_id,
                revalidator=revalidator,
                now=NOW,
            )
    with engine.begin() as connection:
        job = service.get(connection, receipt.job_id)
        assert job is not None
        assert job.status is JobStatus.DEAD_LETTER
        assert job.actor_context.organization_id == "org-1"
    assert revalidator.calls == 1


def test_admin_read_api_is_authorized_and_returns_redacted_failure_data() -> None:
    engine = _engine()
    repository = WorkflowRepository()
    job_id = _make_dead_letter(engine, repository)
    actor, tenant = _contexts()
    authorizer = _Authorizer(allow=True)
    service = WorkflowAdminService(engine, authorizer, repository)
    app = FastAPI()
    app.include_router(
        create_workflow_admin_router(
            service,
            lambda: WorkflowAdminRequestContext(actor=actor, tenant=tenant),
        )
    )
    client = TestClient(app)

    summary = client.get("/api/enterprise/jobs/summary")
    failed = client.get("/api/enterprise/jobs/failed")
    assert summary.status_code == 200
    assert summary.json()["dead_letters"] == 1
    assert failed.status_code == 200
    assert failed.json()[0]["job_id"] == job_id
    assert "student@example.test" not in str(failed.json())
    assert authorizer.calls[0][0] == "enterprise_jobs.read"

    replay = client.post(
        f"/api/enterprise/jobs/{job_id}/retry",
        json={"reason": "validated for replay"},
    )
    assert replay.status_code == 200
    assert replay.json()["status"] == JobStatus.PENDING.value
    assert "payload" not in replay.json()
    assert "actor_context" not in replay.json()
    cancelled = client.post(f"/api/enterprise/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == JobStatus.CANCELLED.value

    denied = WorkflowAdminService(engine, _Authorizer(allow=False), repository)
    denied_app = FastAPI()
    denied_app.include_router(
        create_workflow_admin_router(
            denied,
            lambda: WorkflowAdminRequestContext(actor=actor, tenant=tenant),
        )
    )
    assert TestClient(denied_app).get("/api/enterprise/jobs/summary").status_code == 403
