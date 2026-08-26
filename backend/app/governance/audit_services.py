from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import uuid4

from app.enterprise.contracts_v1 import (
    AccessDecisionKind,
    AuditEnvelope,
    AuditMetadataEntry,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.governance.audit import AuditRecord, SQLiteAuditLog
from app.governance.authorization import AuthorizationService, BreakGlassDenied
from app.governance.models import (
    Action,
    BreakGlassGrant,
    GovernanceActorAdapter,
    PolicyRequest,
    ResourceType,
    Role,
)


def _required(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("governance audit timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _metadata(envelope: AuditEnvelope) -> dict[str, str | int | float | bool | None]:
    return {entry.key: entry.value for entry in envelope.metadata_redacted}


@dataclass(frozen=True, slots=True)
class AuditQuery:
    organization_id: str | None = None
    workspace_id: str | None = None
    actor_id: str | None = None
    action: str | None = None
    resource_type: str | None = None
    outcome: str | None = None
    occurred_after: datetime | None = None
    occurred_before: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "organization_id",
            "workspace_id",
            "actor_id",
            "action",
            "resource_type",
            "outcome",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required(name, value))
        for name in ("occurred_after", "occurred_before"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _utc(value))
        if (
            self.occurred_after is not None
            and self.occurred_before is not None
            and self.occurred_after > self.occurred_before
        ):
            raise ValueError("occurred_after must not be later than occurred_before")


@dataclass(frozen=True, slots=True)
class AuditEntryView:
    sequence: int
    event_id: str
    actor_id: str
    organization_id: str | None
    workspace_id: str | None
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    reason_code: str
    occurred_at: datetime
    trace_id: str

    @classmethod
    def from_record(cls, record: AuditRecord) -> "AuditEntryView":
        envelope = record.envelope
        metadata = _metadata(envelope)
        organization_id = metadata.get("organization_id")
        workspace_id = metadata.get("workspace_id")
        return cls(
            sequence=record.sequence,
            event_id=envelope.event_id,
            actor_id=envelope.actor_context.subject_id,
            organization_id=(
                str(organization_id)
                if organization_id is not None
                else envelope.actor_context.organization_id
            ),
            workspace_id=(
                str(workspace_id)
                if workspace_id is not None
                else envelope.actor_context.workspace_id
            ),
            action=envelope.action,
            resource_type=envelope.resource_type,
            resource_id=envelope.resource_id,
            outcome=envelope.outcome,
            reason_code=envelope.reason_code,
            occurred_at=envelope.occurred_at,
            trace_id=envelope.trace_id,
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "event_id": self.event_id,
            "actor_id": self.actor_id,
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "occurred_at": self.occurred_at.isoformat(),
            "trace_id": self.trace_id,
        }


@dataclass(frozen=True, slots=True)
class AuditQueryResult:
    entries: tuple[AuditEntryView, ...]
    authorization_audit_sequence: int


class AuditAccessDenied(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class AuditQueryService:
    """Authorized, tenant-fenced query over redacted audit projections."""

    def __init__(self, authorization: AuthorizationService, audit_log: SQLiteAuditLog) -> None:
        self._authorization = authorization
        self._audit_log = audit_log

    @staticmethod
    def _scope(query: AuditQuery, tenant: TenantContext) -> ResourceScope:
        organization_id = _required(
            "organization_id",
            query.organization_id or tenant.organization_id or "",
        )
        workspace_id = query.workspace_id or tenant.workspace_id
        if workspace_id is not None:
            return ResourceScope(
                scope_type=ResourceScopeType.WORKSPACE,
                organization_id=organization_id,
                workspace_id=workspace_id,
                classification=ResourceClassification.RESTRICTED,
            )
        return ResourceScope(
            scope_type=ResourceScopeType.ORGANIZATION,
            organization_id=organization_id,
            classification=ResourceClassification.RESTRICTED,
        )

    @staticmethod
    def _matches(entry: AuditEntryView, query: AuditQuery, scope: ResourceScope) -> bool:
        if entry.organization_id != scope.organization_id:
            return False
        if scope.workspace_id is not None and entry.workspace_id != scope.workspace_id:
            return False
        if query.actor_id is not None and entry.actor_id != query.actor_id:
            return False
        if query.action is not None and entry.action != query.action:
            return False
        if query.resource_type is not None and entry.resource_type != query.resource_type:
            return False
        if query.outcome is not None and entry.outcome != query.outcome:
            return False
        if query.occurred_after is not None and entry.occurred_at < query.occurred_after:
            return False
        if query.occurred_before is not None and entry.occurred_at > query.occurred_before:
            return False
        return True

    def _run(
        self,
        query: AuditQuery,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
        action: Action,
    ) -> AuditQueryResult:
        scope = self._scope(query, tenant)
        decision = self._authorization.authorize(
            PolicyRequest(
                subject=subject,
                tenant=tenant,
                action=action,
                resource=ResourceType.AUDIT,
                resource_id=f"audit-query:{uuid4().hex}",
                resource_scope=scope,
                trace_id=trace_id,
            )
        )
        if not decision.allowed:
            raise AuditAccessDenied(decision.reason_code)
        entries = tuple(
            entry
            for record in self._audit_log.records()
            if record.sequence < decision.audit_sequence
            for entry in (AuditEntryView.from_record(record),)
            if self._matches(entry, query, scope)
        )
        return AuditQueryResult(entries, decision.audit_sequence)

    def query(
        self,
        query: AuditQuery,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> AuditQueryResult:
        return self._run(
            query,
            subject=subject,
            tenant=tenant,
            trace_id=trace_id,
            action=Action.READ,
        )

    def query_for_export(
        self,
        query: AuditQuery,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
    ) -> AuditQueryResult:
        return self._run(
            query,
            subject=subject,
            tenant=tenant,
            trace_id=trace_id,
            action=Action.EXPORT,
        )


@dataclass(frozen=True, slots=True)
class AuditExportRequest:
    query: AuditQuery = field(default_factory=AuditQuery)
    format: str = "JSONL"

    def __post_init__(self) -> None:
        if self.format != "JSONL":
            raise ValueError("audit export currently supports JSONL only")


@dataclass(frozen=True, slots=True)
class AuditExportReceipt:
    export_id: str
    record_count: int
    sha256: str
    created_at: datetime
    json_lines: tuple[str, ...]
    authorization_audit_sequence: int


class AuditExportService:
    def __init__(self, queries: AuditQueryService) -> None:
        self._queries = queries

    def export(
        self,
        request: AuditExportRequest,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
        now: datetime | None = None,
    ) -> AuditExportReceipt:
        result = self._queries.query_for_export(
            request.query,
            subject=subject,
            tenant=tenant,
            trace_id=trace_id,
        )
        lines = tuple(
            json.dumps(
                entry.to_public_dict(),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            for entry in result.entries
        )
        payload = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        return AuditExportReceipt(
            export_id="audit_export_" + uuid4().hex,
            record_count=len(lines),
            sha256=hashlib.sha256(payload).hexdigest(),
            created_at=_utc(now or datetime.now(timezone.utc)),
            json_lines=lines,
            authorization_audit_sequence=result.authorization_audit_sequence,
        )


class BreakGlassStatus(str, Enum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class BreakGlassAccessRequest:
    reason: str
    actions: frozenset[Action]
    resources: frozenset[ResourceType]
    resource_ids: frozenset[str]
    resource_scope: ResourceScope
    duration: timedelta = timedelta(minutes=10)

    def __post_init__(self) -> None:
        reason = _required("reason", self.reason)
        if len(reason) < 8:
            raise ValueError("break-glass reason must contain at least 8 characters")
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "actions", frozenset(self.actions))
        object.__setattr__(self, "resources", frozenset(self.resources))
        object.__setattr__(
            self,
            "resource_ids",
            frozenset(_required("resource_id", value) for value in self.resource_ids),
        )
        if not self.actions or not self.resources or not self.resource_ids:
            raise ValueError("break-glass request must be resource and action scoped")
        if self.duration <= timedelta(0) or self.duration > timedelta(minutes=30):
            raise ValueError("break-glass duration must be short and at most 30 minutes")


@dataclass(frozen=True, slots=True)
class BreakGlassWorkflowRecord:
    request_id: str
    requester_id: str
    organization_id: str
    workspace_id: str | None
    request: BreakGlassAccessRequest
    requested_at: datetime
    approval_expires_at: datetime
    status: BreakGlassStatus = BreakGlassStatus.REQUESTED
    approved_by: str | None = None
    approved_at: datetime | None = None
    grant: BreakGlassGrant | None = None


class BreakGlassWorkflowDenied(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BreakGlassWorkflowService:
    """Two-person, short-lived, scope-bound emergency access workflow."""

    def __init__(self, authorization: AuthorizationService, audit_log: SQLiteAuditLog) -> None:
        self._authorization = authorization
        self._audit_log = audit_log
        self._records: dict[str, BreakGlassWorkflowRecord] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _resource_scope(record: BreakGlassWorkflowRecord) -> ResourceScope:
        return record.request.resource_scope

    def _authorize_admin(
        self,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        resource_id: str,
        resource_scope: ResourceScope,
        trace_id: str,
    ) -> None:
        decision = self._authorization.authorize(
            PolicyRequest(
                subject=subject,
                tenant=tenant,
                action=Action.MANAGE,
                resource=ResourceType.ORGANIZATION,
                resource_id=resource_id,
                resource_scope=resource_scope,
                trace_id=trace_id,
            )
        )
        if not decision.allowed or subject.effective_role not in {Role.OWNER, Role.ADMIN}:
            raise BreakGlassWorkflowDenied(decision.reason_code)

    def _audit(
        self,
        *,
        subject: GovernanceActorAdapter,
        action: str,
        record: BreakGlassWorkflowRecord,
        outcome: AccessDecisionKind,
        reason_code: str,
        trace_id: str,
        occurred_at: datetime,
    ) -> None:
        self._audit_log.append(
            AuditEnvelope(
                event_id="audit_" + uuid4().hex,
                actor_context=subject.actor,
                action=action,
                resource_type="break_glass",
                resource_id=record.request_id,
                outcome=outcome.value,
                reason_code=reason_code,
                occurred_at=occurred_at,
                request_id=subject.actor.request_id,
                trace_id=trace_id,
                metadata_redacted=(
                    AuditMetadataEntry(key="organization_id", value=record.organization_id),
                    AuditMetadataEntry(key="workspace_id", value=record.workspace_id),
                    AuditMetadataEntry(key="status", value=record.status.value),
                ),
            )
        )

    def request_access(
        self,
        request: BreakGlassAccessRequest,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
        now: datetime | None = None,
    ) -> BreakGlassWorkflowRecord:
        occurred_at = _utc(now or datetime.now(timezone.utc))
        if tenant.mode is not TenantMode.ENTERPRISE_SERVER:
            raise BreakGlassWorkflowDenied("BREAK_GLASS_ENTERPRISE_ONLY")
        self._authorize_admin(
            subject=subject,
            tenant=tenant,
            resource_id="break-glass-request",
            resource_scope=request.resource_scope,
            trace_id=trace_id,
        )
        record = BreakGlassWorkflowRecord(
            request_id="bg_request_" + uuid4().hex,
            requester_id=subject.actor_id,
            organization_id=_required("organization_id", tenant.organization_id or ""),
            workspace_id=tenant.workspace_id,
            request=request,
            requested_at=occurred_at,
            approval_expires_at=occurred_at + request.duration,
        )
        with self._lock:
            self._records[record.request_id] = record
        self._audit(
            subject=subject,
            action="break_glass.request",
            record=record,
            outcome=AccessDecisionKind.ALLOW,
            reason_code="BREAK_GLASS_REQUESTED",
            trace_id=trace_id,
            occurred_at=occurred_at,
        )
        return record

    def get(self, request_id: str) -> BreakGlassWorkflowRecord:
        with self._lock:
            try:
                return self._records[request_id]
            except KeyError as exc:
                raise BreakGlassWorkflowDenied("BREAK_GLASS_REQUEST_NOT_FOUND") from exc

    def approve(
        self,
        request_id: str,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
        now: datetime | None = None,
    ) -> BreakGlassWorkflowRecord:
        occurred_at = _utc(now or datetime.now(timezone.utc))
        record = self.get(request_id)
        self._authorize_admin(
            subject=subject,
            tenant=tenant,
            resource_id=request_id,
            resource_scope=self._resource_scope(record),
            trace_id=trace_id,
        )
        if subject.actor_id == record.requester_id:
            self._audit(
                subject=subject,
                action="break_glass.approve",
                record=record,
                outcome=AccessDecisionKind.DENY,
                reason_code="BREAK_GLASS_SELF_APPROVAL_DENIED",
                trace_id=trace_id,
                occurred_at=occurred_at,
            )
            raise BreakGlassWorkflowDenied("BREAK_GLASS_SELF_APPROVAL_DENIED")
        if record.status is not BreakGlassStatus.REQUESTED:
            raise BreakGlassWorkflowDenied("BREAK_GLASS_REQUEST_NOT_PENDING")
        if occurred_at >= record.approval_expires_at:
            raise BreakGlassWorkflowDenied("BREAK_GLASS_APPROVAL_WINDOW_EXPIRED")
        updated = replace(
            record,
            status=BreakGlassStatus.APPROVED,
            approved_by=subject.actor_id,
            approved_at=occurred_at,
        )
        with self._lock:
            self._records[request_id] = updated
        self._audit(
            subject=subject,
            action="break_glass.approve",
            record=updated,
            outcome=AccessDecisionKind.ALLOW,
            reason_code="BREAK_GLASS_APPROVED",
            trace_id=trace_id,
            occurred_at=occurred_at,
        )
        return updated

    def activate(
        self,
        request_id: str,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
        now: datetime | None = None,
    ) -> BreakGlassWorkflowRecord:
        occurred_at = _utc(now or datetime.now(timezone.utc))
        record = self.get(request_id)
        if subject.actor_id != record.requester_id:
            self._audit(
                subject=subject,
                action="break_glass.activate",
                record=record,
                outcome=AccessDecisionKind.DENY,
                reason_code="BREAK_GLASS_REQUESTER_REQUIRED",
                trace_id=trace_id,
                occurred_at=occurred_at,
            )
            raise BreakGlassWorkflowDenied("BREAK_GLASS_REQUESTER_REQUIRED")
        if record.status is not BreakGlassStatus.APPROVED or record.approved_by is None:
            raise BreakGlassWorkflowDenied("BREAK_GLASS_APPROVAL_REQUIRED")
        if occurred_at >= record.approval_expires_at:
            return self.expire(
                request_id,
                subject=subject,
                tenant=tenant,
                trace_id=trace_id,
                now=occurred_at,
            )
        try:
            grant = self._authorization.issue_break_glass(
                subject=subject,
                tenant=tenant,
                reason=record.request.reason,
                approved_by=record.approved_by,
                actions=record.request.actions,
                resources=record.request.resources,
                resource_ids=record.request.resource_ids,
                resource_scope=record.request.resource_scope,
                trace_id=trace_id,
                duration=record.request.duration,
                now=occurred_at,
            )
        except BreakGlassDenied as exc:
            raise BreakGlassWorkflowDenied(str(exc)) from exc
        updated = replace(record, status=BreakGlassStatus.ACTIVE, grant=grant)
        with self._lock:
            self._records[request_id] = updated
        self._audit(
            subject=subject,
            action="break_glass.activate",
            record=updated,
            outcome=AccessDecisionKind.ALLOW,
            reason_code="BREAK_GLASS_ACTIVE",
            trace_id=trace_id,
            occurred_at=occurred_at,
        )
        return updated

    def expire(
        self,
        request_id: str,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
        now: datetime | None = None,
    ) -> BreakGlassWorkflowRecord:
        occurred_at = _utc(now or datetime.now(timezone.utc))
        record = self.get(request_id)
        self._authorize_admin(
            subject=subject,
            tenant=tenant,
            resource_id=request_id,
            resource_scope=self._resource_scope(record),
            trace_id=trace_id,
        )
        if record.grant is not None and occurred_at < record.grant.expires_at:
            raise BreakGlassWorkflowDenied("BREAK_GLASS_STILL_ACTIVE")
        if record.grant is None and occurred_at < record.approval_expires_at:
            raise BreakGlassWorkflowDenied("BREAK_GLASS_APPROVAL_STILL_VALID")
        updated = replace(record, status=BreakGlassStatus.EXPIRED)
        with self._lock:
            self._records[request_id] = updated
        if record.grant is not None:
            self._authorization.revoke_break_glass(record.grant.grant_id)
        self._audit(
            subject=subject,
            action="break_glass.expire",
            record=updated,
            outcome=AccessDecisionKind.ALLOW,
            reason_code="BREAK_GLASS_EXPIRED",
            trace_id=trace_id,
            occurred_at=occurred_at,
        )
        return updated

    def revoke(
        self,
        request_id: str,
        *,
        subject: GovernanceActorAdapter,
        tenant: TenantContext,
        trace_id: str,
        now: datetime | None = None,
    ) -> BreakGlassWorkflowRecord:
        occurred_at = _utc(now or datetime.now(timezone.utc))
        record = self.get(request_id)
        self._authorize_admin(
            subject=subject,
            tenant=tenant,
            resource_id=request_id,
            resource_scope=self._resource_scope(record),
            trace_id=trace_id,
        )
        if record.status not in {BreakGlassStatus.APPROVED, BreakGlassStatus.ACTIVE}:
            raise BreakGlassWorkflowDenied("BREAK_GLASS_NOT_REVOCABLE")
        if record.grant is not None:
            self._authorization.revoke_break_glass(record.grant.grant_id)
        updated = replace(record, status=BreakGlassStatus.REVOKED)
        with self._lock:
            self._records[request_id] = updated
        self._audit(
            subject=subject,
            action="break_glass.revoke",
            record=updated,
            outcome=AccessDecisionKind.ALLOW,
            reason_code="BREAK_GLASS_REVOKED",
            trace_id=trace_id,
            occurred_at=occurred_at,
        )
        return updated


__all__ = [
    "AuditAccessDenied",
    "AuditEntryView",
    "AuditExportReceipt",
    "AuditExportRequest",
    "AuditExportService",
    "AuditQuery",
    "AuditQueryResult",
    "AuditQueryService",
    "BreakGlassAccessRequest",
    "BreakGlassStatus",
    "BreakGlassWorkflowDenied",
    "BreakGlassWorkflowRecord",
    "BreakGlassWorkflowService",
]
