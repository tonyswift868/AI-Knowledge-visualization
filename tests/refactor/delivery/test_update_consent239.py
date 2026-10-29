from refactor.desktop.delivery import per_user_update_core as core
from pathlib import Path
from types import SimpleNamespace
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest


def pending_doc(*, target='2.0.38', release_sha256='a' * 64):
    return {
        'schema': core.SCHEMA,
        'state': 'UPDATE_DOWNLOADED',
        'stage': 'C:/test/cache/App',
        'target': target,
        'release_sha256': release_sha256,
    }


def test_optional_click_records_authorization_bound_to_exact_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(core.install, 'target', lambda root: tmp_path)
    (tmp_path / core.PENDING).write_bytes(core.canonical(pending_doc()))

    assert core.record_update_authorization(tmp_path, '2.0.38', 'a' * 64) is True

    pending = core.read_pending(tmp_path)
    assert core.authorization_matches(pending)
    assert pending['user_authorization'] == {
        'schema': core.AUTHORIZATION_SCHEMA,
        'target': '2.0.38',
        'release_sha256': 'a' * 64,
    }


def test_background_pending_without_click_has_no_authorization(tmp_path, monkeypatch):
    monkeypatch.setattr(core.install, 'target', lambda root: tmp_path)
    (tmp_path / core.PENDING).write_bytes(core.canonical(pending_doc()))

    assert not core.authorization_matches(core.read_pending(tmp_path))
    assert not (tmp_path / core.UPDATE_AUTHORIZATION).exists()


def test_click_intent_does_not_authorize_a_different_target_or_envelope(tmp_path, monkeypatch):
    monkeypatch.setattr(core.install, 'target', lambda root: tmp_path)

    assert core.record_update_authorization(tmp_path, '2.0.38', 'a' * 64) is False
    (tmp_path / core.PENDING).write_bytes(core.canonical(pending_doc(target='2.0.39', release_sha256='b' * 64)))

    assert not core.authorization_matches(core.read_pending(tmp_path))
    assert core.read_update_authorization(tmp_path)['target'] == '2.0.38'


def test_legacy_pending_authorization_is_not_inferred(tmp_path, monkeypatch):
    monkeypatch.setattr(core.install, 'target', lambda root: tmp_path)
    (tmp_path / core.PENDING).write_bytes(core.canonical(pending_doc()))

    pending = core.read_pending(tmp_path)
    assert not core.authorization_matches(pending)


def prepare_small_pending(root, monkeypatch, target, envelope):
    monkeypatch.setattr(core.install, 'target', lambda _: root)
    monkeypatch.setattr(core.install, 'plain', lambda path: Path(path))
    monkeypatch.setattr(core.install, 'keys', lambda: {})
    monkeypatch.setattr(core.install, 'admit', lambda *_: (None, SimpleNamespace(version=target)))
    from refactor.desktop.delivery import prepared_update
    monkeypatch.setattr(prepared_update, 'ready', lambda *_: True)
    return core.remember_download(root, root / 'cache' / 'App', envelope)


def test_repreparing_same_release_preserves_existing_click_authorization(tmp_path, monkeypatch):
    envelope = b'signed-release-38'
    release_sha256 = core.digest(envelope)
    doc = pending_doc(release_sha256=release_sha256)
    doc['user_authorization'] = {
        'schema': core.AUTHORIZATION_SCHEMA,
        'target': '2.0.38',
        'release_sha256': release_sha256,
    }
    (tmp_path / core.PENDING).write_bytes(core.canonical(doc))

    prepare_small_pending(tmp_path, monkeypatch, '2.0.38', envelope)

    assert core.authorization_matches(core.read_pending(tmp_path))


def test_preparing_changed_release_does_not_inherit_click_authorization(tmp_path, monkeypatch):
    old_envelope = b'signed-release-38'
    old_hash = core.digest(old_envelope)
    old = pending_doc(release_sha256=old_hash)
    old['user_authorization'] = {
        'schema': core.AUTHORIZATION_SCHEMA,
        'target': '2.0.38',
        'release_sha256': old_hash,
    }
    (tmp_path / core.PENDING).write_bytes(core.canonical(old))

    prepare_small_pending(tmp_path, monkeypatch, '2.0.39', b'signed-release-39')

    changed = core.read_pending(tmp_path)
    assert changed['target'] == '2.0.39'
    assert not core.authorization_matches(changed)


def test_click_during_background_receipt_write_is_not_lost239(tmp_path, monkeypatch):
    envelope = b'signed-release-38'
    fingerprint = core.digest(envelope)
    (tmp_path / core.PENDING).write_bytes(core.canonical(pending_doc(release_sha256=fingerprint)))
    read_started, allow_write, click_waiting = (threading.Event() for _ in range(3))
    actual_read = core.read_pending
    lock = threading.RLock()

    class ObservedLock:
        def __enter__(self):
            if threading.current_thread().name == 'click239':
                click_waiting.set()
            lock.acquire()
        def __exit__(self, *_): lock.release()

    def read(root):
        result = actual_read(root)
        if threading.current_thread().name == 'prepare239':
            read_started.set()
            assert allow_write.wait(5)
        return result

    monkeypatch.setattr(core, '_authorization_lock', ObservedLock())
    monkeypatch.setattr(core, 'read_pending', read)
    monkeypatch.setattr(core.install, 'target', lambda _: tmp_path)
    def prepare():
        threading.current_thread().name = 'prepare239'
        return prepare_small_pending(tmp_path, monkeypatch, '2.0.38', envelope)
    def click():
        threading.current_thread().name = 'click239'
        return core.record_update_authorization(tmp_path, '2.0.38', fingerprint)

    with ThreadPoolExecutor(max_workers=2) as pool:
        preparing = pool.submit(prepare)
        try:
            assert read_started.wait(5)
            clicking = pool.submit(click)
            assert click_waiting.wait(5)
            assert not clicking.done()  # blocked only while tiny metadata is rewritten
        finally:
            allow_write.set()
        preparing.result(timeout=5)
        assert clicking.result(timeout=5)
    assert core.authorization_matches(actual_read(tmp_path))


def test_failed_pending_write_keeps_exact_click_intent239(tmp_path, monkeypatch):
    envelope = b'signed-release-38'
    fingerprint = core.digest(envelope)
    monkeypatch.setattr(core.install, 'target', lambda _: tmp_path)
    core.record_update_authorization(tmp_path, '2.0.38', fingerprint)
    original = core.install.atomic
    def fail_pending(path, data):
        if Path(path).name == core.PENDING:
            raise OSError('injected disk failure')
        return original(path, data)
    monkeypatch.setattr(core.install, 'atomic', fail_pending)
    with pytest.raises(OSError, match='injected'):
        prepare_small_pending(tmp_path, monkeypatch, '2.0.38', envelope)
    assert core.read_update_authorization(tmp_path)['release_sha256'] == fingerprint
