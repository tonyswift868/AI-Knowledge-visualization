"""208: retry UI remains truthful when its scheduled time has elapsed."""
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_sync203_status_browser import _build


def test_sync208_expired_retry_time_displays_attempt(tmp_path):
    artifact_dir = tmp_path / "screenshots"
    artifact_dir.mkdir()
    _build(tmp_path)
    env = dict(os.environ, SYNC203_ARTIFACTS=str(artifact_dir),
               PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
               QT_QPA_PLATFORM="offscreen", QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu")
    child = subprocess.run([sys.executable, "-B", str(HERE / "test_sync203_status_browser.py"),
                            "--browser-expired", str(tmp_path)], env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert child.returncode == 0, child.stdout + child.stderr
