"""Parser-level XML safety for untrusted document inputs.

All callers that parse XML supplied by a user, archive, or network feed should
use :func:`fromstring` instead of the stdlib parser directly.  The defusedxml
parser rejects document type declarations, entities, and external references
while retaining the normal ElementTree return type for existing callers.
"""

from __future__ import annotations

from xml.etree.ElementTree import Element

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException


class XMLSecurityError(ValueError):
    """Raised when XML contains a forbidden DTD, entity, or external reference."""


def fromstring(source: str | bytes | bytearray) -> Element:
    """Parse XML with DTD, entity, and external-reference handling disabled."""

    try:
        return ElementTree.fromstring(
            source,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except DefusedXmlException as exc:
        raise XMLSecurityError(
            "XML document type declarations, entities, and external references are forbidden"
        ) from exc
