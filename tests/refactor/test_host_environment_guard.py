import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("host_env_guard", Path(__file__).with_name("conftest.py"))
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def test_unchanged_environment_reports_nothing():
    snapshot = {"user:PATH": r"C:\a;C:\b", "system:TEMP": r"C:\t"}
    assert guard._changes(snapshot, dict(snapshot)) == []


def test_path_growth_and_new_variable_are_reported():
    before = {"user:PATH": r"C:\a"}
    after = {"user:PATH": r"C:\a;C:\dotnet\tools", "user:NEW": "x"}
    lines = guard._changes(before, after)
    assert len(lines) == 2
    assert "user:PATH" in lines[1] and r"C:\dotnet\tools" in lines[1]
    assert lines[0].startswith("user:NEW: 0 -> 1")


def test_real_host_snapshot_reads_path():
    snapshot = guard._host_environment()
    assert not snapshot or "user:PATH" in snapshot or "system:PATH" in snapshot
