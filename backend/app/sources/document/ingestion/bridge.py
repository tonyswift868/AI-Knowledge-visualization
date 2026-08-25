"""Endpoint-neutral handoff seam for a future document-aware pipeline adapter."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..contracts import DocumentEvidenceBundle, sha256_json
from .contracts import (
    DOCUMENT_HANDOFF_SCHEMA,
    DocumentConversionIdentity,
    DocumentPipelineHandoff,
)


@runtime_checkable
class DocumentKnowledgePipelinePort(Protocol):
    """A fake-friendly port; implementations must not write the Knowledge Map."""

    def submit(self, handoff: DocumentPipelineHandoff) -> Any: ...


def build_document_pipeline_handoff(
    bundle: DocumentEvidenceBundle,
    *,
    conversion_identity: DocumentConversionIdentity,
    limitations: tuple[str, ...] = (),
) -> DocumentPipelineHandoff:
    generic_evidence = tuple(item.to_generic_evidence() for item in bundle.evidence)
    identity_payload = {
        "schema": DOCUMENT_HANDOFF_SCHEMA,
        "conversion_identity": conversion_identity.value,
        "bundle_hash": bundle.bundle_hash,
        "generic_evidence_ids": [item["chunk_id"] for item in generic_evidence],
        "limitations": list(limitations),
    }
    return DocumentPipelineHandoff(
        schema=DOCUMENT_HANDOFF_SCHEMA,
        handoff_id=f"dochandoff_{sha256_json(identity_payload)[:40]}",
        conversion_identity=conversion_identity,
        source_id=bundle.source_id,
        source_revision=bundle.source_sha256,
        evidence_bundle=bundle,
        generic_evidence=generic_evidence,
        limitations=limitations,
    )


__all__ = ["DocumentKnowledgePipelinePort", "build_document_pipeline_handoff"]
