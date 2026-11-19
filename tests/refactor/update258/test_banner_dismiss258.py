"""Real React update components in isolated Qt Chromium with a fake bridge."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

REPO = Path(__file__).resolve().parents[3]
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")
ARTIFACTS = Path(os.environ.get("UPDATE258_ARTIFACTS", "D:/MentorFlow/.local/test-tmp/banner258"))


def _build(directory: Path) -> None:
    here = Path(__file__).parent
    (directory / "entry.tsx").write_text(
        "import " + json.dumps((here / "browser258.tsx").as_posix()), encoding="utf-8"
    )
    (directory / "index.html").write_text(
        '<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8"
    )
    config = """import path from 'node:path';import {pathToFileURL} from 'node:url';
const deps=__DEPS__;const {default:react}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {root:__ROOT__,base:'./',resolve:{alias:__ALIASES__},plugins:[react(),{name:'deps',enforce:'pre',async resolveId(s,i,o){if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{...o,skipSelf:true})}}],build:{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}};"""
    config = config.replace("__DEPS__", json.dumps(DEPS.as_posix()))
    config = config.replace("__ROOT__", json.dumps(directory.as_posix()))
    config = config.replace("__ALIASES__", json.dumps([
        {"find": "../bridge", "replacement": (here / "bridge258.ts").as_posix()},
        {"find": "./bridge", "replacement": (here / "bridge258.ts").as_posix()},
    ]))
    (directory / "vite.config.mjs").write_text(config, encoding="utf-8")
    result = subprocess.run(
        [str(NODE), str(DEPS / "node_modules/vite/bin/vite.js"), "build", "--config", str(directory / "vite.config.mjs")],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_optional_update_dismissal_version_change_mandatory_and_relogin(tmp_path):
    _build(tmp_path)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    temp, local = tmp_path / "temp", tmp_path / "localappdata"
    temp.mkdir(); local.mkdir()
    env = dict(os.environ)
    env.update(
        PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
        QTWEBENGINE_REMOTE_DEBUGGING="127.0.0.1:0",
        QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*",
        TEMP=str(temp), TMP=str(temp), LOCALAPPDATA=str(local),
    )
    env["PATH"] = str(NODE.parent) + os.pathsep + env.get("PATH", "")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    env["QTWEBENGINE_REMOTE_DEBUGGING"] = f"127.0.0.1:{port}"
    env["MF_CDP_ENDPOINT"] = f"http://127.0.0.1:{port}"
    with (tmp_path / "qt258.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(tmp_path)],
                                 env=env, stdout=log, stderr=log)
        try:
            browser = subprocess.run(
                [str(NODE), str(Path(__file__).with_name("browser258.mjs")), str(tmp_path), str(ARTIFACTS)],
                env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            log_text = (tmp_path / "qt258.log").read_text(encoding="utf-8")[-5000:]
            assert browser.returncode == 0, browser.stdout + browser.stderr + log_text
            result = json.loads((tmp_path / "update258-result.json").read_text(encoding="utf-8"))
            assert result["dismissedAcrossRoute"] and result["sameVersionPollingHidden"]
            assert result["newVersionShown"] and result["mandatoryModalAndLock"]
            assert result["reloginShowsAgain"]
            assert {p.name for p in ARTIFACTS.glob("*.png")} >= {"light-banner258.png", "dark-banner258.png"}
        finally:
            (tmp_path / "done").touch()
            child.wait(timeout=15)


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    from tests.refactor.skip127.test_skip127_browser import host127
    host127(Path(sys.argv[2]))
