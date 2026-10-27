"""Real Windows child-directory sharing checks for voice resource renames."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import threading

import pytest

from refactor import filesystem_ops
from refactor.learning.voice import download as download_module
from refactor.learning.voice.download import AssetDownloader, DownloadAsset, DownloadError


pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows directory sharing semantics")


def _hold_directory(path: Path):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create_file.restype = ctypes.c_void_p
    handle = create_file(str(path), 0x0001, 0x00000007, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel32, handle


def _close_directory(owner):
    kernel32, handle = owner
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    assert kernel32.CloseHandle(handle)


def _asset():
    content = b"verified voice model bytes"
    return DownloadAsset("voice", ("http://127.0.0.1/not-requested",), len(content),
                         hashlib.sha256(content).hexdigest(), "voice.bin"), content


def _install_complete(manager: AssetDownloader, root: Path, asset: DownloadAsset, content: bytes):
    root.mkdir(parents=True)
    (root / asset.install_path).write_bytes(content)
    (root / "installed.json").write_text(json.dumps(manager._receipt(root)), encoding="utf-8")
    (root / "held-child").mkdir()


@pytest.mark.parametrize("phase", ["publish_backup", "restore_backup"])
@pytest.mark.parametrize("release_after_failures", [1, 3, None], ids=["first-retry", "middle-retry", "persistent"])
def test_voice211_directory_sharing_retries_and_preserves_old_install(
    tmp_path, monkeypatch, phase, release_after_failures
):
    asset, old_bytes = _asset()
    manager = AssetDownloader(tmp_path / "models", (asset,))
    if phase == "publish_backup":
        _install_complete(manager, manager.path, asset, old_bytes)
        ready = tmp_path / "ready"
        ready.mkdir()
        (ready / "new.bin").write_bytes(b"candidate")
        held_path = manager.path / "held-child"
    else:
        _install_complete(manager, manager.backup, asset, old_bytes)
        held_path = manager.backup / "held-child"

    held = _hold_directory(held_path)
    sleeps = []
    real_sleep = filesystem_ops.time.sleep
    closed = [False]

    def controlled_sleep(seconds):
        sleeps.append(seconds)
        if release_after_failures is not None and len(sleeps) == release_after_failures:
            _close_directory(held)
            closed[0] = True
        real_sleep(seconds)

    monkeypatch.setattr(filesystem_ops.time, "sleep", controlled_sleep)
    try:
        if release_after_failures is None:
            if phase == "publish_backup":
                with pytest.raises(OSError) as denied:
                    manager._publish(ready)
                assert getattr(denied.value, "winerror", None) == 5
                assert (manager.path / "voice.bin").read_bytes() == old_bytes
                assert not manager.backup.exists()
                assert (ready / "new.bin").read_bytes() == b"candidate"
            else:
                with pytest.raises(DownloadError) as denied:
                    manager.download(lambda *_: None, threading.Event())
                assert denied.value.code == "恢复未完成"
                assert not manager.path.exists()
                assert (manager.backup / "voice.bin").read_bytes() == old_bytes
                assert (manager.backup / "held-child").is_dir()
        else:
            if phase == "publish_backup":
                manager._publish(ready)
                assert (manager.path / "new.bin").read_bytes() == b"candidate"
                assert not manager.backup.exists()
            else:
                manager.download(lambda *_: None, threading.Event())
                assert manager.status()["installed"]
                assert (manager.path / "voice.bin").read_bytes() == old_bytes
                assert not manager.backup.exists()
            assert len(sleeps) == release_after_failures
    finally:
        if not closed[0]:
            _close_directory(held)


def test_voice211_publish_exhaustion_keeps_backup_for_next_launch_recovery(tmp_path, monkeypatch):
    asset, old_bytes = _asset()
    manager = AssetDownloader(tmp_path / "models", (asset,))
    _install_complete(manager, manager.path, asset, old_bytes)
    ready = manager.staging / "ready"
    ready.mkdir(parents=True)
    (ready / "voice.bin").write_bytes(old_bytes)
    (ready / "held-child").mkdir()
    (ready / "installed.json").write_text(json.dumps(manager._receipt(ready)), encoding="utf-8")

    held_ready = _hold_directory(ready / "held-child")
    held_backup = []
    original_rename = download_module.rename_with_retry

    def open_backup_child_after_move(source, target, **kwargs):
        result = original_rename(source, target, **kwargs)
        if Path(source) == manager.path and not held_backup:
            held_backup.append(_hold_directory(manager.backup / "held-child"))
        return result

    monkeypatch.setattr(download_module, "rename_with_retry", open_backup_child_after_move)
    try:
        with pytest.raises(DownloadError) as failure:
            manager._publish(ready)
        assert failure.value.code == "恢复未完成"
        assert "下次启动再试" in failure.value.message
        assert not manager.path.exists()
        assert (manager.backup / "voice.bin").read_bytes() == old_bytes
        assert (ready / "voice.bin").read_bytes() == old_bytes
    finally:
        _close_directory(held_ready)
        if held_backup:
            _close_directory(held_backup[0])

    # The next explicit run restores the old complete tree before checking or
    # downloading anything; it then clears the abandoned candidate staging.
    manager.download(lambda *_: None, threading.Event())
    assert manager.status()["installed"]
    assert (manager.path / "voice.bin").read_bytes() == old_bytes
    assert not manager.backup.exists() and not manager.staging.exists()


def _tree_bytes(root: Path):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("release_after_failures", [1, 3, None], ids=["first-retry", "middle-retry", "persistent"])
def test_voice211_ready_to_install_rename_retries_through_download(
    tmp_path, monkeypatch, release_after_failures
):
    asset, new_bytes = _asset()
    manager = AssetDownloader(tmp_path / "models", (asset,))
    _install_complete(manager, manager.path, asset, b"previous asset bytes")
    old_snapshot = _tree_bytes(manager.path)
    source = tmp_path / "verified-source.bin"
    source.write_bytes(new_bytes)
    manager._fetch = lambda *_args: source
    original_unpack = manager._unpack
    open_child = []

    def unpack_and_hold(asset_spec, source_path, ready, cancel):
        original_unpack(asset_spec, source_path, ready, cancel)
        (ready / "held-child").mkdir()
        open_child.append(_hold_directory(ready / "held-child"))

    manager._unpack = unpack_and_hold
    sleeps = []
    closed = [False]
    real_sleep = filesystem_ops.time.sleep

    def controlled_sleep(seconds):
        sleeps.append(seconds)
        if release_after_failures is not None and len(sleeps) == release_after_failures:
            _close_directory(open_child[0])
            closed[0] = True
        real_sleep(seconds)

    monkeypatch.setattr(filesystem_ops.time, "sleep", controlled_sleep)
    try:
        if release_after_failures is None:
            with pytest.raises(DownloadError) as failure:
                manager.download(lambda *_: None, threading.Event())
            assert failure.value.code == "安装失败"
            assert getattr(failure.value.__cause__, "winerror", None) == 5
            assert _tree_bytes(manager.path) == old_snapshot
            assert not manager.backup.exists() and not manager.staging.exists()
        else:
            manager.download(lambda *_: None, threading.Event())
            assert manager.status()["installed"]
            assert (manager.path / "voice.bin").read_bytes() == new_bytes
            assert not manager.backup.exists()
            assert len(sleeps) == release_after_failures
    finally:
        if open_child and not closed[0]:
            _close_directory(open_child[0])


def test_voice211_rollback_exhaustion_keeps_both_trees_then_recovers(tmp_path, monkeypatch):
    asset, old_bytes = _asset()
    manager = AssetDownloader(tmp_path / "models", (asset,))
    _install_complete(manager, manager.path, asset, old_bytes)
    old_snapshot = _tree_bytes(manager.path)
    source = tmp_path / "verified-source.bin"
    source.write_bytes(old_bytes)
    manager._fetch = lambda *_args: source
    original_unpack = manager._unpack
    opened_ready = []

    def unpack_and_hold(asset_spec, source_path, ready, cancel):
        original_unpack(asset_spec, source_path, ready, cancel)
        (ready / "held-child").mkdir()
        opened_ready.append(_hold_directory(ready / "held-child"))

    manager._unpack = unpack_and_hold
    real_status = manager.status
    status_calls = [0]

    def force_one_rebuild():
        status_calls[0] += 1
        return {"installed": False} if status_calls[0] == 1 else real_status()

    manager.status = force_one_rebuild
    original_rename = download_module.rename_with_retry
    opened_backup = []
    ready_snapshot = []

    def hold_both_trees(source_path, target_path, **kwargs):
        source_path = Path(source_path)
        if source_path == manager.staging / "ready" and not ready_snapshot:
            ready_snapshot.append(_tree_bytes(source_path))
        result = original_rename(source_path, target_path, **kwargs)
        if source_path == manager.path and not opened_backup:
            opened_backup.append(_hold_directory(manager.backup / "held-child"))
        return result

    monkeypatch.setattr(download_module, "rename_with_retry", hold_both_trees)
    try:
        with pytest.raises(DownloadError) as failure:
            manager.download(lambda *_: None, threading.Event())
        assert failure.value.code == "恢复未完成"
        assert not manager.path.exists()
        assert _tree_bytes(manager.backup) == old_snapshot
        assert _tree_bytes(manager.staging / "ready") == ready_snapshot[0]
    finally:
        if opened_ready:
            _close_directory(opened_ready[0])
        if opened_backup:
            _close_directory(opened_backup[0])

    # Once both transient opens are released, a new download call first
    # restores the untouched backup, then clears its previously prepared set.
    manager.download(lambda *_: None, threading.Event())
    assert manager.status()["installed"]
    assert _tree_bytes(manager.path) == old_snapshot
    assert not manager.backup.exists() and not manager.staging.exists()


def test_voice211_invalid_active_tree_never_deletes_complete_backup(tmp_path):
    asset, good_bytes = _asset()
    manager = AssetDownloader(tmp_path / "models", (asset,))
    _install_complete(manager, manager.backup, asset, good_bytes)
    _install_complete(manager, manager.path, asset, b"X" * len(good_bytes))
    active_before = _tree_bytes(manager.path)
    backup_before = _tree_bytes(manager.backup)

    with pytest.raises(DownloadError) as failure:
        manager.download(lambda *_: None, threading.Event())

    assert failure.value.code == "恢复未完成"
    assert _tree_bytes(manager.path) == active_before
    assert _tree_bytes(manager.backup) == backup_before


def test_voice211_valid_active_tree_allows_stale_backup_cleanup(tmp_path):
    asset, good_bytes = _asset()
    manager = AssetDownloader(tmp_path / "models", (asset,))
    _install_complete(manager, manager.path, asset, good_bytes)
    _install_complete(manager, manager.backup, asset, good_bytes)
    active_before = _tree_bytes(manager.path)

    manager.download(lambda *_: None, threading.Event())

    assert _tree_bytes(manager.path) == active_before
    assert not manager.backup.exists()

