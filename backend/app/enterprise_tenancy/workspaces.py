"""Canonical PostgreSQL workspace and goal persistence for Enterprise routes."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text

from app.enterprise.contracts_v1 import ActorContext, TenantContext
from app.schemas import GoalPatch, GoalProfile, WorkspaceIn

from .context import (
    PostgresTenantTransactionManager,
    TenantTransactionContext,
    require_matching_actor_context,
    target_workspace_transaction_context,
)
from .models import (
    EnterprisePersistenceRequestError,
    TenantResourceKind,
)
from .repository import PostgresTenantResourceRepository


_CREATE_ROLES = frozenset({"OWNER", "ORGANIZATION_OWNER", "ADMIN"})


def _error(code: str, message: str, status: int) -> EnterprisePersistenceRequestError:
    return EnterprisePersistenceRequestError(code, message, status=status)


def _workspace_id() -> str:
    return f"ws_{uuid.uuid4().hex}"


def _slug(title: str, workspace_id: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")[:120]
    return f"{stem or 'workspace'}-{workspace_id[-12:]}"


def _goal_id(organization_id: str, workspace_id: str) -> str:
    digest = hashlib.sha256(
        f"{organization_id}\0{workspace_id}".encode("utf-8")
    ).hexdigest()
    return f"goal_{digest[:32]}"


def _json(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        decoded = json.loads(value)
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return dict(value) if isinstance(value, Mapping) else {}


def _timestamp(value: object) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def _workspace_response(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": str(row["name"]),
        "description": str(row.get("description") or ""),
        "status": str(row["status"]),
        "character_id": str(row.get("character_id") or "warm"),
        "allow_external_knowledge": bool(row.get("allow_external_knowledge")),
        "persona_prompt": str(row.get("persona_prompt") or ""),
        "execution_mode": str(row.get("execution_mode") or "demo"),
        "provider_name": str(row.get("provider_name") or ""),
        "provider_model": str(row.get("provider_model") or ""),
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("updated_at")),
    }


def _goal_response(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": str(row["goal_id"]),
        "workspace_id": str(row["workspace_id"]),
        "payload": _json(row["payload_json"]),
        "confirmed": bool(row["confirmed"]),
        "created_at": _timestamp(row.get("created_at")),
        "updated_at": _timestamp(row.get("updated_at")),
    }


class EnterpriseWorkspaceService:
    """One transaction boundary for canonical Enterprise workspace state."""

    def __init__(
        self,
        engine=None,
        *,
        transaction_manager=None,
        resource_repository: PostgresTenantResourceRepository | None = None,
    ) -> None:
        if transaction_manager is None:
            if engine is None:
                raise ValueError("engine or transaction_manager is required")
            transaction_manager = PostgresTenantTransactionManager(engine)
        self.transactions = transaction_manager
        self.resources = resource_repository or PostgresTenantResourceRepository()

    @staticmethod
    def _current_context(
        actor: ActorContext,
        tenant: TenantContext,
        workspace_id: str,
    ) -> TenantTransactionContext:
        organization_id, current_workspace_id, _subject = require_matching_actor_context(
            tenant, actor
        )
        requested = str(workspace_id or "").strip()
        if requested != current_workspace_id:
            raise _error(
                "ENTERPRISE_WORKSPACE_NOT_FOUND",
                "Workspace was not found in the selected tenant context",
                404,
            )
        return TenantTransactionContext(tenant=tenant, actor=actor)

    @staticmethod
    def _workspace_row(transaction, workspace_id: str):
        return transaction.connection.execute(
            text(
                """
                SELECT workspace.id, workspace.organization_id, workspace.name,
                       workspace.status, workspace.created_at, workspace.updated_at,
                       profile.description, profile.character_id,
                       profile.allow_external_knowledge, profile.persona_prompt,
                       profile.execution_mode, profile.provider_name,
                       profile.provider_model
                FROM workspaces AS workspace
                JOIN workspace_profiles AS profile
                  ON profile.organization_id = workspace.organization_id
                 AND profile.workspace_id = workspace.id
                WHERE workspace.id = :workspace_id
                  AND workspace.organization_id = :organization_id
                """
            ),
            {
                "workspace_id": workspace_id,
                "organization_id": transaction.tenant.organization_id,
            },
        ).mappings().one_or_none()

    @staticmethod
    def _goal_row(transaction, workspace_id: str):
        return transaction.connection.execute(
            text(
                """
                SELECT goal_id, workspace_id, payload_json, confirmed,
                       created_at, updated_at
                FROM workspace_goals
                WHERE workspace_id = :workspace_id
                  AND organization_id = :organization_id
                """
            ),
            {
                "workspace_id": workspace_id,
                "organization_id": transaction.tenant.organization_id,
            },
        ).mappings().one_or_none()

    def create(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        body: WorkspaceIn,
    ) -> dict[str, Any]:
        organization_id = tenant.organization_id
        if organization_id is None or actor.organization_id != organization_id:
            raise _error(
                "ENTERPRISE_MEMBERSHIP_REQUIRED",
                "An active organization membership is required",
                403,
            )
        workspace_id = _workspace_id()
        target = target_workspace_transaction_context(tenant, actor, workspace_id)
        profile = body.model_dump(mode="json")
        with self.transactions.transaction(target) as transaction:
            membership = transaction.connection.execute(
                text(
                    """
                    SELECT role
                    FROM organization_memberships
                    WHERE organization_id = :organization_id
                      AND subject_id = :subject_id
                      AND status = 'active'
                    """
                ),
                {
                    "organization_id": organization_id,
                    "subject_id": actor.subject_id,
                },
            ).scalar_one_or_none()
            role = str(membership or "").upper()
            if role not in _CREATE_ROLES:
                raise _error(
                    "ENTERPRISE_WORKSPACE_CREATE_FORBIDDEN",
                    "Only an active organization owner or administrator can create a workspace",
                    403,
                )
            creator_role = "OWNER" if role in {"OWNER", "ORGANIZATION_OWNER"} else "ADMIN"
            transaction.connection.execute(
                text(
                    """
                    INSERT INTO workspaces(id, organization_id, slug, name, status)
                    VALUES (:id, :organization_id, :slug, :name, 'active')
                    """
                ),
                {
                    "id": workspace_id,
                    "organization_id": organization_id,
                    "slug": _slug(body.title, workspace_id),
                    "name": body.title,
                },
            )
            transaction.connection.execute(
                text(
                    """
                    INSERT INTO workspace_memberships(
                      organization_id, workspace_id, subject_id, status
                    ) VALUES (
                      :organization_id, :workspace_id, :subject_id, 'active'
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "workspace_id": workspace_id,
                    "subject_id": actor.subject_id,
                },
            )
            transaction.connection.execute(
                text(
                    """
                    INSERT INTO workspace_role_bindings(
                      organization_id, workspace_id, subject_id, role, status
                    ) VALUES (
                      :organization_id, :workspace_id, :subject_id, :role, 'active'
                    )
                    """
                ),
                {
                    "organization_id": organization_id,
                    "workspace_id": workspace_id,
                    "subject_id": actor.subject_id,
                    "role": creator_role,
                },
            )
            transaction.connection.execute(
                text(
                    """
                    INSERT INTO workspace_profiles(
                      workspace_id, organization_id, description, character_id,
                      allow_external_knowledge, persona_prompt, execution_mode,
                      provider_name, provider_model
                    ) VALUES (
                      :workspace_id, :organization_id, :description, :character_id,
                      :allow_external_knowledge, :persona_prompt, :execution_mode,
                      '', ''
                    )
                    """
                ),
                {
                    "workspace_id": workspace_id,
                    "organization_id": organization_id,
                    "description": profile.get("description") or "",
                    "character_id": profile.get("character_id") or "warm",
                    "allow_external_knowledge": bool(
                        profile.get("allow_external_knowledge")
                    ),
                    "persona_prompt": profile.get("persona_prompt") or "",
                    "execution_mode": profile.get("execution_mode") or "demo",
                },
            )
            row = self._workspace_row(transaction, workspace_id)
            if row is None:
                raise RuntimeError("created Enterprise workspace is unavailable")
            return _workspace_response(row)

    def get(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        workspace_id: str,
    ) -> dict[str, Any]:
        context = self._current_context(actor, tenant, workspace_id)
        with self.transactions.transaction(context) as transaction:
            row = self._workspace_row(transaction, workspace_id)
            if row is None:
                exists = transaction.connection.execute(
                    text("SELECT id FROM workspaces WHERE id = :workspace "
                         "AND organization_id = :organization"),
                    {"workspace": workspace_id, "organization": tenant.organization_id},
                ).scalar_one_or_none()
                if exists is not None:
                    raise _error(
                        "ENTERPRISE_WORKSPACE_INITIALIZATION_REQUIRED",
                        "Workspace profile requires account initialization",
                        409,
                    )
                raise _error(
                    "ENTERPRISE_WORKSPACE_NOT_FOUND",
                    "Workspace was not found in the selected tenant context",
                    404,
                )
            result = _workspace_response(row)
            documents = self.resources.list_records(
                transaction, TenantResourceKind.DOCUMENT, limit=200
            )
            plans = self.resources.list_records(
                transaction, TenantResourceKind.PLAN, limit=50
            )
            result.update(
                {
                    "sources": [
                        {
                            "id": item.resource_id,
                            "title": item.title,
                            "status": item.status,
                            **dict(item.payload),
                        }
                        for item in documents
                        if item.organization_id == tenant.organization_id
                        and item.workspace_id == tenant.workspace_id
                        and item.payload.get("record_type") == "document"
                    ],
                    "goal": _goal_response(self._goal_row(transaction, workspace_id))
                    or None,
                    "feasibility": None,
                    "plans": [
                        dict(item.payload)
                        for item in plans
                        if item.organization_id == tenant.organization_id
                        and item.workspace_id == tenant.workspace_id
                    ],
                }
            )
            return result

    def set_goal(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        workspace_id: str,
        body: GoalProfile,
    ) -> dict[str, Any]:
        context = self._current_context(actor, tenant, workspace_id)
        organization_id = str(tenant.organization_id)
        encoded = json.dumps(
            body.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self.transactions.transaction(context) as transaction:
            payload = (
                "CAST(:payload_json AS JSONB)"
                if transaction.connection.dialect.name == "postgresql"
                else ":payload_json"
            )
            transaction.connection.execute(
                text(
                    f"""
                    INSERT INTO workspace_goals(
                      goal_id, organization_id, workspace_id,
                      payload_json, confirmed
                    ) VALUES (
                      :goal_id, :organization_id, :workspace_id,
                      {payload}, false
                    )
                    ON CONFLICT (organization_id, workspace_id) DO UPDATE SET
                      payload_json = EXCLUDED.payload_json,
                      confirmed = false,
                      updated_at = CURRENT_TIMESTAMP
                    """
                ),
                {
                    "goal_id": _goal_id(organization_id, workspace_id),
                    "organization_id": organization_id,
                    "workspace_id": workspace_id,
                    "payload_json": encoded,
                },
            )
            row = self._goal_row(transaction, workspace_id)
            if row is None:
                raise RuntimeError("created Enterprise goal is unavailable")
            return _goal_response(row)

    def get_goal(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        workspace_id: str,
    ) -> dict[str, Any]:
        context = self._current_context(actor, tenant, workspace_id)
        with self.transactions.transaction(context) as transaction:
            return _goal_response(self._goal_row(transaction, workspace_id))

    def patch_goal(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        workspace_id: str,
        body: GoalPatch,
    ) -> dict[str, Any]:
        context = self._current_context(actor, tenant, workspace_id)
        organization_id = str(tenant.organization_id)
        with self.transactions.transaction(context) as transaction:
            lock = (
                " FOR UPDATE"
                if transaction.connection.dialect.name == "postgresql"
                else ""
            )
            current = transaction.connection.execute(
                text(
                    "SELECT goal_id, workspace_id, payload_json, confirmed, "
                    "created_at, updated_at FROM workspace_goals "
                    "WHERE workspace_id = :workspace_id "
                    "AND organization_id = :organization_id" + lock
                ),
                {
                    "workspace_id": workspace_id,
                    "organization_id": organization_id,
                },
            ).mappings().one_or_none()
            if current is None:
                raise _error(
                    "ENTERPRISE_GOAL_NOT_FOUND",
                    "Create a workspace goal before updating it",
                    404,
                )
            merged = GoalProfile.model_validate(
                {
                    **_json(current["payload_json"]),
                    **body.model_dump(mode="json", exclude_none=True),
                }
            )
            encoded = json.dumps(
                merged.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            payload = (
                "CAST(:payload_json AS JSONB)"
                if transaction.connection.dialect.name == "postgresql"
                else ":payload_json"
            )
            transaction.connection.execute(
                text(
                    f"""
                    UPDATE workspace_goals
                    SET payload_json = {payload},
                        confirmed = false,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE goal_id = :goal_id
                      AND organization_id = :organization_id
                      AND workspace_id = :workspace_id
                    """
                ),
                {
                    "goal_id": current["goal_id"],
                    "organization_id": organization_id,
                    "workspace_id": workspace_id,
                    "payload_json": encoded,
                },
            )
            updated = self._goal_row(transaction, workspace_id)
            if updated is None:
                raise RuntimeError("updated Enterprise goal is unavailable")
            return _goal_response(updated)


__all__ = ["EnterpriseWorkspaceService"]
