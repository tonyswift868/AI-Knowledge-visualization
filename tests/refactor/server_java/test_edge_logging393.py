from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
EDGE = ROOT / "refactor" / "server-java" / "deploy" / "edge"
BASH = Path(r"C:\Program Files\Git\bin\bash.exe")


def posix(path: Path) -> str:
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if len(value) >= 2 and value[1] == ":" else value


def config() -> str:
    return """events { worker_connections 64; }
http {
    access_log off;
    server {
        server_name _;
        access_log off;
    }
    server {
        server_name auth.clannad.win;
        access_log off;
    }
    server {
        server_name aiteacher.clannad.win;
        access_log off;
    }
}
"""


def test_assembler_only_enables_the_two_requested_vhosts(tmp_path):
    source = tmp_path / "edge.conf"
    source.write_text(config(), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(EDGE / "edge-config393.py"), "apply", str(source)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.count("log_format mentorflow_edge") == 1
    assert result.stdout.count("access_log /var/log/nginx/mentorflow-access.log mentorflow_edge;") == 2
    assert result.stdout.count("access_log off;") == 2
    assert "$time_iso8601\\t$server_name\\t$status" in result.stdout
    assert "remote_addr" not in result.stdout
    assert "request" not in result.stdout


def test_assembler_rejects_missing_or_ambiguous_target(tmp_path):
    source = tmp_path / "edge.conf"
    source.write_text(config().replace("aiteacher.clannad.win", "other.example"), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(EDGE / "edge-config393.py"), "apply", str(source)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "expected one server" in result.stderr


def test_install_and_rollback_keep_the_config_path_and_restore_content(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    proc = tmp_path / "proc"
    log_file = proc / "4321" / "root" / "var" / "log" / "nginx" / "mentorflow-access.log"
    log_file.parent.mkdir(parents=True)
    log_file.touch()
    docker = bin_dir / "docker"
    docker.write_text(
        '#!/bin/sh\nif [ "$1" = inspect ]; then printf "4321\\n"; else printf "%s\\n" "$*" >> "$FAKE_DOCKER_LOG"; fi\n',
        encoding="ascii",
        newline="\n",
    )
    docker.chmod(0o755)
    logrotate = bin_dir / "logrotate"
    logrotate.write_text("#!/bin/sh\nfor arg do config=$arg; done\ncat \"$config\" >> \"$FAKE_ROTATE_CAPTURE\"\nexit 0\n", encoding="ascii", newline="\n")
    logrotate.chmod(0o755)
    python = bin_dir / "python3"
    python.write_text("#!/bin/sh\nexec /d/MentorFlow/.venv/Scripts/python.exe \"$@\"\n", encoding="ascii", newline="\n")
    python.chmod(0o755)
    conf = tmp_path / "edge.conf"
    original = config()
    conf.write_text(original, encoding="utf-8", newline="\n")
    backup = tmp_path / "edge.conf.backup"
    cron = tmp_path / "cron.d" / "edge393"
    cron.parent.mkdir()
    rotate_tmp = tmp_path / "run"
    rotate_tmp.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "PATH": posix(bin_dir) + ":" + env["PATH"],
            "DOCKER_BIN": posix(docker),
            "PROC_ROOT": posix(proc),
            "ROTATE_TMPDIR": posix(rotate_tmp),
            "FAKE_ROTATE_CAPTURE": posix(tmp_path / "rotate.conf.capture"),
            "FAKE_DOCKER_LOG": posix(tmp_path / "docker.log"),
            "CRON_FILE": posix(cron),
        }
    )
    before_inode = conf.stat().st_ino
    installer = EDGE / "install-edge-logging393.sh"
    install = subprocess.run([str(BASH), "-c", 'sh "$1" install "$2" "$3"', "_", posix(installer), posix(conf), posix(backup)], env=env, capture_output=True, text=True)
    assert install.returncode == 0, install.stderr
    assert conf.stat().st_ino == before_inode
    assert conf.read_text(encoding="utf-8").count("access_log /var/log/nginx/mentorflow-access.log mentorflow_edge;") == 2
    rotation_text = (tmp_path / "rotate.conf.capture").read_text(encoding="utf-8")
    assert "/proc/4321/root/var/log/nginx/mentorflow-access.log" in rotation_text
    assert "size 1M" in rotation_text
    assert "rotate 7" in rotation_text
    assert "nginx -s reopen" in rotation_text
    assert "* * * * *" in cron.read_text(encoding="utf-8")
    assert "rotate-edge-access393.sh rotate" in cron.read_text(encoding="utf-8")

    rollback = subprocess.run([str(BASH), "-c", 'sh "$1" rollback "$2" "$3"', "_", posix(installer), posix(conf), posix(backup)], env=env, capture_output=True, text=True)
    assert rollback.returncode == 0, rollback.stderr
    assert conf.stat().st_ino == before_inode
    assert conf.read_text(encoding="utf-8") == original
    assert not cron.exists()
    commands = (tmp_path / "docker.log").read_text(encoding="ascii")
    assert "exec mentorflow-e1-preview-edge-1 nginx -t" in commands
    assert "exec mentorflow-e1-preview-edge-1 nginx -s reload" in commands


def test_failed_nginx_check_restores_original_and_removes_partial_rotation(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    proc = tmp_path / "proc"
    log_file = proc / "4321" / "root" / "var" / "log" / "nginx" / "mentorflow-access.log"
    log_file.parent.mkdir(parents=True)
    log_file.touch()
    docker = bin_dir / "docker"
    docker.write_text(
        '#!/bin/sh\nif [ "$1" = inspect ]; then printf "4321\\n"; else printf "%s\\n" "$*" >> "$FAKE_DOCKER_LOG"; if [ "$*" = "exec mentorflow-e1-preview-edge-1 nginx -t" ] && [ ! -e "$FAKE_FAIL_MARKER" ]; then touch "$FAKE_FAIL_MARKER"; exit 1; fi; fi\n',
        encoding="ascii",
        newline="\n",
    )
    docker.chmod(0o755)
    logrotate = bin_dir / "logrotate"
    logrotate.write_text("#!/bin/sh\nexit 0\n", encoding="ascii", newline="\n")
    logrotate.chmod(0o755)
    python = bin_dir / "python3"
    python.write_text("#!/bin/sh\nexec /d/MentorFlow/.venv/Scripts/python.exe \"$@\"\n", encoding="ascii", newline="\n")
    python.chmod(0o755)
    conf = tmp_path / "edge.conf"
    original = config()
    conf.write_text(original, encoding="utf-8", newline="\n")
    backup = tmp_path / "edge.conf.backup"
    cron_dir = tmp_path / "cron.d"
    cron_dir.mkdir()
    rotate_tmp = tmp_path / "run"
    rotate_tmp.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "PATH": posix(bin_dir) + ":" + env["PATH"],
            "DOCKER_BIN": posix(docker),
            "PROC_ROOT": posix(proc),
            "ROTATE_TMPDIR": posix(rotate_tmp),
            "FAKE_DOCKER_LOG": posix(tmp_path / "docker.log"),
            "FAKE_FAIL_MARKER": posix(tmp_path / "failed-once"),
            "CRON_FILE": posix(cron_dir / "edge393"),
        }
    )
    before_inode = conf.stat().st_ino
    installer = EDGE / "install-edge-logging393.sh"
    result = subprocess.run([str(BASH), "-c", 'sh "$1" install "$2" "$3"', "_", posix(installer), posix(conf), posix(backup)], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert conf.stat().st_ino == before_inode
    assert conf.read_text(encoding="utf-8") == original
    assert not (cron_dir / "edge393").exists()
    assert backup.read_text(encoding="utf-8") == original
    commands = (tmp_path / "docker.log").read_text(encoding="ascii")
    assert "exec mentorflow-e1-preview-edge-1 nginx -s reload" in commands


def test_restore_reload_failure_keeps_backup_and_marks_edge_rollback_failure(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    proc = tmp_path / "proc"
    log_file = proc / "4321" / "root" / "var" / "log" / "nginx" / "mentorflow-access.log"
    log_file.parent.mkdir(parents=True)
    log_file.touch()
    docker = bin_dir / "docker"
    docker.write_text(
        '#!/bin/sh\nif [ "$1" = inspect ]; then printf "4321\\n"; else printf "%s\\n" "$*" >> "$FAKE_DOCKER_LOG"; if [ "$*" = "exec mentorflow-e1-preview-edge-1 nginx -s reload" ]; then exit 1; fi; fi\n',
        encoding="ascii",
        newline="\n",
    )
    docker.chmod(0o755)
    logrotate = bin_dir / "logrotate"
    logrotate.write_text("#!/bin/sh\nexit 0\n", encoding="ascii", newline="\n")
    logrotate.chmod(0o755)
    python = bin_dir / "python3"
    python.write_text("#!/bin/sh\nexec /d/MentorFlow/.venv/Scripts/python.exe \"$@\"\n", encoding="ascii", newline="\n")
    python.chmod(0o755)
    conf = tmp_path / "edge.conf"
    original = config()
    conf.write_text(original, encoding="utf-8", newline="\n")
    backup = tmp_path / "edge.conf.backup"
    cron_dir = tmp_path / "cron.d"
    cron_dir.mkdir()
    rotate_tmp = tmp_path / "run"
    rotate_tmp.mkdir()
    marker = Path(str(backup) + ".rollback-failed")
    env = os.environ.copy()
    env.update(
        {
            "PATH": posix(bin_dir) + ":" + env["PATH"],
            "DOCKER_BIN": posix(docker),
            "PROC_ROOT": posix(proc),
            "ROTATE_TMPDIR": posix(rotate_tmp),
            "FAKE_DOCKER_LOG": posix(tmp_path / "docker.log"),
            "CRON_FILE": posix(cron_dir / "edge393"),
        }
    )
    installer = EDGE / "install-edge-logging393.sh"
    result = subprocess.run([str(BASH), "-c", 'sh "$1" install "$2" "$3"', "_", posix(installer), posix(conf), posix(backup)], env=env, capture_output=True, text=True)
    assert result.returncode == 70
    assert "EDGE393_ROLLBACK_FAILED reason=nginx-reload" in result.stderr
    assert marker.read_text(encoding="ascii") == "reason=nginx-reload\n"
    assert backup.read_text(encoding="utf-8") == original
    assert conf.read_text(encoding="utf-8") == original
    assert (cron_dir / "edge393").exists()


def test_rotation_helper_resolves_container_pid_each_run(tmp_path):
    proc = tmp_path / "proc"
    for pid in ("4321", "9876"):
        log_file = proc / pid / "root" / "var" / "log" / "nginx" / "mentorflow-access.log"
        log_file.parent.mkdir(parents=True)
        log_file.touch()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_DOCKER_LOG"\nif [ "$1" = inspect ]; then if [ -e "$FAKE_PID_CHANGED" ]; then printf "9876\\n"; else touch "$FAKE_PID_CHANGED"; printf "4321\\n"; fi; else exit 9; fi\n',
        encoding="ascii",
        newline="\n",
    )
    docker.chmod(0o755)
    logrotate = bin_dir / "logrotate"
    logrotate.write_text(
        "#!/bin/sh\nfor arg do config=$arg; done\ncat \"$config\" >> \"$FAKE_ROTATE_CAPTURE\"\nexit 0\n",
        encoding="ascii",
        newline="\n",
    )
    logrotate.chmod(0o755)
    rotate_tmp = tmp_path / "run"
    rotate_tmp.mkdir()
    capture = tmp_path / "configs"
    env = os.environ.copy()
    env.update(
        {
            "DOCKER_BIN": posix(docker),
            "LOGROTATE_BIN": posix(logrotate),
            "PROC_ROOT": posix(proc),
            "ROTATE_TMPDIR": posix(rotate_tmp),
            "LOGROTATE_STATUS": posix(tmp_path / "status"),
            "FAKE_PID_CHANGED": posix(tmp_path / "pid-changed"),
            "FAKE_ROTATE_CAPTURE": posix(capture),
            "FAKE_DOCKER_LOG": posix(tmp_path / "docker.log"),
        }
    )
    helper = EDGE / "rotate-edge-access393.sh"
    for _ in range(2):
        result = subprocess.run([str(BASH), "-c", 'sh "$1" rotate', "_", posix(helper)], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    configs = capture.read_text(encoding="utf-8")
    assert "/proc/4321/root/var/log/nginx/mentorflow-access.log" in configs
    assert "/proc/9876/root/var/log/nginx/mentorflow-access.log" in configs
    docker_calls = (tmp_path / "docker.log").read_text(encoding="ascii").splitlines()
    assert len(docker_calls) == 2
    assert all(".State.Pid" in call for call in docker_calls)
    assert all("GraphDriver" not in call for call in docker_calls)
