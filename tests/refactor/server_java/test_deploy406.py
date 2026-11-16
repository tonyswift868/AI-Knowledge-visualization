"""Exercise the 406 cutover and rollback with the isolated 401 fake runtime."""
from __future__ import annotations

import shutil

import pytest
import test_deploy401 as legacy


@pytest.fixture
def runtime(tmp_path):
    state = legacy.runtime.__wrapped__(tmp_path)
    deploy = state["candidate_deploy"]
    source = legacy.DEPLOY
    for name in ("deploy406.sh", "rollback406.sh", "preflight406.sh"):
        shutil.copyfile(source / name, deploy / name)
    (deploy / "preflight.sh").write_text(
        "#!/bin/sh\nif [ -n \"${PREFLIGHT_SOURCE_REVISION:-}\" ]; then event=candidate_preflight; else event=final_preflight; fi\nprintf '%s\\n' \"$event\" >> '"
        + legacy.posix(state["order"]) + "'\n",
        encoding="ascii", newline="\n",
    )
    (deploy / "preflight.sh").chmod(0o755)
    (deploy / "preflight406.sh").write_text(
        "#!/bin/sh\nexec sh \"$(dirname \"$0\")/preflight.sh\" \"$@\"\n",
        encoding="ascii", newline="\n",
    )
    (deploy / "preflight406.sh").chmod(0o755)
    (deploy / "build.sh").write_text(
        "#!/bin/sh\nset -eu\ncd \"$(dirname \"$0\")\"\nprintf 'build:%s\\n' \"$1\" >> \"$FAKE_ORDER\"\nprintf 'MF_JAVA_VERSION=%s\\n' \"$1\" > .env\n",
        encoding="ascii", newline="\n",
    )
    (deploy / "build.sh").chmod(0o755)
    (deploy / "migrate.sh").write_text(
        "#!/bin/sh\nset -eu\ncase \"$1\" in\n"
        " current) if [ -f \"$FAKE_REVISION_FILE\" ]; then cat \"$FAKE_REVISION_FILE\"; else printf '0053_workbench_question_index'; fi ;;\n"
        " upgrade) test -f \"$FAKE_MIGRATION_MARKER\"; printf 'migration_0054\\n' >> \"$FAKE_ORDER\"; printf 'migration_0055\\n' >> \"$FAKE_ORDER\"; printf 'migration_0056\\n' >> \"$FAKE_ORDER\"; "
        "printf '0054 sync receipt payload scrub UPDATE elapsed 1.234 ms\\n0055 deleted plan progress scrub UPDATE elapsed 2.345 ms\\nclosure session cleanup elapsed 3.456 ms\\n'; "
        "if [ \"${FAIL_MIGRATION:-0}\" = 1 ]; then exit 1; fi; printf '0056_closure_session_cleanup\\n' > \"$FAKE_REVISION_FILE\" ;;\n"
        "esac\n",
        encoding="ascii", newline="\n",
    )
    (deploy / "migrate.sh").chmod(0o755)
    state["verification"].write_text(
        "backup-restore=PASS\ncopy-migration=PASS\ncopy-revision=0056_closure_session_cleanup\n",
        encoding="ascii",
    )
    return state


def run(runtime, script, extra=None):
    env = runtime["env"].copy()
    if extra:
        env.update(extra)
    return legacy.subprocess.run(
        [str(legacy.BASH), "-c", 'sh "$1"', "_", legacy.posix(runtime["candidate_deploy"] / script)],
        env=env, capture_output=True, text=True, timeout=60,
    )


def test_deploy406_runs_all_migrations_and_vacuums_both_tables(runtime):
    result = run(runtime, "deploy406.sh")
    assert result.returncode == 0, result.stderr
    lines = runtime["order"].read_text(encoding="ascii").splitlines()
    ordered = [
        "online_preflight", "candidate_preflight", "build:406", "jvm_check", "compose:up:406",
        "health_live", "health_startup", "health_ready", "java_probe", "table_size_before",
        "migration_0054", "migration_0055", "migration_0056", "db_revision_read",
        "vacuum", "vacuum", "table_size_after", "table_size_after", "final_preflight",
    ]
    cursor = -1
    for item in ordered:
        cursor = lines.index(item, cursor + 1)
    assert lines.count("compose:up:406") == 1
    assert "compose:up:393" not in lines
    assert (runtime["tmp"] / "state/migration-started").is_file()
    assert (runtime["tmp"] / "state/complete").is_file()
    assert result.stdout.count("DEPLOY406_OK") == 1
    for metric in (
        "migration_0054_update_elapsed_ms=1.234", "migration_0055_update_elapsed_ms=2.345",
        "migration_0056_elapsed_ms=3.456", "structured_sync_requests_before_bytes=4096",
        "structured_sync_entities_before_bytes=4096", "structured_sync_requests_after_vacuum_bytes=4096",
        "structured_sync_entities_after_vacuum_bytes=4096",
    ):
        assert metric in result.stdout


def test_deploy406_pre_migration_failure_uses_393_rollback(runtime):
    result = run(runtime, "deploy406.sh", {"FAIL_PROBE": "1"})
    assert result.returncode != 0
    assert "AUTO_ROLLBACK406_OK" in result.stderr
    lines = runtime["order"].read_text(encoding="ascii").splitlines()
    assert "compose:up:406" in lines and "compose:up:393" in lines
    assert "migration_0054" not in lines


def test_deploy406_migration_failure_never_rolls_back_or_cleans(runtime):
    result = run(runtime, "deploy406.sh", {"FAIL_MIGRATION": "1"})
    assert result.returncode != 0
    assert "AUTO_ROLLBACK406_FORBIDDEN migration_started" in result.stderr
    lines = runtime["order"].read_text(encoding="ascii").splitlines()
    assert (runtime["tmp"] / "state/migration-started").is_file()
    assert lines.count("compose:up:406") == 1
    assert "compose:up:393" not in lines
    assert not (runtime["tmp"] / "state/complete").exists()


def test_rollback406_refuses_unknown_revision(runtime):
    (runtime["tmp"] / "state").mkdir()
    result = run(runtime, "rollback406.sh", {"FAKE_REVISION": "0055_purge_deleted_plan_progress"})
    assert result.returncode == 3
    assert "ROLLBACK406_REFUSED database_revision_not_confirmed_0053" in result.stderr
    assert "compose:up:393" not in runtime["order"].read_text(encoding="ascii")


def test_rollback406_refuses_after_migration_marker(runtime):
    state = runtime["tmp"] / "state"
    state.mkdir()
    (state / "migration-started").touch()
    result = run(runtime, "rollback406.sh")
    assert result.returncode == 3
    assert "ROLLBACK406_REFUSED migration_started" in result.stderr
    assert not runtime["order"].exists()
