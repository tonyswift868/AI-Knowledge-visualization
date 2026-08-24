from __future__ import annotations

import hashlib
import io
import json
import os
import re
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import db
from .core_security import (
    FileIdentity,
    PathSecurityError,
    lexical_absolute,
    read_verified_bytes,
    snapshot_verified_file,
    validate_allowed_root,
    validate_no_follow_path,
    walk_safe_files,
)


SUPPORTED = {
    ".pdf",
    ".docx",
    ".md",
    ".markdown",
    ".txt",
    ".html",
    ".htm",
    ".py",
    ".java",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".kt",
    ".kts",
    ".swift",
    ".php",
    ".rb",
    ".sh",
    ".ps1",
    ".sql",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".csv",
    ".ipynb",
}
_LOCAL_PREVIEW_ROOT_NAME = "local_previews"


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex[:14]


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def norm(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def parse_file(path: Path, expected_identity: FileIdentity | None = None):
    """Parse bytes from one verified handle, never by reopening ``path`` in a parser."""

    candidate = lexical_absolute(path)
    try:
        raw, _identity = read_verified_bytes(
            candidate,
            expected=expected_identity,
            max_bytes=50_000_000,
        )
    except PathSecurityError as exc:
        raise OSError("文件无法读取") from exc
    suffix = candidate.suffix.lower()
    if suffix == ".pdf":
        import fitz

        try:
            doc = fitz.open(stream=raw, filetype="pdf")
            pages = [page.get_text("text") for page in doc]
        except Exception as exc:
            raise ValueError("PDF 解析失败") from exc
        finally:
            try:
                doc.close()
            except UnboundLocalError:
                pass
        if sum(len(x.strip()) for x in pages) < max(40, len(pages) * 15):
            return [], "needs_ocr", len(pages)
        return [
            {"text": x, "page_start": i + 1, "page_end": i + 1, "heading": ""}
            for i, x in enumerate(pages)
            if x.strip()
        ], "ready", len(pages)
    if suffix == ".docx":
        from docx import Document

        try:
            text = "\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs if p.text.strip())
        except Exception as exc:
            raise ValueError("DOCX 解析失败") from exc
        return [{"text": text, "page_start": None, "page_end": None, "heading": ""}], "ready", 0
    if suffix == ".ipynb":
        notebook = json.loads(_decode_text(raw))
        parts = []
        for index, cell in enumerate(notebook.get("cells", []), start=1):
            cell_type = cell.get("cell_type", "code")
            source = cell.get("source", "")
            raw_cell = "".join(source) if isinstance(source, list) else str(source)
            if raw_cell.strip():
                parts.append(
                    {
                        "text": raw_cell,
                        "page_start": None,
                        "page_end": None,
                        "heading": f"{cell_type} cell {index}",
                    }
                )
        return parts, "ready", 0
    raw_text = _decode_text(raw)
    if suffix in {".html", ".htm"}:
        from bs4 import BeautifulSoup

        raw_text = BeautifulSoup(raw_text, "html.parser").get_text("\n")
    heading = next((x.lstrip("# ").strip() for x in raw_text.splitlines() if x.startswith("#")), "")
    return [{"text": raw_text, "page_start": None, "page_end": None, "heading": heading}], "ready", 0


def _decode_text(data: bytes):
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def chunks(parts, source_id, size=900):
    output = []
    ordinal = 1
    for part in parts:
        text = part["text"].strip()
        for block in [x.strip() for x in re.split(r"\n{2,}", text) if x.strip()]:
            output.append(
                {
                    "id": new_id("chunk"),
                    "source_id": source_id,
                    "ordinal": ordinal,
                    "page_start": part.get("page_start"),
                    "page_end": part.get("page_end"),
                    "heading": part.get("heading", ""),
                    "text": block,
                    "normalized_text": norm(block),
                    "content_hash": sha_bytes(block.encode()),
                }
            )
            ordinal += 1
    return output


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _preview_storage_root() -> Path:
    root = lexical_absolute(Path(db.DATA_DIR) / _LOCAL_PREVIEW_ROOT_NAME)
    validate_no_follow_path(root.parent, "dir")
    if not root.exists():
        root.mkdir()
    validate_no_follow_path(root, "dir")
    return root


def _preview_storage_directory(preview_id: str, *, allow_missing: bool = False) -> Path:
    if not isinstance(preview_id, str) or not re.fullmatch(r"preview_[0-9a-f]{14}", preview_id):
        raise ValueError("Preview 不存在或已过期")
    root = _preview_storage_root()
    path = root / preview_id
    if allow_missing and not path.exists():
        return path
    validate_no_follow_path(path, "dir")
    return path


def _cleanup_preview_storage(preview_id: str) -> None:
    try:
        path = _preview_storage_directory(preview_id, allow_missing=True)
    except (PathSecurityError, ValueError):
        return
    if not path.exists():
        return
    # Snapshots are owned, no-follow files.  Avoid shutil.rmtree following a
    # replacement directory; validate each child before unlinking.
    try:
        for entry in list(os.scandir(path)):
            child = lexical_absolute(entry.path)
            metadata = entry.stat(follow_symlinks=False)
            if getattr(metadata, "st_file_attributes", 0) & 0x400 or os.path.islink(child):
                raise PathSecurityError("拒绝 symlink/junction")
            if entry.is_file(follow_symlinks=False):
                child.unlink()
            elif entry.is_dir(follow_symlinks=False):
                raise PathSecurityError("拒绝特殊文件")
        path.rmdir()
    except (OSError, PathSecurityError):
        return


def create_preview(raw_path, allowed_root):
    root = validate_allowed_root(allowed_root)
    candidate = lexical_absolute(raw_path)
    if candidate != root and not _inside(candidate, root):
        raise ValueError("路径必须位于允许根目录内")
    files, rejected = walk_safe_files(candidate, root, SUPPORTED)
    if not files:
        raise ValueError("没有可导入的支持格式文件")
    preview_id = new_id("preview")
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    storage_root = _preview_storage_root()
    storage_directory = storage_root / preview_id
    try:
        storage_directory.mkdir()
        validate_no_follow_path(storage_directory, "dir")
        for item in files:
            source = Path(item["path"])
            expected = FileIdentity.from_dict(item.get("identity"))
            snapshot_name = item["id"] + source.suffix.lower()
            snapshot = storage_directory / snapshot_name
            _identity, digest = snapshot_verified_file(
                source,
                snapshot,
                expected=expected,
                max_bytes=5_000_000,
            )
            if digest != item.get("hash"):
                raise PathSecurityError("文件已发生变化，请重新预览")
            item["snapshot_path"] = str(snapshot)
    except Exception as exc:
        _cleanup_preview_storage(preview_id)
        if isinstance(exc, PathSecurityError):
            raise ValueError("文件已发生变化，请重新预览") from exc
        raise
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO local_previews VALUES (?,?,?,?,?)",
            (preview_id, str(root), json.dumps(files, ensure_ascii=False), expires.isoformat(), db.now()),
        )
    return {"preview_id": preview_id, "expires_at": expires.isoformat(), "root": str(root), "files": files, "rejected": rejected}


def consume_preview(preview_id, selected_ids):
    if not selected_ids:
        raise ValueError("必须选择至少一个文件")
    row = db.row("local_previews", "id", preview_id)
    if not row:
        raise ValueError("Preview 不存在或已过期")
    try:
        expires = datetime.fromisoformat(row["expires_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Preview 已过期") from exc
    if expires < datetime.now(timezone.utc):
        raise ValueError("Preview 已过期")
    try:
        entries = json.loads(row["entries_json"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Preview 不存在或已过期") from exc
    by_id = {x["id"]: x for x in entries if isinstance(x, dict) and x.get("id")}
    if any(x not in by_id for x in selected_ids):
        raise ValueError("selected_file_ids 不属于当前 Preview")
    root = validate_allowed_root(row["root"])
    storage_directory = _preview_storage_directory(preview_id)
    selected = []
    for selected_id in selected_ids:
        item = dict(by_id[selected_id])
        source = lexical_absolute(item.get("path", ""))
        if not _inside(source, root):
            raise ValueError("Preview 文件已移动或逃逸允许目录")
        expected = FileIdentity.from_dict(item.get("identity"))
        try:
            source_data, source_identity = read_verified_bytes(source, expected=expected, max_bytes=5_000_000)
        except PathSecurityError as exc:
            raise ValueError("Preview 文件已发生变化，请重新预览") from exc
        if len(source_data) != int(item.get("size", -1)) or sha_bytes(source_data) != item.get("hash"):
            raise ValueError("Preview 文件已发生变化，请重新预览")
        snapshot_raw = item.get("snapshot_path")
        if not snapshot_raw:
            raise ValueError("Preview 文件已发生变化，请重新预览")
        snapshot = lexical_absolute(snapshot_raw)
        if not _inside(snapshot, storage_directory):
            raise ValueError("Preview 文件已移动或逃逸允许目录")
        try:
            snapshot_data, snapshot_identity = read_verified_bytes(snapshot, max_bytes=5_000_000)
        except PathSecurityError as exc:
            raise ValueError("Preview 文件已发生变化，请重新预览") from exc
        if snapshot_identity.st_nlink != 1 or len(snapshot_data) != len(source_data) or sha_bytes(snapshot_data) != item.get("hash"):
            raise ValueError("Preview 文件已发生变化，请重新预览")
        item["snapshot_identity"] = snapshot_identity.as_dict()
        item["parse_path"] = str(snapshot)
        # Keep ``path`` as the user-owned source for persistence/deletion.  The
        # main route parses ``parse_path`` so the parser never reopens it.
        selected.append(item)
    return selected


def cleanup_preview(preview_id: str) -> None:
    """Best-effort deletion of consumed private snapshots."""

    _cleanup_preview_storage(preview_id)
