"""Exercise cleanup-docker406 without a Docker daemon."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "refactor" / "server-java" / "deploy" / "cleanup-docker406.sh"
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
IMAGES = """sha256:new mentorflow-java-server:406
sha256:new local/java-alias:stable
sha256:old mentorflow-java-server:393
sha256:migration mentorflow-migration-tool:MF-275
sha256:migration-base mentorflow-slim-backend:269-python-fallback
sha256:running unused/running:latest
sha256:stopped unused/stopped:latest
sha256:free unused/free:latest
sha256:free unused/free-alias:latest
sha256:blocked unused/blocked:latest
"""
CERT_IMAGE = "sha256:cert certbot/certbot:v5.4.0\n"
CERT_REFS = "certbot/certbot@sha256:cert sha256:cert\ncertbot/certbot:v5.4.0 sha256:cert\n"


def posix(path: Path) -> str:
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if len(value) >= 2 and value[1] == ":" else value


def fixture(tmp_path: Path, *, fail_remove: str = "", omit_393: bool = False, images: str = "",
            refs: str = "", renew: str | None = None, write_renew_script: bool = True,
            broken_unit: bool = False) -> tuple[dict[str, str], Path]:
    assert BASH.is_file(), "Git for Windows Bash is required"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.log"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/bin/sh\nset -eu\nprintf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
        "case \"$1:$2\" in\n"
        " 'system:df') printf 'TYPE TOTAL\\nImages 6\\n' ;;\n"
        " 'image:ls') cat \"$FAKE_IMAGES\" ;;\n"
        " 'image:inspect') id=$(awk -v r=\"$5\" '$1 == r { print $2; exit }' \"$FAKE_REFS\"); [ -n \"$id\" ] || exit 1; printf '%s\\n' \"$id\" ;;\n"
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
    images_file = tmp_path / "images.txt"
    lines = IMAGES + images
    if omit_393:
        lines = "".join(line for line in lines.splitlines(keepends=True) if "mentorflow-java-server:393" not in line)
    images_file.write_text(lines, encoding="ascii", newline="\n")
    refs_file = tmp_path / "refs.txt"
    refs_file.write_text(refs, encoding="ascii", newline="\n")
    # Systemd units and scripts under a temp root stand in for /etc/systemd/system and /opt/mentorflow.
    units = tmp_path / "units"
    scripts = tmp_path / "scripts"
    units.mkdir()
    scripts.mkdir()
    (scripts / "heartbeat.sh").write_text("#!/bin/sh\necho ok\n", encoding="ascii", newline="\n")
    (units / "mentorflow-heartbeat.service").write_text(
        f"[Service]\nExecStart={posix(scripts)}/heartbeat.sh\n", encoding="ascii", newline="\n")
    if renew is not None:
        (units / "mentorflow-e108-ip-renew.service").write_text(
            f"[Service]\nExecStart={posix(scripts)}/renew.sh\nExecStartPost=-{posix(scripts)}/heartbeat.sh\n",
            encoding="ascii", newline="\n")
        if write_renew_script:
            (scripts / "renew.sh").write_text(
                "#!/bin/sh\nset -eu\n"
                f"docker run --rm --pull=never \\\n  --network none \\\n  {renew} certbot renew\n",
                encoding="ascii", newline="\n")
    if broken_unit:
        (units / "mentorflow-broken.service").mkdir()
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
        "FAKE_IMAGES": posix(images_file),
        "FAKE_REFS": posix(refs_file),
        "SYSTEMD_UNIT_ROOT": posix(units),
        "MF_SCRIPT_ROOT": posix(scripts),
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
    env, log = fixture(tmp_path, omit_393=True)
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


@pytest.mark.parametrize("ref", ["certbot/certbot@sha256:cert", "certbot/certbot:v5.4.0"])
def test_image_run_by_timer_script_is_kept_by_digest_or_tag(tmp_path: Path, ref: str):
    env, log = fixture(tmp_path, images=CERT_IMAGE, refs=CERT_REFS, renew=ref)
    result = run(env)
    assert result.returncode == 0, result.stderr
    commands = log.read_text(encoding="ascii").splitlines()
    assert not any(line == "image rm sha256:cert" for line in commands)
    metrics = (tmp_path / "state/metrics.txt").read_text(encoding="ascii")
    assert f"image_ref_keep ref={ref} id=sha256:cert" in metrics
    assert "image_keep id=sha256:cert" in metrics
    assert "docker_cleanup406_removed=2 kept=7 remove_failed=0" in metrics


def test_image_not_referenced_by_any_unit_is_still_removed(tmp_path: Path):
    env, log = fixture(tmp_path, images=CERT_IMAGE, refs=CERT_REFS)
    result = run(env)
    assert result.returncode == 0, result.stderr
    commands = log.read_text(encoding="ascii").splitlines()
    assert "image rm sha256:cert" in commands


def test_reference_not_present_locally_is_logged_not_an_error(tmp_path: Path):
    env, log = fixture(tmp_path, renew="certbot/certbot@sha256:absent")
    result = run(env)
    assert result.returncode == 0, result.stderr
    metrics = (tmp_path / "state/metrics.txt").read_text(encoding="ascii")
    assert "image_ref_not_local ref=certbot/certbot@sha256:absent ignored" in metrics


def test_unreadable_unit_removes_nothing(tmp_path: Path):
    env, log = fixture(tmp_path, broken_unit=True)
    result = run(env)
    assert result.returncode != 0
    commands = log.read_text(encoding="ascii").splitlines()
    assert not any(line.startswith("image rm ") for line in commands)
    assert "builder prune -a -f" not in commands


def test_script_run_by_unit_missing_removes_nothing(tmp_path: Path):
    env, log = fixture(tmp_path, images=CERT_IMAGE, refs=CERT_REFS,
                       renew="certbot/certbot@sha256:cert", write_renew_script=False)
    result = run(env)
    assert result.returncode != 0
    commands = log.read_text(encoding="ascii").splitlines()
    assert not any(line.startswith("image rm ") for line in commands)
    assert "builder prune -a -f" not in commands
