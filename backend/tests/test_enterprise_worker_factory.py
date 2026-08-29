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
from app.enterprise.jobs_v1 import production
from app.enterprise.jobs_v1.models import WorkflowKind
from app.enterprise.jobs_v1.production import (
    PRODUCTION_FACTORY_DOTTED_PATH,
    ProductionTenantContextRevalidator,
    build,
    handler_environment_name,
    required_handler_environment_names,
    supported_workflow_kinds,
)
from app.enterprise.jobs_v1.retry import WorkflowFailure
from app.enterprise.jobs_v1.runtime import (
    DurableWorkerDependencies,
    WorkerConfigurationError,
    load_worker_dependencies,
)


NOW = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)
ISSUER = "https://identity.example.test/realms/mentorflow"
CALLBACK_MODULE = "mentorflow_product_workflows"
CALLBACK_PATH = f"{CALLBACK_MODULE}:execute"


def _postgres_engine() -> sa.Engine:
    return sa.create_engine(
        "postgresql+psycopg://mentorflow_app:password@database/mentorflow",
        future=True,
    )


def _source() -> dict[str, str]:
    source = {
        "MENTORFLOW_DEPLOYMENT_MODE": TenantMode.ENTERPRISE_SERVER.value,
        "MENTORFLOW_KEYCLOAK_ISSUER": ISSUER,
        "MENTORFLOW_WORKER_RUNTIME_FACTORY": PRODUCTION_FACTORY_DOTTED_PATH,
    }
    source.update(
        {handler_environment_name(kind): CALLBACK_PATH for kind in WorkflowKind}
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


class _Directory:
    def __init__(self, membership: MembershipGrant | None) -> None:
        self.membership = membership
        self.calls: list[dict[str, object]] = []

    def resolve_membership(self, **kwargs):
        self.calls.append(kwargs)
        return self.membership

    def resolve_service_identity(self, **kwargs):
        raise AssertionError("service identities use the current configured grant projection")


def test_production_factory_imports_and_constructs_exact_authoritative_coverage(
    product_callback_module,
    monkeypatch,
) -> None:
    engine = _postgres_engine()
    source = _source()
    for name, value in source.items():
        monkeypatch.setenv(name, value)
    try:
        dependencies = load_worker_dependencies(
            PRODUCTION_FACTORY_DOTTED_PATH,
            engine=engine,
        )
    finally:
        engine.dispose()

    assert PRODUCTION_FACTORY_DOTTED_PATH == "app.enterprise.jobs_v1.production:build"
    assert tuple(WorkflowKind) == supported_workflow_kinds()
    assert set(dependencies.handlers) == set(WorkflowKind)
    assert all(type(kind) is WorkflowKind for kind in dependencies.handlers)
    assert dependencies.missing_workflow_kinds == ()
    assert dependencies.unsupported_workflow_kinds == ()
    assert dependencies.invalid_workflow_handlers == ()
    assert tuple(handler_environment_name(kind) for kind in WorkflowKind) == (
        required_handler_environment_names()
    )
    for kind, handler in dependencies.handlers.items():
        assert handler.workflow_kind is kind
        assert handler.dotted_path == CALLBACK_PATH


def test_production_factory_has_no_test_or_inmemory_runtime_dependency(
    product_callback_module,
) -> None:
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
            lambda source: source.pop(handler_environment_name(WorkflowKind.EXPORT)),
            "MENTORFLOW_WORKER_HANDLER_EXPORT is required",
        ),
        (
            lambda source: source.__setitem__(
                "MENTORFLOW_WORKER_HANDLER_UNKNOWN_KIND", CALLBACK_PATH
            ),
            "unknown workflow kind",
        ),
        (
            lambda source: source.__setitem__(
                handler_environment_name(WorkflowKind.EXPORT),
                "tests.worker_fixture:build",
            ),
            "must not reference test modules",
        ),
    ],
)
def test_production_factory_rejects_missing_unknown_or_test_handler(
    product_callback_module,
    mutate,
    message: str,
) -> None:
    source = _source()
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
            object(),
            complete,
        )
        with pytest.raises(WorkerConfigurationError, match="tenant revalidator"):
            load_worker_dependencies(f"{module.__name__}:build", engine=engine)

        module.build = lambda *, engine: DurableWorkerDependencies(  # type: ignore[attr-defined]
            revalidator,
            {kind: object() for kind in WorkflowKind},
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


def test_each_production_handler_dispatches_only_its_authoritative_kind(
    product_callback_module,
) -> None:
    engine = _postgres_engine()
    dependencies = build(engine=engine, source=_source())
    checkpoints: list[tuple[str, dict[str, str]]] = []
    cancellation_checks: list[bool] = []
    try:
        for kind, handler in dependencies.handlers.items():
            job = SimpleNamespace(workflow_kind=kind)
            result = handler.execute(
                job=job,
                checkpoint=lambda phase, value: checkpoints.append((phase, value)),
                ensure_not_cancelled=lambda: cancellation_checks.append(True),
            )
            assert result == {"status": "processed", "kind": kind.value}

        with pytest.raises(WorkflowFailure, match="does not match"):
            dependencies.handlers[WorkflowKind.EXPORT].execute(
                job=SimpleNamespace(workflow_kind=WorkflowKind.PLAN_CREATION),
                checkpoint=lambda phase, value: None,
                ensure_not_cancelled=lambda: None,
            )
    finally:
        engine.dispose()

    assert len(product_callback_module) == len(WorkflowKind)
    assert len(checkpoints) == len(WorkflowKind)
    assert len(cancellation_checks) == len(WorkflowKind) * 2


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
    actor = _actor()
    tenant = _tenant()
    allowed = revalidator.revalidate(
        actor=actor,
        tenant=tenant,
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
        actor=actor,
        tenant=tenant,
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

    stale_actor = actor.model_copy(update={"service_permissions": ()})
    denied = revalidator.revalidate(
        actor=stale_actor,
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


def test_worker_health_constructs_the_real_factory_before_reporting_ready(
    product_callback_module,
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
    for name, value in source.items():
        monkeypatch.setenv(name, value)
    engine = _postgres_engine()
    events: list[str] = []

    class _App:
        pass

    monkeypatch.setattr(
        worker_entry.orm,
        "configure_enterprise_orm",
        lambda candidate: engine,
    )
    monkeypatch.setattr(
        worker_entry,
        "verify_worker_database",
        lambda candidate: events.append("database"),
    )
    monkeypatch.setattr(
        worker_entry,
        "build_procrastinate_app",
        lambda config, executor: (_App(), object()),
    )
    monkeypatch.setattr(
        worker_entry,
        "_check_connection",
        lambda app: events.append("queue"),
    )
    monkeypatch.setattr(
        worker_entry,
        "read_readiness",
        lambda path, maximum_age_seconds: events.append("readiness") or {"status": "READY"},
    )

    assert worker_entry._health(source) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "READY"
    assert events == ["database", "queue", "readiness"]


def test_worker_health_rejects_a_live_process_with_incomplete_factory(
    product_callback_module,
    monkeypatch,
    capsys,
) -> None:
    source = _source()
    missing = handler_environment_name(WorkflowKind.ACCOUNT_DELETION)
    source.pop(missing)
    source["MENTORFLOW_DATABASE_URL"] = (
        "postgresql+psycopg://mentorflow_app:password@database/mentorflow"
    )
    for name, value in source.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv(missing, raising=False)
    engine = _postgres_engine()
    monkeypatch.setattr(
        worker_entry.orm,
        "configure_enterprise_orm",
        lambda candidate: engine,
    )
    monkeypatch.setattr(worker_entry, "verify_worker_database", lambda candidate: None)
    monkeypatch.setattr(
        worker_entry,
        "read_readiness",
        lambda *args, **kwargs: pytest.fail(
            "process evidence must not override an incomplete production factory"
        ),
    )

    with pytest.raises(WorkerConfigurationError, match=f"{missing} is required"):
        worker_entry._health(source)
    assert capsys.readouterr().out == ""


def test_factory_validation_errors_never_expose_database_credentials(
    product_callback_module,
) -> None:
    source = _source()
    source[handler_environment_name(WorkflowKind.EXPORT)] = (
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


def test_handler_environment_contract_is_derived_from_workflow_enum() -> None:
    assert len(required_handler_environment_names()) == len(WorkflowKind)
    assert len(set(required_handler_environment_names())) == len(WorkflowKind)
    assert {
        name.removeprefix(production.HANDLER_ENV_PREFIX)
        for name in required_handler_environment_names()
    } == {kind.value for kind in WorkflowKind}
