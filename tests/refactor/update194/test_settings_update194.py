"""Real Settings + global update notice rendered in isolated Qt WebEngine with a fake bridge."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

REPO = Path(__file__).resolve().parents[3]
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")


def _prepare_page(directory: Path) -> None:
    here = Path(__file__).parent
    entry = here / "browser194.tsx"
    (directory / "entry.tsx").write_text("import " + json.dumps(entry.as_posix()), encoding="utf-8")
    (directory / "index.html").write_text(
        '<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8"
    )
    config = """import path from 'node:path';import {pathToFileURL} from 'node:url';
const deps=__DEPS__;const {default:react}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {root:__ROOT__,base:'./',resolve:{alias:__ALIASES__},plugins:[react(),{name:'deps',enforce:'pre',async resolveId(s,i,o){if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{...o,skipSelf:true})}}],build:{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}};"""
    config = config.replace("__DEPS__", json.dumps(DEPS.as_posix()))
    config = config.replace("__ROOT__", json.dumps(directory.as_posix()))
    config = config.replace("__ALIASES__", json.dumps([
        {"find": "../bridge", "replacement": (here / "bridge194.ts").as_posix()},
        {"find": "./bridge", "replacement": (here / "bridge194.ts").as_posix()},
    ]))
    (directory / "vite.config.mjs").write_text(config, encoding="utf-8")


def test_settings_and_global_notice_show_each_update_state_once_in_both_themes(tmp_path):
    _prepare_page(tmp_path)
    artifact_root = Path(os.environ.get("UPDATE194_ARTIFACTS", "D:/MentorFlow/.local/test-tmp/upd194/screenshots"))
    artifact_root.mkdir(parents=True, exist_ok=True)
    for old_image in artifact_root.glob("*.png"):
        old_image.unlink()
    temp = tmp_path / "temp"
    local = tmp_path / "localappdata"
    temp.mkdir()
    local.mkdir()
    env = dict(os.environ)
    env.update(
        PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
        QTWEBENGINE_REMOTE_DEBUGGING="127.0.0.1:0",
        QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*",
        TEMP=str(temp), TMP=str(temp), LOCALAPPDATA=str(local),
        UPDATE194_ARTIFACTS=str(artifact_root),
    )
    env["PATH"] = str(NODE.parent) + os.pathsep + env.get("PATH", "")
    build = subprocess.run(
        [str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build", "--config", str(tmp_path / "vite.config.mjs")],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env["QTWEBENGINE_REMOTE_DEBUGGING"] = f"127.0.0.1:{port}"
    env["MF_CDP_ENDPOINT"] = f"http://127.0.0.1:{port}"
    with (tmp_path / "qt194.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(tmp_path)],
                                 env=env, stdout=log, stderr=log)
        try:
            browser = subprocess.run(
                [str(NODE), str(Path(__file__).with_name("browser194.mjs")), str(tmp_path)],
                env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            log_text = (tmp_path / "qt194.log").read_text(encoding="utf-8")[-5000:]
            assert browser.returncode == 0, browser.stdout + browser.stderr + log_text
            result = json.loads((tmp_path / "update194-browser.json").read_text(encoding="utf-8"))
            assert result["automaticCheckCalls"] >= 1
            assert result["manualCheckCalls"] >= 2
            phases = {entry["phase"] for entry in result["checks"]}
            assert phases == {"checking", "automatic-offer", "manual-offer", "downloading", "latest-automatic",
                              "latest-manual", "manual-error", "stale-download-failure", "stale-download-applying",
                              "manual-checking-pending", "matrix-checking", "matrix-offer", "matrix-downloading",
                              "matrix-latest", "matrix-error"}
            matrix = [entry for entry in result["checks"] if entry["phase"].startswith("matrix-")]
            assert len(matrix) == 10
            assert {(entry["phase"], entry["theme"]) for entry in matrix} == {
                (f"matrix-{state}", theme)
                for state in ("checking", "offer", "downloading", "latest", "error")
                for theme in ("sakura", "aurora")
            }
            assert all(entry["count"] == 1 and entry["statusVisible"] for entry in result["checks"])
        finally:
            (tmp_path / "done").touch()
            child.wait(timeout=15)


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    from tests.refactor.skip127.test_skip127_browser import host127
    host127(Path(sys.argv[2]))
