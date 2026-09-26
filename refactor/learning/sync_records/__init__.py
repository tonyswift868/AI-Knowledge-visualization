"""Account-scoped synchronization for the refactor learning records.

The package deliberately contains only the transport-independent record
contract and its local reconciliation ledger.  Product stores provide the
small adapter interface consumed by :class:`SyncEngine`.
"""

from .contract import (
    MAX_RECORD_BYTES,
    RECORD_TYPES,
    RefactorRecord,
    SyncRecordError,
    entity_id_for,
    envelope_for,
    kind_for,
    parse_envelope,
    validate_learning_record,
)
from .engine import SyncEngine

__all__ = [
    "MAX_RECORD_BYTES",
    "RECORD_TYPES",
    "RefactorRecord",
    "SyncRecordError",
    "SyncEngine",
    "entity_id_for",
    "envelope_for",
    "kind_for",
    "parse_envelope",
    "validate_learning_record",
]
