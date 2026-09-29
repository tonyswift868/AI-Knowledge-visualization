"""Evidence based Live2D asset discovery for the detached classroom stage.

The refactor must never invent a model path or silently substitute a bundled
model.  This module only inspects the explicitly configured asset root and the
checked-in teacher manifest.  Missing or incomplete assets are represented as
an explicit status so the text classroom can continue independently.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
import os
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


LIVE2D_ASSET_NOT_FOUND = "LIVE2D_ASSET_NOT_FOUND"
LIVE2D_ASSET_INVALID = "LIVE2D_ASSET_INVALID"
LIVE2D_ASSET_READY = "LIVE2D_ASSET_READY"
_SETTINGS_ORG = "MentorFlow"
_SETTINGS_APP = "MentorFlow"
_ASSET_ROOT_KEY = "live2d/asset_root"
_CATALOG_RELATIVE = Path("teacher2d") / "catalog.json"


@dataclass(frozen=True, slots=True)
class Live2DAssetStatus:
    code: str
    message: str
    manifest_path: Path | None = None
    skeleton_path: Path | None = None

    @property
    def available(self) -> bool:
        return self.code == LIVE2D_ASSET_READY


def _normalise_root(path: Path) -> Path:
    """Accept the public root or a directly selected teacher folder."""
    root = Path(path).expanduser().resolve()
    if (root / "teacher2d" / "tsubaki-live2d" / "manifest.json").is_file():
        return root
    if (root / "tsubaki-live2d" / "manifest.json").is_file() and root.name == "teacher2d":
        return root.parent
    if (root / "manifest.json").is_file() and root.name == "tsubaki-live2d" and root.parent.name == "teacher2d":
        return root.parent.parent
    return root


def saved_asset_root() -> Path | None:
    """Read the device-local model directory; it is never account synced."""
    try:
        from PySide6.QtCore import QSettings
        value = str(QSettings(_SETTINGS_ORG, _SETTINGS_APP).value(_ASSET_ROOT_KEY, "") or "").strip()
    except Exception:
        value = os.environ.get("MENTORFLOW_LIVE2D_ASSET_ROOT", "").strip()
    if not value:
        return None
    path = _normalise_root(Path(value))
    return path if path.exists() else None


def save_asset_root(path: Path) -> Path:
    """Persist a user-selected model folder on this device only."""
    root = _normalise_root(Path(path))
    try:
        from PySide6.QtCore import QSettings
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_ASSET_ROOT_KEY, str(root))
    except Exception:
        pass
    return root


def configured_download(asset_root: Path | None = None, *, teacher_id: str = "tsubaki-live2d") -> dict[str, Any] | None:
    """Return an explicitly configured, verifiable model download descriptor.

    The shipped catalog contains no download URL today.  This function keeps
    that absence explicit and never derives a URL from a local manifest.
    Future deployments may add ``download_url``, ``sha256`` and
    ``size_bytes`` to the matching catalog entry; all three are required.
    """
    root = _normalise_root(Path(asset_root or Path(__file__).resolve().parents[2] / "desktop/resources"))
    path = root / _CATALOG_RELATIVE
    if not path.is_file():
        bundled = Path(__file__).resolve().parents[2] / "desktop/resources" / _CATALOG_RELATIVE
        path = bundled
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    for entry in payload.get("teachers", []) if isinstance(payload, dict) else ():
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "")
        manifest = str(entry.get("manifest") or "")
        if teacher_id not in {entry_id, manifest.rsplit("/", 2)[-2] if "/" in manifest else ""}:
            continue
        url = str(entry.get("download_url") or "").strip()
        digest = str(entry.get("sha256") or "").strip().lower()
        size = entry.get("size_bytes")
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 0
        if (not url or urlparse(url).scheme != "https" or urlparse(url).netloc == ""
                or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)
                or size <= 0):
            return None
        return {"url": url, "sha256": digest, "size_bytes": size,
                "teacher_id": teacher_id, "manifest": manifest}
    return None


def download_configured_asset(config: dict[str, Any], target_dir: Path) -> Path:
    """Download and install one catalog-approved archive after verification."""
    url = str(config["url"])
    expected_size = int(config["size_bytes"])
    expected_hash = str(config["sha256"]).lower()
    target = Path(target_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="mentorflow-live2d-", suffix=".download")
    os.close(fd)
    temp = Path(temp_name)
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temp.open("wb") as stream:
            total = 0
            digest = hashlib.sha256()
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size:
                    raise ValueError("Live2D 下载文件超过清单声明大小")
                stream.write(chunk)
                digest.update(chunk)
        if total != expected_size or digest.hexdigest().lower() != expected_hash:
            raise ValueError("Live2D 下载校验失败")
        with zipfile.ZipFile(temp) as archive:
            root = target.resolve()
            for member in archive.infolist():
                destination = (root / member.filename).resolve()
                if destination != root and root not in destination.parents:
                    raise ValueError("Live2D 压缩包路径无效")
            archive.extractall(root)
        return target
    finally:
        temp.unlink(missing_ok=True)


def inspect_live2d_assets(asset_root: Path, *, teacher_id: str = "tsubaki-live2d") -> Live2DAssetStatus:
    """Inspect one configured teacher asset directory without mutating it."""

    root = _normalise_root(Path(asset_root))
    manifest = root / "teacher2d" / teacher_id / "manifest.json"
    direct_package = False
    if not manifest.is_file() and (root / "manifest.json").is_file():
        # A manually selected downloaded package may contain the model files
        # directly, without the app's public/teacher2d wrapper.
        manifest = root / "manifest.json"
        direct_package = True
    if not manifest.is_file():
        return Live2DAssetStatus(
            LIVE2D_ASSET_NOT_FOUND,
            "Live2D 模型资源不存在；课堂已降级为文字模式。",
            manifest_path=manifest,
        )
    try:
        payload: Any = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return Live2DAssetStatus(
            LIVE2D_ASSET_INVALID,
            "Live2D 模型清单无法读取；课堂已降级为文字模式。",
            manifest_path=manifest,
        )
    skeleton = payload.get("skeleton") if isinstance(payload, dict) else None
    if not isinstance(skeleton, str) or not skeleton.strip():
        return Live2DAssetStatus(
            LIVE2D_ASSET_INVALID,
            "Live2D 模型清单缺少 skeleton；课堂已降级为文字模式。",
            manifest_path=manifest,
        )
    # Manifest paths are URL-like and must not escape the configured root.
    skeleton_rel = skeleton.lstrip("/")
    # Public manifests use a root-relative URL (``/teacher2d/...``), while
    # custom account asset roots may use a file relative name.  Resolve both
    # against the configured public root without accepting traversal.
    if direct_package and skeleton.startswith("/"):
        skeleton_path = manifest.parent / Path(skeleton_rel).name
    else:
        skeleton_path = (root / skeleton_rel) if skeleton.startswith("/") else (manifest.parent / skeleton_rel)
    try:
        skeleton_path = skeleton_path.resolve()
        if manifest.parent.resolve() not in skeleton_path.parents and skeleton_path != manifest.parent:
            return Live2DAssetStatus(
                LIVE2D_ASSET_NOT_FOUND,
                "Live2D 模型文件不存在；课堂已降级为文字模式。",
                manifest_path=manifest,
                skeleton_path=skeleton_path,
            )
    except (OSError, RuntimeError, ValueError):
        return Live2DAssetStatus(
            LIVE2D_ASSET_INVALID,
            "Live2D 模型路径无效；课堂已降级为文字模式。",
            manifest_path=manifest,
        )
    return Live2DAssetStatus(
        LIVE2D_ASSET_READY,
        "Live2D 模型资源已就绪。",
        manifest_path=manifest,
        skeleton_path=skeleton_path,
    )


__all__ = [
    "LIVE2D_ASSET_INVALID",
    "LIVE2D_ASSET_NOT_FOUND",
    "LIVE2D_ASSET_READY",
    "Live2DAssetStatus",
    "inspect_live2d_assets",
    "saved_asset_root",
    "save_asset_root",
    "configured_download",
    "download_configured_asset",
]
