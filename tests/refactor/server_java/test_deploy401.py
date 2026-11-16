"""Exercise deploy401 and rollback401 with isolated fake runtime commands."""
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
def runtime(tmp_path: Path):
    assert BASH.is_file(), "Git for Windows Bash is required"
    candidate = tmp_path / "candidate"
    candidate_deploy = candidate / "refactor/server-java/deploy"
    online = tmp_path / "online"
    online_deploy = online / "refactor/server-java/deploy"
    candidate_deploy.mkdir(parents=True)
    online_deploy.mkdir(parents=True)
    for name in ("deploy401.sh", "rollback401.sh"):
        shutil.copyfile(DEPLOY / name, candidate_deploy / name)
    order = tmp_path / "order.log"
    image = tmp_path / "image"
    image.write_text("mentorflow-java-server:393\n", encoding="ascii")
    for directory in (candidate_deploy, online_deploy):
        (directory / ".env").write_text("MF_JAVA_VERSION=393\n", encoding="ascii")
        (directory / ".java.env").write_text("fixture=true\n", encoding="ascii")
        (directory / "compose.generated.yml").write_text("services: {}\n", encoding="ascii")
    (candidate_deploy / "compose.yml").write_text(
        "services:\n  backend:\n    environment:\n      JAVA_TOOL_OPTIONS: >-\n        -Xms256m\n", encoding="ascii"
    )
    (candidate / "refactor/server").mkdir(parents=True)
    (candidate_deploy / "probe.py").write_text("# fixture\n", encoding="ascii")
    (candidate_deploy / "preflight.sh").write_text(
        f"#!/bin/sh\nif [ -n \"${{PREFLIGHT_SOURCE_REVISION:-}}\" ]; then event=candidate_preflight; else event=final_preflight; fi\nprintf '%s\\n' \"$event\" >> '{posix(order)}'\n",
        encoding="ascii",
    )
    (online_deploy / "preflight.sh").write_text(
        f"#!/bin/sh\nprintf 'online_preflight\\n' >> '{posix(order)}'\n", encoding="ascii"
    )
    (candidate_deploy / "preflight.sh").chmod(0o755)
    (online_deploy / "preflight.sh").chmod(0o755)
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
        candidate_deploy / "migrate.sh",
        "#!/bin/sh\nset -eu\ncase \"$1\" in\n"
        " current) if [ -f \"$FAKE_REVISION_FILE\" ]; then revision=$(cat \"$FAKE_REVISION_FILE\"); else revision=0053_workbench_question_index; fi; printf '%s\\n' \"$revision\" >> \"$FAKE_ORDER\"; printf '%s\\n' \"$revision\" ;;\n"
        " upgrade) test -f \"$FAKE_MIGRATION_MARKER\"; printf 'marker_present_upgrade\\n' >> \"$FAKE_ORDER\"; printf 'migration_upgrade\\n' >> \"$FAKE_ORDER\"; printf '0054 sync receipt payload scrub UPDATE elapsed 1.234 ms\\n'; if [ \"${FAIL_MIGRATION:-0}\" = 1 ]; then exit 1; fi; printf '0054_sync_receipts_without_payload\\n' > \"$FAKE_REVISION_FILE\" ;;\nesac\n",
    )
    write_script(online_deploy / "migrate.sh", (candidate_deploy / "migrate.sh").read_text(encoding="ascii"))

    fake_bin = tmp_path / "bin"
    docker = fake_bin / "docker"
    write_script(
        docker,
        "#!/bin/sh\nset -eu\nprintf 'docker:%s\\n' \"$*\" >> \"$FAKE_ORDER\"\n"
        "case \"$1\" in\n"
        " inspect) case \"$3\" in *Config.Image*) if [ \"$4\" = mentorflow-e1-preview-backend-1 ]; then printf 'mentorflow-slim-backend:269-python-fallback\\n'; else cat \"$FAKE_IMAGE\"; fi ;; *HostConfig.NetworkMode*) printf 'container:backend-id\\n' ;; *Id*) printf 'backend-id\\n' ;; *) exit 8 ;; esac ;;\n"
        " run) case \"$*\" in *--entrypoint?java*) printf 'jvm_check\\n' >> \"$FAKE_ORDER\" ;; *probe.py*) printf 'java_probe\\n' >> \"$FAKE_ORDER\"; [ \"${FAIL_PROBE:-0}\" = 0 ] ;; *) exit 9 ;; esac ;;\n"
        " exec) case \"$*\" in *pg_total_relation_size*) if [ -f \"$FAKE_MIGRATION_MARKER\" ]; then printf 'table_size_after\\n' >> \"$FAKE_ORDER\"; else printf 'table_size_before\\n' >> \"$FAKE_ORDER\"; fi; printf '4096\\n' ;; *version_num*) printf 'db_revision_read\\n' >> \"$FAKE_ORDER\"; if [ -n \"${FAKE_REVISION:-}\" ]; then printf '%s\\n' \"$FAKE_REVISION\"; elif [ -f \"$FAKE_REVISION_FILE\" ]; then cat \"$FAKE_REVISION_FILE\"; else printf '0053_workbench_question_index\\n'; fi ;; *) printf 'vacuum\\n' >> \"$FAKE_ORDER\" ;; esac ;;\n"
        " *) exit 7 ;;\nesac\n",
    )
    curl = fake_bin / "curl"
    write_script(
        curl,
        "#!/bin/sh\nset -eu\nurl=\nfor arg do url=$arg; done\ncase \"$url\" in */health/live) label=health_live ;; */health/startup) label=health_startup ;; */health/ready) label=health_ready ;; *) label=unexpected_curl ;; esac\nprintf '%s\\n' \"$label\" >> \"$FAKE_ORDER\"\nif [ \"${FAIL_HEALTH:-0}\" = 1 ] && [ \"$url\" = */health/ready ]; then printf 503; else printf 200; fi\n",
    )
    sleep = fake_bin / "sleep"
    write_script(sleep, "#!/bin/sh\nprintf 'sleep:%s\\n' \"$*\" >> \"$FAKE_ORDER\"\n")
    env = os.environ.copy()
    env.update(
        {
            "PATH": posix(fake_bin) + ":" + env.get("PATH", ""),
            "RELEASE_ROOT": posix(candidate),
            "ONLINE_RELEASE_ROOT": posix(online),
            "ONLINE_PREFLIGHT": posix(online_deploy / "preflight.sh"),
            "STATE": posix(tmp_path / "state"),
            "SOURCE_SHA256_MANIFEST": posix(candidate / "manifest.sha256"),
            "BACKEND_ENV": posix(candidate / "backend.env"),
            "DOCKER_BIN": posix(docker),
            "CURL_BIN": posix(curl),
            "SLEEP_BIN": posix(sleep),
            "FAKE_ORDER": posix(order),
            "FAKE_IMAGE": posix(image),
            "FAKE_REVISION_FILE": posix(tmp_path / "db-revision"),
            "FAKE_MIGRATION_MARKER": posix(tmp_path / "state/migration-started"),
        }
    )
    (candidate / "manifest.sha256").write_text("fixture\n", encoding="ascii")
    (candidate / "backend.env").write_text("fixture\n", encoding="ascii")
    verification = candidate / "backup-restore-verification.txt"
    verification.write_text(
        "backup-restore=PASS\ncopy-migration=PASS\ncopy-revision=0054_sync_receipts_without_payload\n",
        encoding="ascii",
    )
    env["BACKUP_RESTORE_VERIFICATION"] = posix(verification)
    return {"tmp": tmp_path, "candidate_deploy": candidate_deploy, "online_deploy": online_deploy,
            "order": order, "image": image, "env": env, "verification": verification}


def run(runtime, script: str, extra: dict[str, str] | None = None):
    env = runtime["env"].copy()
    if extra:
        env.update(extra)
    return subprocess.run(
        [str(BASH), "-c", 'sh "$1"', "_", posix(runtime["candidate_deploy"] / script)],
        env=env, capture_output=True, text=True, timeout=60,
    )


def test_deploy401_orders_checks_migration_marker_and_vacuum(runtime):
    result = run(runtime, "deploy401.sh")
    assert result.returncode == 0, result.stderr
    lines = runtime["order"].read_text(encoding="ascii").splitlines()
    positions = [lines.index(item) for item in (
        "online_preflight", "candidate_preflight", "build:401", "jvm_check", "compose:config:401",
        "compose:up:401", "health_live", "health_startup", "health_ready", "java_probe",
        "table_size_before", "marker_present_upgrade", "migration_upgrade", "db_revision_read", "vacuum", "table_size_after", "final_preflight",
    )]
    assert positions == sorted(positions)
    assert (runtime["tmp"] / "state/migration-started").is_file()
    assert runtime["image"].read_text(encoding="ascii") == "mentorflow-java-server:401\n"
    assert result.stdout.count("DEPLOY401_OK") == 1
    assert "migration_update_elapsed_ms=1.234" in result.stdout
    assert "migration_command_elapsed_ms=" in result.stdout
    assert "structured_sync_requests_before_bytes=4096" in result.stdout
    assert "structured_sync_requests_after_vacuum_bytes=4096" in result.stdout


def test_health_failure_uses_real_rollback_before_migration(runtime):
    result = run(runtime, "deploy401.sh", {"FAIL_PROBE": "1"})
    assert result.returncode != 0
    assert "AUTO_ROLLBACK401_OK" in result.stderr
    lines = runtime["order"].read_text(encoding="ascii").splitlines()
    assert "compose:up:401" in lines and "compose:up:393" in lines
    assert "migration_upgrade" not in lines
    assert runtime["image"].read_text(encoding="ascii") == "mentorflow-java-server:393\n"


def test_migration_failure_never_rolls_back_to_393(runtime):
    result = run(runtime, "deploy401.sh", {"FAIL_MIGRATION": "1"})
    assert result.returncode != 0
    assert "AUTO_ROLLBACK401_FORBIDDEN migration_started" in result.stderr
    assert (runtime["tmp"] / "state/migration-started").is_file()
    lines = runtime["order"].read_text(encoding="ascii").splitlines()
    assert lines.count("compose:up:401") == 1
    assert "compose:up:393" not in lines
    assert runtime["image"].read_text(encoding="ascii") == "mentorflow-java-server:401\n"


def test_manual_rollback_refuses_after_migration_marker(runtime):
    state = runtime["tmp"] / "state"
    state.mkdir()
    (state / "migration-started").touch()
    result = run(runtime, "rollback401.sh")
    assert result.returncode == 3
    assert "ROLLBACK401_REFUSED migration_started" in result.stderr
    assert not runtime["order"].exists()


def test_manual_rollback_confirms_0053_before_switch(runtime):
    state = runtime["tmp"] / "state"
    state.mkdir()
    result = run(runtime, "rollback401.sh")
    assert result.returncode == 0, result.stderr
    lines = runtime["order"].read_text(encoding="ascii").splitlines()
    assert lines.index("db_revision_read") < lines.index("compose:config:393")
    assert any("psql -XqAt -v ON_ERROR_STOP=1 -U postgres -d mentorflow" in line for line in lines)
    assert "compose:up:393" in lines
    assert result.stdout.splitlines()[-1] == "ROLLBACK401_OK image=mentorflow-java-server:393 schema_confirmed=0053"


def test_manual_rollback_refuses_unknown_database_revision(runtime):
    state = runtime["tmp"] / "state"
    state.mkdir()
    result = run(runtime, "rollback401.sh", {"FAKE_REVISION": "0054_sync_receipts_without_payload"})
    assert result.returncode == 3
    assert "ROLLBACK401_REFUSED database_revision_not_confirmed_0053" in result.stderr
    assert "compose:up:393" not in runtime["order"].read_text(encoding="ascii")


def test_deploy_requires_backup_restore_and_copy_migration_evidence(runtime):
    runtime["verification"].write_text("backup-restore=PASS\n", encoding="ascii")
    result = run(runtime, "deploy401.sh")
    assert result.returncode != 0
    assert not runtime["order"].exists()
    assert runtime["image"].read_text(encoding="ascii") == "mentorflow-java-server:393\n"
