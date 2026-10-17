"""Real React classroom and update notice behavior in an isolated Qt Chromium page."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

REPO = Path(__file__).resolve().parents[3]
NODE = Path("D:/MentorFlow/.local/node/node.exe")
DEPS = Path("D:/MentorFlow/.local/web-deps")


def _write_vite_config(directory: Path, entry_name: str = "browser160.tsx") -> None:
    here = Path(__file__).parent
    aliases = [
        {"find": "../bridge", "replacement": (here / "bridge160.ts").as_posix()},
        {"find": "./bridge", "replacement": (here / "bridge160.ts").as_posix()},
        {"find": "../teacher/TeacherStage", "replacement": (REPO / "tests/refactor/conversation37/conversation37_optional.tsx").as_posix()},
        {"find": "../voice", "replacement": (REPO / "tests/refactor/conversation37/conversation37_optional.tsx").as_posix()},
    ]
    (directory / "entry.tsx").write_text(
        "import " + json.dumps((here / entry_name).as_posix()), encoding="utf-8"
    )
    (directory / "index.html").write_text(
        '<div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding="utf-8"
    )
    config = f"""import path from 'node:path';import {{pathToFileURL}} from 'node:url';
const deps={json.dumps(DEPS.as_posix())};const {{default:react}}=await import(pathToFileURL(path.join(deps,'node_modules/@vitejs/plugin-react/dist/index.js')));
export default {{root:{json.dumps(directory.as_posix())},base:'./',resolve:{{alias:{json.dumps(aliases)}}},plugins:[react(),{{name:'deps',enforce:'pre',async resolveId(s,i,o){{if(!i||s.startsWith('.')||s.startsWith('/')||s.includes(':')||i.includes('node_modules'))return null;return this.resolve(s,path.join(deps,'package.json'),{{...o,skipSelf:true}})}}}}],build:{{outDir:'dist',emptyOutDir:true,chunkSizeWarningLimit:2000}}}};"""
    (directory / "vite.config.mjs").write_text(config, encoding="utf-8")


def test_classroom_saves_input_before_required_update_and_optional_stays_quiet(tmp_path):
    _write_vite_config(tmp_path)
    env = dict(os.environ)
    env.update(
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
        QT_QPA_PLATFORM="offscreen",
        QTWEBENGINE_REMOTE_DEBUGGING="127.0.0.1:0",
        QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*",
        TEMP=str(tmp_path / "temp"),
        TMP=str(tmp_path / "temp"),
        LOCALAPPDATA=str(tmp_path / "localappdata"),
    )
    (tmp_path / "temp").mkdir()
    (tmp_path / "localappdata").mkdir()
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
    with (tmp_path / "qt160.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(tmp_path)], env=env, stdout=log, stderr=log)
        try:
            browser = subprocess.run(
                [str(NODE), str(Path(__file__).with_name("browser160.mjs")), str(tmp_path)],
                env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            assert browser.returncode == 0, browser.stdout + browser.stderr + (tmp_path / "qt160.log").read_text(encoding="utf-8")[-4000:]
        finally:
            (tmp_path / "done").touch()
            child.wait(timeout=15)


def test_actual_app_bridge_polling_enforces_mandatory_update_in_classroom160(tmp_path):
    """Exercise App's real UpdateNotice mount and polling, not Zustand injection."""
    _write_vite_config(tmp_path, "browser160_app.tsx")
    env = dict(os.environ)
    env.update(
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
        QT_QPA_PLATFORM="offscreen",
        QTWEBENGINE_REMOTE_DEBUGGING="127.0.0.1:0",
        QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --remote-allow-origins=*",
        TEMP=str(tmp_path / "temp"),
        TMP=str(tmp_path / "temp"),
        LOCALAPPDATA=str(tmp_path / "localappdata"),
    )
    (tmp_path / "temp").mkdir()
    (tmp_path / "localappdata").mkdir()
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
    with (tmp_path / "qt160-app.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen([sys.executable, "-B", str(Path(__file__)), "--host", str(tmp_path)], env=env, stdout=log, stderr=log)
        try:
            browser = subprocess.run(
                [str(NODE), str(Path(__file__).with_name("browser160_app.mjs")), str(tmp_path)],
                env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
            )
            assert browser.returncode == 0, browser.stdout + browser.stderr + (tmp_path / "qt160-app.log").read_text(encoding="utf-8")[-5000:]
        finally:
            (tmp_path / "done").touch()
            child.wait(timeout=15)


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    from tests.refactor.skip127.test_skip127_browser import host127
    host127(Path(sys.argv[2]))
