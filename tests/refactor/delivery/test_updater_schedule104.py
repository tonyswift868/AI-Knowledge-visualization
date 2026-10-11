"""Bounded background maintenance, including a still-running coordinator."""
import json
import threading

from refactor.desktop.delivery import per_user_install as install


def test_refresh104_busy_then_success(tmp_path, monkeypatch):
    attempts, delays, events = [], [], []
    monkeypatch.setattr(install.time, 'sleep', delays.append)
    def refresh(root, *, persist_deferred=True):
        assert not persist_deferred
        attempts.append(root)
        return {'status': 'DEFERRED' if len(attempts) < 4 else 'UPDATED'}
    monkeypatch.setattr(install, 'refresh_updater', refresh)
    monkeypatch.setattr(install, 'maintenance_log', lambda *a, **k: events.append((a[1], k)))
    worker = install.schedule_updater_refresh(tmp_path)
    worker.join(1)
    assert not worker.is_alive()
    assert delays == [2, 3, 5, 10]
    assert [e[1]['status'] for e in events] == ['DEFERRED'] * 3 + ['UPDATED']
    assert not (tmp_path / 'updater-refresh.json').exists()


def test_refresh104_budget_exhausted(tmp_path, monkeypatch):
    delays, events = [], []
    monkeypatch.setattr(install.time, 'sleep', delays.append)
    monkeypatch.setattr(install, 'refresh_updater', lambda *a, **k: {'status': 'DEFERRED', 'detail': 'busy'})
    monkeypatch.setattr(install, 'maintenance_log', lambda *a, **k: events.append((a[1], k)))
    worker = install.schedule_updater_refresh(tmp_path)
    worker.join(1)
    assert not worker.is_alive()
    assert delays == [2, 3, 5, 10, 20, 40]
    assert json.loads((tmp_path / 'updater-refresh.json').read_bytes())['status'] == 'DEFERRED'
    assert events[-1][1]['attempts'] == 6


def test_refresh104_cleanup_serialized_and_startup_nonblocking(tmp_path, monkeypatch):
    (tmp_path / install.STATE).write_text('{}')
    entered, release, cleaned = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(install.time, 'sleep', lambda _: None)
    def refresh(*a, **k):
        entered.set()
        assert release.wait(2)
        return {'status': 'UPDATED'}
    def cleanup(*a, **k):
        assert release.is_set()
        cleaned.set()
    monkeypatch.setattr(install, 'refresh_updater', refresh)
    monkeypatch.setattr(install, 'cleanup_program_files', cleanup)
    first = install.schedule_updater_refresh(tmp_path)
    assert entered.wait(1)
    second = install.schedule_program_cleanup(tmp_path)
    # Both scheduling calls returned while refresh is still deliberately held.
    assert first.is_alive() and second.is_alive() and not cleaned.is_set()
    release.set()
    first.join(1); second.join(1)
    assert cleaned.is_set() and not first.is_alive() and not second.is_alive()


def test_refresh104_transient_result_not_published(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from refactor.desktop.delivery import update_download
    @contextmanager
    def busy(*args):
        raise install.RecoverableError('busy coordinator')
        yield
    monkeypatch.setattr(install, 'target', lambda root: root)
    monkeypatch.setattr(update_download, 'InstallUpdateLock', busy)
    assert install.refresh_updater(tmp_path, persist_deferred=False)['status'] == 'DEFERRED'
    assert not (tmp_path / 'updater-refresh.json').exists()

