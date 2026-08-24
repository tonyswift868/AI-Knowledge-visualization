from __future__ import annotations

import pytest

from app.safe_xml import XMLSecurityError, fromstring


@pytest.mark.parametrize(
    "payload",
    [
        b"<!DOCTYPE root><root />",
        b"<!DOCTYPE root [<!ENTITY boom 'expanded'>]><root>&boom;</root>",
        b"<!DOCTYPE root [<!ENTITY ext SYSTEM 'file:///etc/passwd'>]><root>&ext;</root>",
        b" " * 4096 + b"<!DOCTYPE root [<!ENTITY boom 'expanded'>]><root>&boom;</root>",
        b" " * 8192 + b"<!DOCTYPE root [<!ENTITY boom 'expanded'>]><root>&boom;</root>",
    ],
)
def test_safe_xml_rejects_dtd_and_entities_at_any_offset(payload: bytes) -> None:
    with pytest.raises(XMLSecurityError):
        fromstring(payload)


def test_safe_xml_accepts_normal_xml() -> None:
    root = fromstring(b"<root xmlns='urn:test'><item id='1'>normal</item></root>")

    assert root.tag == "{urn:test}root"
    assert root[0].text == "normal"
