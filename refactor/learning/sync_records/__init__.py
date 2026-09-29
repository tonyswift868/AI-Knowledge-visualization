"""Wire envelopes shared with the existing structured-sync endpoint."""

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

__all__ = [
    "MAX_RECORD_BYTES",
    "RECORD_TYPES",
    "RefactorRecord",
    "SyncRecordError",
    "entity_id_for",
    "envelope_for",
    "kind_for",
    "parse_envelope",
    "validate_learning_record",
]
