"""Canonical Enterprise teaching-session persistence."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text

from app.enterprise.contracts_v1 import (
    ActorContext,
    ResourceClassification,
    ResourceScopeType,
    TenantContext,
)
from app.enterprise.jobs_v1.learning_runs import LearningRunError, LearningRunService
from app.provider_runtime import provider_for_session, require_provider_session
from app.provider_service import ProviderError
from app.provider_session_repository import ProviderSessionRepository

from .context import PostgresTenantTransactionManager, TenantTransactionContext
from .models import (
    EnterprisePersistenceRequestError,
    TenantResourceDraft,
    TenantResourceKind,
)
from .repository import PostgresTenantResourceRepository


def _error(code: str, message: str, status: int) -> EnterprisePersistenceRequestError:
    return EnterprisePersistenceRequestError(code, message, status=status)


class EnterpriseTeachingService:
    def __init__(
        self,
        engine=None,
        *,
        transaction_manager=None,
        resource_repository: PostgresTenantResourceRepository | None = None,
        learning_runs: LearningRunService | None = None,
        provider_sessions: ProviderSessionRepository | None = None,
        provider_turn: Callable[[dict[str, Any]], Awaitable[str]] | None = None,
    ) -> None:
        if transaction_manager is None:
            if engine is None:
                raise ValueError("engine or transaction_manager is required")
            transaction_manager = PostgresTenantTransactionManager(engine)
        self.transactions = transaction_manager
        self.resources = resource_repository or PostgresTenantResourceRepository()
        self.learning_runs = learning_runs
        self.provider_sessions = provider_sessions
        if self.provider_sessions is None and engine is not None:
            self.provider_sessions = ProviderSessionRepository(engine)
        self.provider_turn = provider_turn

    @staticmethod
    def _context(
        actor: ActorContext,
        tenant: TenantContext,
        workspace_id: str | None = None,
    ) -> TenantTransactionContext:
        context = TenantTransactionContext(tenant=tenant, actor=actor)
        if workspace_id is not None and workspace_id != tenant.workspace_id:
            raise _error(
                "ENTERPRISE_WORKSPACE_NOT_FOUND",
                "Workspace was not found in the selected tenant context",
                404,
            )
        return context

    @staticmethod
    def _provider(transaction, actor: ActorContext, provider_session_id: str):
        if not provider_session_id:
            raise _error(
                "PROVIDER_CONFIGURATION_REQUIRED",
                "Configure and verify an Enterprise Provider session before teaching",
                409,
            )
        row = transaction.connection.execute(
            text(
                """
                SELECT provider_session_id, organization_id, workspace_id,
                       provider, model
                FROM enterprise_provider_sessions
                WHERE provider_session_id = :provider_session_id
                  AND subject_id = :subject_id
                  AND organization_id = :organization_id
                  AND workspace_id = :workspace_id
                  AND verified_at IS NOT NULL
                  AND revoked_at IS NULL
                  AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                """
            ),
            {
                "provider_session_id": provider_session_id,
                "subject_id": actor.subject_id,
                "organization_id": transaction.tenant.organization_id,
                "workspace_id": transaction.tenant.workspace_id,
            },
        ).mappings().one_or_none()
        if row is None or str(row["provider"]).casefold().replace("-", "_") == "fake":
            raise _error(
                "PROVIDER_CONFIGURATION_REQUIRED",
                "Configure and verify a real Enterprise Provider session before teaching",
                409,
            )
        return row

    async def _provider_text(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        provider_session_id: str,
        prompt: dict[str, Any],
    ) -> str:
        if self.provider_turn is not None:
            content = await self.provider_turn(prompt)
        else:
            if self.provider_sessions is None:
                raise _error(
                    "PROVIDER_CONFIGURATION_REQUIRED",
                    "Enterprise Provider persistence is not configured",
                    409,
                )
            try:
                session = require_provider_session(
                    provider_session_id,
                    actor=actor,
                    tenant=tenant,
                    repository=self.provider_sessions,
                )
                if session.provider.casefold().replace("-", "_") == "fake":
                    raise _error(
                        "PROVIDER_CONFIGURATION_REQUIRED",
                        "Enterprise teaching requires a verified real Provider",
                        409,
                    )
                content = await provider_for_session(session).text(
                    [
                        {
                            "role": "system",
                            "content": (
                                "You are the MentorFlow teacher. Produce one concise, "
                                "source-grounded teaching turn. Do not claim learner "
                                "mastery or invent evidence."
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                prompt,
                                ensure_ascii=False,
                                sort_keys=True,
                                default=str,
                            ),
                        },
                    ],
                    temperature=session.temperature,
                )
            except EnterprisePersistenceRequestError:
                raise
            except ProviderError as exc:
                configuration_errors = {
                    "CREDENTIAL_NOT_FOUND",
                    "PROVIDER_CONFIGURATION_REQUIRED",
                    "PROVIDER_SESSION_EXPIRED",
                    "PROVIDER_SESSION_REQUIRED",
                    "PROVIDER_SESSION_REVOKED",
                    "PROVIDER_SESSION_UNVERIFIED",
                }
                if exc.code in configuration_errors:
                    raise _error(
                        "PROVIDER_CONFIGURATION_REQUIRED",
                        "Configure and verify a real Enterprise Provider session before teaching",
                        409,
                    ) from exc
                raise _error(exc.code, exc.message, exc.status) from exc
        resolved = str(content or "").strip()
        if not resolved:
            raise _error(
                "PROVIDER_EMPTY_RESPONSE",
                "Provider returned an empty teaching turn",
                502,
            )
        return resolved

    def _resolve_source(
        self,
        transaction,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        body: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        plan_id = str(body.get("plan_id") or "").strip()
        learning_run_id = str(body.get("learning_run_id") or "").strip()
        if plan_id:
            plan = self.resources.get(
                transaction, TenantResourceKind.PLAN, plan_id
            )
            if (
                plan is None
                or plan.organization_id != tenant.organization_id
                or plan.workspace_id != tenant.workspace_id
            ):
                raise _error(
                    "ENTERPRISE_PLAN_NOT_FOUND",
                    "Teaching plan was not found in the selected tenant context",
                    404,
                )
            return "plan", plan.resource_id, dict(plan.payload)
        if learning_run_id:
            if self.learning_runs is None:
                raise _error(
                    "ENTERPRISE_LEARNING_RUN_NOT_READY",
                    "Completed learning-run state is unavailable",
                    409,
                )
            try:
                status = self.learning_runs.status(
                    learning_run_id,
                    actor=actor,
                    tenant=tenant,
                )
                if status.get("status") != "SUCCEEDED":
                    raise _error(
                        "ENTERPRISE_LEARNING_RUN_NOT_READY",
                        "Teaching requires a completed learning run",
                        409,
                    )
                result = self.learning_runs.result(
                    learning_run_id,
                    actor=actor,
                    tenant=tenant,
                )
            except EnterprisePersistenceRequestError:
                raise
            except LearningRunError as exc:
                raise _error(exc.code, exc.message, exc.status) from exc
            return "learning_run", learning_run_id, dict(result.get("plan") or {})
        plans = self.resources.list_records(
            transaction, TenantResourceKind.PLAN, limit=1
        )
        plans = tuple(
            plan
            for plan in plans
            if plan.organization_id == tenant.organization_id
            and plan.workspace_id == tenant.workspace_id
        )
        if not plans:
            raise _error(
                "ENTERPRISE_PLAN_REQUIRED",
                "Teaching requires a tenant-owned plan or completed learning run",
                409,
            )
        return "plan", plans[0].resource_id, dict(plans[0].payload)

    def start(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        workspace_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        context = self._context(actor, tenant, workspace_id)
        provider_session_id = str(body.get("provider_session_id") or "").strip()
        with self.transactions.transaction(context) as transaction:
            workspace_exists = transaction.connection.execute(
                text("SELECT 1 FROM workspaces WHERE id = :workspace_id"),
                {"workspace_id": workspace_id},
            ).scalar_one_or_none()
            if workspace_exists is None:
                raise _error(
                    "ENTERPRISE_WORKSPACE_NOT_FOUND",
                    "Workspace was not found in the selected tenant context",
                    404,
                )
            provider = self._provider(transaction, actor, provider_session_id)
            source_type, source_id, source_payload = self._resolve_source(
                transaction,
                actor=actor,
                tenant=tenant,
                body=body,
            )
            session_id = f"teaching_{uuid.uuid4().hex}"
            state = {
                "current_teaching_step": "ready",
                "next_action": "deliver_provider_grounded_turn",
                "awaiting_learner": False,
                "provider_session_id": provider_session_id,
                "provider": str(provider["provider"]),
                "provider_model": str(provider["model"]),
                "source_type": source_type,
                "source_id": source_id,
                "source_snapshot": source_payload,
            }
            record = self.resources.upsert(
                transaction,
                TenantResourceDraft(
                    resource_id=session_id,
                    kind=TenantResourceKind.TEACHER_SESSION,
                    scope_type=ResourceScopeType.WORKSPACE,
                    owner_subject_id=actor.subject_id,
                    classification=ResourceClassification.CONFIDENTIAL,
                    title="Enterprise teaching session",
                    status="READY",
                    payload={
                        "record_type": "teaching_session",
                        "workspace_id": workspace_id,
                        "plan_id": source_id if source_type == "plan" else None,
                        "learning_run_id": (
                            source_id if source_type == "learning_run" else None
                        ),
                        "state": state,
                    },
                ),
            )
        return {
            "session_id": record.resource_id,
            "workspace_id": workspace_id,
            "plan_id": record.payload.get("plan_id"),
            "learning_run_id": record.payload.get("learning_run_id"),
            "status": record.status,
            "state": dict(record.payload["state"]),
        }

    async def teach_next(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        session_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        context = self._context(actor, tenant)
        with self.transactions.transaction(context) as transaction:
            record = self.resources.get(
                transaction, TenantResourceKind.TEACHER_SESSION, session_id
            )
            if (
                record is None
                or record.organization_id != tenant.organization_id
                or record.workspace_id != tenant.workspace_id
                or record.payload.get("record_type") != "teaching_session"
            ):
                raise _error(
                    "ENTERPRISE_TEACHING_SESSION_NOT_FOUND",
                    "Teaching session was not found in the selected tenant context",
                    404,
                )
            payload = dict(record.payload)
            state = dict(payload.get("state") or {})
            provider = self._provider(
                transaction,
                actor,
                str(state.get("provider_session_id") or ""),
            )
            existing_turn = state.get("teacher_turn")
            if (
                state.get("current_teaching_step") == "awaiting_learner"
                and isinstance(existing_turn, dict)
                and existing_turn.get("status") == "PROVIDER_COMPLETED"
            ):
                return {
                    "session_id": session_id,
                    "workspace_id": str(record.workspace_id),
                    "plan_id": payload.get("plan_id"),
                    "learning_run_id": payload.get("learning_run_id"),
                    "status": "AWAITING_LEARNER",
                    "state": state,
                }
            prompt = {
                "source_type": state.get("source_type"),
                "source_id": state.get("source_id"),
                "source_snapshot": state.get("source_snapshot") or {},
                "current_teaching_step": state.get("current_teaching_step"),
                "learner_message": str(
                    body.get("message") or body.get("learner_message") or ""
                )[:4000],
            }
            provider_session_id = str(state["provider_session_id"])
            provider_name = str(provider["provider"])
            provider_model = str(provider["model"])

        teacher_text = await self._provider_text(
            actor=actor,
            tenant=tenant,
            provider_session_id=provider_session_id,
            prompt=prompt,
        )

        with self.transactions.transaction(context) as transaction:
            record = self.resources.get(
                transaction, TenantResourceKind.TEACHER_SESSION, session_id
            )
            if (
                record is None
                or record.organization_id != tenant.organization_id
                or record.workspace_id != tenant.workspace_id
                or record.payload.get("record_type") != "teaching_session"
            ):
                raise _error(
                    "ENTERPRISE_TEACHING_SESSION_NOT_FOUND",
                    "Teaching session was not found in the selected tenant context",
                    404,
                )
            payload = dict(record.payload)
            state = dict(payload.get("state") or {})
            self._provider(transaction, actor, provider_session_id)
            existing_turn = state.get("teacher_turn")
            if not (
                state.get("current_teaching_step") == "awaiting_learner"
                and isinstance(existing_turn, dict)
                and existing_turn.get("status") == "PROVIDER_COMPLETED"
            ):
                state.update(
                    {
                        "current_teaching_step": "awaiting_learner",
                        "next_action": "interpret_learner_feedback",
                        "awaiting_learner": True,
                        "teacher_turn": {
                            "status": "PROVIDER_COMPLETED",
                            "content": teacher_text,
                            "provider_session_id": provider_session_id,
                            "provider": provider_name,
                            "model": provider_model,
                        },
                    }
                )
                payload["state"] = state
                if not self.resources.update_payload(
                    transaction,
                    TenantResourceKind.TEACHER_SESSION,
                    session_id,
                    payload=payload,
                    status="AWAITING_LEARNER",
                ):
                    raise RuntimeError("Enterprise teaching turn was not persisted")
            return {
                "session_id": session_id,
                "workspace_id": str(record.workspace_id),
                "plan_id": payload.get("plan_id"),
                "learning_run_id": payload.get("learning_run_id"),
                "status": "AWAITING_LEARNER",
                "state": state,
            }


__all__ = ["EnterpriseTeachingService"]
