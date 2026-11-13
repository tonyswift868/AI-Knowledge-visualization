from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
CLIENT = ROOT / "refactor/server-java/deploy/monitoring/client"
POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL.is_file(), reason="requires Windows PowerShell 5.1")
def test_workbench_scripts_are_bom_and_parse_in_windows_powershell():
    for name in ("open-workbench.ps1", "create-workbench-shortcut.ps1"):
        data = (CLIENT / name).read_bytes()
        assert data.startswith(b"\xef\xbb\xbf")
        path = CLIENT / name
        command = (
            "$e=$null; $t=$null; [System.Management.Automation.Language.Parser]::ParseFile"
            f"('{path}',[ref]$t,[ref]$e)|Out-Null; if($e.Count){{$e|% Message; exit 1}}"
        )
        result = subprocess.run([str(POWERSHELL), "-NoProfile", "-Command", command],
                                capture_output=True, encoding="utf-8", errors="replace", timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt" or not POWERSHELL.is_file(), reason="requires Windows PowerShell 5.1")
def test_shortcut_uses_system_powershell_and_stable_script_path(tmp_path: Path):
    target = Path(r"D:\MentorFlow\refactor\server-java\deploy\monitoring\client\open-workbench.ps1")
    if not target.is_file():
        pytest.skip("main checkout workbench script is unavailable")
    script = CLIENT / "create-workbench-shortcut.ps1"
    subprocess.run([str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                    "-TargetScript", str(target), "-ShortcutDirectory", str(tmp_path)],
                   check=True, capture_output=True, encoding="utf-8", errors="replace", timeout=20)
    shortcut = tmp_path / "MentorFlow 工作台.lnk"
    assert shortcut.is_file()
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('" + str(shortcut) + "');"
        "[Console]::WriteLine($s.TargetPath); [Console]::WriteLine($s.Arguments);"
        "[Console]::WriteLine($s.WorkingDirectory)"
    )
    result = subprocess.run([str(POWERSHELL), "-NoProfile", "-Command", ps],
                            check=True, capture_output=True, encoding="utf-8", errors="replace", timeout=20)
    target_line, args, working_directory = result.stdout.splitlines()[:3]
    expected = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) /
                   "System32/WindowsPowerShell/v1.0/powershell.exe")
    assert Path(target_line).resolve() == Path(expected).resolve()
    assert f'-File "{target}"' in args
    assert Path(working_directory).resolve() == target.parent.resolve()
    combined = (target_line + args + working_directory).casefold()
    assert "windowsapps" not in combined and "pwsh" not in combined
    assert "mentorflow-office" not in combined
