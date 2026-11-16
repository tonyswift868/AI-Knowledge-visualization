import os
from pathlib import Path
import shutil
import subprocess


SCRIPT = Path(__file__).resolve().parents[3] / "refactor/server-java/deploy/usage-stats-readonly.sh"
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def test_readonly_wrapper_accepts_valid_date_range(tmp_path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copy2(SCRIPT.with_name("usage-stats.py"), deploy / "usage-stats.py")
    docker = tmp_path / "docker.sh"
    docker.write_text("#!/bin/sh\nif [ \"$1\" = exec ]; then printf '2026-11-12|2\\n'; else cat >/dev/null; fi\n", encoding="utf-8", newline="\n")
    docker.chmod(0o755)
    env = dict(os.environ, DOCKER_BIN=docker.as_posix(), PYTHON_BIN=Path(os.sys.executable).as_posix())
    result = subprocess.run([str(BASH), SCRIPT.as_posix(), "2026-11-12", "2026-11-12", deploy.as_posix()],
                            input="", text=True, capture_output=True, env=env)
    assert result.returncode == 0, result.stderr
    assert '"active_accounts":[{"day":"2026-11-12","accounts":2}]' in result.stdout
