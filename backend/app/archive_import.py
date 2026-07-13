from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db


DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".md", ".markdown", ".txt", ".html", ".htm"}
CODE_LANGUAGES = {
    ".py": "python", ".java": "java", ".js": "javascript", ".ts": "typescript",
    ".tsx": "tsx", ".jsx": "jsx", ".go": "go", ".rs": "rust", ".c": "c",
    ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp", ".kt": "kotlin",
    ".kts": "kotlin", ".swift": "swift", ".php": "php", ".rb": "ruby",
    ".sh": "shell", ".ps1": "powershell", ".sql": "sql", ".json": "json",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".xml": "xml",
    ".csv": "csv", ".ipynb": "jupyter",
}
CODE_EXTENSIONS = set(CODE_LANGUAGES)
IGNORED_DIRS = {".git", ".github", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "target", "coverage", ".idea", ".vscode"}
REJECTED_EXTENSIONS = {".exe", ".dll", ".com", ".bat", ".cmd", ".msi", ".apk", ".dmg", ".iso", ".bin", ".class", ".jar", ".war", ".pyd", ".so", ".dylib"}
NESTED_ARCHIVE_EXTENSIONS = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 500 * 1024 * 1024
MAX_SINGLE_BYTES = 50 * 1024 * 1024
MAX_FILES = 2000
MAX_DEPTH = 12
MAX_SINGLE_RATIO = 100
MAX_OVERALL_RATIO = 50
PREVIEW_TTL_MINUTES = 30
CHUNK_BYTES = 1024 * 1024


class ArchiveError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details or {}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:14]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _root() -> Path:
    return Path(db.DATA_DIR) / "archive_previews"


def _archive_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_member_path(raw_name: str) -> tuple[str, list[str]]:
    if not raw_name or "\x00" in raw_name:
        raise ArchiveError("ARCHIVE_PATH_TRAVERSAL", "ZIP 成员路径不安全")
    normalized = raw_name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//") or re.match(r"^[A-Za-z]:", normalized):
        raise ArchiveError("ARCHIVE_PATH_TRAVERSAL", "ZIP 成员路径必须是纯相对路径", details={"path": raw_name})
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts) or not parts:
        raise ArchiveError("ARCHIVE_PATH_TRAVERSAL", "ZIP 成员路径包含路径穿越", details={"path": raw_name})
    relative = "/".join(parts)
    depth = len(parts) - 1
    if depth > MAX_DEPTH:
        raise ArchiveError("ARCHIVE_TOO_DEEP", "ZIP 目录深度超过限制", details={"path": relative, "max_depth": MAX_DEPTH})
    return relative, parts


def _is_link_or_special(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if file_type in {stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK}:
        return True
    return bool(file_type and file_type not in {stat.S_IFREG, stat.S_IFDIR})


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文本编码无法识别")


def _read_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > MAX_SINGLE_BYTES:
        raise ArchiveError("ARCHIVE_EXPANDED_SIZE_TOO_LARGE", "ZIP 单文件解压后超过限制", details={"path": info.filename, "limit_bytes": MAX_SINGLE_BYTES})
    output = bytearray()
    with zf.open(info, "r") as source:
        while True:
            block = source.read(CHUNK_BYTES)
            if not block:
                break
            output.extend(block)
            if len(output) > MAX_SINGLE_BYTES:
                raise ArchiveError("ARCHIVE_EXPANDED_SIZE_TOO_LARGE", "ZIP 单文件解压后超过限制", details={"path": info.filename, "limit_bytes": MAX_SINGLE_BYTES})
    return bytes(output)


def _content_check(extension: str, data: bytes) -> tuple[bool, str]:
    if extension == ".pdf":
        return data.startswith(b"%PDF-"), "PDF header 不匹配"
    if extension == ".docx":
        return data.startswith(b"PK\x03\x04"), "DOCX ZIP header 不匹配"
    if b"\x00" in data:
        return False, "内容包含二进制空字节"
    try:
        text = _decode_text(data)
    except ValueError as exc:
        return False, str(exc)
    if extension == ".ipynb":
        try:
            notebook = json.loads(text)
            if not isinstance(notebook, dict) or not isinstance(notebook.get("cells", []), list):
                return False, "ipynb 结构不匹配"
        except json.JSONDecodeError:
            return False, "ipynb JSON 结构不匹配"
    if extension == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError:
            return False, "JSON 结构不匹配"
    return True, ""


def _member_kind(relative: str) -> tuple[str, str | None, str, str | None]:
    parts = relative.split("/")
    if any(part.lower() in IGNORED_DIRS for part in parts[:-1]):
        return "ignored", None, "ignored", "默认忽略目录"
    extension = Path(relative).suffix.lower()
    if extension in NESTED_ARCHIVE_EXTENSIONS:
        return "archive", None, "ignored", "嵌套归档默认不递归解压"
    if extension in REJECTED_EXTENSIONS:
        return "binary", None, "rejected", "默认拒绝可执行或二进制文件"
    if extension in DOCUMENT_EXTENSIONS:
        return "document", None, "supported", None
    if extension in CODE_EXTENSIONS:
        return "code", CODE_LANGUAGES[extension], "supported", None
    return "unknown", None, "ignored", "不在当前支持格式列表"


def scan_archive(path: Path) -> dict:
    if not zipfile.is_zipfile(path):
        raise ArchiveError("ARCHIVE_INVALID_FORMAT", "上传文件不是有效 ZIP")
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveError("ARCHIVE_INVALID_FORMAT", "ZIP 目录无法读取", details={"error": str(exc)}) from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise ArchiveError("ARCHIVE_TOO_MANY_FILES", "ZIP 成员数超过限制", details={"count": len(infos), "max_files": MAX_FILES})
        total_expanded = sum(max(0, info.file_size) for info in infos if not info.is_dir())
        total_compressed = sum(max(0, info.compress_size) for info in infos if not info.is_dir())
        if total_expanded > MAX_EXPANDED_BYTES:
            raise ArchiveError("ARCHIVE_EXPANDED_SIZE_TOO_LARGE", "ZIP 解压后总大小超过限制", details={"expanded_size_bytes": total_expanded, "limit_bytes": MAX_EXPANDED_BYTES})
        if total_compressed and total_expanded / total_compressed > MAX_OVERALL_RATIO:
            raise ArchiveError("ARCHIVE_SUSPICIOUS_COMPRESSION_RATIO", "ZIP 整体压缩比超过安全限制", details={"ratio": round(total_expanded / total_compressed, 2), "max_ratio": MAX_OVERALL_RATIO})
        items = []
        for info in infos:
            relative, _parts = _safe_member_path(info.filename)
            if info.flag_bits & 0x1:
                raise ArchiveError("ARCHIVE_ENCRYPTED_NOT_SUPPORTED", "不支持加密 ZIP")
            if _is_link_or_special(info):
                raise ArchiveError("ARCHIVE_LINK_NOT_ALLOWED", "ZIP 不允许链接或特殊文件", details={"path": relative})
            if info.is_dir():
                continue
            if info.compress_size == 0 and info.file_size > 0:
                raise ArchiveError("ARCHIVE_SUSPICIOUS_COMPRESSION_RATIO", "ZIP 单文件压缩比异常", details={"path": relative})
            if info.compress_size and info.file_size / info.compress_size > MAX_SINGLE_RATIO:
                raise ArchiveError("ARCHIVE_SUSPICIOUS_COMPRESSION_RATIO", "ZIP 单文件压缩比超过安全限制", details={"path": relative, "ratio": round(info.file_size / info.compress_size, 2), "max_ratio": MAX_SINGLE_RATIO})
            kind, language, status, reason = _member_kind(relative)
            extension = Path(relative).suffix.lower()
            content_hash = ""
            if status == "supported":
                data = _read_member(zf, info)
                valid, mismatch_reason = _content_check(extension, data)
                content_hash = hashlib.sha256(data).hexdigest()
                if not valid:
                    status, reason = "rejected", mismatch_reason
            items.append({
                "id": _new_id("af"), "relative_path": relative, "filename": Path(relative).name,
                "extension": extension, "size_bytes": info.file_size, "compressed_size_bytes": info.compress_size,
                "content_hash": content_hash, "kind": kind, "language": language,
                "status": status, "reason": reason, "selected_by_default": status == "supported",
            })
    return {"items": items, "file_count": len(infos), "expanded_size_bytes": total_expanded, "compressed_size_bytes": total_compressed}


def _preview_directory(preview_id: str) -> Path:
    return _root() / preview_id


def _store_preview(preview: dict, scan: dict) -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO archive_previews (id,workspace_id,archive_name,archive_hash,archive_size_bytes,expanded_size_bytes,file_count,status,expires_at,created_at,storage_path,archive_mtime_ns) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (preview["id"], preview["workspace_id"], preview["archive_name"], preview["archive_hash"], preview["archive_size_bytes"], scan["expanded_size_bytes"], scan["file_count"], "ready", preview["expires_at"], preview["created_at"], preview["storage_path"], preview["archive_mtime_ns"]),
        )
        for item in scan["items"]:
            conn.execute(
                "INSERT INTO archive_preview_items (id,preview_id,relative_path,filename,extension,size_bytes,compressed_size_bytes,content_hash,kind,language,status,reason,selected_by_default) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item["id"], preview["id"], item["relative_path"], item["filename"], item["extension"], item["size_bytes"], item["compressed_size_bytes"], item["content_hash"], item["kind"], item["language"], item["status"], item["reason"], int(item["selected_by_default"])),
            )


def create_preview(workspace_id: str, archive_name: str, archive_bytes: bytes) -> dict:
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("ARCHIVE_TOO_LARGE", "单 ZIP 上传不能超过 100 MB", status=413, details={"limit_bytes": MAX_ARCHIVE_BYTES})
    preview_id = _new_id("ap")
    directory = _preview_directory(preview_id)
    directory.mkdir(parents=True, exist_ok=False)
    archive_path = directory / "archive.zip"
    archive_path.write_bytes(archive_bytes)
    try:
        scan = scan_archive(archive_path)
        created = _now()
        preview = {"id": preview_id, "workspace_id": workspace_id, "archive_name": archive_name or "study_bundle.zip", "archive_hash": _archive_hash(archive_path), "archive_size_bytes": len(archive_bytes), "expires_at": _iso(created + timedelta(minutes=PREVIEW_TTL_MINUTES)), "created_at": _iso(created), "storage_path": str(archive_path), "archive_mtime_ns": archive_path.stat().st_mtime_ns}
        _store_preview(preview, scan)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    items = scan["items"]
    return {**preview, "preview_id": preview_id, "tree": items, "supported_count": sum(x["status"] == "supported" for x in items), "ignored_count": sum(x["status"] == "ignored" for x in items), "rejected_count": sum(x["status"] == "rejected" for x in items), "warnings": [x["reason"] for x in items if x["reason"]]}


def _load_preview(preview_id: str, workspace_id: str) -> dict:
    row = db.row("archive_previews", "id", preview_id)
    if not row or row.get("workspace_id") != workspace_id:
        raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 预览不存在或已过期")
    if row.get("status") != "ready" or datetime.fromisoformat(row["expires_at"]) <= _now():
        raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 预览不存在或已过期")
    path = Path(row["storage_path"])
    if not path.is_file() or path.stat().st_mtime_ns != int(row["archive_mtime_ns"]) or _archive_hash(path) != row["archive_hash"]:
        raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 已发生变化，请重新预览")
    return row


def _create_archive_source(workspace_id: str, row: dict) -> str:
    source_id, created = _new_id("source"), db.now()
    source = {"id": source_id, "workspace_id": workspace_id, "title": row["archive_name"], "source_type": "archive", "original_filename": row["archive_name"], "original_url": "", "local_path": "", "trust_level": "reference", "content_hash": row["archive_hash"], "status": "container", "parse_error": "", "page_count": 0, "text": "", "created_at": created, "updated_at": created, "archive_preview_id": row["id"], "archive_relative_path": "", "parent_archive_name": row["archive_name"], "language": ""}
    with db.connection() as conn:
        conn.execute("INSERT INTO sources (id,workspace_id,title,source_type,original_filename,original_url,local_path,trust_level,content_hash,status,parse_error,page_count,text,created_at,updated_at,archive_preview_id,archive_relative_path,parent_archive_name,language) VALUES (:id,:workspace_id,:title,:source_type,:original_filename,:original_url,:local_path,:trust_level,:content_hash,:status,:parse_error,:page_count,:text,:created_at,:updated_at,:archive_preview_id,:archive_relative_path,:parent_archive_name,:language)", source)
    return source_id


def _extract_selected(archive_path: Path, item: dict, destination: Path) -> Path:
    with zipfile.ZipFile(archive_path) as zf:
        members = { _safe_member_path(info.filename)[0]: info for info in zf.infolist() if not info.is_dir() }
        info = members.get(item["relative_path"])
        if not info:
            raise ArchiveError("ARCHIVE_ITEM_NOT_IN_PREVIEW", "所选文件不再位于 ZIP 预览中")
        relative, _ = _safe_member_path(info.filename)
        target = (destination / Path(relative)).resolve()
        if destination.resolve() not in target.parents:
            raise ArchiveError("ARCHIVE_PATH_TRAVERSAL", "ZIP 提取目标逃逸临时目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        total = 0
        with zf.open(info, "r") as source, target.open("wb") as output:
            while True:
                block = source.read(CHUNK_BYTES)
                if not block:
                    break
                total += len(block)
                if total > MAX_SINGLE_BYTES:
                    raise ArchiveError("ARCHIVE_EXPANDED_SIZE_TOO_LARGE", "ZIP 单文件解压后超过限制")
                digest.update(block)
                output.write(block)
        if total != int(item["size_bytes"]) or digest.hexdigest() != item["content_hash"]:
            raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "所选文件哈希或大小已变化，请重新预览")
        return target


def import_preview(workspace_id: str, preview_id: str, selected_file_ids: list[str], trust_level: str = "reference") -> dict:
    if not selected_file_ids:
        raise ArchiveError("ARCHIVE_SELECTION_EMPTY", "至少选择一个 ZIP 内文件")
    row = _load_preview(preview_id, workspace_id)
    items = db.rows("archive_preview_items", "preview_id", preview_id)
    by_id = {item["id"]: item for item in items}
    if any(item_id not in by_id for item_id in selected_file_ids):
        raise ArchiveError("ARCHIVE_ITEM_NOT_IN_PREVIEW", "所选文件不属于当前 ZIP 预览")
    selected = [by_id[item_id] for item_id in selected_file_ids]
    if any(item["status"] != "supported" for item in selected):
        raise ArchiveError("ARCHIVE_ITEM_NOT_IN_PREVIEW", "只能选择支持导入的 ZIP 内文件")
    scan = scan_archive(Path(row["storage_path"]))
    fresh_by_path = {item["relative_path"]: item for item in scan["items"]}
    for item in selected:
        fresh = fresh_by_path.get(item["relative_path"])
        if not fresh or fresh["content_hash"] != item["content_hash"] or fresh["size_bytes"] != item["size_bytes"]:
            raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 内容已变化，请重新预览")
    archive_source_id = _create_archive_source(workspace_id, row)
    destination = _preview_directory(preview_id) / "extracted"
    imported, duplicates, failed = [], [], []
    try:
        destination.mkdir(parents=True, exist_ok=True)
        from .ingestion_secure import parse_file
        from .services import create_source, source_detail
        for item in selected:
            target = None
            try:
                target = _extract_selected(Path(row["storage_path"]), item, destination)
                parts, status, pages = parse_file(target)
                if status != "ready":
                    failed.append({"file_id": item["id"], "relative_path": item["relative_path"], "error": "文件需要 OCR，当前版本不生成假内容"})
                    continue
                text = "\n\n".join(part["text"] for part in parts)
                source = create_source(workspace_id, f"{row['archive_name']} / {item['relative_path']}", "archive_file", text, original_filename=item["relative_path"], page_count=pages, trust_level=trust_level, parts=parts, archive_preview_id=preview_id, archive_relative_path=item["relative_path"], parent_archive_name=row["archive_name"], language=item.get("language") or "")
                imported.append({"file_id": item["id"], "relative_path": item["relative_path"], "source": source_detail(source["id"])})
            except ValueError as exc:
                if str(exc).startswith("DUPLICATE_SOURCE"):
                    duplicates.append({"file_id": item["id"], "relative_path": item["relative_path"], "reason": str(exc)})
                else:
                    failed.append({"file_id": item["id"], "relative_path": item["relative_path"], "error": str(exc)})
            except Exception as exc:
                failed.append({"file_id": item["id"], "relative_path": item["relative_path"], "error": str(exc)})
            finally:
                if target:
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
        ignored = [{"file_id": item["id"], "relative_path": item["relative_path"], "status": item["status"], "reason": item["reason"]} for item in items if item["status"] in {"ignored", "rejected"}]
        with db.connection() as conn:
            conn.execute("UPDATE archive_previews SET status='imported' WHERE id=?", (preview_id,))
        shutil.rmtree(_preview_directory(preview_id), ignore_errors=True)
        return {"preview_id": preview_id, "archive_source_id": archive_source_id, "imported": imported, "duplicates": duplicates, "failed": failed, "ignored": ignored}
    finally:
        shutil.rmtree(destination, ignore_errors=True)


def discard_preview(workspace_id: str, preview_id: str) -> bool:
    row = db.row("archive_previews", "id", preview_id)
    if not row or row.get("workspace_id") != workspace_id:
        raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 预览不存在或已过期")
    shutil.rmtree(_preview_directory(preview_id), ignore_errors=True)
    with db.connection() as conn:
        conn.execute("UPDATE archive_previews SET status='cancelled' WHERE id=?", (preview_id,))
    return True


def cleanup_expired_previews() -> None:
    now = _now()
    with db.connection() as conn:
        rows = [dict(row) for row in conn.execute("SELECT id,expires_at,storage_path,status FROM archive_previews WHERE status='ready'").fetchall()]
        for row in rows:
            try:
                expired = datetime.fromisoformat(row["expires_at"]) <= now
            except (TypeError, ValueError):
                expired = True
            if expired:
                shutil.rmtree(_preview_directory(row["id"]), ignore_errors=True)
                conn.execute("UPDATE archive_previews SET status='expired' WHERE id=?", (row["id"],))
