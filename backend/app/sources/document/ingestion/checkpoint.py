"""Atomic JSON persistence for document-ingestion checkpoints."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..contracts import (
    DocumentBoundingBox,
    DocumentEvidence,
    DocumentEvidenceBundle,
    DocumentEvidenceKind,
    DocumentFormat,
    DocumentItemProvenance,
    canonical_json_bytes,
    sha256_json,
)
from .contracts import (
    DOCUMENT_INGESTION_SCHEMA,
    DocumentCheckpointEntry,
    DocumentConversionIdentity,
    DocumentFingerprint,
    DocumentIngestionCheckpoint,
    DocumentIngestionFailure,
    DocumentIngestionReceipt,
    DocumentIngestionStage,
    DocumentIngestionStatus,
    DocumentPipelineHandoff,
)


DOCUMENT_CHECKPOINT_FILE_SCHEMA = "mentorflow.document-checkpoint-file.v1"


class DocumentCheckpointIntegrityError(ValueError):
    pass


class DocumentCheckpointStore:
    def __init__(self, path: Path) -> None:
        if not path.is_absolute():
            raise ValueError("checkpoint path must be absolute")
        self.path = path

    def load(self) -> DocumentIngestionCheckpoint:
        if not self.path.is_file():
            return DocumentIngestionCheckpoint()
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DocumentCheckpointIntegrityError(
                "document checkpoint file is unreadable"
            ) from error
        if envelope.get("schema") != DOCUMENT_CHECKPOINT_FILE_SCHEMA:
            raise DocumentCheckpointIntegrityError("document checkpoint schema mismatch")
        payload = envelope.get("checkpoint")
        if not isinstance(payload, dict):
            raise DocumentCheckpointIntegrityError("document checkpoint payload is invalid")
        if envelope.get("checkpoint_hash") != sha256_json(payload):
            raise DocumentCheckpointIntegrityError("document checkpoint hash mismatch")
        return _checkpoint_from_dict(payload)

    def save(self, checkpoint: DocumentIngestionCheckpoint) -> str:
        payload = checkpoint.to_dict()
        checkpoint_hash = sha256_json(payload)
        envelope = {
            "schema": DOCUMENT_CHECKPOINT_FILE_SCHEMA,
            "checkpoint_hash": checkpoint_hash,
            "checkpoint": payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f"{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(canonical_json_bytes(envelope))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return checkpoint_hash


def _checkpoint_from_dict(payload: dict[str, Any]) -> DocumentIngestionCheckpoint:
    if payload.get("schema") != DOCUMENT_INGESTION_SCHEMA:
        raise DocumentCheckpointIntegrityError("ingestion checkpoint schema mismatch")
    completed_payload = payload.get("completed")
    if not isinstance(completed_payload, dict):
        raise DocumentCheckpointIntegrityError("checkpoint completed map is invalid")
    checkpoint = DocumentIngestionCheckpoint(schema=DOCUMENT_INGESTION_SCHEMA)
    for key, value in completed_payload.items():
        if not isinstance(value, dict):
            raise DocumentCheckpointIntegrityError("checkpoint entry is invalid")
        entry = _entry_from_dict(value)
        if key != entry.conversion_identity.value:
            raise DocumentCheckpointIntegrityError("checkpoint entry key mismatch")
        checkpoint.record(entry)
    return checkpoint


def _entry_from_dict(payload: dict[str, Any]) -> DocumentCheckpointEntry:
    identity = _identity_from_dict(payload["conversion_identity"])
    receipt = _receipt_from_dict(payload["receipt"])
    bundle_payload = payload.get("bundle")
    handoff_payload = payload.get("handoff")
    return DocumentCheckpointEntry(
        conversion_identity=identity,
        receipt=receipt,
        bundle=_bundle_from_dict(bundle_payload) if bundle_payload else None,
        handoff=_handoff_from_dict(handoff_payload) if handoff_payload else None,
        handoff_submitted=bool(payload.get("handoff_submitted", False)),
    )


def _fingerprint_from_dict(payload: dict[str, Any]) -> DocumentFingerprint:
    return DocumentFingerprint(
        sha256=str(payload["sha256"]),
        byte_count=int(payload["byte_count"]),
    )


def _identity_from_dict(payload: dict[str, Any]) -> DocumentConversionIdentity:
    return DocumentConversionIdentity(
        value=str(payload["value"]),
        fingerprint=_fingerprint_from_dict(payload["fingerprint"]),
        source_type=DocumentFormat(payload["source_type"]),
        conversion_config_identity=str(payload["conversion_config_identity"]),
    )


def _failure_from_dict(payload: dict[str, Any]) -> DocumentIngestionFailure:
    return DocumentIngestionFailure(
        error_class=str(payload["error_class"]),
        source_identity=str(payload["source_identity"]),
        source_file=str(payload["source_file"]),
        stage=DocumentIngestionStage(payload["stage"]),
        retryable=bool(payload["retryable"]),
        error_code=str(payload["error_code"]),
        message=str(payload["message"]),
    )


def _receipt_from_dict(payload: dict[str, Any]) -> DocumentIngestionReceipt:
    fingerprint_payload = payload.get("fingerprint")
    identity_payload = payload.get("conversion_identity")
    failure_payload = payload.get("failure")
    return DocumentIngestionReceipt(
        index=int(payload["index"]),
        source_file=str(payload["source_file"]),
        source_id=(str(payload["source_id"]) if payload.get("source_id") else None),
        status=DocumentIngestionStatus(payload["status"]),
        stage=DocumentIngestionStage(payload["stage"]),
        fingerprint=(
            _fingerprint_from_dict(fingerprint_payload)
            if fingerprint_payload is not None
            else None
        ),
        conversion_identity=(
            _identity_from_dict(identity_payload)
            if identity_payload is not None
            else None
        ),
        bundle_id=(str(payload["bundle_id"]) if payload.get("bundle_id") else None),
        evidence_count=int(payload.get("evidence_count", 0)),
        limitations=tuple(str(value) for value in payload.get("limitations", ())),
        failure=(
            _failure_from_dict(failure_payload)
            if failure_payload is not None
            else None
        ),
        duplicate_of=(
            str(payload["duplicate_of"]) if payload.get("duplicate_of") else None
        ),
        resumed=bool(payload.get("resumed", False)),
        handoff_submitted=bool(payload.get("handoff_submitted", False)),
    )


def _bounding_box_from_dict(payload: dict[str, Any]) -> DocumentBoundingBox:
    return DocumentBoundingBox(
        left=float(payload["left"]),
        top=float(payload["top"]),
        right=float(payload["right"]),
        bottom=float(payload["bottom"]),
        coord_origin=(
            str(payload["coord_origin"]) if payload.get("coord_origin") else None
        ),
    )


def _provenance_from_dict(payload: dict[str, Any]) -> DocumentItemProvenance:
    bbox_payload = payload.get("bbox")
    return DocumentItemProvenance(
        page_no=(int(payload["page_no"]) if payload.get("page_no") else None),
        bbox=_bounding_box_from_dict(bbox_payload) if bbox_payload else None,
        char_start=(
            int(payload["char_start"])
            if payload.get("char_start") is not None
            else None
        ),
        char_end=(
            int(payload["char_end"])
            if payload.get("char_end") is not None
            else None
        ),
    )


def _evidence_from_dict(payload: dict[str, Any]) -> DocumentEvidence:
    return DocumentEvidence(
        evidence_id=str(payload["evidence_id"]),
        source_id=str(payload["source_id"]),
        source_file=str(payload["source_file"]),
        source_sha256=str(payload["source_sha256"]),
        item_id=str(payload["item_id"]),
        parent_item_id=(
            str(payload["parent_item_id"])
            if payload.get("parent_item_id") is not None
            else None
        ),
        ordinal=int(payload["ordinal"]),
        hierarchy_level=int(payload["hierarchy_level"]),
        kind=DocumentEvidenceKind(payload["kind"]),
        content=str(payload["content"]),
        content_hash=str(payload["content_hash"]),
        section_path=tuple(str(value) for value in payload.get("section_path", ())),
        content_layer=(
            str(payload["content_layer"])
            if payload.get("content_layer") is not None
            else None
        ),
        provenance=tuple(
            _provenance_from_dict(value) for value in payload.get("provenance", ())
        ),
    )


def _bundle_from_dict(payload: dict[str, Any]) -> DocumentEvidenceBundle:
    return DocumentEvidenceBundle(
        schema=str(payload["schema"]),
        adapter_name=str(payload["adapter_name"]),
        adapter_version=str(payload["adapter_version"]),
        docling_version=str(payload["docling_version"]),
        bundle_id=str(payload["bundle_id"]),
        bundle_hash=str(payload["bundle_hash"]),
        source_id=str(payload["source_id"]),
        source_file=str(payload["source_file"]),
        source_sha256=str(payload["source_sha256"]),
        document_name=str(payload["document_name"]),
        evidence=tuple(
            _evidence_from_dict(value) for value in payload.get("evidence", ())
        ),
        warnings=tuple(str(value) for value in payload.get("warnings", ())),
    )


def _handoff_from_dict(payload: dict[str, Any]) -> DocumentPipelineHandoff:
    return DocumentPipelineHandoff(
        schema=str(payload["schema"]),
        handoff_id=str(payload["handoff_id"]),
        conversion_identity=_identity_from_dict(payload["conversion_identity"]),
        source_id=str(payload["source_id"]),
        source_revision=str(payload["source_revision"]),
        evidence_bundle=_bundle_from_dict(payload["evidence_bundle"]),
        generic_evidence=tuple(
            dict(value) for value in payload.get("generic_evidence", ())
        ),
        limitations=tuple(str(value) for value in payload.get("limitations", ())),
    )


__all__ = [
    "DOCUMENT_CHECKPOINT_FILE_SCHEMA",
    "DocumentCheckpointIntegrityError",
    "DocumentCheckpointStore",
]
