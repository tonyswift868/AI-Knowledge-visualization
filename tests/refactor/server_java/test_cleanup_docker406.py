"""Exercise cleanup-docker406 without a Docker daemon."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "refactor" / "server-java" / "deploy" / "cleanup-docker406.sh"
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def posix(path: Path) -> str:
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if len(value) >= 2 and value[1] == ":" else value


def fixture(tmp_path: Path, *, fail_remove: str = "") -> tuple[dict[str, str], Path]:
    assert BASH.is_file(), "Git for Windows Bash is required"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\nset -eu\nprintf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
        "case \"$1:$2\" in\n"
        " 'system:df') printf 'TYPE TOTAL\nImages 6\n' ;;\n"
        " 'image:ls') cat <<'IMAGES' | { if [ \"${FAKE_OMIT_393:-0}\" = 1 ]; then grep -v 'mentorflow-java-server:393'; else cat; fi; }\n"
        "sha256:new mentorflow-java-server:406\n"
        "sha256:new local/java-alias:stable\n"
        "sha256:old mentorflow-java-server:393\n"
        "sha256:migration mentorflow-migration-tool:MF-275\n"
        "sha256:migration-base mentorflow-slim-backend:269-python-fallback\n"
        "sha256:running unused/running:latest\n"
        "sha256:stopped unused/stopped:latest\n"
        "sha256:free unused/free:latest\n"
        "sha256:free unused/free-alias:latest\n"
        "sha256:blocked unused/blocked:latest\n"
        "IMAGES\n ;;\n"
        " 'ps:-aq') printf 'running-container\\nstopped-container\\n' ;;\n"
        " 'inspect:--format') case \"$4\" in running-container) printf 'sha256:running\\n' ;; stopped-container) printf 'sha256:stopped\\n' ;; *) exit 8 ;; esac ;;\n"
        " 'builder:prune') printf 'cache reclaimed\\n' ;;\n"
        " 'image:rm') [ \"$3\" != \"${FAKE_FAIL_REMOVE:-}\" ] || { echo 'image in use' >&2; exit 1; }; printf 'Untagged: %s\\n' \"$3\" ;;\n"
        " *) echo \"unexpected docker invocation: $*\" >&2; exit 9 ;;\nesac\n",
        encoding="ascii",
        newline="\n",
    )
    docker.chmod(0o755)
    df = bin_dir / "df"
    df.write_text("#!/bin/sh\nprintf 'Filesystem Size Used\\n/dev/fake 100G 50G\\n'\n", encoding="ascii", newline="\n")
    df.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    env = os.environ.copy()
    env.update({
        "PATH": posix(bin_dir) + ":" + env.get("PATH", ""),
        "STATE": posix(state),
        "JAVA_IMAGE": "mentorflow-java-server:406",
        "MIGRATION_TOOL_IMAGE": "mentorflow-migration-tool:MF-275",
        "DOCKER_BIN": posix(docker),
        "FAKE_DOCKER_LOG": posix(log),
        "FAKE_FAIL_REMOVE": fail_remove,
        "TMPDIR": posix(tmp_path),
        "TEMP": posix(tmp_path),
        "TMP": posix(tmp_path),
    })
    return env, log


def run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(BASH), "-c", 'sh "$1"', "_", posix(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=30)


def test_cleanup_preserves_deployed_old_migration_and_all_container_image_ids(tmp_path: Path):
    env, log = fixture(tmp_path, fail_remove="sha256:blocked")
    result = run(env)
    assert result.returncode == 0, result.stderr
    commands = log.read_text(encoding="ascii").splitlines()
    removed = [line.split()[-1] for line in commands if line.startswith("image rm ")]
    assert removed == ["sha256:free", "sha256:blocked"]
    assert commands.count("image rm sha256:free") == 1  # same image ID has two tags
    assert "builder prune -a -f" in commands
    metrics = (tmp_path / "state/metrics.txt").read_text(encoding="ascii")
    assert "docker_system_df_before" in metrics and "filesystem_root_before" in metrics
    assert "docker_system_df_after" in metrics and "filesystem_root_after" in metrics
    assert "docker_cleanup406_removed=1 kept=6 remove_failed=1" in metrics
    assert "image_remove_skipped id=sha256:blocked" in metrics


def test_failed_remove_does_not_stop_following_images(tmp_path: Path):
    env, log = fixture(tmp_path, fail_remove="sha256:free")
    result = run(env)
    assert result.returncode == 0, result.stderr
    commands = log.read_text(encoding="ascii").splitlines()
    assert "image rm sha256:free" in commands
    assert "image rm sha256:blocked" in commands
    metrics = (tmp_path / "state/metrics.txt").read_text(encoding="ascii")
    assert "docker_cleanup406_removed=1 kept=6 remove_failed=1" in metrics


def test_missing_retained_image_stops_before_builder_prune(tmp_path: Path):
    env, log = fixture(tmp_path)
    env["JAVA_IMAGE"] = "mentorflow-java-server:missing"
    result = run(env)
    assert result.returncode != 0
    assert "builder prune -a -f" not in log.read_text(encoding="ascii").splitlines()


def test_missing_java393_rollback_image_stops_before_prune_or_rmi(tmp_path: Path):
    env, log = fixture(tmp_path)
    env["FAKE_OMIT_393"] = "1"
    result = run(env)
    assert result.returncode != 0
    commands = log.read_text(encoding="ascii").splitlines()
    assert "builder prune -a -f" not in commands
    assert not any(line.startswith("image rm ") for line in commands)


def test_migration_build_base_is_kept_without_container_reference(tmp_path: Path):
    env, log = fixture(tmp_path)
    result = run(env)
    assert result.returncode == 0, result.stderr
    commands = log.read_text(encoding="ascii").splitlines()
    assert "inspect --format {{.Image}} running-container" in commands
    assert "inspect --format {{.Image}} stopped-container" in commands
    assert not any(line == "image rm sha256:migration-base" for line in commands)
    metrics = (tmp_path / "state/metrics.txt").read_text(encoding="ascii")
    assert "image_keep id=sha256:migration-base ref=mentorflow-slim-backend:269-python-fallback" in metrics
