"""Personal Local bridge and scope-enforcing PostgreSQL domain adapters."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping

from app.enterprise.contracts_v1 import (
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)

from .context import TenantDbSession, require_enterprise_workspace_context
from .models import (
    ACCOUNT_PROGRESS_SNAPSHOT_RECORD_TYPE,
    ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION,
    AccountProgressSnapshotDraft,
    AccountProgressSnapshotRecord,
    ProgressSnapshotVersionError,
    TenantContextRequiredError,
    TenantPersistenceError,
    TenantResourceDraft,
    TenantResourceKind,
    TenantResourceRecord,
)
from .ports import (
    PersonalLocalResourceStorePort,
    TenantDomainRecord,
    TenantDomainRepositoryPort,
    TenantDomainWrite,
)
from .repository import TenantResourceRepository


def _validated_limit(limit: int, maximum: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return limit


def _require_local_scope(tenant: TenantContext, scope: ResourceScope) -> None:
    if not isinstance(tenant, TenantContext) or not isinstance(scope, ResourceScope):
        raise TypeError("tenant and scope must use enterprise-context.v1 contracts")
    if tenant.mode is not TenantMode.PERSONAL_LOCAL:
        raise TenantContextRequiredError("Personal Local adapter requires PERSONAL_LOCAL")
    if scope.scope_type is not ResourceScopeType.PERSONAL:
        raise TenantContextRequiredError("Personal Local resources require PERSONAL scope")


def _require_enterprise_scope(
    transaction: TenantDbSession,
    tenant: TenantContext,
    scope: ResourceScope,
) -> None:
    if not isinstance(tenant, TenantContext) or not isinstance(scope, ResourceScope):
        raise TypeError("tenant and scope must use enterprise-context.v1 contracts")
    organization_id, workspace_id = require_enterprise_workspace_context(tenant)
    if transaction.tenant != tenant:
        raise TenantContextRequiredError("transaction tenant must match repository tenant")
    if scope.scope_type is ResourceScopeType.PERSONAL:
        if scope.owner_subject_id != transaction.actor.subject_id:
            raise TenantContextRequiredError("PERSONAL scope owner must match the actor")
    elif scope.scope_type is ResourceScopeType.ORGANIZATION:
        if scope.organization_id != organization_id:
            raise TenantContextRequiredError("organization scope must match the tenant")
    elif (
        scope.organization_id != organization_id
        or scope.workspace_id != workspace_id
    ):
        raise TenantContextRequiredError("workspace scope must match the tenant")


def _scope_from_record(record: TenantResourceRecord) -> ResourceScope:
    if record.scope_type is ResourceScopeType.PERSONAL:
        organization_id = None
        workspace_id = None
    elif record.scope_type is ResourceScopeType.ORGANIZATION:
        organization_id = record.organization_id
        workspace_id = None
    else:
        organization_id = record.organization_id
        workspace_id = record.workspace_id
    return ResourceScope(
        scope_type=record.scope_type,
        owner_subject_id=record.owner_subject_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        classification=record.classification,
    )


def _domain_record(record: TenantResourceRecord) -> TenantDomainRecord:
    return TenantDomainRecord(
        resource_id=record.resource_id,
        kind=record.kind,
        scope=_scope_from_record(record),
        title=record.title,
        status=record.status,
        payload=dict(record.payload),
        search_text=record.search_text,
        job_id=record.job_id,
        cache_key=record.cache_key,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _account_progress_resource_id(
    tenant: TenantContext,
    scope: ResourceScope,
    progress_id: str,
) -> str:
    if tenant.mode is not TenantMode.ENTERPRISE_SERVER:
        raise TenantContextRequiredError(
            "Account progress snapshots require ENTERPRISE_SERVER"
        )
    if tenant.organization_id is None or tenant.workspace_id is None:
        raise TenantContextRequiredError(
            "Account progress snapshots require organization and workspace"
        )
    if (
        scope.scope_type is not ResourceScopeType.PERSONAL
        or scope.owner_subject_id is None
    ):
        raise TenantContextRequiredError(
            "Account progress snapshots require an account-owned PERSONAL scope"
        )
    digest = hashlib.sha256(
        (
            f"{ACCOUNT_PROGRESS_SNAPSHOT_RECORD_TYPE}\0"
            f"{tenant.organization_id}\0{tenant.workspace_id}\0"
            f"{scope.owner_subject_id}\0{progress_id}"
        ).encode("utf-8")
    ).hexdigest()
    return f"progress_{digest[:48]}"


def _account_progress_record(
    record: TenantDomainRecord,
    *,
    tenant: TenantContext,
    scope: ResourceScope,
) -> AccountProgressSnapshotRecord:
    payload = dict(record.payload)
    if (
        record.kind is not TenantResourceKind.STUDENT_EVENT
        or payload.get("schema_version")
        != ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION
        or payload.get("record_type") != ACCOUNT_PROGRESS_SNAPSHOT_RECORD_TYPE
    ):
        raise TenantPersistenceError(
            "stored student event is not an Account Core progress snapshot"
        )
    try:
        validated = AccountProgressSnapshotDraft(
            progress_id=payload["progress_id"],
            version=payload["version"],
            snapshot=payload["snapshot"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TenantPersistenceError(
            "stored Account Core progress snapshot is invalid"
        ) from exc
    if (
        tenant.organization_id is None
        or tenant.workspace_id is None
        or scope.owner_subject_id is None
    ):
        raise TenantPersistenceError(
            "stored Account Core progress snapshot has no account ownership"
        )
    return AccountProgressSnapshotRecord(
        progress_id=validated.progress_id,
        version=validated.version,
        snapshot=validated.snapshot,
        organization_id=tenant.organization_id,
        workspace_id=tenant.workspace_id,
        owner_subject_id=scope.owner_subject_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class InMemoryPersonalLocalResourceStore:
    """Reference bridge for offline/local tests; existing durable stores stay canonical."""

    def __init__(self) -> None:
        self._records: dict[tuple[TenantResourceKind, str], TenantDomainRecord] = {}
        self._lock = RLock()

    def put(self, record: TenantDomainRecord) -> TenantDomainRecord:
        with self._lock:
            key = (record.kind, record.resource_id)
            current = self._records.get(key)
            now = datetime.now(timezone.utc)
            saved = replace(
                record,
                created_at=now if current is None else current.created_at,
                updated_at=now,
            )
            self._records[key] = saved
            return saved

    def get(
        self,
        kind: TenantResourceKind,
        resource_id: str,
    ) -> TenantDomainRecord | None:
        with self._lock:
            return self._records.get((TenantResourceKind(kind), resource_id))

    def list_records(
        self,
        kind: TenantResourceKind,
    ) -> tuple[TenantDomainRecord, ...]:
        resolved = TenantResourceKind(kind)
        with self._lock:
            return tuple(
                sorted(
                    (record for (item_kind, _), record in self._records.items() if item_kind is resolved),
                    key=lambda record: (str(record.updated_at), record.resource_id),
                    reverse=True,
                )
            )

    def delete(self, kind: TenantResourceKind, resource_id: str) -> bool:
        with self._lock:
            return self._records.pop((TenantResourceKind(kind), resource_id), None) is not None


class PersonalLocalDomainRepository:
    def __init__(
        self,
        store: PersonalLocalResourceStorePort,
        kind: TenantResourceKind,
    ) -> None:
        self.store = store
        self.kind = TenantResourceKind(kind)

    def upsert(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        value: TenantDomainWrite,
    ) -> TenantDomainRecord:
        _require_local_scope(tenant, scope)
        return self.store.put(
            TenantDomainRecord(
                resource_id=value.resource_id,
                kind=self.kind,
                scope=scope,
                title=value.title,
                status=value.status,
                payload=value.payload,
                search_text=value.search_text,
                job_id=value.job_id,
                cache_key=value.cache_key,
            )
        )

    def get(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        resource_id: str,
    ) -> TenantDomainRecord | None:
        _require_local_scope(tenant, scope)
        record = self.store.get(self.kind, resource_id)
        return record if record is not None and record.scope == scope else None

    def list_records(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        status: str | None = None,
        limit: int = 200,
    ) -> tuple[TenantDomainRecord, ...]:
        _require_local_scope(tenant, scope)
        selected = (
            record
            for record in self.store.list_records(self.kind)
            if record.scope == scope and (status is None or record.status == status)
        )
        return tuple(selected)[: _validated_limit(limit, 10_000)]

    def search(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        query: str,
        limit: int = 200,
    ) -> tuple[TenantDomainRecord, ...]:
        normalized = query.strip().casefold()
        if not normalized:
            raise ValueError("query must be non-empty")
        return tuple(
            record
            for record in self.list_records(
                tenant=tenant,
                scope=scope,
                limit=_validated_limit(limit, 10_000),
            )
            if normalized in f"{record.title} {record.search_text}".casefold()
        )

    def update_payload(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        resource_id: str,
        payload: Mapping[str, Any],
        status: str,
    ) -> bool:
        current = self.get(
            tenant=tenant,
            scope=scope,
            resource_id=resource_id,
        )
        if current is None:
            return False
        self.store.put(replace(current, payload=dict(payload), status=status))
        return True

    def delete(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        resource_id: str,
    ) -> bool:
        if self.get(tenant=tenant, scope=scope, resource_id=resource_id) is None:
            return False
        return self.store.delete(self.kind, resource_id)

    def export_records(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        limit: int = 10_000,
    ) -> tuple[TenantDomainRecord, ...]:
        return self.list_records(tenant=tenant, scope=scope, limit=_validated_limit(limit, 50_000))

    def read_job(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        job_id: str,
    ) -> TenantDomainRecord | None:
        return next(
            (record for record in self.export_records(tenant=tenant, scope=scope) if record.job_id == job_id),
            None,
        )

    def read_cache(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        cache_key: str,
    ) -> TenantDomainRecord | None:
        return next(
            (record for record in self.export_records(tenant=tenant, scope=scope) if record.cache_key == cache_key),
            None,
        )


class PostgresTenantDomainRepository:
    """Bind one canonical domain table to an already tenant-scoped transaction."""

    def __init__(
        self,
        repository: TenantResourceRepository,
        transaction: TenantDbSession,
        kind: TenantResourceKind,
    ) -> None:
        self.repository = repository
        self.transaction = transaction
        self.kind = TenantResourceKind(kind)

    def _require(self, tenant: TenantContext, scope: ResourceScope) -> None:
        _require_enterprise_scope(self.transaction, tenant, scope)

    @staticmethod
    def _matches(record: TenantDomainRecord, scope: ResourceScope) -> bool:
        actual = record.scope
        return (
            actual.scope_type is scope.scope_type
            and actual.organization_id == scope.organization_id
            and actual.workspace_id == scope.workspace_id
            and actual.classification is scope.classification
            and (
                scope.owner_subject_id is None
                or actual.owner_subject_id == scope.owner_subject_id
            )
        )

    def upsert(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        value: TenantDomainWrite,
    ) -> TenantDomainRecord:
        self._require(tenant, scope)
        owner = scope.owner_subject_id or self.transaction.actor.subject_id
        record = self.repository.upsert(
            self.transaction,
            TenantResourceDraft(
                resource_id=value.resource_id,
                kind=self.kind,
                scope_type=scope.scope_type,
                owner_subject_id=owner,
                classification=scope.classification,
                title=value.title,
                status=value.status,
                payload=value.payload,
                search_text=value.search_text,
                job_id=value.job_id,
                cache_key=value.cache_key,
            ),
        )
        resolved = _domain_record(record)
        if not self._matches(resolved, scope):
            raise TenantContextRequiredError("database returned a different resource scope")
        return resolved

    def get(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        resource_id: str,
    ) -> TenantDomainRecord | None:
        self._require(tenant, scope)
        record = self.repository.get(self.transaction, self.kind, resource_id)
        if record is None:
            return None
        resolved = _domain_record(record)
        return resolved if self._matches(resolved, scope) else None

    def list_records(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        status: str | None = None,
        limit: int = 200,
    ) -> tuple[TenantDomainRecord, ...]:
        self._require(tenant, scope)
        requested = _validated_limit(limit, 10_000)
        records = self.repository.list_records(
            self.transaction,
            self.kind,
            status=status,
            limit=10_000,
        )
        return tuple(
            record
            for record in map(_domain_record, records)
            if self._matches(record, scope)
        )[:requested]

    def search(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        query: str,
        limit: int = 200,
    ) -> tuple[TenantDomainRecord, ...]:
        self._require(tenant, scope)
        requested = _validated_limit(limit, 10_000)
        records = self.repository.search(
            self.transaction,
            self.kind,
            query,
            limit=10_000,
        )
        return tuple(
            record
            for record in map(_domain_record, records)
            if self._matches(record, scope)
        )[:requested]

    def update_payload(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        resource_id: str,
        payload: Mapping[str, Any],
        status: str,
    ) -> bool:
        if self.get(tenant=tenant, scope=scope, resource_id=resource_id) is None:
            return False
        return self.repository.update_payload(
            self.transaction,
            self.kind,
            resource_id,
            payload=payload,
            status=status,
        )

    def delete(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        resource_id: str,
    ) -> bool:
        if self.get(tenant=tenant, scope=scope, resource_id=resource_id) is None:
            return False
        return self.repository.delete(self.transaction, self.kind, resource_id)

    def export_records(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        limit: int = 10_000,
    ) -> tuple[TenantDomainRecord, ...]:
        self._require(tenant, scope)
        requested = _validated_limit(limit, 50_000)
        records = self.repository.export_records(
            self.transaction,
            self.kind,
            limit=50_000,
        )
        return tuple(
            record
            for record in map(_domain_record, records)
            if self._matches(record, scope)
        )[:requested]

    def read_job(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        job_id: str,
    ) -> TenantDomainRecord | None:
        self._require(tenant, scope)
        record = self.repository.read_job(self.transaction, self.kind, job_id)
        if record is None:
            return None
        resolved = _domain_record(record)
        return resolved if self._matches(resolved, scope) else None

    def read_cache(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        cache_key: str,
    ) -> TenantDomainRecord | None:
        self._require(tenant, scope)
        record = self.repository.read_cache(self.transaction, self.kind, cache_key)
        if record is None:
            return None
        resolved = _domain_record(record)
        return resolved if self._matches(resolved, scope) else None


class AccountProgressSnapshotRepository:
    """Typed Account Core projection over the existing student_events table."""

    def __init__(self, repository: TenantDomainRepositoryPort) -> None:
        if TenantResourceKind(repository.kind) is not TenantResourceKind.STUDENT_EVENT:
            raise ValueError(
                "Account progress snapshots must use the canonical STUDENT_EVENT repository"
            )
        self.repository = repository

    @staticmethod
    def _resource_id(
        tenant: TenantContext,
        scope: ResourceScope,
        progress_id: str,
    ) -> str:
        validated = AccountProgressSnapshotDraft(
            progress_id=progress_id,
            version=1,
            snapshot={},
        )
        return _account_progress_resource_id(
            tenant,
            scope,
            validated.progress_id,
        )

    def upsert(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        value: AccountProgressSnapshotDraft,
    ) -> AccountProgressSnapshotRecord:
        if not isinstance(value, AccountProgressSnapshotDraft):
            raise TypeError("value must be AccountProgressSnapshotDraft")
        resource_id = _account_progress_resource_id(
            tenant,
            scope,
            value.progress_id,
        )
        current_raw = self.repository.get(
            tenant=tenant,
            scope=scope,
            resource_id=resource_id,
        )
        if current_raw is not None:
            current = _account_progress_record(
                current_raw,
                tenant=tenant,
                scope=scope,
            )
            if value.version < current.version:
                raise ProgressSnapshotVersionError(
                    "progress snapshot version must not move backwards"
                )
            if value.version == current.version:
                if dict(value.snapshot) != dict(current.snapshot):
                    raise ProgressSnapshotVersionError(
                        "the same progress snapshot version has different metadata"
                    )
                return current
        saved = self.repository.upsert(
            tenant=tenant,
            scope=scope,
            value=TenantDomainWrite(
                resource_id=resource_id,
                title=f"Account progress: {value.progress_id}",
                status="CURRENT",
                payload={
                    "schema_version": ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION,
                    "record_type": ACCOUNT_PROGRESS_SNAPSHOT_RECORD_TYPE,
                    "progress_id": value.progress_id,
                    "version": value.version,
                    "snapshot": dict(value.snapshot),
                },
                search_text=value.progress_id,
            ),
        )
        result = _account_progress_record(saved, tenant=tenant, scope=scope)
        if result.version != value.version:
            raise TenantPersistenceError(
                "progress snapshot upsert did not preserve the requested version"
            )
        return result

    def get(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        progress_id: str,
    ) -> AccountProgressSnapshotRecord | None:
        resource_id = self._resource_id(tenant, scope, progress_id)
        record = self.repository.get(
            tenant=tenant,
            scope=scope,
            resource_id=resource_id,
        )
        return (
            None
            if record is None
            else _account_progress_record(record, tenant=tenant, scope=scope)
        )

    def list_snapshots(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        limit: int = 200,
    ) -> tuple[AccountProgressSnapshotRecord, ...]:
        requested = _validated_limit(limit, 10_000)
        records = self.repository.list_records(
            tenant=tenant,
            scope=scope,
            limit=10_000,
        )
        snapshots = (
            _account_progress_record(record, tenant=tenant, scope=scope)
            for record in records
            if record.payload.get("schema_version")
            == ACCOUNT_PROGRESS_SNAPSHOT_SCHEMA_VERSION
            and record.payload.get("record_type")
            == ACCOUNT_PROGRESS_SNAPSHOT_RECORD_TYPE
        )
        return tuple(snapshots)[:requested]

    def delete(
        self,
        *,
        tenant: TenantContext,
        scope: ResourceScope,
        progress_id: str,
    ) -> bool:
        return self.repository.delete(
            tenant=tenant,
            scope=scope,
            resource_id=self._resource_id(tenant, scope, progress_id),
        )


__all__ = [
    "AccountProgressSnapshotRepository",
    "InMemoryPersonalLocalResourceStore",
    "PersonalLocalDomainRepository",
    "PostgresTenantDomainRepository",
]
