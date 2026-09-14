"""Fail-closed Device-B dispatcher for a trusted structured snapshot.

The dispatcher only invokes domain adapters supplied by the product store. It
does not execute tutor, reward, notification, download, or import behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .sync_registry import SYNC_ITEM_REGISTRY


SUPPORTED_KINDS = frozenset(SYNC_ITEM_REGISTRY)


class RehydrationRejected(ValueError):
    pass


class DomainAdapter(Protocol):
    def apply(self, item: Mapping[str, Any], *, scope: Mapping[str, str], store: Any) -> Any: ...


@dataclass(frozen=True)
class DispatchContext:
    issuer: str
    subject_id: str
    organization_id: str
    workspace_id: str
    snapshot_revision: str

    @property
    def scope(self):
        return {key: getattr(self, key) for key in
                ('issuer', 'subject_id', 'organization_id', 'workspace_id')}


class RehydrationDispatcher:
    """Apply each item once, in one caller-owned product transaction."""

    def __init__(self, adapters: Mapping[str, DomainAdapter]):
        self.adapters = dict(adapters)
        unknown = set(self.adapters) - SUPPORTED_KINDS
        if unknown:
            raise RehydrationRejected('UNSUPPORTED_ADAPTER_KIND:' + ','.join(sorted(unknown)))

    def apply(self, snapshot: Mapping[str, Any], *, context: DispatchContext, store: Any):
        if not isinstance(snapshot, Mapping) or snapshot.get('complete') is not True:
            raise RehydrationRejected('INCOMPLETE_SNAPSHOT')
        scope = snapshot.get('snapshot_scope')
        if not isinstance(scope, Mapping) or any(scope.get(k) != v for k, v in context.scope.items()):
            raise RehydrationRejected('WRONG_REHYDRATION_SCOPE')
        revision = snapshot.get('snapshot_revision')
        if not isinstance(revision, str) or not revision:
            raise RehydrationRejected('SNAPSHOT_REVISION_REQUIRED')
        items = snapshot.get('entities')
        if not isinstance(items, list):
            raise RehydrationRejected('SNAPSHOT_ENTITIES_REQUIRED')
        results = []
        seen = set()
        for item in items:
            if not isinstance(item, Mapping):
                raise RehydrationRejected('INVALID_SNAPSHOT_ITEM')
            kind, entity = item.get('kind'), item.get('entity_id')
            if kind not in SUPPORTED_KINDS or kind not in self.adapters:
                raise RehydrationRejected('UNSUPPORTED_SYNC_KIND')
            if not isinstance(entity, str) or not entity or (kind, entity) in seen:
                raise RehydrationRejected('DUPLICATE_OR_INVALID_ENTITY')
            if type(item.get('revision')) is not int or item['revision'] <= 0:
                raise RehydrationRejected('INVALID_ENTITY_REVISION')
            seen.add((kind, entity))
            if item.get('deleted') is not True and not isinstance(item.get('payload'), Mapping):
                raise RehydrationRejected('ENTITY_PAYLOAD_REQUIRED')
            results.append(self.adapters[kind].apply(item, scope=context.scope, store=store))
        # The caller owns the transaction. No commit, network, or side effect
        # happens here; a raised adapter error rolls back all prior applies.
        return tuple(results)
