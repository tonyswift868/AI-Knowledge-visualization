"""Explicit preview/confirm seam for local-to-enterprise imports."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from app.enterprise.contracts_v1 import TenantContext

from .context import TenantConnection, require_enterprise_workspace_context
from .models import (
    ImportConfirmationError,
    LocalImportDescriptor,
    LocalImportPreview,
    LocalImportResult,
    TenantResourceDraft,
)
from .repository import TenantResourceRepository


class PersonalLocalImportSource(Protocol):
    def describe_importable(self) -> Sequence[LocalImportDescriptor]: ...

    def load_payload(self, local_id: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class _PendingImport:
    preview: LocalImportPreview
    source: PersonalLocalImportSource
    descriptors: tuple[LocalImportDescriptor, ...]
    target: TenantContext


def _descriptor_digest(descriptors: Sequence[LocalImportDescriptor]) -> str:
    payload = [
        {
            "local_id": item.local_id,
            "kind": item.kind.value,
            "title": item.title,
            "scope_type": item.scope_type.value,
            "classification": item.classification.value,
            "status": item.status,
            "search_text": item.search_text,
            "job_id": item.job_id,
            "cache_key": item.cache_key,
        }
        for item in descriptors
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _enterprise_resource_id(descriptor: LocalImportDescriptor) -> str:
    digest = hashlib.sha256(
        f"{descriptor.kind.value}\0{descriptor.local_id}".encode("utf-8")
    ).hexdigest()
    return f"import:{descriptor.kind.value.lower()}:{digest[:40]}"


class LocalToEnterpriseImportService:
    """Never reads payloads or writes enterprise data during preview."""

    def __init__(self, repository: TenantResourceRepository) -> None:
        self._repository = repository
        self._pending: dict[str, _PendingImport] = {}
        self._lock = threading.RLock()

    def preview(
        self,
        source: PersonalLocalImportSource,
        *,
        target: TenantContext,
        requested_by: str,
    ) -> LocalImportPreview:
        requester = requested_by.strip()
        if not requester:
            raise ValueError("requested_by must be non-empty")
        organization_id, workspace_id = require_enterprise_workspace_context(target)
        descriptors = tuple(source.describe_importable())
        local_ids = tuple(item.local_id for item in descriptors)
        if len(local_ids) != len(set(local_ids)):
            raise ValueError("local import descriptors must have unique local_id values")
        preview_id = f"import-preview:{uuid.uuid4().hex}"
        confirmation_token = secrets.token_urlsafe(32)
        counts = Counter(item.kind.value for item in descriptors)
        preview = LocalImportPreview(
            preview_id=preview_id,
            confirmation_token=confirmation_token,
            organization_id=organization_id,
            workspace_id=workspace_id,
            requested_by=requester,
            item_count=len(descriptors),
            counts_by_kind=dict(sorted(counts.items())),
            descriptor_digest=_descriptor_digest(descriptors),
        )
        with self._lock:
            self._pending[preview_id] = _PendingImport(
                preview=preview,
                source=source,
                descriptors=descriptors,
                target=target,
            )
        return preview

    def confirm(
        self,
        preview_id: str,
        confirmation_token: str,
        *,
        confirmed_by: str,
        transaction: TenantConnection,
    ) -> LocalImportResult:
        confirmer = confirmed_by.strip()
        if not confirmer:
            raise ImportConfirmationError("confirmed_by must be non-empty")
        with self._lock:
            pending = self._pending.get(preview_id)
        if pending is None:
            raise ImportConfirmationError("import preview does not exist or was consumed")
        if not secrets.compare_digest(
            pending.preview.confirmation_token,
            confirmation_token,
        ):
            raise ImportConfirmationError("import confirmation token is invalid")
        if transaction.context != pending.target:
            raise ImportConfirmationError(
                "import confirmation tenant does not match preview target"
            )

        imported: list[str] = []
        for descriptor in pending.descriptors:
            payload = pending.source.load_payload(descriptor.local_id)
            resource_id = _enterprise_resource_id(descriptor)
            self._repository.upsert(
                transaction,
                TenantResourceDraft(
                    resource_id=resource_id,
                    kind=descriptor.kind,
                    scope_type=descriptor.scope_type,
                    owner_subject_id=transaction.actor.subject_id,
                    classification=descriptor.classification,
                    title=descriptor.title,
                    status=descriptor.status,
                    payload=payload,
                    search_text=descriptor.search_text,
                    job_id=descriptor.job_id,
                    cache_key=descriptor.cache_key,
                ),
            )
            imported.append(resource_id)
        with self._lock:
            current = self._pending.get(preview_id)
            if current is pending:
                self._pending.pop(preview_id, None)
        return LocalImportResult(
            preview_id=preview_id,
            imported_count=len(imported),
            imported_resource_ids=tuple(imported),
        )


__all__ = [
    "LocalToEnterpriseImportService",
    "PersonalLocalImportSource",
]
