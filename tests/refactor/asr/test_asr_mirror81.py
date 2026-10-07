"""Offline checks for pinned ASR mirrors, source reporting and fallback."""
import hashlib
import io
import threading
import urllib.error

import pytest

from refactor.learning.voice.asr import model_manager as model
from refactor.learning.voice.asr.errors import DownloadCancelled


MIRROR = "https://ghfast.top/https://github.com/test/model.onnx"
ORIGIN = "https://github.com/test/model.onnx"
CONTENT = b"pinned-model-bytes"


class Response(io.BytesIO):
    def __init__(self, data, url, offset=0):
        super().__init__(data)
        self.url = url
        self.status = 206 if offset else 200
        self.headers = {"Content-Range": f"bytes {offset}-{len(CONTENT)-1}/{len(CONTENT)}"} if offset else {}

    def geturl(self):
        return self.url


@pytest.fixture
def mirror_manager(tmp_path, monkeypatch):
    asset = model.DownloadAsset("test.onnx", len(CONTENT), hashlib.sha256(CONTENT).hexdigest(),
                                ORIGIN, (MIRROR,))
    monkeypatch.setattr(model, "ASSETS", (asset,))
    monkeypatch.setattr(model, "SENSEVOICE_ARCHIVE", model.DownloadAsset("unused.tar.bz2", 1, "0"*64, ORIGIN, (MIRROR,)))
    return model.ModelManager(tmp_path)


def test_mirror_config_preserves_pinned_assets_and_idle_timeout(tmp_path):
    downloader = model._downloader(tmp_path / "models")
    assert downloader.timeout == 30 and downloader.fallback_on_network_error
    assert downloader.keep_partial_on_network_error
    assert downloader.size_bytes == 163646737
    assert [a.sha256 for a in downloader.assets] == [
        "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
        "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    ]
    for asset in downloader.assets:
        assert asset.urls == ("https://ghfast.top/" + model.RELEASE_URL + asset.name,
                              model.RELEASE_URL + asset.name)
    assert downloader.required == model.REQUIRED_FILES


@pytest.mark.parametrize("failure", [None, "connection", "idle", "checksum"])
def test_mirror_first_source_visible_before_connect_and_origin_fallback(mirror_manager, monkeypatch, failure):
    attempts, displayed = [], []

    def open_url(request, timeout):
        attempts.append(request.full_url)
        assert timeout == 30
        assert mirror_manager.status()["download_url"] == request.full_url
        if request.full_url == MIRROR:
            if failure == "connection":
                raise urllib.error.URLError("offline mirror")
            if failure == "idle":
                raise TimeoutError("30 seconds without data")
            if failure == "checksum":
                return Response(b"x" * len(CONTENT), request.full_url)
        return Response(CONTENT, request.full_url)

    monkeypatch.setattr(model.urllib.request, "urlopen", open_url)
    mirror_manager.download(lambda *_: displayed.append(mirror_manager.status()["download_url"]), threading.Event())
    assert attempts == ([MIRROR, ORIGIN] if failure else [MIRROR])
    assert displayed[0] == MIRROR
    assert displayed[-1] == (ORIGIN if failure else MIRROR)
    assert mirror_manager.status()["installed"]
    assert (mirror_manager.path / "test.onnx").read_bytes() == CONTENT


def test_stalled_mirror_partial_resumes_on_origin(mirror_manager, monkeypatch):
    calls = []

    class Stalled(Response):
        def read1(self, size):
            if self.tell():
                raise TimeoutError("stalled after initial bytes")
            return self.read(3)

    def open_url(request, timeout):
        calls.append((request.full_url, request.get_header("Range")))
        if request.full_url == MIRROR:
            return Stalled(CONTENT, MIRROR)
        assert request.get_header("Range") == "bytes=3-"
        return Response(CONTENT[3:], ORIGIN, offset=3)

    monkeypatch.setattr(model.urllib.request, "urlopen", open_url)
    mirror_manager.download(lambda *_: None, threading.Event())
    assert calls == [(MIRROR, None), (ORIGIN, "bytes=3-")]
    assert (mirror_manager.path / "test.onnx").read_bytes() == CONTENT


def test_cancel_does_not_try_origin(mirror_manager, monkeypatch):
    cancel, attempts = threading.Event(), []

    class Cancelling(Response):
        def read1(self, size):
            cancel.set()
            return self.read(3)

    def open_url(request, timeout):
        attempts.append(request.full_url)
        return Cancelling(CONTENT, request.full_url)

    monkeypatch.setattr(model.urllib.request, "urlopen", open_url)
    with pytest.raises(DownloadCancelled):
        mirror_manager.download(lambda *_: None, cancel)
    assert attempts == [MIRROR]
    assert not mirror_manager._staging.exists()
    assert not mirror_manager.path.exists()


def test_cancel_at_source_notification_never_connects(mirror_manager, monkeypatch):
    cancel = threading.Event()
    monkeypatch.setattr(model.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("cancelled before connecting"))
    with pytest.raises(DownloadCancelled):
        mirror_manager.download(lambda *_: cancel.set(), cancel)
    assert not mirror_manager._staging.exists()


def test_bad_partial_mirror_does_not_poison_origin_resume(mirror_manager, monkeypatch):
    calls = []

    class BrokenMirror(Response):
        def read1(self, size):
            if self.tell():
                raise TimeoutError("mirror interrupted after corrupt prefix")
            return self.read(3)

    def open_url(request, timeout):
        calls.append((request.full_url, request.get_header("Range")))
        if request.full_url == MIRROR:
            return BrokenMirror(b"bad" + CONTENT[3:], MIRROR)
        offset = 3 if request.get_header("Range") else 0
        return Response(CONTENT[offset:], ORIGIN, offset=offset)

    monkeypatch.setattr(model.urllib.request, "urlopen", open_url)
    mirror_manager.download(lambda *_: None, threading.Event())
    assert calls == [(MIRROR, None), (ORIGIN, "bytes=3-"), (ORIGIN, None)]
    assert (mirror_manager.path / "test.onnx").read_bytes() == CONTENT
