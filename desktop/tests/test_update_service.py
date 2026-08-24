from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from desktop import update_service
from scripts import verify_windows_package


def _manifest(path: Path, *, enabled: bool = True) -> Path:
    payload = {
        "schema": "mentorflow.version.v1",
        "product": "MentorFlow",
        "version": "1.0.0",
        "channel": "stable",
        "update": {
            "enabled": enabled,
            "owner": "tonyswift868",
            "repo": "AI-Knowledge-visualization",
            "asset_name_pattern": "MentorFlow-{version}-win-x64-Setup.exe",
            "staging_root": str(path.parent / "MentorFlowUpdates"),
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _release(version: str, digest: str = "0" * 64) -> dict:
    return {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "html_url": f"https://github.com/tonyswift868/AI-Knowledge-visualization/releases/tag/v{version}",
        "assets": [
            {
                "name": f"MentorFlow-{version}-win-x64-Setup.exe",
                "digest": f"sha256:{digest}",
                "size": 12,
                "browser_download_url": "https://github.com/tonyswift868/AI-Knowledge-visualization/releases/download/v1.1.0/MentorFlow-1.1.0-win-x64-Setup.exe",
            }
        ],
    }


def test_fixture_check_does_not_open_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    version_file = _manifest(tmp_path / "mentorflow-version.json")
    fixture = tmp_path / "release.json"
    fixture.write_text(json.dumps(_release("1.0.0")), encoding="utf-8")

    def fail_network(*_args, **_kwargs):
        raise AssertionError("fixture check attempted network access")

    monkeypatch.setattr(verify_windows_package.urllib.request, "urlopen", fail_network)
    result = update_service.check_for_update(version_file=version_file, release_json=fixture)
    assert result == {"update": False, "current": "1.0.0", "latest": "1.0.0"}


def test_disabled_manifest_fails_closed_without_network(tmp_path: Path) -> None:
    version_file = _manifest(tmp_path / "mentorflow-version.json", enabled=False)
    with pytest.raises(update_service.UpdateError, match="未启用"):
        update_service.check_for_update(version_file=version_file)


def test_hash_mismatch_refuses_installer_launch(tmp_path: Path) -> None:
    installer = tmp_path / "MentorFlow-1.1.0-win-x64-Setup.exe"
    installer.write_bytes(b"safe test installer")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    (installer.parent / "update-staging.json").write_text(
        json.dumps({"staged_path": str(installer), "sha256": "f" * 64}), encoding="utf-8"
    )
    with pytest.raises(update_service.UpdateError, match="SHA-256"):
        update_service.launch_verified_installer(installer, expected_sha256=digest, launcher=lambda *_: None)


def test_verified_installer_launch_uses_argument_list(tmp_path: Path) -> None:
    installer = tmp_path / "MentorFlow 1.1.0 Setup.exe"
    installer.write_bytes(b"safe test installer")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    (installer.parent / "update-staging.json").write_text(
        json.dumps({"staged_path": str(installer), "sha256": digest}), encoding="utf-8"
    )
    calls: list[tuple[list[str], Path]] = []
    result = update_service.launch_verified_installer(
        installer,
        expected_sha256=digest,
        launcher=lambda command, cwd: calls.append((command, cwd)),
    )
    assert result["launched"] is True
    assert calls == [([str(installer.absolute())], installer.parent)]
