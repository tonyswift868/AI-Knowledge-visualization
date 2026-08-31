"""Production dependency factory for the Enterprise durable worker.

Engineering Preview loads real product callbacks for the learning-chain
workflows.  Workflow kinds that are not implemented for Preview remain present
in the authoritative enum and worker registry, but execute through an explicit
non-retryable fail-closed handler.  They never require a fake callback and never
prevent the factory or worker from starting.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from sqlalchemy.engine import Engine

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
from app.enterprise.identity.directory import (
    PostgresIdentityDirectory,
    service_identity_grants_from_env,
)
from app.enterprise.identity.models import (
    IdentityDirectoryPort,
    MembershipStatus,
    ServiceIdentityGrant,
    ServicePermission,
)

from .models import FailureClass, JobRecord, WorkflowKind
from .retry import WorkflowFailure
from .runtime import DurableWorkerDependencies, WorkerConfigurationError


PRODUCTION_FACTORY_DOTTED_PATH = "app.enterprise.jobs_v1.production:build"
HANDLER_ENV_PREFIX = "MENTORFLOW_WORKER_HANDLER_"
PREVIEW_SUPPORTED_WORKFLOW_KINDS = (
    WorkflowKind.DOCUMENT_INGESTION,
    WorkflowKind.KNOWLEDGE_PIPELINE,
    WorkflowKind.PLAN_CREATION,
)
PREVIEW_UNSUPPORTED_WORKFLOW_KINDS = (
    WorkflowKind.EXPORT,
    WorkflowKind.ACCOUNT_DELETION,
)
_CALLBACK_ARGUMENTS = {
    "engine": object(),
    "job": object(),
    "checkpoint": object(),
    "ensure_not_cancelled": object(),
}


def handler_environment_name(kind: WorkflowKind) -> str:
    """Return the deployment variable derived from the authoritative enum."""

    return f"{HANDLER_ENV_PREFIX}{kind.value}"


def required_handler_environment_names() -> tuple[str, ...]:
    return tuple(
        handler_environment_name(kind) for kind in PREVIEW_SUPPORTED_WORKFLOW_KINDS
    )


def supported_workflow_kinds() -> tuple[WorkflowKind, ...]:
    return PREVIEW_SUPPORTED_WORKFLOW_KINDS


def unsupported_workflow_kinds() -> tuple[WorkflowKind, ...]:
    return PREVIEW_UNSUPPORTED_WORKFLOW_KINDS


def _required(source: Mapping[str, str], name: str) -> str:
    value = str(source.get(name) or "").strip()
    if not value:
        raise WorkerConfigurationError(f"{name} is required")
    return value


def _parse_dotted_path(value: str, *, field: str) -> tuple[str, str]:
    module_name, separator, attribute = value.partition(":")
    parts = module_name.split(".")
    if (
        not separator
        or not module_name
        or not attribute
        or ":" in attribute
        or not attribute.isidentifier()
        or any(not part.isidentifier() for part in parts)
    ):
        raise WorkerConfigurationError(f"{field} must be module:callable")
    normalized_parts = {part.casefold() for part in parts}
    if (
        "tests" in normalized_parts
        or "pytest" in normalized_parts
        or any(part.casefold().startswith("test_") for part in parts)
    ):
        raise WorkerConfigurationError(
            "production workflow handlers must not reference test modules"
        )
    return module_name, attribute


def _load_callback(value: str, *, field: str) -> Callable[..., object]:
    module_name, attribute = _parse_dotted_path(value, field=field)
    try:
        callback = getattr(importlib.import_module(module_name), attribute)
        if not callable(callback) or inspect.iscoroutinefunction(callback):
            raise TypeError("callback must be synchronous")
        inspect.signature(callback).bind(**_CALLBACK_ARGUMENTS)
    except WorkerConfigurationError:
        raise
    except Exception as exc:
        raise WorkerConfigurationError(
            "production workflow handler failed validation"
        ) from exc
    return callback


@dataclass(frozen=True, slots=True)
class ProductionWorkflowHandler:
    """Validated adapter from the durable runtime to a product workflow."""

    workflow_kind: WorkflowKind
    dotted_path: str
    callback: Callable[..., object]
    engine: Engine

    def execute(
        self,
        *,
        job: JobRecord,
        checkpoint: Callable[[str, dict[str, Any]], None],
        ensure_not_cancelled: Callable[[], None],
    ) -> dict[str, Any]:
        if job.workflow_kind is not self.workflow_kind:
            raise WorkflowFailure(
                FailureClass.NON_RETRYABLE,
                "WORKFLOW_KIND_MISMATCH",
                "Workflow handler kind does not match the durable job",
            )
        ensure_not_cancelled()
        result = self.callback(
            engine=self.engine,
            job=job,
            checkpoint=checkpoint,
            ensure_not_cancelled=ensure_not_cancelled,
        )
        if not isinstance(result, Mapping):
            raise WorkflowFailure(
                FailureClass.NON_RETRYABLE,
                "WORKFLOW_RESULT_INVALID",
                "Workflow handler returned an invalid result",
            )
        normalized = dict(result)
        if any(not isinstance(key, str) for key in normalized):
            raise WorkflowFailure(
                FailureClass.NON_RETRYABLE,
                "WORKFLOW_RESULT_INVALID",
                "Workflow handler returned an invalid result",
            )
        try:
            json.dumps(normalized, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise WorkflowFailure(
                FailureClass.NON_RETRYABLE,
                "WORKFLOW_RESULT_INVALID",
                "Workflow handler returned an invalid result",
            ) from exc
        return normalized


@dataclass(frozen=True, slots=True)
class UnsupportedWorkflowHandler:
    """Terminal Preview policy for a product workflow that does not exist yet."""

    workflow_kind: WorkflowKind

    def execute(
        self,
        *,
        job: JobRecord,
        checkpoint: Callable[[str, dict[str, Any]], None],
        ensure_not_cancelled: Callable[[], None],
    ) -> dict[str, Any]:
        del checkpoint, ensure_not_cancelled
        if job.workflow_kind is not self.workflow_kind:
            raise WorkflowFailure(
                FailureClass.NON_RETRYABLE,
                "WORKFLOW_KIND_MISMATCH",
                "Workflow handler kind does not match the durable job",
            )
        raise WorkflowFailure(
            FailureClass.NON_RETRYABLE,
            "WORKFLOW_UNSUPPORTED_IN_ENGINEERING_PREVIEW",
            "Workflow is not supported in Engineering Preview",
        )


class ProductionTenantContextRevalidator:
    """Fail-closed RP-03-compatible revalidation for a recovered job."""

    def __init__(
        self,
        directory: IdentityDirectoryPort,
        *,
        issuer: str,
        service_grants: tuple[ServiceIdentityGrant, ...] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._directory = directory
        self._issuer = issuer.rstrip("/")
        self._service_grants = {
            grant.service_id: grant for grant in service_grants
        }
        if len(self._service_grants) != len(service_grants):
            raise WorkerConfigurationError(
                "service identity grants must use unique service ids"
            )
        self._clock = clock

    @staticmethod
    def _resource(tenant: TenantContext) -> ResourceScope:
        if tenant.mode is TenantMode.PERSONAL_LOCAL:
            return ResourceScope(
                scope_type=ResourceScopeType.PERSONAL,
                classification=ResourceClassification.INTERNAL,
            )
        if tenant.workspace_id is not None:
            return ResourceScope(
                scope_type=ResourceScopeType.WORKSPACE,
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                classification=ResourceClassification.INTERNAL,
            )
        return ResourceScope(
            scope_type=ResourceScopeType.ORGANIZATION,
            organization_id=tenant.organization_id,
            classification=ResourceClassification.INTERNAL,
        )

    def _decision(
        self,
        actor: ActorContext,
        tenant: TenantContext,
        *,
        allowed: bool,
        reason: str,
    ) -> AccessDecision:
        return AccessDecision(
            decision=(
                AccessDecisionKind.ALLOW if allowed else AccessDecisionKind.DENY
            ),
            policy_id="enterprise_jobs.worker_revalidation.v1",
            reason_code=reason,
            actor=actor,
            resource=self._resource(tenant),
            action="enterprise_jobs.execute",
            evaluated_at=self._clock(),
        )

    def revalidate(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        workflow_kind: WorkflowKind,
    ) -> AccessDecision:
        del workflow_kind
        scope_matches = (
            tenant.mode is TenantMode.ENTERPRISE_SERVER
            and actor.organization_id == tenant.organization_id
            and actor.workspace_id == tenant.workspace_id
            and actor.issuer.rstrip("/") == self._issuer
        )
        if not scope_matches:
            return self._decision(
                actor,
                tenant,
                allowed=False,
                reason="WORKER_TENANT_CONTEXT_INVALID",
            )

        if actor.actor_kind is ActorKind.SERVICE:
            grant = self._service_grants.get(actor.subject_id)
            allowed = (
                grant is not None
                and grant.status is MembershipStatus.ACTIVE
                and grant.issuer.rstrip("/") == self._issuer
                and grant.organization_id == tenant.organization_id
                and grant.workspace_id == tenant.workspace_id
                and ServicePermission.JOB_EXECUTE in grant.permissions
                and ServicePermission.JOB_EXECUTE.value in actor.service_permissions
            )
            return self._decision(
                actor,
                tenant,
                allowed=allowed,
                reason=(
                    "SERVICE_JOB_EXECUTE_ACTIVE"
                    if allowed
                    else "SERVICE_JOB_EXECUTE_DENIED"
                ),
            )

        grant = self._directory.resolve_membership(
            issuer=actor.issuer,
            subject=actor.subject_id,
            organization_id=tenant.organization_id or "",
            workspace_id=tenant.workspace_id,
        )
        allowed = (
            grant is not None
            and grant.status is MembershipStatus.ACTIVE
            and grant.issuer.rstrip("/") == self._issuer
            and grant.subject == actor.subject_id
            and grant.organization_id == tenant.organization_id
            and grant.workspace_id == tenant.workspace_id
            and actor.membership_id == grant.membership_id
        )
        return self._decision(
            actor,
            tenant,
            allowed=allowed,
            reason="MEMBERSHIP_ACTIVE" if allowed else "MEMBERSHIP_NOT_ACTIVE",
        )


def _validate_engine(engine: Engine) -> None:
    if (
        engine.dialect.name != "postgresql"
        or engine.url.drivername != "postgresql+psycopg"
        or engine.url.username != "mentorflow_app"
    ):
        raise WorkerConfigurationError(
            "production worker factory requires the PostgreSQL application role"
        )


def build(
    *,
    engine: Engine,
    source: Mapping[str, str] | None = None,
) -> DurableWorkerDependencies:
    """Build the complete production dependency bundle from environment/config."""

    env = os.environ if source is None else source
    if str(env.get("MENTORFLOW_DEPLOYMENT_MODE") or "").strip() != TenantMode.ENTERPRISE_SERVER.value:
        raise WorkerConfigurationError(
            "production worker factory requires ENTERPRISE_SERVER mode"
        )
    _validate_engine(engine)

    expected_environment = set(required_handler_environment_names())
    unknown_environment = {
        name
        for name in env
        if name.startswith(HANDLER_ENV_PREFIX) and name not in expected_environment
    }
    if unknown_environment:
        raise WorkerConfigurationError(
            "production worker factory received an unknown workflow kind"
        )

    handlers: dict[
        WorkflowKind, ProductionWorkflowHandler | UnsupportedWorkflowHandler
    ] = {}
    for kind in PREVIEW_SUPPORTED_WORKFLOW_KINDS:
        field = handler_environment_name(kind)
        dotted_path = _required(env, field)
        handlers[kind] = ProductionWorkflowHandler(
            workflow_kind=kind,
            dotted_path=dotted_path,
            callback=_load_callback(dotted_path, field=field),
            engine=engine,
        )
    handlers.update(
        {
            kind: UnsupportedWorkflowHandler(workflow_kind=kind)
            for kind in PREVIEW_UNSUPPORTED_WORKFLOW_KINDS
        }
    )

    issuer = _required(env, "MENTORFLOW_KEYCLOAK_ISSUER")
    service_grants = service_identity_grants_from_env(env)
    directory = PostgresIdentityDirectory(
        engine,
        issuer=issuer,
        service_grants=service_grants,
        verify_application_role=False,
    )
    return DurableWorkerDependencies(
        revalidator=ProductionTenantContextRevalidator(
            directory,
            issuer=issuer,
            service_grants=service_grants,
        ),
        handlers=MappingProxyType(handlers),
    )


__all__ = [
    "HANDLER_ENV_PREFIX",
    "PREVIEW_SUPPORTED_WORKFLOW_KINDS",
    "PREVIEW_UNSUPPORTED_WORKFLOW_KINDS",
    "PRODUCTION_FACTORY_DOTTED_PATH",
    "ProductionTenantContextRevalidator",
    "ProductionWorkflowHandler",
    "UnsupportedWorkflowHandler",
    "build",
    "handler_environment_name",
    "required_handler_environment_names",
    "supported_workflow_kinds",
    "unsupported_workflow_kinds",
]
