import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
import socket
import tarfile
import threading

import pytest

from refactor.learning.voice.asr import model_manager as module
from refactor.learning.voice.asr.errors import AsrError, DownloadCancelled


@pytest.fixture
def model_server(monkeypatch):
    files = {"model.int8.onnx": bytes(range(256)) * 20, "tokens.txt": b"word 0\n", "silero_vad.onnx": b"VAD" * 500}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as archive:
        for name in ("model.int8.onnx", "tokens.txt"):
            entry = tarfile.TarInfo(f"{module.MODEL_NAME}/{name}")
            entry.size = len(files[name])
            archive.addfile(entry, io.BytesIO(files[name]))
        # A downloader must never extract unrelated archive members.
        entry = tarfile.TarInfo("../../escaped.txt")
        entry.size = 6
        archive.addfile(entry, io.BytesIO(b"escape"))
    archive_name = module.SENSEVOICE_ARCHIVE.name
    payloads = {archive_name: buffer.getvalue(), "silero_vad.onnx": files["silero_vad.onnx"]}
    state = {"requests": [], "drop": False, "block": False, "corrupt": False, "ignore_range": False, "bad_range": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            name = self.path.lstrip("/")
            offset = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
            state["requests"].append((name, offset))
            if state["block"]:
                self.send_error(503)
                return
            content = payloads[name]
            if state["corrupt"]:
                content = bytes([content[0] ^ 0xff]) + content[1:]
            if state["ignore_range"]:
                offset = 0
            self.send_response(206 if offset else 200)
            self.send_header("Content-Length", str(len(content) - offset))
            if offset:
                start = offset + 1 if state["bad_range"] else offset
                self.send_header("Content-Range", f"bytes {start}-{len(content)-1}/{len(content)}")
            self.end_headers()
            if state["drop"]:
                state["drop"] = False
                self.wfile.write(content[offset:offset + len(content) // 2])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True
                state["block"] = True
            else:
                self.wfile.write(content[offset:])

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    assets = tuple(module.DownloadAsset(name, len(raw), hashlib.sha256(raw).hexdigest(),
                    f"http://127.0.0.1:{server.server_port}/{name}") for name, raw in payloads.items())
    monkeypatch.setattr(module, "ASSETS", assets)
    monkeypatch.setattr(module, "SENSEVOICE_ARCHIVE", assets[0])
    monkeypatch.setattr(module, "SILERO_VAD", assets[1])
    monkeypatch.setattr(module, "REQUIRED_FILES", {name: (len(raw), hashlib.sha256(raw).hexdigest()) for name, raw in files.items()})
    try:
        yield files, state, assets
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_status_does_not_download_or_create_directories(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("status must be local")
    monkeypatch.setattr(module.urllib.request, "urlopen", forbidden)
    manager = module.ModelManager(tmp_path)
    status = manager.status()
    assert status["installed"] is False
    assert status["size_bytes"] == 163646737
    assert manager.path == tmp_path / "models" / "asr" / module.MODEL_NAME
    assert not list(tmp_path.iterdir())


def test_verified_install_progress_and_receipt(tmp_path, model_server):
    files, state, assets = model_server
    progress = []
    manager = module.ModelManager(tmp_path)
    manager.download(lambda *value: progress.append(value), threading.Event())
    total = sum(a.size for a in assets)
    assert progress[-1] == (total, total)
    assert all(0 <= value <= total and limit == total for value, limit in progress)
    assert manager.status()["installed"]
    assert not manager._staging.exists()
    for name, raw in files.items():
        assert (manager.path / name).read_bytes() == raw
    assert not list(tmp_path.rglob("escaped.txt"))
    requests = len(state["requests"])
    manager.download(lambda *_: None, threading.Event())
    assert len(state["requests"]) == requests
    (manager.path / "tokens.txt").write_bytes(b"broken")
    assert not manager.status()["installed"]


def test_checksum_failure_never_installs(tmp_path, model_server):
    _, state, _ = model_server
    state["corrupt"] = True
    manager = module.ModelManager(tmp_path)
    with pytest.raises(AsrError, match="SHA256"):
        manager.download(lambda *_: None, threading.Event())
    assert not manager.status()["installed"]
    assert not manager.path.exists()
    assert not list(tmp_path.rglob("*.part"))


def test_cancel_removes_partial_download(tmp_path, model_server):
    manager = module.ModelManager(tmp_path)
    cancel = threading.Event()
    def progress(done, total):
        if done:
            cancel.set()
    with pytest.raises(DownloadCancelled):
        manager.download(progress, cancel)
    assert not manager._staging.exists()
    assert not manager.path.exists()


@pytest.mark.parametrize("ignore_range", [False, True])
def test_resume_after_network_failure_in_new_manager(tmp_path, model_server, ignore_range):
    files, state, _ = model_server
    state["drop"] = True
    manager = module.ModelManager(tmp_path)
    with pytest.raises(AsrError, match="失败|中断"):
        manager.download(lambda *_: None, threading.Event())
    partials = list(manager._staging.glob("*.part"))
    assert len(partials) == 1 and partials[0].stat().st_size > 0
    assert not manager.status()["installed"]
    state["block"] = False
    state["ignore_range"] = ignore_range
    manager = module.ModelManager(tmp_path)
    manager.download(lambda *_: None, threading.Event())
    assert any(offset > 0 for _, offset in state["requests"])
    assert manager.status()["installed"]
    assert (manager.path / "model.int8.onnx").read_bytes() == files["model.int8.onnx"]


def test_bad_range_is_rejected(tmp_path, model_server):
    _, state, assets = model_server
    manager = module.ModelManager(tmp_path)
    manager._staging.mkdir(parents=True)
    (manager._staging / (assets[0].name + ".part")).write_bytes(b"partial")
    state["bad_range"] = True
    with pytest.raises(AsrError, match="续传范围"):
        manager.download(lambda *_: None, threading.Event())
    assert not manager.status()["installed"]


def test_cancel_before_retry_also_cleans_saved_parts(tmp_path, model_server):
    manager = module.ModelManager(tmp_path)
    manager._staging.mkdir(parents=True)
    (manager._staging / "old.part").write_bytes(b"partial")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DownloadCancelled):
        manager.download(lambda *_: None, cancel)
    assert not manager._staging.exists()


def test_explicit_redownload_repairs_same_size_and_timestamp_corruption(tmp_path, model_server):
    files, state, _ = model_server
    manager = module.ModelManager(tmp_path)
    manager.download(lambda *_: None, threading.Event())
    path = manager.path / "model.int8.onnx"
    stat = path.stat()
    path.write_bytes(b"x" * stat.st_size)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert manager.status()["installed"]  # Cheap status; worker uses full SHA256.
    with pytest.raises(AsrError, match="校验"):
        module.verify_model_path(manager.path)
    prior_requests = len(state["requests"])
    manager.download(lambda *_: None, threading.Event())
    assert len(state["requests"]) > prior_requests
    assert path.read_bytes() == files["model.int8.onnx"]
    assert manager.status()["installed"]
