from pathlib import Path

from scripts.verify_windows_installer import verify_script


ROOT = Path(__file__).resolve().parents[2]


def test_windows_installer_contract_is_explicit_and_per_user():
    report = verify_script(ROOT / "installer" / "MentorFlow.nsi")
    assert all(report["checks"].values())


def test_windows_installer_build_path_is_separate_from_distributable():
    wrapper = (ROOT / "scripts" / "build_windows_installer.ps1").read_text(encoding="utf-8")
    assert "artifacts\\windows-installer" in wrapper
    assert "release\\distributable" in wrapper
    assert "Payload verification failed" in wrapper
    assert "v3\\.12" in wrapper
    assert "[string]$PayloadRoot = ''" in wrapper
    assert "[string]$OutputRoot = ''" in wrapper
    assert "Join-Path $ProjectRoot 'release\\distributable\\MentorFlow'" in wrapper
    assert "MAX_INSTALL_DIR_CHARS" in wrapper
    assert "longestPayloadRelativePath" in wrapper
