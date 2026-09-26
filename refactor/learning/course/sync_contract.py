"""Shared, transport-independent course record contract (MF-03 / MF-07)."""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SyncRecord:
    entity: str
    entity_id: str
    plan_key: str
    schema: str
    payload: dict
    deleted: bool
    local_revision: int


@runtime_checkable
class RecordStore(Protocol):
    def pending_records(self, limit) -> list[SyncRecord]: ...
    def mark_synced(self, entity, entity_id, local_revision, server_revision) -> None: ...
    def apply_remote(self, records: list[tuple[SyncRecord, int]]) -> None: ...
    def sync_cursor(self) -> str: ...
    def set_sync_cursor(self, cursor: str) -> None: ...
