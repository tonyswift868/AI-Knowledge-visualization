"""Canonical identity policy for frozen UTF-8 text contracts.

Git may store LF text while a legal working-tree checkout uses CRLF.  Frozen
contract identity therefore normalizes line endings only; raw byte hashes stay
available for diagnostics and must not be used as semantic contract identity.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path


CANONICAL_CONTRACT_IDENTITY_POLICY = "utf8-text-lf-sha256.v1"


class ContractIdentityError(Exception):
    def __init__(self, code: str, contract_id: str) -> None:
        super().__init__(f"{code}: {contract_id}")
        self.code = code
        self.contract_id = contract_id


def raw_worktree_byte_sha256(path: Path) -> str:
    """Hash exact checkout bytes for diagnostics, never contract identity."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_contract_bytes(raw: bytes, *, contract_id: str) -> bytes:
    """Return strict UTF-8 bytes with all legal text EOL forms normalized to LF."""

    if b"\x00" in raw:
        raise ContractIdentityError("NON_TEXT_CONTRACT", contract_id)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContractIdentityError("NON_TEXT_CONTRACT", contract_id) from error
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def canonical_contract_sha256(path: Path, *, contract_id: str) -> str:
    if not path.is_file():
        raise ContractIdentityError("MISSING_CONTRACT", contract_id)
    canonical = canonical_contract_bytes(path.read_bytes(), contract_id=contract_id)
    return hashlib.sha256(canonical).hexdigest()


def verify_contract_identity(
    *,
    contract_id: str,
    path: Path,
    expected_identities: Mapping[str, str],
) -> str:
    """Verify a known frozen contract and return its canonical SHA-256."""

    expected = expected_identities.get(contract_id)
    if expected is None:
        raise ContractIdentityError("UNKNOWN_CONTRACT", contract_id)
    actual = canonical_contract_sha256(path, contract_id=contract_id)
    if actual != expected:
        raise ContractIdentityError("CONTRACT_DRIFT", contract_id)
    return actual


__all__ = [
    "CANONICAL_CONTRACT_IDENTITY_POLICY",
    "ContractIdentityError",
    "canonical_contract_bytes",
    "canonical_contract_sha256",
    "raw_worktree_byte_sha256",
    "verify_contract_identity",
]
