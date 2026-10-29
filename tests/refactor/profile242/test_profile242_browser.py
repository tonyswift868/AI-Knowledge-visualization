"""Personal profile surfaces in bundled React inside Qt WebEngine with fake account bridge."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")
SCREENSHOTS = Path("D:/MentorFlow/.local/test-tmp/ui242/screenshots")
TMP_ROOT = Path("D:/MentorFlow/.local/test-tmp/ui242/pytest")
sys.path.insert(0, str(REPO))


@pytest.fixture
def tmp_path(request):
    path = TMP_ROOT / request.node.name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _build(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for old_shot in (directory / "screenshots").glob("*.png"):
        old_shot.unlink()
    here = Path(__file__).parent
    (directory / "entry.tsx").write_text("import " + json.dumps((here / "profile242_browser.tsx").as_posix()), encoding="utf-8")
    (directory / "index.html").write_text('<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8")
    bridge = (Path(__file__).parent / "bridge242.ts").as_posix()
    aliases = [{"find": "../bridge", "replacement": bridge}, {"find": "./bridge", "replacement": bridge}]
    config = f"""import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(DEPS.as_posix())};const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};"""
    (directory / "vite.config.mjs").write_text(config, encoding="utf-8")
    subprocess.run([str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build", "--config", str(directory / "vite.config.mjs")],
                   check=True, capture_output=True, text=True, encoding="utf-8", timeout=120)


def _host(directory):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from tests.refactor.simulation.engine import ensure_offscreen_fonts

    app = QApplication([])
    ensure_offscreen_fonts()
    view = QWebEngineView()
    profile = QWebEngineProfile(view)
    page = QWebEnginePage(profile, view)

    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ("http", "https"):
                info.block(True)

    interceptor = Offline(page)
    profile.setUrlRequestInterceptor(interceptor)
    view.setPage(page)
    view.resize(1100, 900)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(directory / "dist/index.html")))
    timer = QTimer()
    timer.timeout.connect(lambda: app.quit() if (directory / "done").exists() else None)
    timer.start(100)
    app.exec()


def test_profile242_settings_and_account_card_light_dark(tmp_path):
    _build(tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QTWEBENGINE_REMOTE_DEBUGGING=f"127.0.0.1:{port}",
               QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*",
               MF_CDP_ENDPOINT=f"http://127.0.0.1:{port}", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    log_path = tmp_path / "qt242.log"
    with log_path.open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(tmp_path)], env=env, stdout=log, stderr=log)
        try:
            result = subprocess.run([str(NODE), str(Path(__file__).with_name("profile242_browser.mjs")), str(tmp_path)],
                                    env=env, capture_output=True, text=True, encoding="utf-8", timeout=100)
            assert result.returncode == 0, result.stdout + result.stderr
        finally:
            (tmp_path / "done").touch()
            child.wait(timeout=15)
    result = json.loads((tmp_path / "result242.json").read_text(encoding="utf-8"))
    assert result["ok"], result
    shots = list((tmp_path / "screenshots").glob("*.png"))
    assert len(shots) == 14
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    for shot in shots:
        shutil.copy2(shot, SCREENSHOTS / shot.name)


if __name__ == "__main__":
    _host(Path(sys.argv[2]))
