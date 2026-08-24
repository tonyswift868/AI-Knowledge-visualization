"""Opt-in MentorFlow update checks and verified installer launching.

The package verifier remains the single implementation of GitHub release
validation and download staging.  This module is deliberately a small desktop
boundary around it: importing this module never performs network I/O, and a
network request only happens when :func:`check_for_update` or
:func:`download_update` is called after an explicit user action.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from scripts.verify_windows_package import PackageError, load_version, sha256_file, stage_update


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = PROJECT_ROOT / "mentorflow-version.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


class UpdateError(RuntimeError):
    """An actionable update-check, staging, or installer error."""


def _version_file(path: Path | str | None) -> Path:
    candidate = Path(path) if path is not None else VERSION_FILE
    return candidate.expanduser().resolve()


def _config(path: Path | str | None = None) -> tuple[Path, dict[str, Any]]:
    version_path = _version_file(path)
    try:
        manifest = load_version(version_path)
    except PackageError as exc:
        raise UpdateError(str(exc)) from exc
    update = manifest.get("update")
    if not isinstance(update, dict) or not bool(update.get("enabled")):
        raise UpdateError("官方更新未启用；请先配置可信的 GitHub release 来源")
    owner = str(update.get("owner") or "").strip()
    repo = str(update.get("repo") or "").strip()
    if not owner or not repo:
        raise UpdateError("官方更新缺少 GitHub owner/repo 配置")
    return version_path, update


def check_for_update(
    version_file: Path | str | None = None,
    release_json: Path | str | None = None,
) -> dict[str, Any]:
    """Check the configured official release without downloading anything.

    ``release_json`` is intentionally exposed for offline fixture tests and
    review.  Normal desktop calls omit it and therefore call GitHub only from
    the explicit Check button's worker task.
    """

    version_path, update = _config(version_file)
    try:
        return stage_update(
            version_path,
            owner=str(update["owner"]),
            repo=str(update["repo"]),
            release_json=Path(release_json).expanduser().resolve() if release_json else None,
            staging_root=None,
            download=False,
        )
    except PackageError as exc:
        raise UpdateError(str(exc)) from exc


def download_update(
    version_file: Path | str | None = None,
    release_json: Path | str | None = None,
    staging_root: Path | str | None = None,
) -> dict[str, Any]:
    """Download and hash-verify the configured release into E: staging.

    The verifier never replaces files in the running application directory;
    this function returns the verified staged path for an explicit install
    confirmation in the UI.
    """

    version_path, update = _config(version_file)
    try:
        return stage_update(
            version_path,
            owner=str(update["owner"]),
            repo=str(update["repo"]),
            release_json=Path(release_json).expanduser().resolve() if release_json else None,
            staging_root=Path(staging_root).expanduser().resolve() if staging_root else None,
            download=True,
        )
    except PackageError as exc:
        raise UpdateError(str(exc)) from exc


def _validate_staged_metadata(path: Path, *, expected_sha256: str | None) -> str:
    if not path.is_file():
        raise UpdateError(f"已校验的安装器不存在：{path}")
    if path.suffix.casefold() != ".exe":
        raise UpdateError(f"当前资源不是 Windows 安装器；已暂存到：{path}")
    try:
        digest = sha256_file(path).lower()
    except PackageError as exc:
        raise UpdateError(str(exc)) from exc
    if expected_sha256:
        expected = str(expected_sha256).strip().lower()
        if not _SHA256_RE.fullmatch(expected) or digest != expected:
            raise UpdateError("安装器 SHA-256 校验失败，拒绝启动")
    metadata_path = path.parent / "update-staging.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"缺少有效的更新暂存清单：{metadata_path}") from exc
    if not isinstance(metadata, dict):
        raise UpdateError("更新暂存清单格式无效，拒绝启动")
    metadata_path_value = str(metadata.get("staged_path") or "")
    try:
        if Path(metadata_path_value).expanduser().resolve() != path.resolve():
            raise UpdateError("更新暂存清单与安装器路径不一致，拒绝启动")
    except (OSError, RuntimeError) as exc:
        raise UpdateError("无法验证更新暂存清单路径，拒绝启动") from exc
    metadata_digest = str(metadata.get("sha256") or "").strip().lower()
    if not _SHA256_RE.fullmatch(metadata_digest) or metadata_digest != digest:
        raise UpdateError("更新暂存清单中的 SHA-256 与安装器不一致，拒绝启动")
    return digest


def launch_verified_installer(
    staged_path: Path | str,
    *,
    expected_sha256: str | None = None,
    launcher: Callable[[list[str], Path], Any] | None = None,
) -> dict[str, Any]:
    """Launch one verified ``.exe`` using an argument list, never a shell.

    The caller is responsible for obtaining explicit confirmation before
    calling this function.  ``launcher`` exists solely for deterministic tests
    and receives ``([absolute_path], parent_directory)``.
    """

    path = Path(staged_path).expanduser().absolute()
    _validate_staged_metadata(path, expected_sha256=expected_sha256)
    command = [str(path)]
    try:
        if launcher is not None:
            launcher(command, path.parent)
        else:
            subprocess.Popen(
                command,
                cwd=str(path.parent),
                shell=False,
                close_fds=True,
            )
    except (OSError, ValueError) as exc:
        raise UpdateError(f"无法启动已校验的安装器：{exc}") from exc
    return {"launched": True, "staged_path": str(path), "sha256": sha256_file(path)}


# Concise aliases useful to callers that describe the action as "stage".
check_update = check_for_update
stage_download = download_update


__all__ = [
    "UpdateError",
    "check_for_update",
    "check_update",
    "download_update",
    "stage_download",
    "launch_verified_installer",
]
