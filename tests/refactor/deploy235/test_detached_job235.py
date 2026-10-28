"""Local shell rehearsal for the 235 detached deployment job runner.

Uses Git for Windows Bash and disposable fake stage drivers; it never contacts
Docker, SSH, or a production service.
"""
from __future__ import annotations

import os
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[3]
JOB = ROOT / "refactor/server-java/deploy/job.sh"
CHECKED = ROOT / "refactor/server-java/deploy/checked-step.sh"
CLIENT = ROOT / "refactor/server-java/deploy/remote-job.py"
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def _posix(path: Path) -> str:
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if len(value) >= 2 and value[1] == ":" else value


def _run(args: list[str], *, timeout: float = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BASH), *args], capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )


@pytest.fixture
def job_sandbox(tmp_path: Path) -> tuple[Path, Path]:
    assert BASH.is_file(), "Git for Windows Bash is required"
    assert JOB.is_file(), "235 detached job runner is required"
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copyfile(JOB, deploy / JOB.name)
    return deploy / JOB.name, tmp_path


def _status(job: Path, directory: Path) -> str:
    result = _run([_posix(job), "status", _posix(directory)])
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _wait(job: Path, directory: Path, expected_prefix: str, timeout: float = 15) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = _status(job, directory)
        if state.startswith(expected_prefix):
            return state
        time.sleep(0.05)
    pytest.fail(f"job did not reach {expected_prefix!r}; last status={state!r}")


def _driver(path: Path, *, fail_at: str | None = None) -> None:
    lines = [
        "#!/bin/sh",
        "set -eu",
        'dir=$1',
        'printf "started\\n" > "$dir/driver.started"',
        'for stage in build migrate cutover health probe; do',
        '  printf "%s\\n" "$stage" >> "$dir/stages.log"',
        '  sleep 0.25',
        f'  if [ "$stage" = "{fail_at or "__never__"}" ]; then exit 17; fi',
        'done',
        'printf "checked\\n" > "$dir/server_checks.passed"',
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def test_job235_status_reconnect_after_local_poller_is_killed(job_sandbox):
    job, root = job_sandbox
    directory = root / "release-job"
    driver = root / "fake-release-stages.sh"
    _driver(driver)

    missing = _status(job, directory)
    assert missing == "MISSING"

    started = _run([_posix(job), "start", _posix(directory), "sh", _posix(driver), _posix(directory)])
    assert started.returncode == 0, started.stdout + started.stderr
    assert _wait(job, directory, "RUNNING", timeout=1) == "RUNNING" or _status(job, directory) == "UNKNOWN"
    duplicate = _run([_posix(job), "start", _posix(directory), "sh", _posix(driver), _posix(directory)])
    assert duplicate.returncode == 0, duplicate.stdout + duplicate.stderr
    assert duplicate.stdout.strip() in {"RUNNING", "UNKNOWN"}

    # This short-lived local poller stands in for the SSH/status client. Killing
    # it must not kill the nohup worker executing the simulated server release.
    poller_code = "\n".join((
        "import subprocess,sys,time",
        "bash,job,directory,ready=sys.argv[1:]",
        "while True:",
        " result=subprocess.run([bash, job, 'status', directory], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
        " if result.returncode == 0:",
        "  open(ready,'w').write('ready')",
        " time.sleep(.05)",
    ))
    poller_ready = root / "poller.ready"
    poller = subprocess.Popen(
        [str(Path(os.environ.get("PYTHON", "D:/MentorFlow/.venv/Scripts/python.exe"))),
         "-c", poller_code, str(BASH), _posix(job), _posix(directory), str(poller_ready)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while not poller_ready.exists() and time.monotonic() < deadline and poller.poll() is None:
        time.sleep(.03)
    assert poller_ready.exists() and poller.poll() is None
    poller.terminate()
    poller.wait(timeout=5)

    # Reconnect by asking the durable server-side job record. No client-side
    # failure inference can request rollback; only an explicit FAILED marker can.
    state = _wait(job, directory, "SUCCEEDED 0")
    assert state == "SUCCEEDED 0"
    assert (directory / "done").is_file()
    assert (directory / "exit_code").read_text(encoding="utf-8").strip() == "0"
    assert (directory / "server_checks.passed").is_file()
    assert (directory / "stages.log").read_text(encoding="utf-8").splitlines() == [
        "build", "migrate", "cutover", "health", "probe",
    ]
    assert "rollback" not in (directory / "output.log").read_text(encoding="utf-8").lower()


def test_job235_missing_done_marker_is_unknown_not_failure(job_sandbox):
    job, root = job_sandbox
    directory = root / "interrupted-record"
    directory.mkdir()
    (directory / "started").write_text("started\n", encoding="utf-8")
    (directory / "pid").write_text("99999999\n", encoding="ascii")
    # An absent worker with no atomically published exit marker is ambiguous.
    # The controller must reconnect/reconcile rather than decide it failed.
    assert _status(job, directory) == "UNKNOWN"
    assert not (directory / "done").exists()
    assert not (directory / "exit_code").exists()


def test_remote_job235_treats_dropped_status_connection_as_unknown(job_sandbox, monkeypatch):
    job, root = job_sandbox
    driver = root / "complete-release.sh"
    _driver(driver)
    directory = root / "remote-release"

    started = _run([_posix(job), "start", _posix(directory), "sh", _posix(driver), _posix(directory)])
    assert started.returncode == 0, started.stdout + started.stderr
    spec = importlib.util.spec_from_file_location("remote_job235", CLIENT)
    remote = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = remote
    spec.loader.exec_module(remote)
    args = remote.argparse.Namespace(timeout=8, interval=0.05)
    queries = []
    lost = False

    def disconnected_then_reconnected(_args, action):
        nonlocal lost
        queries.append(action)
        if not lost:
            lost = True
            return None
        return _status(job, directory)

    monkeypatch.setattr(remote, "query", disconnected_then_reconnected)
    assert remote.wait(args, "RUNNING") == 0
    assert queries[0] == "status" and len(queries) >= 2
    assert (directory / "done").is_file()
    assert (directory / "server_checks.passed").is_file()
    stages = (directory / "stages.log").read_text(encoding="utf-8").splitlines()
    assert stages == ["build", "migrate", "cutover", "health", "probe"]


def test_checked_step235_rolls_back_only_after_server_action_fails(job_sandbox):
    job, root = job_sandbox
    action = root / "action.sh"
    rollback = root / "rollback.sh"
    action.write_text("#!/bin/sh\nexit 17\n", encoding="utf-8", newline="\n")
    rollback.write_text('printf "rollback\\n" > "$ROLLBACK_MARKER"\n', encoding="utf-8", newline="\n")
    checked = root / "checked-step.sh"
    shutil.copyfile(CHECKED, checked)
    marker = root / "rollback.marker"
    env = {**os.environ, "ROLLBACK_MARKER": _posix(marker)}
    result = subprocess.run([str(BASH), _posix(checked), _posix(action), _posix(rollback)],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 17
    assert "ACTION_EXIT 17" in result.stdout
    assert "ROLLBACK_EXIT 0" in result.stdout
    assert marker.read_text(encoding="utf-8").strip() == "rollback"


def test_checked_step235_does_not_rollback_when_checks_succeed(job_sandbox):
    job, root = job_sandbox
    action = root / "action-ok.sh"
    rollback = root / "rollback.sh"
    action.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
    rollback.write_text('printf "rollback\\n" > "$ROLLBACK_MARKER"\n', encoding="utf-8", newline="\n")
    checked = root / "checked-step.sh"
    shutil.copyfile(CHECKED, checked)
    marker = root / "rollback.marker"
    result = subprocess.run([str(BASH), _posix(checked), _posix(action), _posix(rollback)],
                            env={**os.environ, "ROLLBACK_MARKER": _posix(marker)},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()


def test_job235_only_server_check_failure_has_failed_exit_marker(job_sandbox):
    job, root = job_sandbox
    directory = root / "release-fails-health"
    driver = root / "fake-failing-stages.sh"
    _driver(driver, fail_at="health")

    started = _run([_posix(job), "start", _posix(directory), "sh", _posix(driver), _posix(directory)])
    assert started.returncode == 0, started.stdout + started.stderr
    state = _wait(job, directory, "FAILED 17")
    assert state == "FAILED 17"
    assert (directory / "done").is_file()
    assert (directory / "exit_code").read_text(encoding="utf-8").strip() == "17"
    assert not (directory / "server_checks.passed").exists()
    stages = (directory / "stages.log").read_text(encoding="utf-8").splitlines()
    assert stages == ["build", "migrate", "cutover", "health"]
    # job.sh records status; the release driver, not a lost local connection,
    # decides whether an application rollback is needed.
    assert "rollback" not in (directory / "output.log").read_text(encoding="utf-8").lower()


def test_job235_detached_real_helpers_survive_poller_disconnect_and_restore_cycle(job_sandbox):
    """Exercise existing deploy helpers behind fake Docker, with a detached wait gap."""
    job, root = job_sandbox
    source_deploy = ROOT / "refactor/server-java/deploy"
    repo = root / "sandbox-repo"
    deploy = repo / "refactor/server-java/deploy"
    deploy.mkdir(parents=True)
    shutil.copytree(source_deploy, deploy, dirs_exist_ok=True)
    (repo / "refactor/server-java/Dockerfile").write_text("# fake build context\n", encoding="utf-8")
    (deploy / ".env").write_text("MF_JAVA_VERSION=old\n", encoding="utf-8")
    (deploy / "compose.generated.yml").write_text("services: {}\n", encoding="utf-8")
    (deploy / "compose.python-fallback.yml").write_text("services: {}\n", encoding="utf-8")
    backend_env = root / "backend.env"
    backend_env.write_text("EXAMPLE=1\n", encoding="utf-8")
    network = root / "network45.override.yml"
    network.write_text("networks: {}\n", encoding="utf-8")
    edge = root / "edge.conf"
    edge.write_text(
        "events {}\nhttp {\n upstream mentorflow_account_core_backend {\n"
        "  server backend:8000 resolve;\n }\n}\n",
        encoding="utf-8", newline="\n",
    )

    # Reuse the established deploy151 fake Docker contract (inspect labels,
    # build/create/cp and healthy status); gate the first build call so this
    # test can simulate the SSH poller disappearing during a real script run.
    import runpy

    helpers = runpy.run_path(str(ROOT / "tests/refactor/server_java/test_deploy151.py"))
    compose_fixture = runpy.run_path(str(ROOT / "tests/refactor/deploy235/test_compose235.py"))
    stub, docker_log = helpers["_docker_stub"](root)
    stub_text = stub.read_text(encoding="utf-8")
    gate = root / "continue-build"
    entered = root / "build-entered"
    stub_text = stub_text.replace(
        "  build*) ;;",
        '  build*)\n'
        '    : > "$FAKE_BUILD_ENTERED"\n'
        '    while [ ! -f "$FAKE_BUILD_GATE" ]; do sleep 0.05; done\n'
        '    ;;',
    )
    stub.write_text(stub_text, encoding="utf-8", newline="\n")

    action = root / "action.sh"
    action.write_text(
        "#!/bin/sh\nset -eu\n"
        f"sh '{_posix(deploy / 'build.sh')}' 235-test\n"
        f"sh '{_posix(deploy / 'python-compose.sh')}' stop\n"
        f"sh '{_posix(deploy / 'java-compose.sh')}' up\n"
        f"sh '{_posix(deploy / 'cutover.sh')}' java\n"
        'state=$(docker inspect --format "{{.State.Health.Status}}" mentorflow-java-backend)\n'
        '[ "$state" = healthy ]\n'
        '"$FAKE_CURL" -fsS http://127.0.0.1/health/live >/dev/null\n'
        '"$FAKE_CURL" -fsS https://auth.example.test/health >/dev/null\n'
        '"$FAKE_CURL" -fsS https://app.example.test/health >/dev/null\n',
        encoding="utf-8", newline="\n",
    )
    rollback = root / "rollback.sh"
    rollback.write_text('echo rollback-called >> "$FAKE_ROLLBACK_LOG"\n', encoding="utf-8", newline="\n")
    checked = deploy / "checked-step.sh"
    runner = root / "release-job"
    fake_path = root / "fake-bin"
    fake_path.mkdir()
    shutil.copyfile(stub, fake_path / "docker")
    curl = fake_path / "curl"
    curl.write_text('#!/bin/sh\nprintf "200\\n"\n', encoding="utf-8", newline="\n")
    chmod = _run(["-c", 'chmod +x "$1" "$2"', "_", _posix(fake_path / "docker"), _posix(curl)])
    assert chmod.returncode == 0, chmod.stderr

    env = os.environ.copy()
    env.update({
        "PATH": _posix(fake_path) + ":" + env.get("PATH", ""),
        "DOCKER_BIN": _posix(fake_path / "docker"),
        "FAKE_DOCKER_LOG": _posix(docker_log),
        "FAKE_RELOAD_COUNT": _posix(root / "reload-count"),
        "FAKE_DOCKER_MODE": "",
        "FAKE_CONFIG_FILES": ",".join(compose_fixture["CONFIG_FILES"]),
        "FAKE_BUILD_GATE": _posix(gate),
        "FAKE_BUILD_ENTERED": _posix(entered),
        "FAKE_ROLLBACK_LOG": _posix(root / "rollback.log"),
        "FAKE_CURL": _posix(curl),
        "BACKEND_ENV": _posix(backend_env),
        "BUG45_OVERRIDE": _posix(network),
        "EDGE_CONF_PATH": _posix(edge),
        "HEALTH_ATTEMPTS": "2",
        "HEALTH_INTERVAL_SECONDS": "0",
        "PYTHON_COMPOSE_PROFILES": "account-core-mvp",
        "JAVA_COMPOSE_PROFILES": "account-core-mvp",
    })
    started = subprocess.run(
        [str(BASH), _posix(job), "start", _posix(runner), "sh", _posix(checked),
         _posix(action), _posix(deploy / "restore-python.sh")],
        capture_output=True, text=True, env=env, timeout=10,
    )
    assert started.returncode == 0, started.stdout + started.stderr
    deadline = time.monotonic() + 8
    while not entered.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert entered.exists(), (runner / "output.log").read_text(encoding="utf-8")

    # Drop the local polling process while the server-side worker is blocked
    # inside build.sh. The marker is intentionally still incomplete/UNKNOWN.
    poller_ready = root / "integrated-poller.ready"
    poller = subprocess.Popen(
        [str(Path("D:/MentorFlow/.venv/Scripts/python.exe")), "-c",
         "import subprocess,sys,time\n"
         "bash,job,directory,ready=sys.argv[1:]\n"
         "while True:\n"
         " result=subprocess.run([bash,job,'status',directory],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
         " if result.returncode == 0: open(ready,'w').write('ready')\n"
         " time.sleep(.03)\n", str(BASH), _posix(job), _posix(runner), str(poller_ready)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while not poller_ready.exists() and time.monotonic() < deadline and poller.poll() is None:
        time.sleep(.03)
    assert poller_ready.exists() and poller.poll() is None
    poller.terminate()
    poller.wait(timeout=5)
    assert not (runner / "done").exists()
    assert _status(job, runner) in {"RUNNING", "UNKNOWN"}
    gate.write_text("go\n", encoding="ascii")
    final_state = _status(job, runner)
    deadline = time.monotonic() + 20
    while not final_state.startswith(("SUCCEEDED", "FAILED")) and time.monotonic() < deadline:
        time.sleep(0.05)
        final_state = _status(job, runner)
    assert final_state == "SUCCEEDED 0", (
        final_state,
        "\nOUTPUT:\n" + (runner / "output.log").read_text(encoding="utf-8"),
        "\nDOCKER:\n" + (docker_log.read_text(encoding="utf-8") if docker_log.exists() else "<no docker log>"),
    )

    log = docker_log.read_text(encoding="utf-8")
    for expected in ("build", "compose", "stop oidc-loopback-proxy backend", "up -d --no-deps java-backend java-oidc"):
        assert expected in log
    assert "network45.override.yml" in log
    assert "nginx -s reload" in log
    assert edge.read_text(encoding="utf-8").find("server java-backend:8080 resolve;") >= 0
    assert "ACTION_EXIT" not in (runner / "output.log").read_text(encoding="utf-8")
    assert "ROLLBACK_EXIT" not in (runner / "output.log").read_text(encoding="utf-8")

    # Rehearse the actual rollback helper, then return to Java with the same
    # tested cutover script. Docker stays fake; this verifies command ordering
    # and in-place edge state only.
    restored = subprocess.run(
        [str(BASH), "-c",
         'PATH="$1:$PATH"; export PATH; sh "$2"', "_",
         _posix(fake_path), _posix(deploy / "restore-python.sh")],
        capture_output=True, text=True, env=env, timeout=20,
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert "server backend:8000 resolve;" in edge.read_text(encoding="utf-8")
    back_to_java = subprocess.run(
        [str(BASH), "-c",
         'PATH="$1:$PATH"; export PATH; sh "$2" java', "_",
         _posix(fake_path), _posix(deploy / "cutover.sh")],
        capture_output=True, text=True, env=env, timeout=20,
    )
    assert back_to_java.returncode == 0, back_to_java.stdout + back_to_java.stderr
    assert "server java-backend:8080 resolve;" in edge.read_text(encoding="utf-8")
