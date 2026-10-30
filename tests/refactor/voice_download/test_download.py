import errno
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import socket
import tarfile
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request
import zipfile

import pytest

from refactor.learning.voice import download as module
from refactor.learning.voice.download import ArchiveMember, AssetDownloader, DownloadAsset, DownloadError


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def server():
    state = SimpleNamespace(data={}, requests=[], drop=False, ignore_range=False, bad_range=False)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            state.requests.append((self.path, self.headers.get("Range")))
            content = state.data.get(self.path)
            if content is None:
                self.send_error(503)
                return
            offset = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
            ranged = bool(offset) and not state.ignore_range
            if not ranged:
                offset = 0
            self.send_response(206 if ranged else 200)
            if ranged:
                self.send_header("Content-Range", f"bytes {offset + int(state.bad_range)}-{len(content)-1}/{len(content)}")
            self.send_header("Content-Length", str(len(content) - offset))
            self.end_headers()
            try:
                if state.drop:
                    state.drop = False
                    self.wfile.write(content[offset:offset + module.CHUNK_SIZE])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self.wfile.write(content[offset:])
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{httpd.server_port}"
    yield state
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def raw(server, data=b"explicit test model", *, urls=None, name="model", install="model.bin"):
    server.data["/" + name] = data
    return DownloadAsset(name, urls or (server.url + "/" + name,), len(data), sha(data), install)


def test_metadata_mirrors_and_path_validation():
    a = DownloadAsset("a", ("https://huggingface.co/org/repo/resolve/id/a",), 1, sha(b"a"), "dir/a")
    assert a.urls == ("https://huggingface.co/org/repo/resolve/id/a", "https://hf-mirror.com/org/repo/resolve/id/a")
    b = DownloadAsset("b", ("https://github.com/org/repo/releases/download/v/a",), 1, sha(b"b"), "b")
    assert len(b.urls) == 1
    for path in ("../escape", "C:/escape", "/escape", "x/../escape", "NUL", "a:stream", "a./b"):
        with pytest.raises(ValueError):
            DownloadAsset("a", a.urls, 1, sha(b"a"), path)


def test_status_is_offline_readonly_and_download_checks_all_bytes(tmp_path, monkeypatch, server):
    asset = raw(server)
    manager = AssetDownloader(tmp_path / "models", (asset,))
    original = module.urllib.request.urlopen
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: pytest.fail("status used network"))
    assert not manager.status()["installed"] and not manager.path.exists()
    monkeypatch.setattr(module.urllib.request, "urlopen", original)
    events = []
    manager.download(lambda done, total: events.append((done, total)), threading.Event())
    assert manager.status()["installed"] and not manager.staging.exists()
    assert events[-1] == (asset.size, asset.size) and all(0 <= d <= t for d, t in events)
    before = len(server.requests)
    manager.download(lambda *_: None, threading.Event())
    assert len(server.requests) == before
    path = manager.path / asset.install_path
    timestamp = path.stat().st_mtime_ns
    path.write_bytes(b"x" * asset.size)
    module.os.utime(path, ns=(timestamp, timestamp))
    assert manager.status()["installed"]  # Cheap receipt; explicit download repairs same-metadata corruption.
    manager.download(lambda *_: None, threading.Event())
    assert path.read_bytes() == server.data["/model"]


@pytest.mark.parametrize("ignore_range", [False, True])
def test_network_drop_resumes_or_restarts_if_range_ignored(tmp_path, server, ignore_range):
    asset = raw(server, b"test" * 200000)
    server.drop, server.ignore_range = True, ignore_range
    manager = AssetDownloader(tmp_path / "models", (asset,))
    manager.download(lambda *_: None, threading.Event())
    assert server.requests[1][1] == f"bytes={module.CHUNK_SIZE}-"
    assert (manager.path / "model.bin").read_bytes() == server.data["/model"]


def test_resume_across_mirrors_and_reject_wrong_range(tmp_path, server):
    asset = raw(server, b"a" * 800000, urls=(server.url + "/offline", server.url + "/model"))
    manager = AssetDownloader(tmp_path / "models", (asset,))
    manager.staging.mkdir()
    (manager.staging / "model.part").write_bytes(b"a" * 1000)
    manager.download(lambda *_: None, threading.Event())
    assert [p for p, _ in server.requests] == ["/offline"] * 3 + ["/model"]
    assert server.requests[-1][1] == "bytes=1000-"
    assert manager.last_download_urls == {"model": server.url + "/model"}
    bad = AssetDownloader(tmp_path / "bad", (asset,))
    bad.staging.mkdir()
    (bad.staging / "model.part").write_bytes(b"a" * 1000)
    server.bad_range = True
    with pytest.raises(DownloadError, match="续传范围") as exc:
        bad.download(lambda *_: None, threading.Event())
    assert exc.value.code == "网络失败" and not bad.staging.exists() and not bad.path.exists()


def test_checksum_failure_tries_next_mirror(tmp_path, server):
    asset = raw(server, urls=(server.url + "/bad", server.url + "/model"))
    server.data["/bad"] = b"x" * asset.size
    manager = AssetDownloader(tmp_path / "models", (asset,))
    manager.download(lambda *_: None, threading.Event())
    assert [p for p, _ in server.requests] == ["/bad", "/model"]
    assert manager.status()["installed"]


@pytest.mark.parametrize("failure", ["checksum", "network", "cancel", "disk", "write_disk"])
def test_failure_leaves_existing_install_untouched_and_no_staging(tmp_path, monkeypatch, server, failure):
    asset = raw(server, b"a" * 600000)
    manager = AssetDownloader(tmp_path / "models", (asset,))
    manager.path.mkdir()
    (manager.path / "previous").write_bytes(b"old model")
    cancel = threading.Event()
    if failure == "checksum":
        server.data["/model"] = b"b" * asset.size
    elif failure == "network":
        server.data.clear()
    elif failure == "disk":
        monkeypatch.setattr(module.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    elif failure == "write_disk":
        monkeypatch.setattr(module.os, "fsync", lambda _: (_ for _ in ()).throw(OSError(errno.ENOSPC, "disk full")))
    def progress(done, total):
        if failure == "cancel" and done:
            cancel.set()
    with pytest.raises(DownloadError) as exc:
        manager.download(progress, cancel)
    assert exc.value.code == {"checksum": "校验失败", "network": "网络失败", "cancel": "已取消",
                              "disk": "磁盘不足", "write_disk": "磁盘不足"}[failure]
    assert sorted(p.name for p in manager.path.iterdir()) == ["previous"]
    assert (manager.path / "previous").read_bytes() == b"old model"
    assert not manager.staging.exists() and not manager.backup.exists()


def archive_bytes(kind, files):
    output = io.BytesIO()
    with (zipfile.ZipFile(output, "w") if kind == "zip" else tarfile.open(fileobj=output, mode="w:bz2")) as arc:
        for name, content in files:
            if kind == "zip":
                arc.writestr(name, content)
            else:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                arc.addfile(info, io.BytesIO(content))
    return output.getvalue()


@pytest.mark.parametrize("kind", ["zip", "tar.bz2"])
@pytest.mark.parametrize("defect", [None, "missing", "hash", "duplicate", "size"])
def test_archive_allowlist_validation_and_complete_publication(tmp_path, server, kind, defect):
    files = [("pack/a", b"aaaa"), ("pack/b", b"bbb"), ("../../escape", b"ignored")]
    if defect == "missing": files.pop(0)
    if defect == "hash": files[0] = ("pack/a", b"xxxx")
    if defect == "size": files[0] = ("pack/a", b"xx")
    if defect == "duplicate": files.append(files[0])
    if kind == "zip" and defect == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate name"):
            content = archive_bytes(kind, files)
    else:
        content = archive_bytes(kind, files)
    server.data["/archive"] = content
    asset = DownloadAsset("archive", (server.url + "/archive",), len(content), sha(content), "archive",
        kind, (ArchiveMember("pack/a", 4, sha(b"aaaa"), "a"), ArchiveMember("pack/b", 3, sha(b"bbb"), "nested/b")))
    manager = AssetDownloader(tmp_path / "models", (asset, raw(server)))
    if defect:
        with pytest.raises(DownloadError) as exc:
            manager.download(lambda *_: None, threading.Event())
        assert exc.value.code == "校验失败" and not manager.path.exists()
    else:
        manager.download(lambda *_: None, threading.Event())
        assert manager.status()["installed"] and (manager.path / "nested/b").read_bytes() == b"bbb"
        assert not (manager.path / "archive").exists()
    assert not (tmp_path / "escape").exists() and not manager.staging.exists()


def test_publish_failure_rolls_back_entire_old_directory(tmp_path, server, monkeypatch):
    manager = AssetDownloader(tmp_path / "models", (raw(server),))
    manager.path.mkdir()
    (manager.path / "old").write_text("previous complete set")
    original = module.rename_with_retry
    def replace(source, destination, **kwargs):
        if Path(source).name == "ready":
            assert (manager.backup / "old").read_text() == "previous complete set"
            assert (Path(source) / "installed.json").is_file()
            raise PermissionError("explicit simulated publish failure")
        return original(source, destination, **kwargs)
    monkeypatch.setattr(module, "rename_with_retry", replace)
    with pytest.raises(DownloadError):
        manager.download(lambda *_: None, threading.Event())
    assert (manager.path / "old").read_text() == "previous complete set"
    assert not manager.staging.exists() and not manager.backup.exists()


def test_cancel_before_commit_and_concurrent_install(tmp_path, server, monkeypatch):
    manager = AssetDownloader(tmp_path / "models", (raw(server),))
    other = AssetDownloader(manager.path, manager.assets)
    cancel = threading.Event()
    original = manager.verify
    def verify(*args, **kwargs):
        with pytest.raises(DownloadError) as exc:
            other.download(lambda *_: None, threading.Event())
        assert exc.value.code == "下载忙"
        original(*args, **kwargs)
        cancel.set()
    monkeypatch.setattr(manager, "verify", verify)
    with pytest.raises(DownloadError) as exc:
        manager.download(lambda *_: None, cancel)
    assert exc.value.code == "已取消" and not manager.path.exists() and not manager.staging.exists()


def test_callback_failure_cleans_cache_and_unlocks(tmp_path, server):
    manager = AssetDownloader(tmp_path / "models", (raw(server),))
    def failed_callback(*_):
        raise RuntimeError("explicit progress callback failure")
    with pytest.raises(RuntimeError):
        manager.download(failed_callback, threading.Event())
    assert not manager.path.exists() and not manager.staging.exists()
    manager.download(lambda *_: None, threading.Event())
    assert manager.status()["installed"]


def test_download_bypasses_a_cached_proxy_without_changing_it(tmp_path, monkeypatch, server):
    proxy_requests = []

    class ProxyHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            proxy_requests.append(self.path)
            self.send_error(502, "explicit local proxy response")

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    previous_opener = urllib.request._opener
    poisoned_opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{proxy.server_port}"})
    )
    monkeypatch.setattr(urllib.request, "_opener", poisoned_opener)
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda _host: False)

    try:
        asset = raw(server)
        request = urllib.request.Request(server.url + "/model")
        with pytest.raises(urllib.error.HTTPError, match="502"):
            urllib.request.urlopen(request, timeout=2)
        assert proxy_requests and server.requests == []

        manager = AssetDownloader(tmp_path / "models", (asset,))
        manager.download(lambda *_: None, threading.Event())

        assert server.requests == [("/model", None)]
        assert len(proxy_requests) == 1
        assert urllib.request._opener is poisoned_opener
    finally:
        proxy.shutdown()
        proxy.server_close()
        proxy_thread.join()
        assert urllib.request._opener is poisoned_opener
        assert previous_opener is not poisoned_opener
