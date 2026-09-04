from __future__ import annotations

import ast
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.enterprise import worker_entry
from app.enterprise.contracts_v1 import (
    AccessDecisionKind,
    ActorContext,
    ActorKind,
    TenantContext,
    TenantMode,
)
from app.enterprise.identity.models import (
    BusinessRole,
    MembershipGrant,
    MembershipStatus,
    ServiceIdentityGrant,
    ServicePermission,
)
from app.enterprise.jobs_v1 import product_handlers, production
from app.enterprise.jobs_v1.models import WorkflowKind
from app.enterprise.jobs_v1.production import (
    PREVIEW_SUPPORTED_WORKFLOW_KINDS,
    PREVIEW_UNSUPPORTED_WORKFLOW_KINDS,
    PRODUCTION_FACTORY_DOTTED_PATH,
    ProductionTenantContextRevalidator,
    ProductionWorkflowHandler,
    UnsupportedWorkflowHandler,
    build,
    handler_environment_name,
    required_handler_environment_names,
    supported_workflow_kinds,
    unsupported_workflow_kinds,
)
from app.enterprise.jobs_v1.retry import WorkflowFailure
from app.enterprise.jobs_v1.runtime import (
    TASK_NAME,
    DurableWorkerDependencies,
    WorkerConfigurationError,
    WorkerRuntimeConfig,
    build_procrastinate_app,
    load_worker_dependencies,
)
from app.knowledge.pipeline_v2.progress import PipelineStatus
from app.sources.document.ingestion.contracts import DocumentBatchStatus
from app.sources.video.transcript_evidence import (
    SelectedTrackEvidence,
    TranscriptEvidenceBundle,
    TranscriptPartEvidence,
    TranscriptSegment,
)
from scripts.enterprise import build_rp05_staging_release as release_builder


NOW = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)
ISSUER = "https://identity.example.test/realms/mentorflow"
CALLBACK_MODULE = "mentorflow_product_workflows"
CALLBACK_PATH = f"{CALLBACK_MODULE}:execute"
REAL_HANDLER_PATHS = {
    WorkflowKind.DOCUMENT_INGESTION: (
        "app.enterprise.jobs_v1.product_handlers:document_ingestion"
    ),
    WorkflowKind.KNOWLEDGE_PIPELINE: (
        "app.enterprise.jobs_v1.product_handlers:knowledge_pipeline"
    ),
    WorkflowKind.PLAN_CREATION: (
        "app.enterprise.jobs_v1.product_handlers:plan_creation"
    ),
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _postgres_engine() -> sa.Engine:
    return sa.create_engine(
        "postgresql+psycopg://mentorflow_app:password@database/mentorflow",
        future=True,
    )


def _source(*, callback_path: str | None = None) -> dict[str, str]:
    source = {
        "MENTORFLOW_DEPLOYMENT_MODE": TenantMode.ENTERPRISE_SERVER.value,
        "MENTORFLOW_KEYCLOAK_ISSUER": ISSUER,
        "MENTORFLOW_WORKER_RUNTIME_FACTORY": PRODUCTION_FACTORY_DOTTED_PATH,
    }
    source.update(
        {
            handler_environment_name(kind): callback_path or REAL_HANDLER_PATHS[kind]
            for kind in PREVIEW_SUPPORTED_WORKFLOW_KINDS
        }
    )
    return source


@pytest.fixture
def product_callback_module(monkeypatch):
    calls: list[tuple[sa.Engine, object]] = []
    module = types.ModuleType(CALLBACK_MODULE)

    def execute(*, engine, job, checkpoint, ensure_not_cancelled):
        ensure_not_cancelled()
        checkpoint("product", {"kind": job.workflow_kind.value})
        calls.append((engine, job))
        return {"status": "processed", "kind": job.workflow_kind.value}

    module.execute = execute  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, CALLBACK_MODULE, module)
    return calls


def _actor(
    *,
    kind: ActorKind = ActorKind.HUMAN,
    subject_id: str = "subject-1",
    permissions: tuple[str, ...] = (),
) -> ActorContext:
    return ActorContext(
        subject_id=subject_id,
        actor_kind=kind,
        issuer=ISSUER,
        token_id="worker-token",
        session_id="worker-session",
        request_id="worker-request",
        authentication_time=NOW,
        authentication_strength="oidc",
        organization_id="org-1",
        workspace_id="ws-1",
        membership_id="membership-1" if kind is ActorKind.HUMAN else None,
        roles=(BusinessRole.LEARNER.value,) if kind is ActorKind.HUMAN else (),
        service_permissions=permissions,
    )


def _tenant() -> TenantContext:
    return TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id="org-1",
        workspace_id="ws-1",
    )


def _job(kind: WorkflowKind, payload: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        workflow_kind=kind,
        payload={
            "organization_id": "org-1",
            "workspace_id": "ws-1",
            **payload,
        },
        request_id="worker-request",
        actor_context=_actor(),
        tenant_context=_tenant(),
    )


class _Directory:
    def __init__(self, membership: MembershipGrant | None) -> None:
        self.membership = membership
        self.calls: list[dict[str, object]] = []

    def resolve_membership(self, **kwargs):
        self.calls.append(kwargs)
        return self.membership

    def resolve_service_identity(self, **kwargs):
        raise AssertionError("service identities use the current configured grant projection")


def test_production_factory_loads_three_real_callbacks_and_two_fail_closed_kinds() -> None:
    engine = _postgres_engine()
    try:
        dependencies = build(engine=engine, source=_source())
    finally:
        engine.dispose()

    assert PRODUCTION_FACTORY_DOTTED_PATH == "app.enterprise.jobs_v1.production:build"
    assert supported_workflow_kinds() == PREVIEW_SUPPORTED_WORKFLOW_KINDS
    assert unsupported_workflow_kinds() == PREVIEW_UNSUPPORTED_WORKFLOW_KINDS
    assert set(dependencies.handlers) == set(WorkflowKind)
    assert dependencies.missing_workflow_kinds == ()
    assert dependencies.unsupported_workflow_kinds == ()
    assert dependencies.invalid_workflow_handlers == ()
    assert required_handler_environment_names() == tuple(
        handler_environment_name(kind) for kind in PREVIEW_SUPPORTED_WORKFLOW_KINDS
    )
    for kind in PREVIEW_SUPPORTED_WORKFLOW_KINDS:
        handler = dependencies.handlers[kind]
        assert isinstance(handler, ProductionWorkflowHandler)
        assert handler.dotted_path == REAL_HANDLER_PATHS[kind]
    for kind in PREVIEW_UNSUPPORTED_WORKFLOW_KINDS:
        assert isinstance(dependencies.handlers[kind], UnsupportedWorkflowHandler)
        assert not hasattr(dependencies.handlers[kind], "dotted_path")


def test_factory_loads_through_generic_loader_without_account_deletion_environment(
    monkeypatch,
) -> None:
    engine = _postgres_engine()
    source = _source()
    assert handler_environment_name(WorkflowKind.ACCOUNT_DELETION) not in source
    with monkeypatch.context() as state:
        for name, value in source.items():
            state.setenv(name, value)
        try:
            dependencies = load_worker_dependencies(
                PRODUCTION_FACTORY_DOTTED_PATH,
                engine=engine,
            )
        finally:
            engine.dispose()
    assert set(dependencies.handlers) == set(WorkflowKind)
    assert isinstance(
        dependencies.handlers[WorkflowKind.ACCOUNT_DELETION],
        UnsupportedWorkflowHandler,
    )


def test_production_factory_has_no_test_or_inmemory_runtime_dependency() -> None:
    source = Path(production.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(name == "tests" or name.startswith("tests.") for name in imported_modules)
    assert "InMemoryConnector" not in source

    sqlite_engine = sa.create_engine("sqlite+pysqlite://", future=True)
    try:
        with pytest.raises(WorkerConfigurationError, match="PostgreSQL application role"):
            build(engine=sqlite_engine, source=_source())
    finally:
        sqlite_engine.dispose()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda source: source.pop(
                handler_environment_name(WorkflowKind.PLAN_CREATION)
            ),
            "MENTORFLOW_WORKER_HANDLER_PLAN_CREATION is required",
        ),
        (
            lambda source: source.__setitem__(
                "MENTORFLOW_WORKER_HANDLER_UNKNOWN_KIND", CALLBACK_PATH
            ),
            "unknown workflow kind",
        ),
        (
            lambda source: source.__setitem__(
                handler_environment_name(WorkflowKind.ACCOUNT_DELETION),
                CALLBACK_PATH,
            ),
            "unknown workflow kind",
        ),
        (
            lambda source: source.__setitem__(
                handler_environment_name(WorkflowKind.PLAN_CREATION),
                "tests.worker_fixture:build",
            ),
            "must not reference test modules",
        ),
    ],
)
def test_factory_rejects_missing_unknown_unsupported_or_test_handler(
    product_callback_module,
    mutate,
    message: str,
) -> None:
    source = _source(callback_path=CALLBACK_PATH)
    mutate(source)
    engine = _postgres_engine()
    try:
        with pytest.raises(WorkerConfigurationError, match=message):
            build(engine=engine, source=source)
    finally:
        engine.dispose()


def test_generic_loader_rejects_unknown_keys_and_invalid_dependencies(monkeypatch) -> None:
    engine = _postgres_engine()
    module = types.ModuleType("invalid_product_factory")
    handler = SimpleNamespace(execute=lambda **kwargs: {})
    revalidator = SimpleNamespace(revalidate=lambda **kwargs: None)
    complete = {kind: handler for kind in WorkflowKind}
    monkeypatch.setitem(sys.modules, module.__name__, module)
    try:
        module.build = lambda *, engine: DurableWorkerDependencies(  # type: ignore[attr-defined]
            revalidator,
            {**complete, "UNKNOWN_KIND": handler},
        )
        with pytest.raises(WorkerConfigurationError, match="unknown workflow kind"):
            load_worker_dependencies(f"{module.__name__}:build", engine=engine)

        module.build = lambda *, engine: DurableWorkerDependencies(  # type: ignore[attr-defined]
            object(), complete
        )
        with pytest.raises(WorkerConfigurationError, match="tenant revalidator"):
            load_worker_dependencies(f"{module.__name__}:build", engine=engine)

        module.build = lambda *, engine: DurableWorkerDependencies(  # type: ignore[attr-defined]
            revalidator, {kind: object() for kind in WorkflowKind}
        )
        with pytest.raises(WorkerConfigurationError, match="invalid workflow handler"):
            load_worker_dependencies(f"{module.__name__}:build", engine=engine)

        module.build = lambda *, engine: DurableWorkerDependencies(  # type: ignore[attr-defined]
            revalidator,
            [],  # type: ignore[arg-type]
        )
        with pytest.raises(WorkerConfigurationError, match="handler registry"):
            load_worker_dependencies(f"{module.__name__}:build", engine=engine)
    finally:
        engine.dispose()


def test_supported_handlers_dispatch_only_their_authoritative_kind(
    product_callback_module,
) -> None:
    engine = _postgres_engine()
    dependencies = build(engine=engine, source=_source(callback_path=CALLBACK_PATH))
    checkpoints: list[tuple[str, dict[str, str]]] = []
    cancellation_checks: list[bool] = []
    try:
        for kind in PREVIEW_SUPPORTED_WORKFLOW_KINDS:
            handler = dependencies.handlers[kind]
            result = handler.execute(
                job=SimpleNamespace(workflow_kind=kind),
                checkpoint=lambda phase, value: checkpoints.append((phase, value)),
                ensure_not_cancelled=lambda: cancellation_checks.append(True),
            )
            assert result == {"status": "processed", "kind": kind.value}

        with pytest.raises(WorkflowFailure, match="does not match"):
            dependencies.handlers[WorkflowKind.PLAN_CREATION].execute(
                job=SimpleNamespace(workflow_kind=WorkflowKind.KNOWLEDGE_PIPELINE),
                checkpoint=lambda phase, value: None,
                ensure_not_cancelled=lambda: None,
            )
    finally:
        engine.dispose()

    assert len(product_callback_module) == len(PREVIEW_SUPPORTED_WORKFLOW_KINDS)
    assert len(checkpoints) == len(PREVIEW_SUPPORTED_WORKFLOW_KINDS)
    assert len(cancellation_checks) == len(PREVIEW_SUPPORTED_WORKFLOW_KINDS) * 2


@pytest.mark.parametrize("kind", PREVIEW_UNSUPPORTED_WORKFLOW_KINDS)
def test_unsupported_workflows_fail_before_checkpoint_or_product_side_effect(kind) -> None:
    handler = UnsupportedWorkflowHandler(kind)
    checkpoints: list[object] = []
    executions: list[object] = []
    with pytest.raises(WorkflowFailure) as failure:
        handler.execute(
            job=SimpleNamespace(workflow_kind=kind),
            checkpoint=lambda *args: checkpoints.append(args),
            ensure_not_cancelled=lambda: executions.append("executed"),
        )
    assert failure.value.failure_class.value == "NON_RETRYABLE"
    assert failure.value.error_code == "WORKFLOW_UNSUPPORTED_IN_ENGINEERING_PREVIEW"
    assert checkpoints == []
    assert executions == []


def test_product_adapter_module_imports_real_services_and_no_document_service_alias() -> None:
    source = Path(product_handlers.__file__).read_text(encoding="utf-8")
    assert product_handlers.DocumentIngestionApplication.__module__ == (
        "app.sources.document.ingestion.application"
    )
    assert product_handlers.KnowledgePipelineV2.__module__ == (
        "app.knowledge.pipeline_v2.pipeline"
    )
    assert product_handlers.PlanCreationService.__module__ == (
        "app.planning.product_runtime.creation"
    )
    assert "DocumentIngestionService" not in source
    assert "AccountDeletion" not in source


def test_document_handler_delegates_to_document_ingestion_application(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "document-import"
    (root / "uploads").mkdir(parents=True)
    monkeypatch.setenv("MENTORFLOW_DOCUMENT_IMPORT_ROOT", str(root))
    calls: list[object] = []

    class _Progress:
        def to_dict(self):
            return {"status": "CONVERTING"}

    class _Result:
        status = DocumentBatchStatus.SUCCESS

        def to_dict(self):
            return {"status": "SUCCESS", "service": "DocumentIngestionApplication"}

    class _Application:
        def __init__(self, policy):
            calls.append(policy)

        def ingest(self, request, *, progress_sink, should_stop):
            assert should_stop() is False
            progress_sink(_Progress())
            calls.append(request)
            return _Result()

    monkeypatch.setattr(product_handlers, "DocumentIngestionApplication", _Application)
    checkpoints: list[tuple[str, object]] = []
    engine = _postgres_engine()
    try:
        result = product_handlers.document_ingestion(
            engine=engine,
            job=_job(
                WorkflowKind.DOCUMENT_INGESTION,
                {
                    "request": {
                        "request_id": "worker-request",
                        "selected_documents": ["selection-1/source.pdf"],
                        "conversion_config_identity": "docling-preview-v1",
                        "conversion_batch_size": 1,
                    }
                },
            ),
            checkpoint=lambda phase, value: checkpoints.append((phase, value)),
            ensure_not_cancelled=lambda: None,
        )
    finally:
        engine.dispose()
    assert result == {"status": "SUCCESS", "service": "DocumentIngestionApplication"}
    assert len(calls) == 2
    assert checkpoints == [("document_ingestion", {"status": "CONVERTING"})]


def _knowledge_bundle() -> dict[str, object]:
    track = SelectedTrackEvidence(
        track_identity="track-1",
        payload_source_identity="payload-1",
        language="zh",
        kind="manual",
        extension="json",
        source="fixture",
        name=None,
    )
    segment = TranscriptSegment(
        stable_segment_id="segment-1",
        ordinal=0,
        part_index=1,
        part_cid="cid-1",
        start_ms=0,
        end_ms=1000,
        original_text="evidence",
        language="zh",
        subtitle_kind="manual",
        source_reference="https://example.test/video",
        segment_hash="1" * 64,
    )
    part = TranscriptPartEvidence(
        part_index=1,
        cid="cid-1",
        title="Part 1",
        duration_seconds=1.0,
        subtitle_identity="subtitle-1",
        selected_track=track,
        raw_payload_hash="2" * 64,
        segment_ids=(segment.segment_id,),
        warnings=(),
    )
    bundle = TranscriptEvidenceBundle(
        schema="mentorflow.transcript-evidence.v1",
        parser_version="mentorflow.subtitle-timeline.v1",
        bundle_id="bundle-1",
        bundle_hash="3" * 64,
        source_reference="https://example.test/video",
        platform="bilibili",
        bvid="BV1test",
        source_identity="source-1",
        source_revision="revision-1",
        part_count=1,
        subtitle_language="zh",
        subtitle_kind="manual",
        fetched_at=NOW.isoformat(),
        raw_payload_hash="2" * 64,
        normalized_timeline_hash="4" * 64,
        parts=(part,),
        segments=(segment,),
        warnings=(),
    )
    return bundle.to_dict()


def test_knowledge_handler_uses_real_pipeline_boundary(monkeypatch) -> None:
    calls: list[object] = []
    session = object()
    provider = object()
    monkeypatch.setattr(
        product_handlers,
        "require_provider_session",
        lambda session_id, owner_user_id: calls.append((session_id, owner_user_id))
        or session,
    )
    monkeypatch.setattr(
        product_handlers,
        "provider_for_session",
        lambda value: calls.append(value) or provider,
    )

    class _Result:
        status = PipelineStatus.COMPLETE

        def to_dict(self):
            return {"status": "COMPLETE", "service": "KnowledgePipelineV2"}

    class _Pipeline:
        def __init__(self, inventory, verification, *, normalization_provider):
            calls.append((inventory, verification, normalization_provider))

        async def run(self, bundle, glossary, *, source_metadata, progress_sink):
            await progress_sink(SimpleNamespace(to_dict=lambda: {"status": "VERIFYING"}))
            calls.append((bundle, glossary, source_metadata))
            return _Result()

    monkeypatch.setattr(product_handlers, "KnowledgePipelineV2", _Pipeline)
    checkpoints: list[tuple[str, object]] = []
    engine = _postgres_engine()
    try:
        result = product_handlers.knowledge_pipeline(
            engine=engine,
            job=_job(
                WorkflowKind.KNOWLEDGE_PIPELINE,
                {
                    "provider_session_id": "provider-session-1",
                    "bundle": _knowledge_bundle(),
                    "glossary": {"version": "v1", "terms": []},
                    "source_metadata": {
                        "source_identity": "source-1",
                        "source_revision": "revision-1",
                        "source_reference": "https://example.test/video",
                        "platform": "bilibili",
                        "title": "Course",
                        "language": "zh",
                    },
                },
            ),
            checkpoint=lambda phase, value: checkpoints.append((phase, value)),
            ensure_not_cancelled=lambda: None,
        )
    finally:
        engine.dispose()
    assert result == {"status": "COMPLETE", "service": "KnowledgePipelineV2"}
    assert calls[0] == ("provider-session-1", "subject-1")
    assert calls[1] is session
    assert calls[2] == (provider, provider, provider)
    assert checkpoints == [("knowledge_pipeline", {"status": "VERIFYING"})]


def test_plan_handler_uses_plan_creation_service(monkeypatch) -> None:
    calls: list[object] = []

    class _Request:
        request_id = "worker-request"

    class _RequestContract:
        @classmethod
        def model_validate(cls, value):
            calls.append(value)
            return _Request()

    class _Preview:
        status = SimpleNamespace(value="READY_FOR_CONFIRMATION")

        def model_dump(self, *, mode):
            assert mode == "json"
            return {"preview_id": "preview-1"}

    class _Service:
        def create(self, request):
            calls.append(request)
            return SimpleNamespace(preview=_Preview())

    monkeypatch.setattr(product_handlers, "PlanCreationRequest", _RequestContract)
    monkeypatch.setattr(product_handlers, "PlanCreationService", _Service)
    checkpoints: list[tuple[str, object]] = []
    engine = _postgres_engine()
    try:
        result = product_handlers.plan_creation(
            engine=engine,
            job=_job(WorkflowKind.PLAN_CREATION, {"request": {"plan_id": "plan-1"}}),
            checkpoint=lambda phase, value: checkpoints.append((phase, value)),
            ensure_not_cancelled=lambda: None,
        )
    finally:
        engine.dispose()
    assert result == {
        "status": "READY_FOR_CONFIRMATION",
        "preview": {"preview_id": "preview-1"},
    }
    assert len(calls) == 2
    assert checkpoints == [
        ("plan_creation", {"status": "STARTED", "request_id": "worker-request"})
    ]


def test_product_handlers_reject_cross_tenant_payload_before_service_call(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        product_handlers,
        "PlanCreationService",
        lambda: pytest.fail("cross-tenant payload reached the product service"),
    )
    job = _job(WorkflowKind.PLAN_CREATION, {"request": {}})
    job.payload["workspace_id"] = "foreign-workspace"
    engine = _postgres_engine()
    try:
        with pytest.raises(WorkflowFailure) as failure:
            product_handlers.plan_creation(
                engine=engine,
                job=job,
                checkpoint=lambda *args: None,
                ensure_not_cancelled=lambda: None,
            )
    finally:
        engine.dispose()
    assert failure.value.error_code == "WORKFLOW_TENANT_BINDING_INVALID"


def test_human_revalidation_uses_current_postgres_membership_and_exact_tenant() -> None:
    membership = MembershipGrant(
        membership_id="membership-1",
        issuer=ISSUER,
        subject="subject-1",
        organization_id="org-1",
        workspace_id="ws-1",
        roles=(BusinessRole.LEARNER,),
        status=MembershipStatus.ACTIVE,
    )
    directory = _Directory(membership)
    revalidator = ProductionTenantContextRevalidator(
        directory,
        issuer=ISSUER,
        clock=lambda: NOW,
    )
    allowed = revalidator.revalidate(
        actor=_actor(),
        tenant=_tenant(),
        workflow_kind=WorkflowKind.KNOWLEDGE_PIPELINE,
    )
    assert allowed.decision is AccessDecisionKind.ALLOW
    assert allowed.reason_code == "MEMBERSHIP_ACTIVE"
    assert directory.calls == [
        {
            "issuer": ISSUER,
            "subject": "subject-1",
            "organization_id": "org-1",
            "workspace_id": "ws-1",
        }
    ]

    denied = ProductionTenantContextRevalidator(
        _Directory(membership.model_copy(update={"status": MembershipStatus.REVOKED})),
        issuer=ISSUER,
        clock=lambda: NOW,
    ).revalidate(
        actor=_actor(),
        tenant=_tenant(),
        workflow_kind=WorkflowKind.KNOWLEDGE_PIPELINE,
    )
    assert denied.decision is AccessDecisionKind.DENY


def test_service_revalidation_requires_current_job_execute_grant() -> None:
    grant = ServiceIdentityGrant(
        service_id="service-1",
        issuer=ISSUER,
        subject="oidc-service-subject",
        client_id="mentorflow-worker",
        organization_id="org-1",
        workspace_id="ws-1",
        permissions=(ServicePermission.JOB_EXECUTE,),
        status=MembershipStatus.ACTIVE,
    )
    actor = _actor(
        kind=ActorKind.SERVICE,
        subject_id="service-1",
        permissions=(ServicePermission.JOB_EXECUTE.value,),
    )
    revalidator = ProductionTenantContextRevalidator(
        _Directory(None),
        issuer=ISSUER,
        service_grants=(grant,),
        clock=lambda: NOW,
    )
    allowed = revalidator.revalidate(
        actor=actor,
        tenant=_tenant(),
        workflow_kind=WorkflowKind.DOCUMENT_INGESTION,
    )
    assert allowed.decision is AccessDecisionKind.ALLOW
    assert allowed.reason_code == "SERVICE_JOB_EXECUTE_ACTIVE"

    denied = revalidator.revalidate(
        actor=actor.model_copy(update={"service_permissions": ()}),
        tenant=_tenant(),
        workflow_kind=WorkflowKind.DOCUMENT_INGESTION,
    )
    assert denied.decision is AccessDecisionKind.DENY

    with pytest.raises(WorkerConfigurationError, match="unique service ids"):
        ProductionTenantContextRevalidator(
            _Directory(None),
            issuer=ISSUER,
            service_grants=(grant, grant),
        )


def test_worker_health_loads_preview_factory_without_account_deletion_handler(
    monkeypatch,
    capsys,
) -> None:
    source = _source()
    source.update(
        {
            "MENTORFLOW_DATABASE_URL": (
                "postgresql+psycopg://mentorflow_app:password@database/mentorflow"
            ),
            "MENTORFLOW_WORKER_READINESS_PATH": "/run/mentorflow/readiness.json",
        }
    )
    engine = _postgres_engine()
    events: list[str] = []

    class _App:
        pass

    with monkeypatch.context() as state:
        for name, value in source.items():
            state.setenv(name, value)
        state.delenv(
            handler_environment_name(WorkflowKind.ACCOUNT_DELETION), raising=False
        )
        state.setattr(worker_entry.orm, "configure_enterprise_orm", lambda candidate: engine)
        state.setattr(
            worker_entry,
            "verify_worker_database",
            lambda candidate: events.append("database"),
        )
        state.setattr(
            worker_entry,
            "build_procrastinate_app",
            lambda config, executor: (_App(), object()),
        )
        state.setattr(worker_entry, "_check_connection", lambda app: events.append("queue"))
        state.setattr(
            worker_entry,
            "read_readiness",
            lambda path, maximum_age_seconds: events.append("readiness")
            or {"status": "READY"},
        )
        try:
            assert worker_entry._health(source) == 0
            assert json.loads(capsys.readouterr().out)["status"] == "READY"
            assert events == ["readiness", "database"]
        finally:
            engine.dispose()


def test_factory_validation_errors_never_expose_database_credentials() -> None:
    source = _source()
    source[handler_environment_name(WorkflowKind.PLAN_CREATION)] = (
        "missing_product_module:password_secret_callback"
    )
    engine = _postgres_engine()
    try:
        with pytest.raises(WorkerConfigurationError) as failure:
            build(engine=engine, source=source)
    finally:
        engine.dispose()
    projection = str(failure.value).casefold()
    assert "postgresql" not in projection
    assert "password" not in projection
    assert "credential" not in projection
    assert "secret" not in projection


def test_release_builder_accepts_only_production_factory_and_supported_handler_keys(
    tmp_path: Path,
) -> None:
    example = (
        REPOSITORY_ROOT
        / "deploy"
        / "enterprise"
        / "staging"
        / "rp05"
        / "compose.env.example"
    )
    values = release_builder._env(example)
    assert values["MENTORFLOW_WORKER_RUNTIME_FACTORY"] == (
        "app.enterprise.jobs_v1.production:build"
    )
    assert {
        key: values[key] for key in release_builder.PRODUCTION_HANDLERS
    } == release_builder.PRODUCTION_HANDLERS
    assert not any("EXPORT" in key or "ACCOUNT_DELETION" in key for key in values)
    assert "placeholder" not in example.read_text(encoding="utf-8").casefold()

    invalid = tmp_path / "invalid.env"
    invalid.write_text(
        example.read_text(encoding="utf-8")
        + "MENTORFLOW_WORKER_HANDLER_ACCOUNT_DELETION=example:fake\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported environment key"):
        release_builder._env(invalid)


def test_task_name_is_registered_on_procrastinate_app(tmp_path: Path) -> None:
    config = WorkerRuntimeConfig(
        database_url=sa.make_url(
            "postgresql+psycopg://mentorflow_app:password@database/mentorflow"
        ),
        runtime_factory=PRODUCTION_FACTORY_DOTTED_PATH,
        readiness_path=tmp_path / "worker-readiness.json",
    )
    app, defer_task = build_procrastinate_app(
        config, SimpleNamespace(execute=lambda **kwargs: None)
    )
    assert TASK_NAME == "mentorflow.enterprise.execute_workflow"
    assert app.tasks[TASK_NAME].name == defer_task.name == TASK_NAME
    assert type(app.connector).__name__ == "PsycopgConnector"
    assert type(defer_task.blueprint.connector).__name__ == "SyncPsycopgConnector"


def test_handler_environment_contract_is_exact_preview_subset() -> None:
    assert len(required_handler_environment_names()) == 3
    assert len(set(required_handler_environment_names())) == 3
    assert {
        name.removeprefix(production.HANDLER_ENV_PREFIX)
        for name in required_handler_environment_names()
    } == {kind.value for kind in PREVIEW_SUPPORTED_WORKFLOW_KINDS}
    assert not {
        handler_environment_name(kind) for kind in PREVIEW_UNSUPPORTED_WORKFLOW_KINDS
    } & set(required_handler_environment_names())
