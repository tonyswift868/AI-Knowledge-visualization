from __future__ import annotations

from pathlib import Path

import pytest

from app.teaching.contract_identity import (
    CANONICAL_CONTRACT_IDENTITY_POLICY,
    ContractIdentityError,
    canonical_contract_sha256,
    raw_worktree_byte_sha256,
    verify_contract_identity,
)


CONTRACT_ID = "teacher/example.py"


def _identity(path: Path) -> str:
    return canonical_contract_sha256(path, contract_id=CONTRACT_ID)


def test_canonical_lf_and_equivalent_crlf_have_the_same_identity(
    tmp_path: Path,
) -> None:
    lf = tmp_path / "lf.py"
    crlf = tmp_path / "crlf.py"
    lf.write_bytes(b'RULE = "stable"\nVALUE = 1\n')
    crlf.write_bytes(b'RULE = "stable"\r\nVALUE = 1\r\n')

    assert CANONICAL_CONTRACT_IDENTITY_POLICY == "utf8-text-lf-sha256.v1"
    assert raw_worktree_byte_sha256(lf) != raw_worktree_byte_sha256(crlf)
    assert _identity(lf) == _identity(crlf)


def test_real_textual_mutation_is_detected(tmp_path: Path) -> None:
    original = tmp_path / "original.py"
    changed = tmp_path / "changed.py"
    original.write_text('RULE = "stable"\n', encoding="utf-8", newline="")
    changed.write_text('RULE = "changed"\n', encoding="utf-8", newline="")
    expected = {CONTRACT_ID: _identity(original)}

    with pytest.raises(ContractIdentityError) as captured:
        verify_contract_identity(
            contract_id=CONTRACT_ID,
            path=changed,
            expected_identities=expected,
        )
    assert captured.value.code == "CONTRACT_DRIFT"


def test_missing_contract_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ContractIdentityError) as captured:
        verify_contract_identity(
            contract_id=CONTRACT_ID,
            path=tmp_path / "missing.py",
            expected_identities={CONTRACT_ID: "0" * 64},
        )
    assert captured.value.code == "MISSING_CONTRACT"


def test_unknown_contract_fails_before_reading_path(tmp_path: Path) -> None:
    unknown_path = tmp_path / "unknown.py"
    unknown_path.write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(ContractIdentityError) as captured:
        verify_contract_identity(
            contract_id="teacher/unknown.py",
            path=unknown_path,
            expected_identities={CONTRACT_ID: "0" * 64},
        )
    assert captured.value.code == "UNKNOWN_CONTRACT"


@pytest.mark.parametrize("raw", [b"\x00\x01binary", b"\xff\xfe\x00\x00"])
def test_binary_or_non_utf8_contract_is_rejected(tmp_path: Path, raw: bytes) -> None:
    binary = tmp_path / "contract.bin"
    binary.write_bytes(raw)

    with pytest.raises(ContractIdentityError) as captured:
        canonical_contract_sha256(binary, contract_id=CONTRACT_ID)
    assert captured.value.code == "NON_TEXT_CONTRACT"


def test_verify_returns_the_expected_canonical_identity(tmp_path: Path) -> None:
    contract = tmp_path / "contract.py"
    contract.write_bytes(b"VALUE = 1\r\n")
    expected = {CONTRACT_ID: _identity(contract)}

    assert (
        verify_contract_identity(
            contract_id=CONTRACT_ID,
            path=contract,
            expected_identities=expected,
        )
        == expected[CONTRACT_ID]
    )
