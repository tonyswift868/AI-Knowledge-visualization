"""Safe, portable migration archives for MentorFlow's local data mode.

The archive is deliberately independent of the running executable.  It contains
only app-owned files and a hash manifest; credentials held by Windows
Credential Locker never enter the archive.  Import is staged and only allowed
into a new or empty directory so an active workspace cannot be overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MIGRATION_FORMAT = "mentorflow-local-migration-v1"
MANIFEST_NAME = "mentorflow-migration.json"
MAX_MEMBERS = 20_000
MAX_TOTAL_BYTES = 8 * 1024 * 1024 * 1024


class MigrationError(RuntimeError):
    """Raised when a migration archive cannot be safely created or imported."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse_or_symlink(path: Path) -> bool:
    attributes = int(getattr(path.stat(), "st_file_attributes", 0))
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _iter_files(root: Path, archive_destination: Path | None) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    excluded: list[str] = []
    destination = archive_destination.resolve() if archive_destination else None
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if _is_reparse_or_symlink(path):
            excluded.append(f"{relative}: reparse point or symlink")
            continue
        if path.is_dir():
            continue
        if destination and path.resolve() == destination:
            excluded.append(f"{relative}: selected archive destination")
            continue
        lower = path.name.casefold()
        if lower.endswith((".key", ".pem", ".p12", ".pfx")) or lower in {
            "secrets.json",
            "credentials.json",
        }:
            excluded.append(f"{relative}: credential material is not portable")
            continue
        files.append(path)
    return files, excluded


def _snapshot_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.casefold() != ".db":
        shutil.copy2(source, destination)
        return
    source_connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection)
        integrity = str(target_connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise MigrationError(f"数据库完整性检查失败：{source.name} ({integrity})")
    except sqlite3.Error as exc:
        raise MigrationError(f"数据库完整性检查失败：{source.name}") from exc
    finally:
        target_connection.close()
        source_connection.close()


def export_migration_archive(data_root: Path, destination: Path) -> dict[str, Any]:
    """Create a portable ZIP at ``destination`` without overwriting it."""

    root = data_root.expanduser().resolve()
    if not root.is_dir():
        raise MigrationError(f"本地数据目录不存在：{root}")
    output = destination.expanduser().resolve()
    if output.exists():
        raise MigrationError(f"目标文件已存在，请选择新的文件名：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    files, excluded = _iter_files(root, output)
    temp_root = Path(os.getenv("MENTORFLOW_TEMP_ROOT", r"E:\Codex\Temp"))
    temp_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="mentorflow-migration-", dir=temp_root))
    temporary_archive = output.with_name(output.name + f".{uuid.uuid4().hex}.part")
    try:
        entries: list[dict[str, Any]] = []
        for source in files:
            relative = source.relative_to(root).as_posix()
            staged = staging / Path(relative)
            _snapshot_file(source, staged)
            entries.append(
                {
                    "path": relative,
                    "bytes": staged.stat().st_size,
                    "sha256": _sha256(staged),
                }
            )
        manifest: dict[str, Any] = {
            "format": MIGRATION_FORMAT,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_label": root.name or "MentorFlowData",
            "files": entries,
            "excluded": excluded,
            "credentials": "Windows Credential Locker references are not exported; enter API keys again on a new device.",
        }
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        with zipfile.ZipFile(temporary_archive, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, manifest_bytes)
            for item in entries:
                archive.write(staging / Path(item["path"]), item["path"])
        temporary_archive.replace(output)
        return {
            "archive": output,
            "files": len(entries),
            "excluded": excluded,
            "bytes": output.stat().st_size,
        }
    except Exception:
        temporary_archive.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = Path(normalized)
    if not normalized or normalized.startswith("/") or path.drive or ".." in path.parts:
        raise MigrationError(f"迁移包包含不安全路径：{name}")
    return normalized


def import_migration_archive(archive_path: Path, target_root: Path) -> dict[str, Any]:
    """Verify and extract a migration ZIP into a new or empty directory."""

    source = archive_path.expanduser().resolve()
    target = target_root.expanduser().resolve()
    if not source.is_file() or not zipfile.is_zipfile(source):
        raise MigrationError("所选文件不是有效的 MentorFlow 迁移压缩包。")
    if target.exists() and any(target.iterdir()):
        raise MigrationError(f"导入目标必须是新目录或空目录：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(os.getenv("MENTORFLOW_TEMP_ROOT", r"E:\Codex\Temp"))
    temp_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="mentorflow-import-", dir=temp_root))
    try:
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise MigrationError("迁移包文件数量超过安全上限。")
            names: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                if info.is_dir():
                    continue
                name = _safe_member_name(info.filename.rstrip("/"))
                if not name:
                    continue
                if name in names:
                    raise MigrationError(f"迁移包包含重复文件：{name}")
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise MigrationError(f"迁移包包含符号链接：{name}")
                names[name] = info
            if MANIFEST_NAME not in names:
                raise MigrationError("迁移包缺少清单文件。")
            try:
                manifest = json.loads(archive.read(names[MANIFEST_NAME]).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MigrationError("迁移包清单无法读取。") from exc
            if manifest.get("format") != MIGRATION_FORMAT:
                raise MigrationError("迁移包版本不受当前软件支持。")
            declared = manifest.get("files")
            if not isinstance(declared, list):
                raise MigrationError("迁移包清单缺少文件列表。")
            expected: dict[str, dict[str, Any]] = {}
            for entry in declared:
                if not isinstance(entry, dict):
                    raise MigrationError("迁移包清单包含无效条目。")
                name = _safe_member_name(str(entry.get("path") or ""))
                if name in expected or name == MANIFEST_NAME:
                    raise MigrationError(f"迁移包清单包含重复文件：{name}")
                expected[name] = entry
            if set(expected) != set(names) - {MANIFEST_NAME}:
                raise MigrationError("迁移包内容与清单不一致。")
            total = 0
            for name, entry in expected.items():
                info = names[name]
                declared_bytes = int(entry.get("bytes", -1))
                if declared_bytes < 0 or info.file_size != declared_bytes:
                    raise MigrationError(f"文件大小校验失败：{name}")
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    raise MigrationError("迁移包超过安全容量上限。")
                destination = staging / Path(name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as input_stream, destination.open("xb") as output:
                    shutil.copyfileobj(input_stream, output, length=1024 * 1024)
                if _sha256(destination) != str(entry.get("sha256") or ""):
                    raise MigrationError(f"文件校验失败：{name}")
        target.mkdir(parents=True, exist_ok=True)
        for item in staging.iterdir():
            destination = target / item.name
            if destination.exists():
                raise MigrationError(f"导入目标在过程中发生变化：{destination}")
            shutil.move(str(item), str(destination))
        return {"target": target, "files": len(expected), "bytes": total}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
