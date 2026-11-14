"""Local fake-runtime coverage for the 393 deploy and its real rollback script."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "refactor" / "server-java" / "deploy"
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def posix(path: Path) -> str:
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if len(value) >= 2 and value[1] == ":" else value


def write_script(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="ascii", newline="\n")
    path.chmod(0o755)


@pytest.fixture
def release(tmp_path: Path):
    assert BASH.is_file(), "Git for Windows Bash is required"
    candidate = tmp_path / "candidate"
    candidate_deploy = candidate / "refactor/server-java/deploy"
    candidate_deploy.mkdir(parents=True)
    shutil.copyfile(DEPLOY / "deploy393.sh", candidate_deploy / "deploy393.sh")
    shutil.copyfile(DEPLOY / "rollback393.sh", candidate_deploy / "rollback393.sh")
    online = tmp_path / "online"
    online_deploy = online / "refactor/server-java/deploy"
    online_deploy.mkdir(parents=True)
    order = tmp_path / "order.log"
    docker_log = tmp_path / "docker.log"
    image_file = tmp_path / "current-image"
    image_file.write_text("mentorflow-java-server:d37112fd\n", encoding="ascii")

    for deploy in (candidate_deploy, online_deploy):
        (deploy / ".env").write_text("MF_JAVA_VERSION=d37112fd\n", encoding="ascii")
        (deploy / ".java.env").write_text("fixture=true\n", encoding="ascii")
        (deploy / "compose.generated.yml").write_text("services: {}\n", encoding="ascii")
    (candidate / "refactor/server-java/deploy/compose.yml").write_text(
        "services:\n  backend:\n    environment:\n      JAVA_TOOL_OPTIONS: >-\n        -Xms256m\n",
        encoding="ascii",
    )
    (candidate / "manifest.sha256").write_text("fixture\n", encoding="ascii")
    backend_env = candidate / "backend.env"
    backend_env.write_text("fixture\n", encoding="ascii")
    heartbeat = tmp_path / "healthchecks-send.sh"
    heartbeat.write_text("old-heartbeat\n", encoding="ascii")
    edge_conf = tmp_path / "edge.conf"
    edge_conf.write_text("old-edge-config\n", encoding="ascii")
    (candidate_deploy / "monitoring").mkdir()
    (candidate_deploy / "monitoring/healthchecks-send.sh").write_text("new-heartbeat\n", encoding="ascii")
    (candidate_deploy / "edge").mkdir()

    candidate_preflight = candidate_deploy / "preflight.sh"
    online_preflight = online_deploy / "preflight.sh"
    write_script(
        candidate_preflight,
        "#!/bin/sh\nif [ -n \"${PREFLIGHT_SOURCE_REVISION:-}\" ]; then event=candidate_preflight; else event=candidate_final_preflight; fi\n"
        f"printf '%s\\n' \"$event\" >> '{posix(order)}'\n",
    )
    write_script(online_preflight, f"#!/bin/sh\nprintf 'online_preflight\\n' >> '{posix(order)}'\n")
    write_script(
        candidate_deploy / "build.sh",
        "#!/bin/sh\nset -eu\ncd \"$(dirname \"$0\")\"\nprintf 'build:%s\\n' \"$1\" >> \"$FAKE_ORDER\"\nprintf 'MF_JAVA_VERSION=%s\\n' \"$1\" > .env\n",
    )
    compose = (
        "#!/bin/sh\nset -eu\ncd \"$(dirname \"$0\")\"\naction=$1\n"
        "version=$(awk -F= '$1 == \"MF_JAVA_VERSION\" {print $2}' .env)\n"
        "printf 'compose:%s:%s\\n' \"$action\" \"$version\" >> \"$FAKE_ORDER\"\n"
        "if [ \"$action\" = up ]; then printf 'mentorflow-java-server:%s\\n' \"$version\" > \"$FAKE_IMAGE\"; fi\n"
    )
    write_script(candidate_deploy / "java-compose.sh", compose)
    write_script(online_deploy / "java-compose.sh", compose)

    write_script(
        candidate_deploy / "edge/install-edge-logging393.sh",
        "#!/bin/sh\nset -eu\naction=$1\nprintf 'edge:%s\\n' \"$action\" >> \"$FAKE_ORDER\"\n"
        "if [ \"$action\" = install ]; then\n"
        "  cp \"$2\" \"$3\"\n  printf 'new-edge\\n' > \"$FAKE_EDGE_STATE\"\n"
        "  if [ \"${FAIL_EDGE_INTERNAL_ROLLBACK:-0}\" = 1 ]; then touch \"$3.rollback-failed\"; exit 70; fi\n"
        "  if [ \"${FAIL_EDGE_INSTALL:-0}\" = 1 ]; then exit 1; fi\n"
        "else\n  if [ \"${FAIL_EDGE_ROLLBACK:-0}\" = 1 ]; then exit 2; fi\n"
        "  printf 'old-edge\\n' > \"$FAKE_EDGE_STATE\"\nfi\n",
    )

    fake_bin = tmp_path / "bin"
    docker = fake_bin / "docker"
    write_script(
        docker,
        "#!/bin/sh\nset -eu\nprintf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
        "case \"$1\" in\n  inspect)\n    case \"$2\" in\n      --format)\n"
        "        case \"$3\" in\n          *Config.Image*) cat \"$FAKE_IMAGE\" ;;\n"
        "          *HostConfig.NetworkMode*) printf 'container:backend-id\\n' ;;\n"
        "          *State.StartedAt*) printf 'started\\n' ;;\n          *Id*) printf 'backend-id\\n' ;;\n"
        "          *) exit 9 ;;\n        esac ;;\n      *) exit 8 ;;\n    esac ;;\n"
        "  run) printf 'docker_run_jvm\\n' >> \"$FAKE_ORDER\" ;;\n  *) exit 7 ;;\nesac\n",
    )
    curl = fake_bin / "curl"
    write_script(
        curl,
        "#!/bin/sh\nset -eu\nurl=\nfor arg do url=$arg; done\ncase \"$url\" in\n"
        "  */health/live) label=health_live ;;\n  */health/startup) label=health_startup ;;\n"
        "  */health/ready) label=health_ready ;;\n  */admin/accounts) label=account_page ;;\n"
        "  *) label=unexpected_curl ;;\nesac\nprintf '%s\\n' \"$label\" >> \"$FAKE_ORDER\"\nprintf 200\n",
    )
    sleep = fake_bin / "sleep"
    write_script(sleep, "#!/bin/sh\nprintf 'sleep:%s\\n' \"$*\" >> \"$FAKE_ORDER\"\n")

    env = os.environ.copy()
    env.update(
        {
            "PATH": posix(fake_bin) + ":" + env.get("PATH", ""),
            "RELEASE_ROOT": posix(candidate),
            "ONLINE_RELEASE_ROOT": posix(online),
            "ONLINE_PREFLIGHT": posix(online_preflight),
            "STATE": posix(tmp_path / "state"),
            "SOURCE_SHA256_MANIFEST": posix(candidate / "manifest.sha256"),
            "BACKEND_ENV": posix(backend_env),
            "HEARTBEAT_SCRIPT": posix(heartbeat),
            "EDGE_CONF_PATH": posix(edge_conf),
            "DOCKER_BIN": posix(docker),
            "CURL_BIN": posix(curl),
            "SLEEP_BIN": posix(sleep),
            "FAKE_ORDER": posix(order),
            "FAKE_DOCKER_LOG": posix(docker_log),
            "FAKE_IMAGE": posix(image_file),
            "FAKE_EDGE_STATE": posix(tmp_path / "edge-state"),
        }
    )
    return {
        "tmp": tmp_path,
        "candidate_deploy": candidate_deploy,
        "online_deploy": online_deploy,
        "order": order,
        "docker_log": docker_log,
        "image": image_file,
        "heartbeat": heartbeat,
        "edge_conf": edge_conf,
        "edge_state": tmp_path / "edge-state",
        "env": env,
    }


def run_deploy(release, *, fail_edge: bool = False, fail_edge_rollback: bool = False, fail_edge_internal_rollback: bool = False):
    env = release["env"].copy()
    env["FAIL_EDGE_INSTALL"] = "1" if fail_edge else "0"
    env["FAIL_EDGE_ROLLBACK"] = "1" if fail_edge_rollback else "0"
    env["FAIL_EDGE_INTERNAL_ROLLBACK"] = "1" if fail_edge_internal_rollback else "0"
    return subprocess.run(
        [str(BASH), "-c", 'sh "$1"', "_", posix(release["candidate_deploy"] / "deploy393.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_deploy393_order_and_boundary_without_database_calls(release):
    result = run_deploy(release)
    assert result.returncode == 0, result.stderr
    lines = release["order"].read_text(encoding="ascii").splitlines()
    expected = [
        "online_preflight", "candidate_preflight", "build:393", "docker_run_jvm", "compose:config:393",
        "compose:up:393", "health_live", "health_startup", "health_ready",
        "account_page", "edge:install", "candidate_final_preflight",
    ]
    positions = [lines.index(item) for item in expected]
    assert positions == sorted(positions)
    assert release["heartbeat"].read_text(encoding="ascii") == "new-heartbeat\n"
    assert release["edge_state"].read_text(encoding="ascii") == "new-edge\n"
    assert release["image"].read_text(encoding="ascii") == "mentorflow-java-server:393\n"
    docker_calls = release["docker_log"].read_text(encoding="ascii")
    assert "migrate" not in docker_calls and "postgres" not in docker_calls
    assert result.stdout.count("DEPLOY393_OK") == 1


def test_edge_failure_runs_real_rollback_and_restores_old_runtime(release):
    result = run_deploy(release, fail_edge=True)
    assert result.returncode != 0
    assert "AUTO_ROLLBACK393_OK original_step_failed" in result.stderr
    lines = release["order"].read_text(encoding="ascii").splitlines()
    assert "edge:install" in lines and "edge:rollback" in lines
    assert "compose:up:d37112fd" in lines
    assert release["heartbeat"].read_text(encoding="ascii") == "old-heartbeat\n"
    assert release["edge_state"].read_text(encoding="ascii") == "old-edge\n"
    assert release["image"].read_text(encoding="ascii") == "mentorflow-java-server:d37112fd\n"


def test_failed_auto_rollback_reports_exit_70(release):
    result = run_deploy(release, fail_edge=True, fail_edge_rollback=True)
    assert result.returncode == 70
    assert "AUTO_ROLLBACK393_FAILED stop_and_report" in result.stderr


def test_failed_edge_internal_rollback_stops_without_retry(release):
    result = run_deploy(release, fail_edge_internal_rollback=True)
    assert result.returncode == 70
    assert "AUTO_ROLLBACK393_FAILED edge_restore_failed stop_and_report" in result.stderr
    lines = release["order"].read_text(encoding="ascii").splitlines()
    assert "edge:rollback" not in lines
    assert "compose:up:d37112fd" not in lines
