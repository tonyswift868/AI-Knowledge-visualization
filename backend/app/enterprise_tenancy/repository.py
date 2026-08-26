"""Dual-mode repository selection and PostgreSQL tenant resource adapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from sqlalchemy import text

from app.enterprise.contracts_v1 import (
    ResourceClassification,
    ResourceScopeType,
    TenantMode,
)

from .context import TenantConnection
from .models import (
    EnterprisePersistenceUnavailableError,
    RESOURCE_TABLE_BY_KIND,
    TenantResourceDraft,
    TenantResourceKind,
    TenantResourceRecord,
)


class TenantResourceRepository(Protocol):
    def upsert(
        self,
        transaction: TenantConnection,
        draft: TenantResourceDraft,
    ) -> TenantResourceRecord: ...

    def get(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        resource_id: str,
    ) -> TenantResourceRecord | None: ...

    def list_records(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> tuple[TenantResourceRecord, ...]: ...

    def search(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        query: str,
        *,
        limit: int = 200,
    ) -> tuple[TenantResourceRecord, ...]: ...

    def update_payload(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        resource_id: str,
        *,
        payload: Mapping[str, Any],
        status: str,
    ) -> bool: ...

    def delete(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        resource_id: str,
    ) -> bool: ...

    def export_records(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        *,
        limit: int = 10_000,
    ) -> tuple[TenantResourceRecord, ...]: ...

    def read_job(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        job_id: str,
    ) -> TenantResourceRecord | None: ...

    def read_cache(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        cache_key: str,
    ) -> TenantResourceRecord | None: ...


@dataclass(frozen=True, slots=True)
class DualPersistenceSelector:
    """Keep the caller-provided local adapter while adding PostgreSQL."""

    personal_local: TenantResourceRepository
    enterprise_server: TenantResourceRepository | None = None

    def select(self, mode: TenantMode | str) -> TenantResourceRepository:
        resolved = TenantMode(mode)
        if resolved is TenantMode.PERSONAL_LOCAL:
            return self.personal_local
        if self.enterprise_server is None:
            raise EnterprisePersistenceUnavailableError(
                "ENTERPRISE_SERVER requires a configured PostgreSQL repository"
            )
        return self.enterprise_server


def _scope_id(kind: TenantResourceKind, resource_id: str) -> str:
    digest = hashlib.sha256(
        f"{kind.value}\0{resource_id}".encode("utf-8")
    ).hexdigest()
    return f"scope:{digest}"


def _validated_limit(limit: int, *, maximum: int) -> int:
    if limit < 1 or limit > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return limit


class PostgresTenantResourceRepository:
    """RLS-backed adapter; queries intentionally rely on database isolation."""

    @staticmethod
    def _table(kind: TenantResourceKind) -> str:
        return RESOURCE_TABLE_BY_KIND[TenantResourceKind(kind)]

    @staticmethod
    def _record(kind: TenantResourceKind, row: Any) -> TenantResourceRecord:
        values = row._mapping
        payload = values["payload_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return TenantResourceRecord(
            resource_id=str(values["resource_id"]),
            kind=kind,
            organization_id=str(values["organization_id"]),
            workspace_id=str(values["workspace_id"]),
            scope_type=ResourceScopeType(values["scope_type"]),
            owner_subject_id=str(values["owner_subject_id"]),
            classification=ResourceClassification(values["classification"]),
            title=str(values["title"]),
            status=str(values["status"]),
            payload=dict(payload),
            search_text=str(values["search_text"]),
            job_id=str(values["job_id"]),
            cache_key=str(values["cache_key"]),
            created_at=values["created_at"],
            updated_at=values["updated_at"],
        )

    @staticmethod
    def _base_select(table: str) -> str:
        return (
            "SELECT resource_id, organization_id, workspace_id, scope_type, "
            "owner_subject_id, classification, title, status, payload_json, "
            f"search_text, job_id, cache_key, created_at, updated_at FROM {table}"
        )

    def upsert(
        self,
        transaction: TenantConnection,
        draft: TenantResourceDraft,
    ) -> TenantResourceRecord:
        kind = TenantResourceKind(draft.kind)
        table = self._table(kind)
        context = transaction.context
        scope_id = _scope_id(kind, draft.resource_id)
        parameters = {
            "scope_id": scope_id,
            "resource_id": draft.resource_id,
            "resource_kind": kind.value,
            "organization_id": context.organization_id,
            "workspace_id": context.workspace_id,
            "scope_type": draft.scope_type.value,
            "owner_subject_id": draft.owner_subject_id,
            "classification": draft.classification.value,
            "title": draft.title,
            "status": draft.status,
            "payload_json": json.dumps(
                dict(draft.payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "search_text": draft.search_text,
            "job_id": draft.job_id,
            "cache_key": draft.cache_key,
        }
        transaction.connection.execute(
            text(
                """
                INSERT INTO resource_scopes(
                  id, resource_kind, resource_id, scope_type,
                  owner_subject_id, organization_id, workspace_id,
                  classification
                ) VALUES (
                  :scope_id, :resource_kind, :resource_id, :scope_type,
                  :owner_subject_id, :organization_id, :workspace_id,
                  :classification
                )
                ON CONFLICT (resource_kind, resource_id) DO UPDATE SET
                  scope_type = EXCLUDED.scope_type,
                  owner_subject_id = EXCLUDED.owner_subject_id,
                  organization_id = EXCLUDED.organization_id,
                  workspace_id = EXCLUDED.workspace_id,
                  classification = EXCLUDED.classification,
                  updated_at = CURRENT_TIMESTAMP
                """
            ),
            parameters,
        )
        row = transaction.connection.execute(
            text(
                f"""
                INSERT INTO {table}(
                  resource_id, resource_scope_id, organization_id,
                  workspace_id, scope_type, owner_subject_id,
                  classification, title, status, payload_json,
                  search_text, job_id, cache_key
                ) VALUES (
                  :resource_id, :scope_id, :organization_id,
                  :workspace_id, :scope_type, :owner_subject_id,
                  :classification, :title, :status,
                  CAST(:payload_json AS JSONB), :search_text,
                  :job_id, :cache_key
                )
                ON CONFLICT (resource_id) DO UPDATE SET
                  resource_scope_id = EXCLUDED.resource_scope_id,
                  organization_id = EXCLUDED.organization_id,
                  workspace_id = EXCLUDED.workspace_id,
                  scope_type = EXCLUDED.scope_type,
                  owner_subject_id = EXCLUDED.owner_subject_id,
                  classification = EXCLUDED.classification,
                  title = EXCLUDED.title,
                  status = EXCLUDED.status,
                  payload_json = EXCLUDED.payload_json,
                  search_text = EXCLUDED.search_text,
                  job_id = EXCLUDED.job_id,
                  cache_key = EXCLUDED.cache_key,
                  updated_at = CURRENT_TIMESTAMP
                RETURNING resource_id, organization_id, workspace_id,
                  scope_type, owner_subject_id, classification, title,
                  status, payload_json, search_text, job_id, cache_key,
                  created_at, updated_at
                """
            ),
            parameters,
        ).one()
        return self._record(kind, row)

    def get(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        resource_id: str,
    ) -> TenantResourceRecord | None:
        resolved = TenantResourceKind(kind)
        table = self._table(resolved)
        row = transaction.connection.execute(
            text(f"{self._base_select(table)} WHERE resource_id = :resource_id"),
            {"resource_id": resource_id},
        ).first()
        return None if row is None else self._record(resolved, row)

    def list_records(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> tuple[TenantResourceRecord, ...]:
        resolved = TenantResourceKind(kind)
        table = self._table(resolved)
        resolved_limit = _validated_limit(limit, maximum=10_000)
        status_sql = "" if status is None else " WHERE status = :status"
        rows = transaction.connection.execute(
            text(
                f"{self._base_select(table)}{status_sql} "
                "ORDER BY updated_at DESC, resource_id LIMIT :limit"
            ),
            {"status": status, "limit": resolved_limit},
        )
        return tuple(self._record(resolved, row) for row in rows)

    def search(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        query: str,
        *,
        limit: int = 200,
    ) -> tuple[TenantResourceRecord, ...]:
        normalized = query.strip().casefold()
        if not normalized:
            raise ValueError("query must be non-empty")
        resolved = TenantResourceKind(kind)
        table = self._table(resolved)
        rows = transaction.connection.execute(
            text(
                f"{self._base_select(table)} "
                "WHERE lower(search_text) LIKE :query "
                "ORDER BY updated_at DESC, resource_id LIMIT :limit"
            ),
            {
                "query": f"%{normalized}%",
                "limit": _validated_limit(limit, maximum=10_000),
            },
        )
        return tuple(self._record(resolved, row) for row in rows)

    def update_payload(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        resource_id: str,
        *,
        payload: Mapping[str, Any],
        status: str,
    ) -> bool:
        table = self._table(TenantResourceKind(kind))
        result = transaction.connection.execute(
            text(
                f"""
                UPDATE {table}
                SET payload_json = CAST(:payload_json AS JSONB),
                    status = :status,
                    updated_at = CURRENT_TIMESTAMP
                WHERE resource_id = :resource_id
                """
            ),
            {
                "resource_id": resource_id,
                "payload_json": json.dumps(
                    dict(payload),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "status": status,
            },
        )
        return result.rowcount == 1

    def delete(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        resource_id: str,
    ) -> bool:
        resolved = TenantResourceKind(kind)
        table = self._table(resolved)
        deleted = transaction.connection.execute(
            text(f"DELETE FROM {table} WHERE resource_id = :resource_id"),
            {"resource_id": resource_id},
        ).rowcount
        if deleted:
            transaction.connection.execute(
                text(
                    """
                    DELETE FROM resource_scopes
                    WHERE resource_kind = :resource_kind
                      AND resource_id = :resource_id
                    """
                ),
                {"resource_kind": resolved.value, "resource_id": resource_id},
            )
        return deleted == 1

    def export_records(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        *,
        limit: int = 10_000,
    ) -> tuple[TenantResourceRecord, ...]:
        return self.list_records(
            transaction,
            kind,
            limit=_validated_limit(limit, maximum=50_000),
        )

    def read_job(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        job_id: str,
    ) -> TenantResourceRecord | None:
        resolved = TenantResourceKind(kind)
        table = self._table(resolved)
        row = transaction.connection.execute(
            text(
                f"{self._base_select(table)} "
                "WHERE job_id = :job_id ORDER BY updated_at DESC LIMIT 1"
            ),
            {"job_id": job_id},
        ).first()
        return None if row is None else self._record(resolved, row)

    def read_cache(
        self,
        transaction: TenantConnection,
        kind: TenantResourceKind,
        cache_key: str,
    ) -> TenantResourceRecord | None:
        resolved = TenantResourceKind(kind)
        table = self._table(resolved)
        row = transaction.connection.execute(
            text(
                f"{self._base_select(table)} "
                "WHERE cache_key = :cache_key ORDER BY updated_at DESC LIMIT 1"
            ),
            {"cache_key": cache_key},
        ).first()
        return None if row is None else self._record(resolved, row)


__all__ = [
    "DualPersistenceSelector",
    "PostgresTenantResourceRepository",
    "TenantResourceRepository",
]
