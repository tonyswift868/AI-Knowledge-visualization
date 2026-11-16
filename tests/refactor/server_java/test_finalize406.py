"""Keep Docker cleanup behind deployment and local E2E success gates."""
from __future__ import annotations

import subprocess
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FINALIZER_PATH = ROOT / "refactor" / "server-java" / "deploy" / "finalize406.py"
SPEC = importlib.util.spec_from_file_location("finalize406", FINALIZER_PATH)
assert SPEC and SPEC.loader
finalize406 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finalize406)


def test_finalize406_defaults_to_offline_plan(monkeypatch, capsys):
    monkeypatch.setattr(finalize406, "remote", lambda _script: pytest.fail("unexpected SSH"))
    assert finalize406.main([]) == 0
    assert "DRY PLAN" in capsys.readouterr().out


@pytest.mark.parametrize("failed_tool,log_name", [
    ("e2e_sync_cleanup401.py", "sync-cleanup401.log"),
    ("e2e_sync_cleanup406.py", "plan-cleanup406.log"),
])
def test_finalize406_e2e_failure_skips_storage_and_docker(monkeypatch, tmp_path, failed_tool, log_name):
    tool401 = tmp_path / "e2e_sync_cleanup401.py"
    tool406 = tmp_path / "e2e_sync_cleanup406.py"
    tool401.write_text("# fake\n", encoding="ascii")
    tool406.write_text("# fake\n", encoding="ascii")
    monkeypatch.setattr(finalize406, "E2E_401", tool401)
    monkeypatch.setattr(finalize406, "E2E_406", tool406)
    monkeypatch.setattr(finalize406, "SCRATCH", tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(finalize406, "remote", lambda script: calls.append(script) or subprocess.CompletedProcess([], 0, b"FINALIZE406_DEPLOYMENT_GATE_OK\n", b""))
    def failed_e2e(argv, **kwargs):
        code = 2 if Path(argv[1]).name == failed_tool else 0
        return subprocess.CompletedProcess([], code, b"safe result", b"safe error")

    monkeypatch.setattr(finalize406.subprocess, "run", failed_e2e)
    assert finalize406.main(["--execute"]) == 2
    assert len(calls) == 1
    log = tmp_path / log_name
    assert log.is_file()
    assert b"safe result" in log.read_bytes()


def test_finalize406_orders_e2e_storage_then_docker(monkeypatch, tmp_path):
    tool401 = tmp_path / "e2e_sync_cleanup401.py"
    tool406 = tmp_path / "e2e_sync_cleanup406.py"
    tool401.write_text("# fake\n", encoding="ascii")
    tool406.write_text("# fake\n", encoding="ascii")
    monkeypatch.setattr(finalize406, "E2E_401", tool401)
    monkeypatch.setattr(finalize406, "E2E_406", tool406)
    monkeypatch.setattr(finalize406, "SCRATCH", tmp_path)
    calls: list[str] = []

    def remote(script):
        calls.append(script)
        marker = b"FINALIZE406_DEPLOYMENT_GATE_OK\n" if len(calls) == 1 else b"FINALIZE406_OK\n"
        return subprocess.CompletedProcess([], 0, marker, b"")

    monkeypatch.setattr(finalize406, "remote", remote)

    def run(argv, **kwargs):
        calls.append(Path(argv[1]).name)
        assert argv[1] in (str(tool401), str(tool406))
        assert argv[2:] == ["--execute", "--account-index", "0"]
        return subprocess.CompletedProcess([], 0, b"", b"")

    monkeypatch.setattr(finalize406.subprocess, "run", run)
    assert finalize406.main(["--execute"]) == 0
    assert calls[0].find("job406") >= 0 and calls[0].find("deploy406/complete") >= 0
    assert calls[1:3] == [tool401.name, tool406.name]
    assert "storage-report.sh" in calls[3]
    assert calls[3].find("storage-report.sh") < calls[3].find("cleanup-docker406.sh")
    assert "MIGRATION_IMAGE=mentorflow-slim-backend:269-python-fallback" in calls[3]
    assert "docker-cleanup-started" in calls[3]
    assert "docker-cleanup-complete" in calls[3]
    assert (tmp_path / "sync-cleanup401.log").is_file()
    assert (tmp_path / "plan-cleanup406.log").is_file()
