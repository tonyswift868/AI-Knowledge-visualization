"""New UI packaging/storage adapters, with explicit local substitutes only."""
import base64
import hashlib
import json
from pathlib import Path
import threading
import time
import zipfile

import pytest

from refactor.learning.voice.download import AssetDownloader, DownloadAsset, DownloadError
from refactor.learning.voice.storage import save_storage, storage_root
from refactor.learning.voice.tts.component_manager import ComponentManager
from scripts.build_learning_installer import validate_feed, read_key, sign


def test_learning_delivery_qt_build_intermediates_are_not_runtime_data():
    from scripts.build_learning_installer import include_payload_data
    assert not include_payload_data('PySide6/qml/Qt/labs/assetdownloader/objects-Debug/QmlAssetDownloaderPrivate_resources_1/.qt/rcc/qrc.cpp.obj')
    assert include_payload_data('PySide6/qml/QtQuick/Controls/Material/Button.qml')
    assert include_payload_data('PySide6/resources/qtwebengine_resources.pak')


def test_learning_delivery_bundles_course_data_read_from_source_tree():
    """CatalogClient and source_filter read course/data via Path(__file__) (BUG-40)."""
    root = Path(__file__).resolve().parents[3]
    script = (root / "scripts/build_learning_installer.py").read_text(encoding="utf-8")
    assert '"refactor/learning/course/data", "refactor/learning/course/data"' in script
    assert (root / "refactor/learning/course/data/source_lists.json").is_file()
    assert (root / "refactor/learning/course/data/model-prices.json").is_file()


def test_learning_delivery_voice_publisher_pins_only_inference_inputs(tmp_path):
    from scripts.build_learning_voice_assets import bundle
    source, target = tmp_path / 'source', tmp_path / 'output'
    target.mkdir()
    for name in ('runtime/python.exe', 'GPT_SoVITS/TTS_infer_pack/TTS.py',
                 'tests/a.py', 'GPT_SoVITS/pretrained_models/training.pth', 'logs/a.txt'):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'explicit substitute')
    row = bundle(source, target, 'engine', 'https://example.invalid/voice/v1')
    assert {m['path'] for m in row['members']} == {
        'engine/runtime/python.exe', 'engine/GPT_SoVITS/TTS_infer_pack/TTS.py'}
    assert row['sha256'] == hashlib.sha256((target / 'engine.zip').read_bytes()).hexdigest()
    with zipfile.ZipFile(target / 'engine.zip') as archive:
        for member in row['members']:
            assert member['sha256'] == hashlib.sha256(archive.read(member['path'])).hexdigest()


def test_learning_delivery_build_does_not_resolve_dlls_from_ambient_tools(tmp_path, monkeypatch):
    from scripts import build_learning_installer as builder
    monkeypatch.setenv("PATH", str(tmp_path / "unrelated-tools"))
    captured = {}
    monkeypatch.setattr(builder.subprocess, "run", lambda command, **kwargs: captured.update(kwargs))
    builder.run(["explicit-compiler"], tmp_path, tmp_path / "build.log")
    assert str(tmp_path / "unrelated-tools") not in captured["env"]["PATH"]
    assert "System32" in captured["env"]["PATH"]
    assert captured["env"]["TMP"] == str(tmp_path)


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Windows file sharing')
def test_learning_delivery_atomic_status_survives_native_reader(tmp_path):
    """A native reader without FILE_SHARE_DELETE reproduces Inno's race."""
    import ctypes
    from ctypes import wintypes
    from refactor.desktop.delivery.per_user_install import atomic
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    path = tmp_path / 'progress.ini'
    path.write_bytes(b'old')
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    reader = threading.Thread(target=lambda: (time.sleep(.08), kernel.CloseHandle(handle)))
    reader.start()
    try:
        started = time.monotonic()
        atomic(path, b'new')
        assert time.monotonic() - started >= .07
        assert path.read_bytes() == b'new'
        assert not list(tmp_path.glob('*.pending'))
    finally:
        reader.join()


def test_learning_delivery_storage_survives_reopen_and_is_component_scoped(tmp_path):
    data, selected = tmp_path / "Data", tmp_path / "voice-disk"
    assert storage_root(data, "tts") == data / "models"
    save_storage(data, "tts", selected)
    assert storage_root(data, "tts") == selected
    assert storage_root(data, "asr") == data / "models"
    with pytest.raises(ValueError):
        save_storage(data, "tts", "relative")


def test_learning_delivery_asr_selection_is_committed_only_after_success(tmp_path, monkeypatch):
    from refactor.learning.voice.asr import model_manager as asr
    from refactor.learning.voice.asr.errors import AsrError
    calls = []
    class FakeDownloader:
        def __init__(self, path): self.path = path
        def download(self, progress, cancel):
            calls.append(self.path)
            if cancel.is_set(): raise DownloadError('已取消', '已取消')
    monkeypatch.setattr(asr, '_downloader', FakeDownloader)
    data, selected = tmp_path / 'Data', tmp_path / 'chosen'
    manager = asr.ModelManager(data)
    original = manager.path
    manager.download_storage = selected
    cancel = threading.Event(); cancel.set()
    with pytest.raises(AsrError): manager.download(lambda *_: None, cancel)
    assert manager.path == original
    assert storage_root(data, 'asr') == data / 'models'
    manager.download_storage = selected
    manager.download(lambda *_: None, threading.Event())
    assert manager.path == selected / 'asr' / asr.MODEL_NAME
    assert asr.ModelManager(data).path == manager.path
    assert calls == [manager.path, manager.path]


def test_learning_delivery_frozen_tts_never_discovers_developer_assets(tmp_path, monkeypatch):
    from refactor.learning.voice.tts.config import TtsConfig
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    config = TtsConfig.from_env(tmp_path)
    root = tmp_path / "models/tts/components-v1"
    assert config.engine_root == root / "engine"
    assert config.voice_manifest == root / "voice/voice-pack.json"
    assert config.base_assets()["bert_root"] == root / "base/chinese-roberta-wwm-ext-large"
    monkeypatch.setenv("MF_TTS_ENGINE_ROOT", str(tmp_path / "explicit-engine"))
    assert TtsConfig.from_env(tmp_path).engine_root == tmp_path / "explicit-engine"


def test_learning_delivery_frozen_data_is_outside_replaceable_app(tmp_path, monkeypatch):
    from refactor.desktop import shell_paths
    import sys
    import os
    import tempfile
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "MentorFlow/App/MentorFlow.exe"))
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(shell_paths, "_build_ca_bundle", lambda root: root / "trust.pem")
    runtime = shell_paths.prepare_shell_runtime()
    assert runtime.data_root == tmp_path / "MentorFlow/Data"
    assert Path(os.environ["TEMP"]) == runtime.data_root / "temp"
    assert runtime.cache_root == runtime.data_root / "cache"
    assert Path(os.environ["LOCALAPPDATA"]) == tmp_path / ".MentorFlow-runtime"
    assert not Path(os.environ["LOCALAPPDATA"]).is_relative_to(tmp_path / "MentorFlow")


def test_learning_delivery_custom_models_inside_data_are_disjoint_from_runtime(tmp_path, monkeypatch):
    from refactor.learning.voice.tts.config import TtsConfig, TtsError
    engine = tmp_path / "custom/tts/components-v1/engine"
    (engine / "runtime").mkdir(parents=True)
    (engine / "runtime/python.exe").write_bytes(b"explicit substitute")
    (engine / "GPT_SoVITS/TTS_infer_pack").mkdir(parents=True)
    (engine / "GPT_SoVITS/TTS_infer_pack/TTS.py").touch()
    base = engine.parent / "base"
    for name in ("chinese-roberta-wwm-ext-large/a", "chinese-hubert-base/a",
                 "G2PWModel/a", "sv/pretrained_eres2netv2w24s4ep4.ckpt"):
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"explicit substitute")
    config = TtsConfig(engine_root=engine, engine_python=engine / "runtime/python.exe",
                       data_root=tmp_path, base_model_root=base,
                       catalog=engine.parent / "voice/catalog.json", pack_roots=())
    sentinel = object()
    monkeypatch.setattr(TtsConfig, "voice", lambda self: sentinel)
    assert config.validate() is sentinel
    from dataclasses import replace
    with pytest.raises(TtsError, match="重叠"):
        replace(config, base_model_root=tmp_path / "tts/base").validate()


def test_learning_delivery_catalog_install_is_atomic_and_relocatable(tmp_path):
    """The real shared extractor/hash/install code handles all three archives."""
    catalog = tmp_path / "catalog.json"
    assets = []
    for label in ("engine", "base", "voice"):
        name, content = f"{label}/explicit-test-substitute.txt", label.encode()
        archive = tmp_path / (label + ".zip")
        with zipfile.ZipFile(archive, "w") as target:
            target.writestr(name, content)
        assets.append(dict(name=archive.name, urls=["http://127.0.0.1:1/" + archive.name],
                           size=archive.stat().st_size, sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                           members=[dict(path=name, size=len(content), sha256=hashlib.sha256(content).hexdigest())]))
    catalog.write_text(json.dumps(dict(schema="mentorflow.tts-components.v1", assets=assets)))
    data, chosen = tmp_path / "Data", tmp_path / "chosen"
    manager = ComponentManager(data, storage=chosen, catalog=catalog)
    assert not manager.status()["installed"]
    # Exercise the real local HTTP/extraction/transaction path.
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from functools import partial
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        for asset in assets:
            asset["urls"] = [f"http://127.0.0.1:{server.server_port}/" + asset["name"]]
        catalog.write_text(json.dumps(dict(schema="mentorflow.tts-components.v1", assets=assets)))
        manager = ComponentManager(data, storage=chosen, catalog=catalog)
        manager.download(cancel=threading.Event())
        assert manager.status()["installed"]
        assert storage_root(data, "tts") == chosen
        assert ComponentManager(data, catalog=catalog).status()["installed"]
        cancelled = threading.Event(); cancelled.set()
        failed = ComponentManager(data, storage=tmp_path / "other", catalog=catalog)
        with pytest.raises(DownloadError):
            failed.download(cancel=cancelled)
        assert storage_root(data, "tts") == chosen
        assert not failed.path.exists()
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_learning_delivery_inventory_rejects_casefolded_parent_collisions(tmp_path):
    def asset(name, path):
        return DownloadAsset(name, ("https://example.invalid/asset",), 1, "0" * 64, path)
    for paths in (("Runtime/a", "runtime"), ("runtime", "RUNTIME/a"), ("Runtime/a", "runtime/A")):
        with pytest.raises(ValueError, match="冲突"):
            AssetDownloader(tmp_path, tuple(asset(str(i), p) for i, p in enumerate(paths)))


def test_learning_delivery_official_catalog_supports_mixed_transports(tmp_path):
    catalog = tmp_path / "catalog.json"
    rows = []
    for label, archive in (("engine", "7z"), ("base", None), ("voice", "zip")):
        member = dict(path="upstream/file", install_path=f"{label}/file", size=3, sha256="0" * 64)
        rows.append(dict(name=label + ".asset", urls=["https://huggingface.co/official/repo/resolve/pinned/file"],
                         size=5, sha256="0" * 64, archive=archive, install_path=f"{label}/file",
                         members=[member] if archive else []))
    catalog.write_text(json.dumps(dict(schema="mentorflow.tts-components.v1", assets=rows)))
    manager = ComponentManager(tmp_path / "Data", catalog=catalog)
    assert manager.component_sizes == dict(engine=5, base=5, voice=5)
    assert manager.required["base/file"] == (5, "0" * 64)
    assert manager.assets[0].archive == "7z"
    assert manager.assets[0].urls[1].startswith("https://hf-mirror.com/")
    assert not manager.status()["installed"]


def test_learning_delivery_update_feed_is_explicit_and_uat_is_isolated():
    assert not validate_feed("https://example.invalid/app/stable/stable.json", "MentorFlow").uat
    assert validate_feed("http://127.0.0.1:18791/stable/stable.json", "MentorFlow.UAT.Batch11").uat
    for feed in ("http://example.invalid/stable/stable.json", "https://localhost/stable/stable.json",
                 "https://example.invalid/stable.json", "https://user:secret@example.invalid/stable/stable.json"):
        with pytest.raises(ValueError):
            validate_feed(feed, "MentorFlow")


def test_learning_delivery_signing_rejects_wrong_private_key(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
    from types import SimpleNamespace
    public = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key = tmp_path / "public.json"
    key.write_text(json.dumps(dict(key_id="test-only", public_key=base64.b64encode(public).decode())))
    secret = tmp_path / "private"
    secret.write_bytes(Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    assert read_key(key) == ("test-only", public)
    output = tmp_path / "release.json"
    with pytest.raises(ValueError, match="mismatch"):
        sign(SimpleNamespace(public_key=key, private_key=secret, output=output, unsigned=tmp_path / "unsigned"))
    assert not output.exists()


def test_learning_delivery_update_worker_uses_existing_core_and_real_window(tmp_path):
    from PySide6.QtWidgets import QApplication, QWidget
    from refactor.desktop.updates import DesktopUpdates
    app = QApplication.instance() or QApplication([])
    gate = QWidget()
    calls = []
    class Core:
        def attach_shutdown_guard(self, _app): pass
        def get_startup_update_state(self, root, full, check_feed, cancel=None):
            # Signed local policy permits login while the feed refresh runs.
            calls.append(("startup", threading.get_ident()))
            return dict(state="HEALTHY", login_allowed=True)
        def check_latest(self, root, cancel=None): return dict(state="UP_TO_DATE")
        def update_to_latest(self, root, **kwargs):
            calls.append(("install", kwargs))
            return dict(state="APPLYING")
    adapter = DesktopUpdates(app, tmp_path, gate, core=Core())
    try:
        adapter.start()
        deadline = time.monotonic() + 4
        while adapter.state["state"] != "HEALTHY" and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.005)
        assert gate.isEnabled()
        assert calls[0][1] != threading.get_ident()
        adapter.run("install", gate)
        while adapter.snapshot()["busy"] and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.005)
        assert adapter.state["state"] == "APPLYING"
        assert calls[-1][1]["hwnd_hex"] == f"{int(gate.winId()):016x}"
        from refactor.desktop.delivery.update_session import UpdateSession
        from refactor.desktop.delivery.update_session import SessionEnding
        import os
        peer = UpdateSession(os.getpid())
        try:
            peer.require_running()  # The stable coordinator can open the GUI event.
            result = tmp_path / 'one-click-test.json'
            result.write_text(json.dumps(dict(status='FAILED', detail='explicit test failure')))
            assert adapter._coordinator_result(dict(result_path=str(result)))['state'] == 'FAILED_RECOVERABLE'
            adapter._defer_session()
            with pytest.raises(SessionEnding): peer.require_running()
        finally:
            peer.close()
    finally:
        adapter.close(); gate.close()
