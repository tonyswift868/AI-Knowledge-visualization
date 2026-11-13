from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "refactor" / "server-java" / "deploy"
TMP = Path(os.environ.get("DEPLOY391_TMP", r"D:\MentorFlow\.local\test-tmp\391-MF01\deploy"))
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def posix(path: Path) -> str:
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if len(value) > 1 and value[1] == ":" else value


def test_migration_rollback_executes_downgrade_with_stateful_fake_docker(tmp_path):
    release = tmp_path / "candidate"
    deploy = release / "refactor/server-java/deploy"
    deploy.mkdir(parents=True)
    for name in ("rollback391.sh", "migrate.sh", "migration.Dockerfile"):
        (deploy / name).write_bytes((DEPLOY / name).read_bytes())
    server = release / "refactor/server"
    (server / "migrations/versions").mkdir(parents=True)
    (server / "alembic.ini").write_text("[alembic]\n", encoding="ascii")
    fake = tmp_path / "docker"
    log = tmp_path / "docker.log"
    fake.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
        "case \"$1\" in\n"
        " build) exit 0 ;;\n"
        " run)\n"
        "   case \"$*\" in *' current'*) cat \"$FAKE_REVISION\" ;; *' downgrade 0052_workbench_account_rows'*) echo 'Current revision(s): 0052_workbench_account_rows (head)' > \"$FAKE_REVISION\" ;; esac\n"
        "   exit 0 ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="ascii",
    )
    fake.chmod(0o755)
    revision = tmp_path / "revision"
    revision.write_text("Current revision(s): 0053_workbench_question_index (head)\n", encoding="ascii")
    env = os.environ.copy()
    env.update(RELEASE_ROOT=release.as_posix(), DOCKER_BIN=fake.as_posix(), FAKE_DOCKER_LOG=log.as_posix(), FAKE_REVISION=revision.as_posix())
    result = subprocess.run([str(BASH), "-c", 'sh "$1"', "_", str(deploy / "rollback391.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "ROLLBACK391_OK revision=0052_workbench_account_rows" in result.stdout
    calls = log.read_text(encoding="ascii")
    assert "downgrade 0052_workbench_account_rows" in calls
    assert "build" in calls and "run" in calls
    assert "0052_workbench_account_rows" in revision.read_text(encoding="ascii")


def test_migration_rollback_refuses_unexpected_revision(tmp_path):
    release = tmp_path / "candidate"
    deploy = release / "refactor/server-java/deploy"
    deploy.mkdir(parents=True)
    for name in ("rollback391.sh", "migrate.sh", "migration.Dockerfile"):
        shutil.copyfile(DEPLOY / name, deploy / name)
    server = release / "refactor/server"
    (server / "migrations/versions").mkdir(parents=True)
    (server / "alembic.ini").write_text("[alembic]\n", encoding="ascii")
    fake = tmp_path / "docker"
    fake.write_text("#!/bin/sh\ncase \"$1\" in build) exit 0;; run) echo 'Current revision(s): 0051_sync_closure_fence (head)';; esac\n", encoding="ascii")
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update(RELEASE_ROOT=release.as_posix(), DOCKER_BIN=fake.as_posix())
    result = subprocess.run([str(BASH), "-c", 'sh "$1"', "_", str(deploy / "rollback391.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "rollback refused" in result.stderr


def test_deploy391_success_and_migration_failure_order_in_fake_environment(tmp_path):
    release = tmp_path / "release"
    deploy = release / "refactor/server-java/deploy"
    deploy.mkdir(parents=True)
    for name in ("deploy391.sh", "migrate.sh"):
        shutil.copyfile(DEPLOY / name, deploy / name)
    (deploy / "migrate.sh").write_text(
        "#!/bin/sh\n"
        "case \"$1\" in current) printf 'migration|current\\n' >> \"$PIPELINE_LOG\";; upgrade) printf 'migration|upgrade head\\n' >> \"$PIPELINE_LOG\";; esac\n"
        "case \"$1\" in current) printf 'Current revision(s): %s (head)\\n' \"$(cat \"$FAKE_REVISION\")\";; "
        "upgrade) [ \"${FAIL_UPGRADE:-0}\" = 1 ] && exit 9; printf '0053_workbench_question_index\\n' > \"$FAKE_REVISION\";; esac\n",
        encoding="ascii",
    )
    # Git Bash cannot apply POSIX directory modes on the Windows temp filesystem.
    (deploy / "deploy391.sh").write_text(
        (deploy / "deploy391.sh").read_text(encoding="utf-8").replace('mkdir -m 700 -p "$state"', 'mkdir -p "$state"'),
        encoding="utf-8",
    )
    log = tmp_path / "pipeline.log"
    (release / "refactor/server/migrations/versions").mkdir(parents=True)
    (release / "refactor/server/alembic.ini").write_text("[alembic]\n", encoding="ascii")
    (deploy / "preflight.sh").write_text(
        "#!/bin/sh\nprintf 'preflight|argc=%s|env_count=%s\\n' \"$#\" \"$(env | wc -l | tr -d ' ')\" >> '" + log.as_posix() + "'\n",
        encoding="ascii",
    )
    manifest = tmp_path / "manifest"
    manifest.write_text("fixture\n", encoding="ascii")
    revision = tmp_path / "revision"
    revision.write_text("0052_workbench_account_rows\n", encoding="ascii")
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "printf 'docker|%s\\n' \"$*\" >> \"$PIPELINE_LOG\"\n"
        "case \"$1\" in\n"
        " exec) case \"$*\" in *pg_dump*) printf 'fake-dump\\n';; *pg_restore*) cat >/dev/null; printf 'LIST\\n';; esac ;;\n"
        "esac\n",
        encoding="ascii",
    )
    docker.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text("#!/bin/sh\nprintf 'curl|%s\\n' \"$*\" >> \"$PIPELINE_LOG\"; printf 200\n", encoding="ascii")
    curl.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_mkdir = fake_bin / "mkdir"
    fake_mkdir.write_text("#!/bin/sh\n[ \"$1\" = -m ] && shift 2\nexec /usr/bin/mkdir \"$@\"\n", encoding="ascii")
    fake_mkdir.chmod(0o755)
    shutil.copyfile(curl, fake_bin / "curl")
    (fake_bin / "curl").chmod(0o755)
    state = tmp_path / "state"
    common = os.environ.copy()
    common.update(RELEASE_ROOT=release.as_posix(), ONLINE_DEPLOY_DIR=deploy.as_posix(),
                  SOURCE_SHA256_MANIFEST=manifest.as_posix(), BACKEND_ENV=(tmp_path / "runtime.env").as_posix(),
                  DOCKER_BIN=posix(docker), CURL_BIN=posix(curl), PIPELINE_LOG=log.as_posix(),
                  FAKE_REVISION=revision.as_posix(), STATE=state.as_posix(), TEMP=tmp_path.as_posix(), TMP=tmp_path.as_posix(),
                  FAKE_BIN=posix(fake_bin))
    (tmp_path / "runtime.env").write_text("fixture\n", encoding="ascii")
    result = subprocess.run([str(BASH), "-c", 'sh "$1"', "_", str(deploy / "deploy391.sh")], env=common, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    lines = log.read_text(encoding="ascii").splitlines()
    assert lines[0].startswith("preflight|")
    assert next(i for i, x in enumerate(lines) if "pg_dump" in x) < next(i for i, x in enumerate(lines) if "pg_restore" in x)
    assert next(i for i, x in enumerate(lines) if "pg_restore" in x) < next(i for i, x in enumerate(lines) if "upgrade head" in x)
    assert next(i for i, x in enumerate(lines) if "upgrade head" in x) < next(i for i, x in enumerate(lines) if x.startswith("curl|"))
    assert lines[-1].startswith("preflight|argc=0|")
    assert "DEPLOY391_OK" in result.stdout

    failed_log = tmp_path / "failed.log"
    failed_state = tmp_path / "failed-state"
    failed_state.mkdir()
    (failed_state / "unused").write_text("x", encoding="ascii")
    failed_revision = tmp_path / "failed-revision"
    failed_revision.write_text("0052_workbench_account_rows\n", encoding="ascii")
    failed_env = {**common, "PIPELINE_LOG": failed_log.as_posix(), "FAKE_REVISION": failed_revision.as_posix(), "FAIL_UPGRADE": "1", "STATE": (tmp_path / "failed-state-new").as_posix()}
    failed = subprocess.run([str(BASH), "-c", 'sh "$1"', "_", str(deploy / "deploy391.sh")], env=failed_env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    assert failed.returncode != 0
    failed_lines = failed_log.read_text(encoding="ascii").splitlines()
    assert any("upgrade head" in x for x in failed_lines)
    assert not any(x.startswith("curl|") or x.startswith("preflight|") for x in failed_lines[1:])


def test_deploy391_never_rebuilds_or_restarts_java():
    source = (DEPLOY / "deploy391.sh").read_text(encoding="utf-8")
    assert "pg_dump" in source and "pg_restore --list" in source
    assert "migrate.sh\" upgrade" in source
    assert "http://127.0.0.1:18081/admin/accounts" in source
    assert "java-compose.sh" not in source and "docker build" not in source
    assert "sh refactor/server-java/deploy/preflight.sh" in source


def test_migrate_accepts_downgrade_revision():
    source = (DEPLOY / "migrate.sh").read_text(encoding="utf-8")
    assert 'downgrade) [ "$#" -eq 2 ] || usage' in source
    assert 'downgrade "$2"' in source
    assert 'current|upgrade|status) [ "$#" -le 1 ] || usage' in source
