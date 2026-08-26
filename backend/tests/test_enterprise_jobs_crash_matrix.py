from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from app.enterprise.contracts_v1 import ActorContext, ActorKind, TenantContext, TenantMode
from app.enterprise.jobs_v1.models import FailureClass, JobRequest, JobStatus, WorkflowKind
from app.enterprise.jobs_v1.repository import WorkflowRepository
from app.enterprise.jobs_v1.retry import RetryPolicy, WorkflowFailure
from app.enterprise.jobs_v1.schema import ENTERPRISE_JOBS_SCHEMA, metadata
from app.enterprise.jobs_v1.service import DurableEffectCoordinator, WorkflowCancellationRequested


NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _engine(path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite+pysqlite:///{path.as_posix()}", future=True).execution_options(
        schema_translate_map={ENTERPRISE_JOBS_SCHEMA: None}
    )
    with engine.begin() as connection:
        metadata.create_all(connection)
    return engine


def _request(key: str, *, max_attempts: int = 3) -> JobRequest:
    actor = ActorContext(
        subject_id="service-worker",
        actor_kind=ActorKind.SERVICE,
        issuer="https://identity.example.test/realms/mentorflow",
        token_id="token-1",
        session_id="session-1",
        request_id="request-crash-matrix",
        authentication_time=NOW,
        authentication_strength="client-credentials",
        organization_id="org-1",
        workspace_id="ws-1",
        service_permissions=("WORKFLOW_EXECUTE",),
    )
    tenant = TenantContext(
        organization_id="org-1",
        workspace_id="ws-1",
        mode=TenantMode.ENTERPRISE_SERVER,
    )
    return JobRequest(
        workflow_kind=WorkflowKind.KNOWLEDGE_PIPELINE,
        payload={"source_id": "source-1"},
        actor_context=actor,
        tenant_context=tenant,
        request_id=actor.request_id,
        trace_id="trace-crash",
        idempotency_key=key,
        max_attempts=max_attempts,
        scheduled_at=NOW,
    )


class _IdempotentEffect:
    def __init__(self) -> None:
        self.invocations = 0
        self.effects: dict[str, dict[str, Any]] = {}

    def execute(self, *, effect_name: str, idempotency_key: str, payload: dict[str, Any]):
        self.invocations += 1
        return self.effects.setdefault(
            idempotency_key,
            {"provider_receipt_id": f"receipt-{len(self.effects) + 1}"},
        )


def test_worker_kill_after_claim_recovers_from_expired_lease(tmp_path: Path) -> None:
    engine = _engine(tmp_path / "worker-kill.db")
    repository = WorkflowRepository()
    with engine.begin() as connection:
        receipt = repository.submit(connection, _request("idem-worker-kill"))
        claimed = repository.claim_job(connection, receipt.job_id, now=NOW, lease_seconds=10)
        assert claimed is not None and claimed.status is JobStatus.RUNNING

    # Simulates SIGKILL: no failure callback, only the durable lease expires.
    with engine.begin() as connection:
        recovered = repository.recover_expired_claims(
            connection,
            now=NOW + timedelta(seconds=11),
            retry_policy=RetryPolicy(base_seconds=1, maximum_seconds=30, jitter_ratio=0),
        )
        assert recovered[0].status is JobStatus.FAILED_RETRYABLE
        assert recovered[0].dispatch_generation == 1
        assert len(repository.get_outbox_for_job(connection, receipt.job_id)) == 2


def test_database_and_app_restart_preserve_job_and_resume(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    first_engine = _engine(path)
    first_repository = WorkflowRepository()
    with first_engine.begin() as connection:
        receipt = first_repository.submit(connection, _request("idem-db-app-restart"))
    first_engine.dispose()

    # New Engine and Repository instances model DB connection and app process restart.
    second_engine = _engine(path)
    second_repository = WorkflowRepository()
    with second_engine.begin() as connection:
        persisted = second_repository.get(connection, receipt.job_id)
        assert persisted is not None and persisted.status is JobStatus.PENDING
        claimed = second_repository.claim_job(connection, receipt.job_id, now=NOW)
        assert claimed is not None and claimed.attempts == 1


def test_after_external_call_crash_reuses_provider_idempotency_key(tmp_path: Path) -> None:
    engine = _engine(tmp_path / "after-external.db")
    repository = WorkflowRepository()
    with engine.begin() as connection:
        receipt = repository.submit(connection, _request("idem-after-external"))
        job = repository.claim_job(connection, receipt.job_id, now=NOW)
        assert job is not None
    effect = _IdempotentEffect()
    coordinator = DurableEffectCoordinator(engine, repository)

    def crash() -> None:
        raise RuntimeError("crash after external call")

    with pytest.raises(RuntimeError, match="after external"):
        coordinator.execute_once(
            job_id=job.job_id,
            claim_token=job.claim_token or "",
            effect_name="provider-summary",
            payload={"source_id": "source-1"},
            effect_port=effect,
            now=lambda: NOW,
            after_external_call=crash,
        )
    with engine.begin() as connection:
        prepared = repository.get(connection, job.job_id)
        assert prepared is not None
        assert prepared.external_effects["provider-summary"]["status"] == "PREPARED"

    recovered_receipt = coordinator.execute_once(
        job_id=job.job_id,
        claim_token=job.claim_token or "",
        effect_name="provider-summary",
        payload={"source_id": "source-1"},
        effect_port=effect,
        now=lambda: NOW + timedelta(seconds=1),
    )
    assert recovered_receipt["provider_receipt_id"] == "receipt-1"
    assert effect.invocations == 2
    assert len(effect.effects) == 1  # one provider effect despite retry delivery


def test_cancellation_before_provider_call_stops_later_phases_and_keeps_evidence(tmp_path: Path) -> None:
    engine = _engine(tmp_path / "cancel.db")
    repository = WorkflowRepository()
    with engine.begin() as connection:
        receipt = repository.submit(connection, _request("idem-cancel"))
        job = repository.claim_job(connection, receipt.job_id, now=NOW)
        assert job is not None
        repository.record_checkpoint(
            connection,
            job.job_id,
            job.claim_token or "",
            phase="validated",
            checkpoint={"source_id": "source-1"},
            evidence={"evidence_id": "evidence-1"},
            now=NOW,
        )
        repository.request_cancel(connection, job.job_id, requested_at=NOW + timedelta(seconds=1))

    effect = _IdempotentEffect()
    with pytest.raises(WorkflowCancellationRequested):
        DurableEffectCoordinator(engine, repository).execute_once(
            job_id=job.job_id,
            claim_token=job.claim_token or "",
            effect_name="provider-summary",
            payload={},
            effect_port=effect,
            now=lambda: NOW + timedelta(seconds=2),
        )
    with engine.begin() as connection:
        cancelled = repository.get(connection, job.job_id)
        assert cancelled is not None
        assert cancelled.status is JobStatus.CANCELLED
        assert cancelled.checkpoint["validated"]["source_id"] == "source-1"
        assert cancelled.evidence[0]["evidence_id"] == "evidence-1"
    assert effect.invocations == 0


def test_cancellation_after_provider_call_persists_receipt_without_duplicate_call(tmp_path: Path) -> None:
    engine = _engine(tmp_path / "cancel-after-provider.db")
    repository = WorkflowRepository()
    with engine.begin() as connection:
        receipt = repository.submit(connection, _request("idem-cancel-after-provider"))
        job = repository.claim_job(connection, receipt.job_id, now=NOW)
        assert job is not None

    class _CancellingEffect(_IdempotentEffect):
        def execute(
            self,
            *,
            effect_name: str,
            idempotency_key: str,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            provider_receipt = super().execute(
                effect_name=effect_name,
                idempotency_key=idempotency_key,
                payload=payload,
            )
            with engine.begin() as connection:
                repository.request_cancel(
                    connection,
                    job.job_id,
                    requested_at=NOW + timedelta(seconds=1),
                )
            return provider_receipt

    effect = _CancellingEffect()
    coordinator = DurableEffectCoordinator(engine, repository)
    with pytest.raises(WorkflowCancellationRequested):
        coordinator.execute_once(
            job_id=job.job_id,
            claim_token=job.claim_token or "",
            effect_name="provider-summary",
            payload={"source_id": "source-1"},
            effect_port=effect,
            now=lambda: NOW + timedelta(seconds=2),
        )

    with engine.begin() as connection:
        cancelled = repository.get(connection, job.job_id)
        assert cancelled is not None
        assert cancelled.status is JobStatus.CANCELLED
        assert cancelled.external_effects["provider-summary"]["status"] == "COMPLETED"
        assert (
            cancelled.external_effects["provider-summary"]["receipt"]["provider_receipt_id"]
            == "receipt-1"
        )
    with pytest.raises(RuntimeError, match="running job lease"):
        coordinator.execute_once(
            job_id=job.job_id,
            claim_token=job.claim_token or "",
            effect_name="provider-summary",
            payload={"source_id": "source-1"},
            effect_port=effect,
            now=lambda: NOW + timedelta(seconds=3),
        )
    assert effect.invocations == 1


def test_before_final_commit_rolls_back_business_effect_then_recovers_once(tmp_path: Path) -> None:
    engine = _engine(tmp_path / "before-final.db")
    repository = WorkflowRepository()
    business = sa.Table(
        "business_results",
        sa.MetaData(),
        sa.Column("job_id", sa.String(36), primary_key=True),
    )
    with engine.begin() as connection:
        business.create(connection)
        receipt = repository.submit(connection, _request("idem-before-final"))
        job = repository.claim_job(connection, receipt.job_id, now=NOW, lease_seconds=10)
        assert job is not None

    with pytest.raises(RuntimeError, match="before final commit"):
        with engine.begin() as connection:
            connection.execute(business.insert().values(job_id=job.job_id))
            repository.mark_succeeded(
                connection,
                job.job_id,
                job.claim_token or "",
                result={"ok": True},
                now=NOW + timedelta(seconds=1),
            )
            raise RuntimeError("crash before final commit")
    with engine.begin() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(business)) == 0
        still_running = repository.get(connection, job.job_id)
        assert still_running is not None and still_running.status is JobStatus.RUNNING
        repository.recover_expired_claims(
            connection,
            now=NOW + timedelta(seconds=11),
            retry_policy=RetryPolicy(base_seconds=1, maximum_seconds=10, jitter_ratio=0),
        )
    with engine.begin() as connection:
        retried = repository.claim_job(connection, job.job_id, now=NOW + timedelta(seconds=12))
        assert retried is not None
        connection.execute(business.insert().values(job_id=job.job_id))
        succeeded = repository.mark_succeeded(
            connection,
            retried.job_id,
            retried.claim_token or "",
            result={"ok": True},
            now=NOW + timedelta(seconds=13),
        )
        assert succeeded.status is JobStatus.SUCCEEDED
    with engine.begin() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(business)) == 1


def test_non_retryable_failure_before_final_commit_can_be_replayed_manually(tmp_path: Path) -> None:
    engine = _engine(tmp_path / "manual-replay.db")
    repository = WorkflowRepository()
    with engine.begin() as connection:
        receipt = repository.submit(connection, _request("idem-manual-replay", max_attempts=1))
        job = repository.claim_job(connection, receipt.job_id, now=NOW)
        assert job is not None
        dead = repository.mark_failed(
            connection,
            job.job_id,
            job.claim_token or "",
            failure=WorkflowFailure(FailureClass.NON_RETRYABLE, "POLICY_DENIED", "policy denied"),
            retry_policy=RetryPolicy(),
            now=NOW,
        )
        assert dead.status is JobStatus.DEAD_LETTER
        replay = repository.manual_retry(
            connection,
            job.job_id,
            actor_id="admin-1",
            reason="policy corrected",
            now=NOW + timedelta(minutes=1),
        )
        assert replay.status is JobStatus.PENDING
