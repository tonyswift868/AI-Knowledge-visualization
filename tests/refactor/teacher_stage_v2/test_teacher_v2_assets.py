import json
import os
from pathlib import Path
import threading
import time

import pytest
from PySide6.QtCore import QByteArray, QObject, QUrl
from PySide6.QtWidgets import QApplication

from refactor.learning.app.bridge import AppBridge
from refactor.learning.app.teacher_assets import TeacherAssetCatalog, TeacherAssets
from refactor.learning.app.window import _LocalOnly


@pytest.fixture
def package(tmp_path):
    root = tmp_path / "public"
    model = root / "teacher2d/tsubaki-live2d"
    (model / "textures").mkdir(parents=True)
    (model / "椿.moc3").write_bytes(b"explicit fake moc")
    (model / "textures/椿.png").write_bytes(b"explicit fake texture")
    (model / "physics.json").write_text("{}")
    (model / "idle.motion3.json").write_text('{"Version":3,"Meta":{"Duration":6}}')
    (model / "unrelated.json").write_text('{"private": true}')
    (model / "unrelated.js").write_text("window.doNotRun = true")
    (model / "manifest.json").write_text(json.dumps({"id": "tsubaki-live2d", "renderer": "live2d2d",
        "skeleton": "/teacher2d/tsubaki-live2d/model.model3.json"}), encoding="utf-8")
    settings = {"Version": 3, "FileReferences": {"Moc": "椿.moc3", "Textures": ["textures/椿.png"],
                 "Physics": "physics.json", "Motions": {"Idle": [{"File": "idle.motion3.json"}]}}}
    (model / "model.model3.json").write_text(json.dumps(settings), encoding="utf-8")
    core = tmp_path / "vendor/core.js"
    core.parent.mkdir()
    core.write_text("// explicit fake cubism core")
    return root, model, core


def catalog(package):
    root, _, core = package
    return TeacherAssetCatalog(root, core=core)


def test_teacher_v2_ready_manifest_only_referenced_files_and_registered_motion(package):
    assets = catalog(package)
    result = assets.status()
    assert result["ready"] and result["manifest"]["id"] == "tsubaki-live2d"
    url = QUrl(result["manifest"]["skeleton"])
    resource = assets.resolve(url)
    settings = json.loads(resource.content)
    assert settings["FileReferences"]["Motions"] == {"Idle": [{"File": "idle.motion3.json"}]}
    assert assets.resolve(url.resolved(QUrl("idle.motion3.json"))).mime == "application/json"
    assert assets.resolve(url.resolved(QUrl("physics.json"))).mime == "application/json"
    assert assets.resolve(url.resolved(QUrl("textures/%E6%A4%BF.png"))).mime == "image/png"
    assert assets.resolve(url.resolved(QUrl("椿.moc3"))).mime == "application/octet-stream"
    assert assets.resolve(QUrl(result["manifest"]["live2d"]["coreScript"])).mime == "text/javascript"
    for tail in ("unrelated.json", "unrelated.js", "old-shake.motion3.json", "manifest.json"):
        assert assets.resolve(url.resolved(QUrl(tail))) is None
    assert "D:" not in json.dumps(result)


def test_teacher_v2_keeps_existing_idle_cry_and_physics_references(package):
    _, model, _ = package
    path = model / "model.model3.json"
    settings = json.loads(path.read_text())
    settings["FileReferences"]["Motions"]["Cry"] = [{"File": "idle.motion3.json"}]
    path.write_text(json.dumps(settings))
    assets = catalog(package)
    result = assets.status()
    assert result["ready"]
    url = QUrl(result["manifest"]["skeleton"])
    refs = json.loads(assets.resolve(url).content)["FileReferences"]
    assert refs == settings["FileReferences"]
    assert assets.resolve(url.resolved(QUrl(refs["Physics"])))
    assert assets.resolve(url.resolved(QUrl(refs["Motions"]["Cry"][0]["File"])))


@pytest.mark.parametrize("motions", [
    {"Unexpected": [{"File": "idle.motion3.json"}]},
    {"Idle": [{"File": "../outside.json"}]},
    {"Cry": [{"File": "https://example.invalid/motion.json"}]},
    {"Idle": [{"File": "missing.motion3.json"}]},
    {"Idle": [None]}, {"Idle": {}}, [],
])
def test_teacher_v2_motion_registration_uses_existing_reference_checks(package, motions):
    _, model, _ = package
    path = model / "model.model3.json"
    settings = json.loads(path.read_text())
    settings["FileReferences"]["Motions"] = motions
    path.write_text(json.dumps(settings))
    assets = catalog(package)
    result = assets.status()
    assert not result["ready"] and result["code"] == "LIVE2D_ASSET_INVALID"
    assert not assets._files


@pytest.mark.parametrize("attack", ["../outside.json", "%2e%2e/outside.json", "%252e%252e/outside.json",
    "textures/..%5coutside.json", "C:%5csecret.txt", "//elsewhere/share", "textures/椿.png?x=1",
    "textures/椿.png#fragment", "textures/椿.png%00", "textures/椿.png:stream"])
def test_teacher_v2_rejects_traversal_and_noncanonical_paths(package, attack):
    assets = catalog(package)
    url = assets.status()["manifest"]["skeleton"]
    base = url.rsplit("/", 1)[0]
    assert assets.resolve(QUrl(base + "/" + attack)) is None
    assert assets.resolve(QUrl(url.replace("//assets/", "//stage/"))) is None
    assert assets.resolve(QUrl(url.replace("//assets/", "//user@assets/"))) is None
    assert assets.resolve(QUrl(url.replace("//assets/", "//assets:80/"))) is None


@pytest.mark.parametrize("value", ["../outside.moc3", "https://example.invalid/model.moc3", "C:/file.moc3", "\\\\server\\file.moc3"])
def test_teacher_v2_rejects_manifest_reference_escape(package, value):
    assets = catalog(package)
    _, model, _ = package
    path = model / "model.model3.json"
    content = json.loads(path.read_text())
    content["FileReferences"]["Moc"] = value
    path.write_text(json.dumps(content))
    result = assets.status()
    assert not result["ready"] and result["code"] == "LIVE2D_ASSET_INVALID"
    assert not assets._files


@pytest.mark.parametrize("missing", ["manifest.json", "椿.moc3", "textures/椿.png", "model.model3.json"])
def test_teacher_v2_missing_file_never_claims_ready(package, missing):
    assets = catalog(package)
    old_url = assets.status()["manifest"]["skeleton"]
    (package[1] / missing).unlink()
    result = assets.status()
    assert not result["ready"] and result["manifest"] is None
    assert "选择" in result["installation"]["message"]
    assert assets.resolve(QUrl(old_url)) is None


def test_teacher_v2_direct_package_and_no_other_character_substitution(package):
    _, model, core = package
    assets = TeacherAssetCatalog(model, core=core)
    assert assets.status()["ready"]
    manifest = json.loads((model / "manifest.json").read_text())
    manifest["id"] = "other-character"
    (model / "manifest.json").write_text(json.dumps(manifest))
    assert not assets.status()["ready"]


def test_teacher_v2_link_outside_root_is_denied_after_discovery(package, tmp_path):
    assets = catalog(package)
    url = QUrl(assets.status()["manifest"]["skeleton"])
    texture_url = url.resolved(QUrl("textures/椿.png"))
    folder = package[1] / "textures"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "椿.png").write_bytes(b"not public")
    folder.rename(package[1] / "original-textures")
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(folder))
    else:
        folder.symlink_to(outside, target_is_directory=True)
    try:
        assert assets.resolve(texture_url) is None
        assert not assets.status()["ready"]
    finally:
        if os.name == "nt":
            folder.rmdir()  # Remove this junction only, never its target.
        else:
            folder.unlink()
    assert (outside / "椿.png").read_bytes() == b"not public"


class Profile(QObject):
    def installUrlSchemeHandler(self, scheme, handler):
        self.scheme, self.handler = scheme, handler


class Job(QObject):
    def __init__(self, url, method=b"GET"):
        super().__init__()
        self.url, self.method, self.body, self.failure = url, method, None, None
    def requestUrl(self): return self.url
    def requestMethod(self): return QByteArray(self.method)
    def fail(self, code): self.failure = code
    def reply(self, mime, device):
        self.body = bytes(device.readAll())
        self.mime = bytes(mime)


def test_teacher_v2_handler_and_interceptor_enforce_same_allowlist(package, tmp_path):
    assets = catalog(package)
    status = assets.status()
    profile = Profile()
    handler = TeacherAssets(profile, catalog=assets)
    url = QUrl(status["manifest"]["skeleton"])
    job = Job(url)
    handler.requestStarted(job)
    assert job.body and job.failure is None and job.mime == b"application/json"
    denied = Job(url, b"POST")
    handler.requestStarted(denied)
    assert denied.failure is not None and denied.body is None

    class Info:
        def __init__(self, u): self.url, self.blocked = u, False
        def requestUrl(self): return self.url
        def requestMethod(self): return QByteArray(b"GET")
        def block(self, blocked): self.blocked = blocked
    interceptor = _LocalOnly((tmp_path / "dist",), teacher_assets=handler)
    for candidate, blocked in ((url, False), (QUrl.fromLocalFile(str(package[1] / "椿.moc3")), True),
        (QUrl("https://example.invalid/image.png"), True), (url.resolved(QUrl("unrelated.json")), True)):
        info = Info(candidate)
        interceptor.interceptRequest(info)
        assert info.blocked == blocked


def test_teacher_v2_bridge_status_runs_off_ui_and_picker_is_ui_only(package, monkeypatch):
    app = QApplication.instance() or QApplication([])
    profile, bridge = Profile(), AppBridge()
    assets = catalog(package)
    handler = TeacherAssets(profile, catalog=assets)
    handler.register(bridge, None)
    threads, sent = [], []
    original = assets.status
    def checked():
        threads.append(threading.current_thread() is threading.main_thread())
        return original()
    monkeypatch.setattr(assets, "status", checked)
    bridge.event.connect(lambda s: sent.append(json.loads(s)))
    try:
        bridge.call(json.dumps({"id": "assets", "method": "teacher.assets", "params": {}}))
        bridge.call(json.dumps({"id": "invalid", "method": "teacher.choose_assets", "params": {"path": "C:/secret"}}))
        until = time.monotonic() + 5
        while len(sent) < 2 and time.monotonic() < until:
            app.processEvents(); time.sleep(.01)
        assert threads == [False]
        messages = {s["id"]: s for s in sent}
        assert messages["assets"]["result"]["ready"]
        assert messages["invalid"]["error"]["code"] == "INVALID_PARAMS"
        assert bridge._handlers["teacher.choose_assets"].ui
    finally:
        bridge.close()
