from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request
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
from app.enterprise.jobs_v1 import product_handlers
from app.enterprise.jobs_v1.learning_runs import (
    LEARNING_RUN_STAGES,
    LearningRunError,
    LearningRunOrchestrator,
    LearningRunService,
    tenant_document_selection_root,
)
from app.enterprise.jobs_v1.learning_runs_api import router
from app.enterprise.jobs_v1.models import (
    FailureClass,
    JobRecord,
    JobStatus,
    WorkflowKind,
)
from app.enterprise.jobs_v1.repository import WorkflowRepository
from app.enterprise.jobs_v1.retry import RetryPolicy, WorkflowFailure
from app.enterprise.jobs_v1.runtime import (
    DurableJobExecutor,
    DurableWorkerDependencies,
)
from app.enterprise.jobs_v1.schema import (
    ENTERPRISE_JOBS_SCHEMA,
    jobs,
    metadata,
    outbox,
)
from app.planning.product_runtime import PlanCreationRequest, PlanCreationService


NOW = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


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


def _context(
    *,
    subject: str = "learner-1",
    organization: str = "org-1",
    workspace: str = "workspace-1",
    request_id: str = "request-learning-run-1",
) -> tuple[ActorContext, TenantContext]:
    actor = ActorContext(
        subject_id=subject,
        actor_kind=ActorKind.HUMAN,
        issuer="https://identity.example.test/realms/mentorflow",
        token_id=f"token-{subject}",
        session_id=f"session-{subject}",
        request_id=request_id,
        authentication_time=NOW,
        authentication_strength="mfa",
        organization_id=organization,
        workspace_id=workspace,
        membership_id=f"membership-{subject}-{organization}-{workspace}",
        roles=("LEARNER",),
    )
    tenant = TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id=organization,
        workspace_id=workspace,
    )
    return actor, tenant


def _body() -> dict[str, object]:
    return {
        "idempotency_key": "learning-run-idempotency-0001",
        "document_selection": {
            "selection_id": "selection-1",
            "document_ids": ["lesson.md"],
            "title": "Enterprise Lesson",
            "language": "en",
            "conversion_config_identity": "document-preview-v1",
            "conversion_batch_size": 1,
        },
        "provider_session_id": "provider-session-opaque-1",
        "glossary": {"version": "v1", "terms": []},
        "plan": {
            "review_rounds": {"selected_rounds": 0},
            "weekly_availability": [
                {"weekday": "monday", "available_minutes": 600}
            ],
            "start_date": "2026-09-07",
            "deadline": "2026-10-31",
            "unavailable_dates": [],
            "buffers": [],
            "effort_interval_policy": "expected",
            "solve_config": {},
            "learner_level": "intermediate",
        },
    }


def _stage_owned_document(
    root: Path,
    *,
    actor: ActorContext,
    tenant: TenantContext,
) -> Path:
    selection = tenant_document_selection_root(
        root,
        actor=actor,
        tenant=tenant,
        selection_id="selection-1",
    )
    selection.mkdir(parents=True)
    document = selection / "lesson.md"
    document.write_text(
        "# Durable learning\n\nEvidence drives verified knowledge and a plan.\n",
        encoding="utf-8",
    )
    return document


def _service(
    engine: sa.Engine,
    root: Path,
    *,
    repository: WorkflowRepository | None = None,
) -> LearningRunService:
    return LearningRunService(
        engine,
        repository=repository,
        document_import_root=root,
        clock=lambda: NOW,
    )


def _jobs_for(
    engine: sa.Engine,
    repository: WorkflowRepository,
    run_id: str,
    *,
    actor: ActorContext,
    tenant: TenantContext,
) -> list[JobRecord]:
    with engine.begin() as connection:
        first = connection.execute(
            sa.select(jobs.c.tenant_partition)
            .where(jobs.c.trace_id == run_id)
            .limit(1)
        ).scalar_one()
        return repository.list_learning_run_jobs(
            connection,
            tenant_partition=first,
            trace_id=run_id,
            subject_id=actor.subject_id,
        )


def _claim(
    engine: sa.Engine,
    repository: WorkflowRepository,
    record: JobRecord,
) -> JobRecord:
    with engine.begin() as connection:
        claimed = repository.claim_job(
            connection,
            record.job_id,
            now=NOW,
            expected_dispatch_generation=record.dispatch_generation,
        )
    assert claimed is not None
    return claimed


def _document_result() -> dict[str, object]:
    quote = "Evidence drives verified knowledge and a plan."
    content_hash = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    source_revision = "1" * 64
    bundle_hash = "2" * 64
    evidence = {
        "material_id": "source-1",
        "source_id": "source-1",
        "source_revision_id": source_revision,
        "chunk_id": "document-chunk-1",
        "char_start": 0,
        "char_end": len(quote),
        "quote": quote,
        "source_file": "lesson.md",
        "heading": "Durable learning",
        "page_start": None,
        "page_end": None,
        "paragraph_start": None,
        "paragraph_end": None,
        "content_hash": content_hash,
        "bbox": None,
        "quote_hash": content_hash,
        "adapter_name": "docling-document",
        "adapter_version": "v1",
        "document_item_id": "item-1",
        "document_kind": "paragraph",
        "document_section_path": ["Durable learning"],
        "document_provenance": [],
    }
    return {
        "status": "SUCCESS",
        "handoffs": [
            {
                "schema": "mentorflow.document-pipeline-handoff.v1",
                "handoff_id": "dochandoff-1",
                "conversion_identity": {"value": "conversion-1"},
                "source_id": "source-1",
                "source_revision": source_revision,
                "evidence_bundle": {
                    "schema": "mentorflow.document-evidence.v1",
                    "adapter_name": "docling-document",
                    "adapter_version": "v1",
                    "docling_version": "2.119.0",
                    "bundle_id": "document-bundle-1",
                    "bundle_hash": bundle_hash,
                    "source_id": "source-1",
                    "source_file": "lesson.md",
                    "source_sha256": source_revision,
                    "document_name": "lesson.md",
                    "evidence": [],
                    "warnings": [],
                },
                "generic_evidence": [evidence],
                "limitations": [],
            }
        ],
    }


def _knowledge_result(job: JobRecord) -> dict[str, object]:
    bundle, _source, adapter = product_handlers._document_handoffs_bundle(
        job, job.payload
    )
    source_identity = bundle.source_identity
    source_revision = bundle.source_revision
    concept_id = "concept-durable-learning"
    return {
        "status": "COMPLETE",
        "document_adapter": adapter,
        "verified_bundle": {
            "source_identity": source_identity,
            "source_revision": source_revision,
            "status": "COMPLETE",
            "inventory": {
                "source_identity": source_identity,
                "source_revision": source_revision,
                "status": "COMPLETE",
                "items": [
                    {
                        "stable_item_id": concept_id,
                        "canonical_name": "Durable learning",
                        "knowledge_type": "CONCEPT",
                        "evidence_refs": [{"segment_id": "document-chunk-1"}],
                    }
                ],
            },
        },
        "map_handoff": {
            "handoff_id": "knowledge-map-handoff-1",
            "source_identity": source_identity,
            "source_revision": source_revision,
            "concepts": [
                {
                    "stable_concept_id": concept_id,
                    "canonical_name": "Durable learning",
                    "teaching_depth": "CORE_EXPLANATION",
                }
            ],
        },
        "knowledge_map": {
            "status": "COMPLETE",
            "snapshot": {
                "snapshot_id": "knowledge-map-snapshot-1",
                "source_identity": source_identity,
                "source_revision": source_revision,
                "status": "COMPLETE",
                "nodes": [
                    {
                        "concept_id": concept_id,
                        "canonical_name": "Durable learning",
                        "knowledge_type": "CONCEPT",
                        "evidence_ids": ["document-chunk-1"],
                        "confidence": 0.95,
                        "criticality": "NORMAL",
                    }
                ],
                "edges": [],
            },
        },
    }


def _complete(
    engine: sa.Engine,
    repository: WorkflowRepository,
    orchestrator: LearningRunOrchestrator,
    record: JobRecord,
    result: dict[str, object],
) -> JobRecord:
    claimed = _claim(engine, repository, record)
    with engine.begin() as connection:
        return orchestrator.complete_stage(
            connection,
            claimed,
            result=dict(result),
            now=NOW,
        )


def _create_run(
    engine: sa.Engine,
    root: Path,
    *,
    repository: WorkflowRepository | None = None,
) -> tuple[dict[str, object], ActorContext, TenantContext, WorkflowRepository]:
    actor, tenant = _context()
    _stage_owned_document(root, actor=actor, tenant=tenant)
    resolved_repository = repository or WorkflowRepository()
    created = _service(
        engine, root, repository=resolved_repository
    ).create(actor=actor, tenant=tenant, body=_body())
    return created, actor, tenant, resolved_repository


def test_create_route_uses_enterprise_context_and_enqueues_only_document(
    tmp_path: Path,
) -> None:
    engine = _engine()
    root = tmp_path / "document-import"
    actor, tenant = _context()
    _stage_owned_document(root, actor=actor, tenant=tenant)
    service = _service(engine, root)
    app = FastAPI()
    app.state.enterprise_learning_run_service = service

    @app.middleware("http")
    async def enterprise_context(request: Request, call_next):
        request.state.actor_context = actor
        request.state.tenant_context = tenant
        return await call_next(request)

    app.include_router(router)
    try:
        with TestClient(app) as client:
            response = client.post("/api/enterprise/learning-runs", json=_body())
            assert response.status_code == 202, response.text
            payload = response.json()
            assert payload["created"] is True
            assert payload["status"] == "PENDING"
            run_id = payload["learning_run_id"]
            duplicate = client.post(
                "/api/enterprise/learning-runs", json=_body()
            )
            assert duplicate.status_code == 202, duplicate.text
            assert duplicate.json()["created"] is False
            assert duplicate.json()["learning_run_id"] == run_id
            conflicting_body = _body()
            conflicting_body["document_selection"]["title"] = "Changed title"
            conflict = client.post(
                "/api/enterprise/learning-runs", json=conflicting_body
            )
            assert conflict.status_code == 409, conflict.text
            assert conflict.json()["detail"]["code"] == (
                "LEARNING_RUN_IDEMPOTENCY_CONFLICT"
            )
            status = client.get(f"/api/enterprise/learning-runs/{run_id}")
            assert status.status_code == 200
            not_ready = client.get(
                f"/api/enterprise/learning-runs/{run_id}/result"
            )
            assert not_ready.status_code == 409
        records = _jobs_for(
            engine,
            service.repository,
            run_id,
            actor=actor,
            tenant=tenant,
        )
        assert [record.workflow_kind for record in records] == [
            WorkflowKind.DOCUMENT_INGESTION
        ]
        assert records[0].actor_context == actor
        assert records[0].tenant_context == tenant
        assert "organization_id" not in _body()
        assert "workspace_id" not in _body()
        assert {route.path for route in router.routes} >= {
            "/api/enterprise/learning-runs",
            "/api/enterprise/learning-runs/{learning_run_id}",
            "/api/enterprise/learning-runs/{learning_run_id}/result",
        }
    finally:
        engine.dispose()


def test_postgresql_learning_run_fails_closed_without_canonical_selection_resolver(
) -> None:
    actor, tenant = _context()

    class _Dialect:
        name = "postgresql"

    class _PostgresqlEngine:
        dialect = _Dialect()

    service = LearningRunService(_PostgresqlEngine())  # type: ignore[arg-type]
    with pytest.raises(LearningRunError) as failure:
        service.create(actor=actor, tenant=tenant, body=_body())
    assert failure.value.code == "DOCUMENT_SELECTION_PERSISTENCE_REQUIRED"
    assert failure.value.status == 503


def test_three_real_contract_stages_chain_once_and_return_final_references(
    tmp_path: Path,
) -> None:
    engine = _engine()
    root = tmp_path / "document-import"
    created, actor, tenant, repository = _create_run(engine, root)
    run_id = str(created["learning_run_id"])
    orchestrator = LearningRunOrchestrator(repository)
    try:
        records = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )
        _complete(engine, repository, orchestrator, records[0], _document_result())
        records = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )
        assert [record.workflow_kind for record in records] == list(
            LEARNING_RUN_STAGES[:2]
        )
        assert records[0].status is JobStatus.SUCCEEDED
        knowledge = records[1]
        assert knowledge.status is JobStatus.PENDING
        assert knowledge.actor_context == records[0].actor_context
        assert knowledge.tenant_context == records[0].tenant_context
        assert knowledge.trace_id == run_id

        _complete(
            engine,
            repository,
            orchestrator,
            knowledge,
            _knowledge_result(knowledge),
        )
        records = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )
        assert [record.workflow_kind for record in records] == list(
            LEARNING_RUN_STAGES
        )
        plan_job = records[2]
        request = PlanCreationRequest.model_validate(plan_job.payload["request"])
        outcome = PlanCreationService().create(request)
        assert outcome.preview.status.value == "ready_for_confirmation"
        _complete(
            engine,
            repository,
            orchestrator,
            plan_job,
            {
                "status": outcome.preview.status.value,
                "preview": outcome.preview.model_dump(mode="json"),
            },
        )

        reloaded = LearningRunService(
            engine,
            repository=WorkflowRepository(),
            document_import_root=root,
            clock=lambda: NOW,
        )
        status = reloaded.status(run_id, actor=actor, tenant=tenant)
        assert status["status"] == "SUCCEEDED"
        assert [item["status"] for item in status["stages"]] == [
            "SUCCEEDED",
            "SUCCEEDED",
            "SUCCEEDED",
        ]
        result = reloaded.result(run_id, actor=actor, tenant=tenant)
        assert result["status"] == "SUCCEEDED"
        assert result["evidence"]
        assert result["knowledge_inventory"]["item_count"] == 1
        assert result["knowledge_map"]["snapshot_id"] == (
            "knowledge-map-snapshot-1"
        )
        assert result["plan"]["plan_id"] == request.plan_id
        assert result["plan"]["preview"]["status"] == "ready_for_confirmation"
        with engine.begin() as connection:
            assert connection.scalar(sa.select(sa.func.count()).select_from(jobs)) == 3
            assert connection.scalar(sa.select(sa.func.count()).select_from(outbox)) == 3
    finally:
        engine.dispose()


def test_worker_runtime_atomically_completes_document_and_enqueues_knowledge(
    tmp_path: Path,
) -> None:
    engine = _engine()
    root = tmp_path / "document-import"
    created, actor, tenant, repository = _create_run(engine, root)
    run_id = str(created["learning_run_id"])

    class _Revalidator:
        def revalidate(self, *, actor, tenant, workflow_kind):
            return AccessDecision(
                decision=AccessDecisionKind.ALLOW,
                policy_id="learning-run-worker-test",
                reason_code="CURRENT_MEMBERSHIP",
                actor=actor,
                resource=ResourceScope(
                    scope_type=ResourceScopeType.WORKSPACE,
                    organization_id=tenant.organization_id,
                    workspace_id=tenant.workspace_id,
                    classification=ResourceClassification.INTERNAL,
                ),
                action="enterprise_jobs.execute",
                evaluated_at=NOW,
            )

    class _DocumentHandler:
        def __init__(self) -> None:
            self.calls = 0

        def execute(self, *, job, checkpoint, ensure_not_cancelled):
            self.calls += 1
            ensure_not_cancelled()
            return _document_result()

    handler = _DocumentHandler()
    dependencies = DurableWorkerDependencies(
        _Revalidator(),
        {kind: handler for kind in WorkflowKind},
    )
    try:
        document = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )[0]
        executor = DurableJobExecutor(
            engine,
            dependencies,
            repository=repository,
            clock=lambda: NOW,
        )
        completed = executor.execute(
            job_id=document.job_id,
            dispatch_generation=document.dispatch_generation,
        )
        duplicate = executor.execute(
            job_id=document.job_id,
            dispatch_generation=document.dispatch_generation,
        )
        records = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )
        assert completed is not None and completed.status is JobStatus.SUCCEEDED
        assert duplicate is not None and duplicate.status is JobStatus.SUCCEEDED
        assert handler.calls == 1
        assert [record.workflow_kind for record in records] == list(
            LEARNING_RUN_STAGES[:2]
        )
        assert records[1].status is JobStatus.PENDING
        with engine.begin() as connection:
            assert connection.scalar(sa.select(sa.func.count()).select_from(jobs)) == 2
            assert connection.scalar(sa.select(sa.func.count()).select_from(outbox)) == 2
    finally:
        engine.dispose()


def test_stage_failure_is_redacted_and_never_creates_a_successor(tmp_path: Path) -> None:
    engine = _engine()
    root = tmp_path / "document-import"
    created, actor, tenant, repository = _create_run(engine, root)
    run_id = str(created["learning_run_id"])
    try:
        document = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )[0]
        claimed = _claim(engine, repository, document)
        with engine.begin() as connection:
            repository.mark_failed(
                connection,
                claimed.job_id,
                claimed.claim_token or "",
                failure=WorkflowFailure(
                    failure_class=FailureClass.NON_RETRYABLE,
                    error_code="DOCUMENT_HANDOFF_REJECTED",
                    safe_message="Bearer product-secret-must-not-leak",
                ),
                retry_policy=RetryPolicy(),
                now=NOW,
            )
        status = _service(engine, root, repository=repository).status(
            run_id, actor=actor, tenant=tenant
        )
        assert status["status"] == "FAILED"
        projection = str(status).casefold()
        assert "product-secret" not in projection
        assert "[redacted]" in projection
        records = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )
        assert len(records) == 1
        assert records[0].status is JobStatus.DEAD_LETTER
    finally:
        engine.dispose()


def test_duplicate_completion_is_idempotent_and_atomic_failure_rolls_back(
    tmp_path: Path,
) -> None:
    engine = _engine()
    root = tmp_path / "document-import"
    created, actor, tenant, repository = _create_run(engine, root)
    run_id = str(created["learning_run_id"])
    orchestrator = LearningRunOrchestrator(repository)
    try:
        document = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )[0]
        claimed = _claim(engine, repository, document)
        with engine.begin() as connection:
            first = orchestrator.complete_stage(
                connection,
                claimed,
                result=_document_result(),
                now=NOW,
            )
        with engine.begin() as connection:
            duplicate = orchestrator.complete_stage(
                connection,
                claimed,
                result=_document_result(),
                now=NOW,
            )
        assert first.job_id == duplicate.job_id
        with engine.begin() as connection:
            assert connection.scalar(sa.select(sa.func.count()).select_from(jobs)) == 2
            assert connection.scalar(sa.select(sa.func.count()).select_from(outbox)) == 2
    finally:
        engine.dispose()

    class _FailingSuccessorRepository(WorkflowRepository):
        def submit(self, connection, request):
            if request.workflow_kind is WorkflowKind.KNOWLEDGE_PIPELINE:
                raise RuntimeError("injected successor insert failure")
            return super().submit(connection, request)

    rollback_engine = _engine()
    rollback_root = tmp_path / "rollback-document-import"
    failing_repository = _FailingSuccessorRepository()
    created, actor, tenant, _ = _create_run(
        rollback_engine,
        rollback_root,
        repository=failing_repository,
    )
    run_id = str(created["learning_run_id"])
    try:
        document = _jobs_for(
            rollback_engine,
            failing_repository,
            run_id,
            actor=actor,
            tenant=tenant,
        )[0]
        claimed = _claim(rollback_engine, failing_repository, document)
        with pytest.raises(RuntimeError, match="successor insert failure"):
            with rollback_engine.begin() as connection:
                LearningRunOrchestrator(failing_repository).complete_stage(
                    connection,
                    claimed,
                    result=_document_result(),
                    now=NOW,
                )
        records = _jobs_for(
            rollback_engine,
            failing_repository,
            run_id,
            actor=actor,
            tenant=tenant,
        )
        assert len(records) == 1
        assert records[0].status is JobStatus.RUNNING
    finally:
        rollback_engine.dispose()


def test_restart_reconstructs_and_cross_tenant_or_subject_is_not_found(
    tmp_path: Path,
) -> None:
    database = tmp_path / "learning-runs.sqlite3"
    root = tmp_path / "document-import"
    engine = _engine(database)
    created, actor, tenant, repository = _create_run(engine, root)
    run_id = str(created["learning_run_id"])
    document = _jobs_for(
        engine, repository, run_id, actor=actor, tenant=tenant
    )[0]
    _complete(
        engine,
        repository,
        LearningRunOrchestrator(repository),
        document,
        _document_result(),
    )
    engine.dispose()

    restarted_engine = _engine(database)
    restarted = _service(restarted_engine, root)
    try:
        status = restarted.status(run_id, actor=actor, tenant=tenant)
        assert status["status"] == "RUNNING"
        assert [item["status"] for item in status["stages"]] == [
            "SUCCEEDED",
            "PENDING",
            "PENDING",
        ]

        other_subject, same_tenant = _context(subject="learner-2")
        with pytest.raises(LearningRunError) as subject_denied:
            restarted.status(
                run_id,
                actor=other_subject,
                tenant=same_tenant,
            )
        assert subject_denied.value.status == 404

        other_actor, other_tenant = _context(
            subject="learner-1",
            organization="org-2",
            workspace="workspace-2",
        )
        with pytest.raises(LearningRunError) as tenant_denied:
            restarted.status(
                run_id,
                actor=other_actor,
                tenant=other_tenant,
            )
        assert tenant_denied.value.status == 404
    finally:
        restarted_engine.dispose()


def test_document_handoff_adapter_uses_real_evidence_and_rejects_stale_lineage(
    tmp_path: Path,
) -> None:
    engine = _engine()
    root = tmp_path / "document-import"
    created, actor, tenant, repository = _create_run(engine, root)
    run_id = str(created["learning_run_id"])
    try:
        document = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )[0]
        _complete(
            engine,
            repository,
            LearningRunOrchestrator(repository),
            document,
            _document_result(),
        )
        knowledge = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )[1]
        bundle, source, adapter = product_handlers._document_handoffs_bundle(
            knowledge, knowledge.payload
        )
        assert bundle.parser_version == "mentorflow.document-evidence-adapter.v1"
        assert bundle.platform == "document"
        assert bundle.segments[0].original_text.startswith("Evidence drives")
        assert source.source_identity == bundle.source_identity
        assert adapter["handoff_ids"] == ["dochandoff-1"]

        stale_payload = dict(knowledge.payload)
        stale_handoffs = [dict(stale_payload["document_handoffs"][0])]
        stale_evidence = [dict(stale_handoffs[0]["generic_evidence"][0])]
        stale_evidence[0]["source_revision_id"] = "f" * 64
        stale_handoffs[0]["generic_evidence"] = stale_evidence
        stale_payload["document_handoffs"] = stale_handoffs
        with pytest.raises(WorkflowFailure) as failure:
            product_handlers._document_handoffs_bundle(knowledge, stale_payload)
        assert failure.value.error_code == "DOCUMENT_KNOWLEDGE_EVIDENCE_STALE"
    finally:
        engine.dispose()


def test_missing_provider_session_fails_closed_before_plan_stage(tmp_path: Path) -> None:
    engine = _engine()
    root = tmp_path / "document-import"
    created, actor, tenant, repository = _create_run(engine, root)
    run_id = str(created["learning_run_id"])
    try:
        document = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )[0]
        _complete(
            engine,
            repository,
            LearningRunOrchestrator(repository),
            document,
            _document_result(),
        )
        knowledge = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )[1]
        with pytest.raises(WorkflowFailure) as failure:
            product_handlers.knowledge_pipeline(
                engine=engine,
                job=knowledge,
                checkpoint=lambda *_args: pytest.fail(
                    "missing Provider session reached product progress"
                ),
                ensure_not_cancelled=lambda: None,
            )
        assert failure.value.error_code == "KNOWLEDGE_PIPELINE_PROVIDER_FAILED"
        records = _jobs_for(
            engine, repository, run_id, actor=actor, tenant=tenant
        )
        assert len(records) == 2
        assert all(record.workflow_kind is not WorkflowKind.PLAN_CREATION for record in records)
    finally:
        engine.dispose()
