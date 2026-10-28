"""Regression coverage for delivery bugs 155, 157 and 158."""
import json

import pytest

from refactor.desktop.delivery import install_receipt_state, per_user_install, update_download
from refactor.desktop.delivery.update_protocol import SecurityError, digest


def test_fix219_155_bad_complete_partial_is_discarded_and_redownloaded(tmp_path):
    good = b"verified asset"

    class Transport:
        calls = 0

        def stream(self, _url, *, offset, cancel):
            self.calls += 1
            assert offset == 0
            body = b"badified asset" if self.calls == 1 else good
            yield "headers", (200, {})
            yield "bytes", body

    transport = Transport()
    cache = update_download.ObjectCache(tmp_path / "cache", transport)
    asset = {"url": "https://example.invalid/a", "sha256": digest(good), "size": len(good)}
    partial = cache.objects / (asset["sha256"] + ".part")

    with pytest.raises(SecurityError, match="SHA-256 mismatch"):
        cache.download(asset)
    assert not partial.exists()

    result = cache.download(asset)
    assert result.read_bytes() == good
    assert transport.calls == 2
    assert not partial.exists()


def test_fix219_155_existing_bad_complete_partial_is_discarded_before_request(tmp_path):
    good = b"verified asset"

    class Transport:
        calls = 0

        def stream(self, _url, *, offset, cancel):
            self.calls += 1
            assert offset == 0
            yield "headers", (200, {})
            yield "bytes", good

    transport = Transport()
    cache = update_download.ObjectCache(tmp_path / "cache", transport)
    asset = {"url": "https://example.invalid/a", "sha256": digest(good), "size": len(good)}
    partial = cache.objects / (asset["sha256"] + ".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"badified asset")

    assert cache.download(asset).read_bytes() == good
    assert transport.calls == 1
    assert not partial.exists()


def test_fix219_155_oversized_partial_is_discarded_and_redownloaded(tmp_path):
    good = b"verified asset"

    class Transport:
        calls = 0

        def stream(self, _url, *, offset, cancel):
            self.calls += 1
            assert offset == 0
            yield "headers", (200, {})
            yield "bytes", good

    transport = Transport()
    cache = update_download.ObjectCache(tmp_path / "cache", transport)
    asset = {"url": "https://example.invalid/a", "sha256": digest(good), "size": len(good)}
    partial = cache.objects / (asset["sha256"] + ".part")
    partial.write_bytes(good + b"trailing")

    assert cache.download(asset).read_bytes() == good
    assert transport.calls == 1
    assert not partial.exists()


def test_fix219_157_missing_recovery_binding_is_not_retry_exhaustion(tmp_path):
    root = tmp_path / "MentorFlow"
    root.mkdir()
    attempt = "a" * 32
    stage = root / ("staging-" + attempt)
    stage.mkdir()
    (stage / "previous-updater.json").write_text('{"had_binding":true}', encoding="utf-8")
    journal = {
        "schema": "mentorflow.per-user-transaction.v1",
        "attempt": attempt,
        "phase": "OLD_MOVED",
        "target": "2.0.5",
        "base": "2.0.3",
        "base_manifest": "b" * 64,
    }
    (root / per_user_install.JOURNAL).write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(per_user_install.RecoverableError):
        per_user_install.recover(root, {})

    log = (root / "Data/logs/mentorflow.log").read_text(encoding="utf-8")
    assert "RECOVERY_EVIDENCE_MISSING" in log
    assert "DIRECTORY_RENAME_RETRY_EXHAUSTED" not in log


def test_fix219_158_unreachable_legacy_inspection_entry_is_removed():
    assert not hasattr(install_receipt_state, "inspect_root")
