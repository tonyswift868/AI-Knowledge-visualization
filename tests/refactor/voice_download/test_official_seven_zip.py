import hashlib
import os
from pathlib import Path
import subprocess
import threading

import pytest

from refactor.learning.voice.download import ArchiveMember, AssetDownloader, DownloadAsset, DownloadError
from refactor.learning.voice import seven_zip


@pytest.fixture
def archive(tmp_path):
    tool = Path(os.environ.get("MF_ASSET_7ZIP", "not-provided"))
    if not tool.is_file():
        pytest.skip("native 7zr supplied by the release build")
    source = tmp_path / "upstream"
    source.mkdir()
    (source / "wanted.txt").write_bytes(b"official fixture payload")
    (source / "ignore.txt").write_bytes(b"do not install")
    packed = tmp_path / "engine.7z"
    subprocess.run([str(tool), "a", str(packed), str(source)], cwd=tmp_path,
                   capture_output=True, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return packed


def asset_for(archive, *, digest=None, size=24, path="upstream/wanted.txt"):
    content = b"official fixture payload"
    return DownloadAsset("engine.7z", ("https://example.invalid/engine.7z",), archive.stat().st_size,
                         hashlib.sha256(archive.read_bytes()).hexdigest(), "engine.7z", "7z",
                         (ArchiveMember(path, size, digest or hashlib.sha256(content).hexdigest(), "engine/runtime/selected.txt"),))


def test_official_7z_selects_exact_members_and_remaps(archive, tmp_path):
    manager = AssetDownloader(tmp_path / "installed", (asset_for(archive),))
    ready = tmp_path / "ready"; ready.mkdir()
    manager._unpack(manager.assets[0], archive, ready, threading.Event())
    manager.verify(ready)
    assert [p.relative_to(ready).as_posix() for p in ready.rglob("*") if p.is_file()] == ["engine/runtime/selected.txt"]
    assert not list(tmp_path.glob("7z-*"))


@pytest.mark.parametrize("overrides", [{"size": 23}, {"path": "upstream/missing.txt"}, {"digest": "0" * 64}])
def test_official_7z_rejects_wrong_inventory_or_hash(archive, tmp_path, overrides):
    asset = asset_for(archive, **overrides)
    manager = AssetDownloader(tmp_path / "installed", (asset,))
    ready = tmp_path / "ready"; ready.mkdir()
    with pytest.raises(DownloadError, match="清单|缺少|校验"):
        manager._unpack(asset, archive, ready, threading.Event())
        manager.verify(ready)
    assert not manager.path.exists()
    assert not list(tmp_path.glob("7z-*"))


def test_official_7z_cancel_does_not_launch_extractor(archive, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError("extractor must not start")
    monkeypatch.setattr(seven_zip.subprocess, "Popen", forbidden)
    ready = tmp_path / "ready"; ready.mkdir()
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(DownloadError) as error:
        seven_zip.unpack(asset_for(archive), archive, ready, cancelled)
    assert error.value.code == "已取消"
    assert not list(tmp_path.glob("7z-*"))


def test_official_7z_cancellation_terminates_its_child(tmp_path, monkeypatch):
    class Child:
        returncode = None
        terminated = False
        def poll(self): return self.returncode
        def terminate(self): self.terminated = True; self.returncode = -1
        def wait(self, timeout=None): return self.returncode
    child = Child()
    cancelled = threading.Event()
    def launch(*args, **kwargs): cancelled.set(); return child
    monkeypatch.setattr(seven_zip, "executable", lambda: tmp_path / "explicit-fake.exe")
    monkeypatch.setattr(seven_zip.subprocess, "Popen", launch)
    with pytest.raises(DownloadError) as error:
        seven_zip._run([], tmp_path, cancelled)
    assert error.value.code == "已取消" and child.terminated


def test_official_7z_link_metadata_rejected_before_extraction(tmp_path, monkeypatch):
    archive = tmp_path / 'test.7z'; archive.write_bytes(b'fake metadata test')
    monkeypatch.setattr(seven_zip, 'inventory', lambda *args: [dict(Path='upstream/wanted.txt', Folder='-', Size='24', **{'Symbolic Link': 'outside'})])
    ready = tmp_path / 'ready'; ready.mkdir()
    with pytest.raises(DownloadError, match='清单'):
        seven_zip.unpack(asset_for(archive), archive, ready, threading.Event())


def test_official_7z_deep_runtime_paths_work_without_os_long_path_setting(archive, tmp_path):
    from refactor.learning.voice.download import filesystem_path
    relative = "/".join(["nested" * 7] * 5) + "/module.py"
    source = tmp_path / "deep-source" / relative
    filesystem_path(source.parent).mkdir(parents=True)
    filesystem_path(source).write_bytes(b"runtime source")
    packed = tmp_path / "deep.7z"
    subprocess.run([str(seven_zip.executable()), "a", str(packed), str(tmp_path / "deep-source")],
                   cwd=tmp_path, capture_output=True, check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    asset = DownloadAsset("deep.7z", ("https://example.invalid/deep.7z",), packed.stat().st_size,
                          hashlib.sha256(packed.read_bytes()).hexdigest(), "deep.7z", "7z",
                          (ArchiveMember("deep-source/" + relative, 14, hashlib.sha256(b"runtime source").hexdigest(), "engine/" + relative),))
    manager = AssetDownloader(tmp_path / "installed", (asset,))
    ready = tmp_path / "ready"; ready.mkdir()
    manager._unpack(asset, packed, ready, threading.Event())
    manager.verify(ready)
    assert filesystem_path(ready / "engine" / relative).read_bytes() == b"runtime source"
    assert not list(tmp_path.glob("7z-*"))
