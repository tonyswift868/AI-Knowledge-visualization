from __future__ import annotations

import os
from pathlib import Path
import threading

import pytest

from app import immutable_evidence


def test_immutable_write_is_idempotent_and_rejects_different_existing_bytes(tmp_path: Path):
    target = tmp_path / "nested" / "receipt.json"
    immutable_evidence.immutable_write(target, b"exact\n")
    immutable_evidence.immutable_write(target, b"exact\n")

    assert target.read_bytes() == b"exact\n"
    with pytest.raises(immutable_evidence.ImmutableEvidenceError) as caught:
        immutable_evidence.immutable_write(target, b"different\n")
    assert caught.value.code == "IMMUTABLE_TARGET_EXISTS"
    assert target.read_bytes() == b"exact\n"


def test_different_existing_target_is_never_overwritten_and_audit_temp_is_retained(
    tmp_path: Path,
):
    target = tmp_path / "evidence" / "scope.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"competitor\n")
    with pytest.raises(immutable_evidence.ImmutableEvidenceError) as caught:
        immutable_evidence.immutable_write(target, b"intended\n")

    assert caught.value.code == "IMMUTABLE_TARGET_EXISTS"
    assert target.read_bytes() == b"competitor\n"
    assert caught.value.temporary_path is not None
    assert caught.value.temporary_path.read_bytes() == b"intended\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing and FileId contract")
def test_windows_committed_handle_blocks_equal_length_concurrent_writer(
    tmp_path: Path, monkeypatch
):
    target = tmp_path / "evidence" / "same-file-id.json"
    intended = b"intended\n"
    evil = b"evilxxxx\n"
    assert len(intended) == len(evil)
    attempted: dict[str, object] = {}
    original_write_all = immutable_evidence._write_all

    def write_with_competitor(fd: int, content: bytes) -> None:
        original_write_all(fd, content)
        during = target.stat()
        attempted["during_identity"] = (during.st_dev, during.st_ino)

        def writer() -> None:
            try:
                with target.open("r+b") as stream:
                    stream.seek(0)
                    stream.write(evil)
                    stream.flush()
                    os.fsync(stream.fileno())
                attempted["writer"] = "wrote"
            except OSError as exc:
                attempted["writer"] = "sharing-violation"
                attempted["winerror"] = getattr(exc, "winerror", None)

        thread = threading.Thread(target=writer)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive()

    monkeypatch.setattr(immutable_evidence, "_write_all", write_with_competitor)
    succeeded = False
    try:
        immutable_evidence.immutable_write(target, intended)
        succeeded = True
    except immutable_evidence.ImmutableEvidenceError:
        pass

    final = target.read_bytes()
    after = target.stat()
    assert not (succeeded and final == evil)
    if succeeded:
        assert attempted["writer"] == "sharing-violation"
        assert final == intended
        assert attempted["during_identity"] == (after.st_dev, after.st_ino)


def test_every_ancestor_is_checked_for_reparse_points(tmp_path: Path, monkeypatch):
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    target = unsafe / "deeper" / "receipt.json"
    real_check = immutable_evidence._is_reparse_or_link

    def injected(path: Path) -> bool:
        return path == unsafe or real_check(path)

    monkeypatch.setattr(immutable_evidence, "_is_reparse_or_link", injected)
    with pytest.raises(immutable_evidence.ImmutableEvidenceError) as caught:
        immutable_evidence.immutable_write(target, b"blocked\n")
    assert caught.value.code == "IMMUTABLE_REPARSE_FORBIDDEN"
    assert not target.exists()
