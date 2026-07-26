from __future__ import annotations

import hashlib

import pytest

from tools.kks_native_conversion_queue import batch_id_for, sha256_file, stage_card


def test_batch_id_is_order_independent_and_stable() -> None:
    assert batch_id_for(["b" * 16, "a" * 16]) == batch_id_for(["a" * 16, "b" * 16])
    assert batch_id_for(["a" * 16]).startswith("batch-")


def test_stage_card_is_resumable_and_rejects_conflicting_content(tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"kks-card")
    expected = hashlib.sha256(b"kks-card").hexdigest()
    staged = tmp_path / "batch" / "cards" / "card.png"

    assert stage_card(source, staged, expected) in {"hardlink", "copy"}
    assert stage_card(source, staged, expected) == "existing"
    assert sha256_file(staged) == expected

    staged.unlink()
    staged.write_bytes(b"wrong-card")
    with pytest.raises(RuntimeError, match="conflicts with source"):
        stage_card(source, staged, expected)
