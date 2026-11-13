"""Focused 388 checks for the localhost workbench and generated rollback."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "refactor" / "server-java" / "deploy"
TEST_TMP = Path(os.environ.get("DEPLOY388_TMP", r"D:\MentorFlow\.local\test-tmp\388-MF01"))
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


@pytest.fixture
def sandbox():
    TEST_TMP.mkdir(parents=True, exist_ok=True)
    path = Path(__import__("tempfile").mkdtemp(prefix="rollback388-", dir=TEST_TMP))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_workbench_exposes_the_third_page_only_on_localhost():
    compose = (DEPLOY / "compose.yml").read_text(encoding="utf-8")
    desktop = (DEPLOY / "monitoring/client/open-workbench.ps1").read_text(encoding="utf-8")
    assert '"127.0.0.1:18081:8081"' in compose
    assert 'MENTORFLOW_ADMIN_ACCOUNT_PAGE_ENABLED: "true"' in compose
    assert "$AccountsLocalPort = 18091" in desktop
    assert "$AccountsRemotePort = 18081" in desktop
    assert '"http://127.0.0.1:$AccountsLocalPort/admin/accounts"' in desktop
    assert "MentorFlow 账号" in desktop
    assert '"127.0.0.1:$AccountsLocalPort`:127.0.0.1:$AccountsRemotePort"' in desktop
    assert "-AccountsUrl $accountsUrl" in desktop
    assert "if (-not $kumaReady) { $sshArgs +=" in desktop
    assert "if (-not $beszelReady) { $sshArgs +=" in desktop
    assert "if (-not $accountsReady) { $sshArgs +=" in desktop


def test_deploy388_generates_and_executes_rollback_in_fake_environment(sandbox):
    assert BASH.is_file(), "Git for Windows Bash is required"
    source = (DEPLOY / "deploy388.sh").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^write_rollback\(\) \{.*?^}", source)
    assert match, "deploy388 rollback generator was not found"
    function = match.group(0)

    online = sandbox / "online"
    deploy = online / "refactor/server-java/deploy"
    deploy.mkdir(parents=True)
    (deploy / ".env").write_text("MF_JAVA_VERSION=0aefe606\n", encoding="ascii")
    (deploy / "java-compose.sh").write_text(
        '#!/bin/sh\nprintf "%s\\n" "$1" >> "$FAKE_COMPOSE_LOG"\n',
        encoding="ascii",
        newline="\n",
    )
    (deploy / "preflight.sh").write_text(
        '#!/bin/sh\nprintf "%s|%s|%s\\n" "$PREFLIGHT_SOURCE_REVISION" '
        '"$EXPECTED_ALEMBIC_REVISION" "$JAVA_IMAGE" >> "$FAKE_PREFLIGHT_LOG"\n',
        encoding="ascii",
        newline="\n",
    )
    shutil.copyfile(DEPLOY / "cutover.sh", deploy / "cutover.sh")
    candidate_deploy = sandbox / "candidate/refactor/server-java/deploy"
    candidate_deploy.mkdir(parents=True)
    (candidate_deploy / "preflight.sh").write_text(
        '#!/bin/sh\nprintf "%s|%s|%s\\n" "$PREFLIGHT_SOURCE_REVISION" '
        '"$EXPECTED_ALEMBIC_REVISION" "$JAVA_IMAGE" >> "$FAKE_PREFLIGHT_LOG"\n',
        encoding="ascii",
        newline="\n",
    )
    edge_conf = sandbox / "edge.conf"
    edge_conf.write_text(
        "upstream mentorflow_account_core_backend {\n  server java-backend:8080 resolve;\n}\n",
        encoding="ascii",
    )
    (sandbox / "runtime.env").write_text("fixture\n", encoding="ascii")
    (sandbox / "candidate.sha256").write_text("fixture\n", encoding="ascii")

    fake_bin = sandbox / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/bin/sh
case "$*" in
  *".State.Status"*) echo running ;;
  *".Id"*) echo backend-id ;;
  *"NetworkMode"*) echo container:backend-id ;;
  *) exit 0 ;;
esac
""",
        encoding="ascii",
        newline="\n",
    )
    curl = fake_bin / "curl"
    curl.write_text("#!/bin/sh\nprintf 200\n", encoding="ascii", newline="\n")
    ss = fake_bin / "ss"
    ss.write_text("#!/bin/sh\nexit 0\n", encoding="ascii", newline="\n")
    for path in (docker, curl, ss, deploy / "java-compose.sh", deploy / "preflight.sh", deploy / "cutover.sh", candidate_deploy / "preflight.sh"):
        path.chmod(0o755)

    generated = sandbox / "rollback-java.sh"
    harness = sandbox / "generate-and-run.sh"
    harness.write_text(
        "#!/bin/sh\nset -eu\n"
        f"docker_bin='{docker.as_posix()}'\n"
        "java_container='mentorflow-java-backend'\n"
        "java_oidc_container='mentorflow-java-oidc'\n"
        "edge_container='fake-edge'\n"
        f"edge_conf_path='{edge_conf.as_posix()}'\n"
        f"backend_env='{(sandbox / 'runtime.env').as_posix()}'\n"
        f"online_deploy='{deploy.as_posix()}'\n"
        f"release_root='{(sandbox / 'candidate').as_posix()}'\n"
        "server_root='unused-server'\n"
        f"candidate_manifest='{(sandbox / 'candidate.sha256').as_posix()}'\n"
        f"{function}\n"
        f"write_rollback '{generated.as_posix()}'\n"
        f"PATH='{fake_bin.as_posix()}:'\"$PATH\" FAKE_COMPOSE_LOG='{(sandbox / 'compose.log').as_posix()}' "
        f"FAKE_PREFLIGHT_LOG='{(sandbox / 'preflight.log').as_posix()}' sh '{generated.as_posix()}'\n",
        encoding="ascii",
        newline="\n",
    )
    harness.chmod(0o755)
    result = subprocess.run([str(BASH), str(harness)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (sandbox / "compose.log").read_text(encoding="ascii").splitlines() == ["config", "up"]
    assert (sandbox / "preflight.log").read_text(encoding="ascii").strip() == (
        "0052_workbench_account_rows|0052_workbench_account_rows|mentorflow-java-server:0aefe606"
    )
