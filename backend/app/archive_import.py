from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import stat
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db
from .core_security import (
    FileIdentity,
    PathSecurityError,
    open_verified_file,
    read_verified_bytes,
    remove_verified_file,
)


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
_PREVIEW_ID_RE = re.compile(r"^ap_[0-9a-f]{14}$")
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
_ARCHIVE_STORAGE_ERROR = "ARCHIVE_STORAGE_UNSAFE"


class ArchiveError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details or {}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:14]}"


def _archive_display_name(value: object, fallback: str = "study_bundle.zip") -> str:
    """Keep archive labels display-only; never persist a user path."""

    raw = str(value or "").replace("\x00", "").strip()
    if not raw:
        return fallback
    label = raw.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
    if re.match(r"^[A-Za-z]:", label):
        label = label[2:].lstrip("/\\")
    return label if label not in {"", ".", ".."} else fallback


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _root() -> Path:
    return Path(db.DATA_DIR) / "archive_previews"


def _storage_error() -> ArchiveError:
    """Return a stable, path-redacted storage-boundary error."""

    return ArchiveError(
        _ARCHIVE_STORAGE_ERROR,
        "归档预览临时存储路径不安全或不可用",
        status=500,
    )


def _lexical_absolute(path: Path | str) -> Path:
    """Make an absolute path without resolving links or reparse points."""

    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (OSError, TypeError, ValueError) as exc:
        raise _storage_error() from exc


def _metadata(path: Path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _storage_error() from exc


def _is_reparse(metadata) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        int(getattr(metadata, "st_file_attributes", 0) or 0) & _WINDOWS_REPARSE_POINT
    )


def _validate_entry(path: Path, kind: str):
    """Validate one lexical entry without following links or reparse points."""

    metadata = _metadata(path)
    if metadata is None:
        raise _storage_error()
    if _is_reparse(metadata):
        raise _storage_error()
    if kind == "dir":
        if not stat.S_ISDIR(metadata.st_mode):
            raise _storage_error()
    elif kind == "file":
        if not stat.S_ISREG(metadata.st_mode) or int(getattr(metadata, "st_nlink", 1) or 1) != 1:
            raise _storage_error()
    elif kind == "entry":
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            raise _storage_error()
    else:
        raise _storage_error()
    return metadata


def _ancestor_components(path: Path) -> list[Path]:
    absolute = _lexical_absolute(path)
    if not absolute.anchor:
        raise _storage_error()
    current = Path(absolute.parts[0])
    components = [current]
    for part in absolute.parts[1:]:
        current = current / part
        components.append(current)
    return components


def _validate_ancestors(path: Path, *, allow_missing_leaf: bool = False) -> Path:
    """Validate every existing component in a lexical path, fail closed."""

    components = _ancestor_components(path)
    for index, component in enumerate(components):
        metadata = _metadata(component)
        if metadata is None:
            if allow_missing_leaf and index == len(components) - 1:
                continue
            raise _storage_error()
        _validate_entry(component, "dir")
    return components[-1]


def _ensure_directory(path: Path) -> Path:
    """Create a directory tree one component at a time with no-follow checks."""

    components = _ancestor_components(path)
    for component in components:
        metadata = _metadata(component)
        if metadata is None:
            try:
                os.mkdir(component)
            except FileExistsError:
                pass
            except OSError as exc:
                raise _storage_error() from exc
        _validate_entry(component, "dir")
    return components[-1]


def _validate_contained(path: Path, root: Path, kind: str, *, allow_missing: bool = False) -> Path:
    """Validate a path lexically below a trusted root and check each entry."""

    root = _lexical_absolute(root)
    candidate = _lexical_absolute(path)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise _storage_error() from exc
    _validate_entry(root, "dir")
    parts = relative.parts
    if not parts:
        if kind != "dir":
            raise _storage_error()
        return candidate
    current = root
    for index, part in enumerate(parts):
        current = current / part
        metadata = _metadata(current)
        if metadata is None:
            if allow_missing:
                return candidate
            raise _storage_error()
        _validate_entry(current, "dir" if index < len(parts) - 1 else kind)
    return candidate


def _validate_preview_id(preview_id: str) -> str:
    if not isinstance(preview_id, str) or not _PREVIEW_ID_RE.fullmatch(preview_id):
        raise _storage_error()
    return preview_id


def _trusted_root(*, create: bool = False) -> Path:
    root = _lexical_absolute(_root())
    if create:
        return _ensure_directory(root)
    _validate_ancestors(root)
    _validate_entry(root, "dir")
    return root


def _validated_preview_directory(preview_id: str, *, allow_missing: bool = False) -> Path:
    _validate_preview_id(preview_id)
    root = _trusted_root()
    return _validate_contained(root / preview_id, root, "dir", allow_missing=allow_missing)


def _validated_archive_path(row: dict) -> Path:
    try:
        preview_id = _validate_preview_id(str(row["id"]))
        directory = _validated_preview_directory(preview_id)
        expected = _validate_contained(directory / "archive.zip", directory, "file")
        stored = _lexical_absolute(row["storage_path"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _storage_error() from exc
    if os.path.normcase(os.fspath(stored)) != os.path.normcase(os.fspath(expected)):
        raise _storage_error()
    return expected


def _write_new_file(path: Path, data: bytes, root: Path) -> Path:
    path = _validate_contained(path, root, "file", allow_missing=True)
    _validate_entry(path.parent, "dir")
    try:
        handle, identity = open_verified_file(path, "wb", exclusive=True)
        try:
            handle.write(data)
            handle.flush()
            after = FileIdentity.from_stat(os.fstat(handle.fileno()))
            if not identity.same_entry(after) or after.st_nlink != 1 or after.st_size != len(data):
                raise PathSecurityError("文件已发生变化，请重新预览")
        finally:
            handle.close()
    except (PathSecurityError, OSError) as exc:
        try:
            _validate_entry(path, "file")
            path.unlink()
        except (ArchiveError, OSError):
            pass
        raise _storage_error() from exc
    return path


def _create_child_directory(path: Path, root: Path) -> Path:
    path = _validate_contained(path, root, "dir", allow_missing=True)
    if _metadata(path) is None:
        try:
            os.mkdir(path)
        except FileExistsError as exc:
            raise _storage_error() from exc
        except OSError as exc:
            raise _storage_error() from exc
    return _validate_contained(path, root, "dir")


def _validate_tree(path: Path, root: Path, *, allow_missing: bool = False) -> Path | None:
    try:
        directory = _validate_contained(path, root, "dir", allow_missing=allow_missing)
    except ArchiveError:
        if allow_missing and _metadata(_lexical_absolute(path)) is None:
            return None
        raise
    if allow_missing and _metadata(directory) is None:
        return None
    try:
        entries = list(os.scandir(directory))
    except OSError as exc:
        raise _storage_error() from exc
    for entry in entries:
        child = Path(entry.path)
        metadata = _metadata(child)
        if metadata is None:
            raise _storage_error()
        if stat.S_ISDIR(metadata.st_mode):
            if _is_reparse(metadata):
                raise _storage_error()
            _validate_tree(child, directory)
        elif stat.S_ISREG(metadata.st_mode):
            _validate_entry(child, "file")
        else:
            raise _storage_error()
    return directory


def _remove_tree(path: Path, root: Path, *, missing_ok: bool = False) -> None:
    directory = _validate_tree(path, root, allow_missing=missing_ok)
    if directory is None:
        return
    initial = _metadata(directory)
    if initial is None:
        if missing_ok:
            return
        raise _storage_error()
    try:
        for entry in list(os.scandir(directory)):
            child = Path(entry.path)
            metadata = _metadata(child)
            if metadata is None:
                raise _storage_error()
            if stat.S_ISDIR(metadata.st_mode):
                _remove_tree(child, directory, missing_ok=False)
            elif stat.S_ISREG(metadata.st_mode):
                _remove_file(child, directory, missing_ok=False)
            else:
                raise _storage_error()
        after = _metadata(directory)
        if after is None or not (
            int(getattr(initial, "st_dev", 0) or 0) == int(getattr(after, "st_dev", 0) or 0)
            and int(getattr(initial, "st_ino", 0) or 0) == int(getattr(after, "st_ino", 0) or 0)
            and stat.S_IFMT(initial.st_mode) == stat.S_IFMT(after.st_mode)
            and not _is_reparse(after)
        ):
            raise _storage_error()
        os.rmdir(directory)
    except ArchiveError:
        raise
    except OSError as exc:
        raise _storage_error() from exc


def _remove_file(path: Path, root: Path, *, missing_ok: bool = True) -> None:
    candidate = _lexical_absolute(path)
    if _metadata(candidate) is None:
        if missing_ok:
            return
        raise _storage_error()
    _validate_contained(candidate, root, "file")
    try:
        remove_verified_file(candidate, missing_ok=missing_ok)
    except (OSError, PathSecurityError) as exc:
        raise _storage_error() from exc


def _archive_hash(path: Path, *, expected: FileIdentity | None = None) -> str:
    """Hash one pinned archive handle; never reopen a validated path."""

    try:
        handle, identity = open_verified_file(path, "rb", expected=expected)
    except PathSecurityError as exc:
        raise _storage_error() from exc
    digest = hashlib.sha256()
    try:
        for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(block)
        after = FileIdentity.from_stat(os.fstat(handle.fileno()))
        if not identity.unchanged(after):
            raise _storage_error()
    except OSError as exc:
        raise _storage_error() from exc
    finally:
        handle.close()
    return digest.hexdigest()


def _safe_member_path(raw_name: str) -> tuple[str, list[str]]:
    if not raw_name or "\x00" in raw_name:
        raise ArchiveError("ARCHIVE_PATH_TRAVERSAL", "ZIP 成员路径不安全")
    normalized = raw_name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//") or re.match(r"^[A-Za-z]:", normalized):
        raise ArchiveError("ARCHIVE_PATH_TRAVERSAL", "ZIP 成员路径必须是纯相对路径")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts) or not parts:
        raise ArchiveError("ARCHIVE_PATH_TRAVERSAL", "ZIP 成员路径包含路径穿越")
    relative = "/".join(parts)
    depth = len(parts) - 1
    if depth > MAX_DEPTH:
        raise ArchiveError("ARCHIVE_TOO_DEEP", "ZIP 目录深度超过限制", details={"max_depth": MAX_DEPTH})
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
        raise ArchiveError("ARCHIVE_EXPANDED_SIZE_TOO_LARGE", "ZIP 单文件解压后超过限制", details={"limit_bytes": MAX_SINGLE_BYTES})
    output = bytearray()
    with zf.open(info, "r") as source:
        while True:
            block = source.read(CHUNK_BYTES)
            if not block:
                break
            output.extend(block)
            if len(output) > MAX_SINGLE_BYTES:
                raise ArchiveError("ARCHIVE_EXPANDED_SIZE_TOO_LARGE", "ZIP 单文件解压后超过限制", details={"limit_bytes": MAX_SINGLE_BYTES})
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


def scan_archive(path: Path, *, expected: FileIdentity | None = None) -> dict:
    try:
        handle, _identity = open_verified_file(path, "rb", expected=expected)
    except PathSecurityError as exc:
        raise _storage_error() from exc
    try:
        if not zipfile.is_zipfile(handle):
            raise ArchiveError("ARCHIVE_INVALID_FORMAT", "上传文件不是有效 ZIP")
        handle.seek(0)
        try:
            zf = zipfile.ZipFile(handle)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ArchiveError("ARCHIVE_INVALID_FORMAT", "ZIP 目录无法读取") from exc
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
                    raise ArchiveError("ARCHIVE_LINK_NOT_ALLOWED", "ZIP 不允许链接或特殊文件")
                if info.is_dir():
                    continue
                if info.compress_size == 0 and info.file_size > 0:
                    raise ArchiveError("ARCHIVE_SUSPICIOUS_COMPRESSION_RATIO", "ZIP 单文件压缩比异常")
                if info.compress_size and info.file_size / info.compress_size > MAX_SINGLE_RATIO:
                    raise ArchiveError("ARCHIVE_SUSPICIOUS_COMPRESSION_RATIO", "ZIP 单文件压缩比超过安全限制", details={"ratio": round(info.file_size / info.compress_size, 2), "max_ratio": MAX_SINGLE_RATIO})
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
        after = FileIdentity.from_stat(os.fstat(handle.fileno()))
        if not _identity.unchanged(after):
            raise _storage_error()
        return {"items": items, "file_count": len(infos), "expanded_size_bytes": total_expanded, "compressed_size_bytes": total_compressed}
    except OSError as exc:
        raise _storage_error() from exc
    finally:
        handle.close()


def _preview_directory(preview_id: str) -> Path:
    return _validated_preview_directory(preview_id)


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
    root = _trusted_root(create=True)
    directory = _validate_contained(root / preview_id, root, "dir", allow_missing=True)
    try:
        os.mkdir(directory)
    except (FileExistsError, OSError) as exc:
        raise _storage_error() from exc
    _validate_contained(directory, root, "dir")
    archive_path = directory / "archive.zip"
    try:
        _write_new_file(archive_path, archive_bytes, directory)
        scan = scan_archive(archive_path)
        created = _now()
        metadata = _validate_entry(archive_path, "file")
        preview = {"id": preview_id, "workspace_id": workspace_id, "archive_name": _archive_display_name(archive_name), "archive_hash": _archive_hash(archive_path, expected=FileIdentity.from_stat(metadata)), "archive_size_bytes": len(archive_bytes), "expires_at": _iso(created + timedelta(minutes=PREVIEW_TTL_MINUTES)), "created_at": _iso(created), "storage_path": str(archive_path), "archive_mtime_ns": metadata.st_mtime_ns}
        _store_preview(preview, scan)
    except Exception:
        _remove_tree(directory, root, missing_ok=True)
        raise
    items = scan["items"]
    return {**preview, "preview_id": preview_id, "tree": items, "supported_count": sum(x["status"] == "supported" for x in items), "ignored_count": sum(x["status"] == "ignored" for x in items), "rejected_count": sum(x["status"] == "rejected" for x in items), "warnings": [x["reason"] for x in items if x["reason"]]}


def _load_preview(preview_id: str, workspace_id: str) -> dict:
    row = db.row("archive_previews", "id", preview_id)
    if not row or row.get("workspace_id") != workspace_id:
        raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 预览不存在或已过期")
    if row.get("status") not in {"ready", "partial"} or datetime.fromisoformat(row["expires_at"]) <= _now():
        raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 预览不存在或已过期")
    path = _validated_archive_path(row)
    metadata = _validate_entry(path, "file")
    try:
        archive_mtime_ns = int(row["archive_mtime_ns"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 已发生变化，请重新预览") from exc
    expected_identity = FileIdentity.from_stat(metadata)
    if metadata.st_mtime_ns != archive_mtime_ns or _archive_hash(path, expected=expected_identity) != row["archive_hash"]:
        raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 已发生变化，请重新预览")
    row["archive_name"] = _archive_display_name(row.get("archive_name"))
    return row


def _create_archive_source(workspace_id: str, row: dict) -> str:
    existing = next(
        (
            item
            for item in db.rows("sources", "workspace_id", workspace_id)
            if item.get("archive_preview_id") == row["id"] and item.get("source_type") == "archive"
        ),
        None,
    )
    if existing:
        return str(existing["id"])
    source_id, created = _new_id("source"), db.now()
    source = {"id": source_id, "workspace_id": workspace_id, "title": row["archive_name"], "source_type": "archive", "original_filename": row["archive_name"], "original_url": "", "local_path": "", "trust_level": "reference", "content_hash": row["archive_hash"], "status": "container", "parse_error": "", "page_count": 0, "text": "", "created_at": created, "updated_at": created, "archive_preview_id": row["id"], "archive_relative_path": "", "parent_archive_name": row["archive_name"], "language": ""}
    with db.connection() as conn:
        conn.execute("INSERT INTO sources (id,workspace_id,title,source_type,original_filename,original_url,local_path,trust_level,content_hash,status,parse_error,page_count,text,created_at,updated_at,archive_preview_id,archive_relative_path,parent_archive_name,language) VALUES (:id,:workspace_id,:title,:source_type,:original_filename,:original_url,:local_path,:trust_level,:content_hash,:status,:parse_error,:page_count,:text,:created_at,:updated_at,:archive_preview_id,:archive_relative_path,:parent_archive_name,:language)", source)
    return source_id


def _extract_selected(
    archive_path: Path,
    item: dict,
    destination: Path,
    *,
    expected_archive_hash: str | None = None,
    expected_archive_identity: FileIdentity | None = None,
) -> Path:
    preview_directory = destination.parent
    root = _trusted_root()
    _validate_contained(preview_directory, root, "dir")
    archive_path = _validate_contained(archive_path, preview_directory, "file")
    destination = _validate_contained(destination, preview_directory, "dir")
    try:
        archive_handle, archive_identity = open_verified_file(
            archive_path, "rb", expected=expected_archive_identity
        )
    except PathSecurityError as exc:
        raise _storage_error() from exc
    try:
        if expected_archive_hash:
            archive_digest = hashlib.sha256()
            for block in iter(lambda: archive_handle.read(CHUNK_BYTES), b""):
                archive_digest.update(block)
            if archive_digest.hexdigest() != expected_archive_hash:
                raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 内容已变化，请重新预览")
            archive_handle.seek(0)
        with zipfile.ZipFile(archive_handle) as zf:
            members = {_safe_member_path(info.filename)[0]: info for info in zf.infolist() if not info.is_dir()}
            info = members.get(item["relative_path"])
            if not info:
                raise ArchiveError("ARCHIVE_ITEM_NOT_IN_PREVIEW", "所选文件不再位于 ZIP 预览中")
            relative, _ = _safe_member_path(info.filename)
            target = destination / Path(relative)
            _validate_contained(target, destination, "file", allow_missing=True)
            _ensure_directory(target.parent)
            _validate_contained(target.parent, destination, "dir")
            digest = hashlib.sha256()
            total = 0
            try:
                output, output_identity = open_verified_file(target, "wb", exclusive=True)
                try:
                    with zf.open(info, "r") as source:
                        while True:
                            block = source.read(CHUNK_BYTES)
                            if not block:
                                break
                            total += len(block)
                            if total > MAX_SINGLE_BYTES:
                                raise ArchiveError("ARCHIVE_EXPANDED_SIZE_TOO_LARGE", "ZIP 单文件解压后超过限制")
                            digest.update(block)
                            output.write(block)
                    output.flush()
                    output_after = FileIdentity.from_stat(os.fstat(output.fileno()))
                    if not output_identity.same_entry(output_after) or output_after.st_nlink != 1:
                        raise _storage_error()
                finally:
                    output.close()
            except FileExistsError as exc:
                raise _storage_error() from exc
            except (OSError, PathSecurityError) as exc:
                raise _storage_error() from exc
            _validate_entry(target, "file")
            if total != int(item["size_bytes"]) or digest.hexdigest() != item["content_hash"]:
                raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "所选文件哈希或大小已变化，请重新预览")
            extracted_target = target
        after = FileIdentity.from_stat(os.fstat(archive_handle.fileno()))
        if not archive_identity.unchanged(after):
            raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 内容已变化，请重新预览")
        return extracted_target
    except (OSError, zipfile.BadZipFile) as exc:
        raise _storage_error() from exc
    finally:
        archive_handle.close()


def import_preview(workspace_id: str, preview_id: str, selected_file_ids: list[str], trust_level: str = "reference") -> dict:
    if not selected_file_ids:
        raise ArchiveError("ARCHIVE_SELECTION_EMPTY", "至少选择一个 ZIP 内文件")
    row = _load_preview(preview_id, workspace_id)
    items = db.rows("archive_preview_items", "preview_id", preview_id)
    by_id = {item["id"]: item for item in items}
    if any(item_id not in by_id for item_id in selected_file_ids):
        raise ArchiveError("ARCHIVE_ITEM_NOT_IN_PREVIEW", "所选文件不属于当前 ZIP 预览")
    selected = [by_id[item_id] for item_id in selected_file_ids]
    if any(item["status"] not in {"supported", "failed"} for item in selected):
        raise ArchiveError("ARCHIVE_ITEM_NOT_IN_PREVIEW", "只能选择尚未导入或上次失败的 ZIP 内文件")
    archive_path = _validated_archive_path(row)
    scan_identity = FileIdentity.from_stat(_validate_entry(archive_path, "file"))
    scan = scan_archive(archive_path, expected=scan_identity)
    fresh_by_path = {item["relative_path"]: item for item in scan["items"]}
    for item in selected:
        fresh = fresh_by_path.get(item["relative_path"])
        if not fresh or fresh["content_hash"] != item["content_hash"] or fresh["size_bytes"] != item["size_bytes"]:
            raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 内容已变化，请重新预览")
    preview_directory = _validated_preview_directory(preview_id)
    destination = preview_directory / "extracted"
    _validate_contained(destination, preview_directory, "dir", allow_missing=True)
    archive_source_id = _create_archive_source(workspace_id, row)
    imported, duplicates, failed = [], [], []
    try:
        if _metadata(destination) is None:
            _create_child_directory(destination, preview_directory)
        else:
            _validate_contained(destination, preview_directory, "dir")
        from .ingestion_secure import parse_file
        from .services import create_source, source_detail
        for item in selected:
            target = None
            try:
                target = _extract_selected(
                    archive_path,
                    item,
                    destination,
                    expected_archive_hash=row.get("archive_hash"),
                    expected_archive_identity=FileIdentity.from_stat(
                        _validate_entry(archive_path, "file")
                    ),
                )
                target_identity = FileIdentity.from_stat(_validate_entry(target, "file"))
                target_bytes, _ = read_verified_bytes(
                    target,
                    expected=target_identity,
                    max_bytes=MAX_SINGLE_BYTES,
                )
                if hashlib.sha256(target_bytes).hexdigest() != item["content_hash"]:
                    raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "所选文件哈希或大小已变化，请重新预览")
                if "expected_identity" in inspect.signature(parse_file).parameters:
                    parts, status, pages = parse_file(target, expected_identity=target_identity)
                else:
                    # Keep compatibility with injected one-argument parsers
                    # used by retry tests while the production parser pins the
                    # extracted file identity itself.
                    parts, status, pages = parse_file(target)
                if status != "ready":
                    failure = {"file_id": item["id"], "relative_path": item["relative_path"], "error_code": "OCR_REQUIRED", "error": "文件需要 OCR，当前版本不生成假内容"}
                    failed.append(failure)
                    with db.connection() as conn:
                        conn.execute("UPDATE archive_preview_items SET status='failed',reason=? WHERE id=?", (failure["error"], item["id"]))
                    continue
                text = "\n\n".join(part["text"] for part in parts)
                source = create_source(workspace_id, f"{row['archive_name']} / {item['relative_path']}", "archive_file", text, original_filename=item["relative_path"], page_count=pages, trust_level=trust_level, parts=parts, archive_preview_id=preview_id, archive_relative_path=item["relative_path"], parent_archive_name=row["archive_name"], language=item.get("language") or "")
                imported.append({"file_id": item["id"], "relative_path": item["relative_path"], "source": source_detail(source["id"])})
                with db.connection() as conn:
                    conn.execute("UPDATE archive_preview_items SET status='imported',reason='' WHERE id=?", (item["id"],))
            except ValueError as exc:
                if isinstance(exc, PathSecurityError):
                    raise _storage_error() from exc
                if str(exc).startswith("DUPLICATE_SOURCE"):
                    duplicates.append({"file_id": item["id"], "relative_path": item["relative_path"], "reason": str(exc)})
                    with db.connection() as conn:
                        conn.execute("UPDATE archive_preview_items SET status='duplicate',reason=? WHERE id=?", ("内容已导入", item["id"]))
                else:
                    failure = {"file_id": item["id"], "relative_path": item["relative_path"], "error_code": "CONVERSION_FAILED", "error": "文件转换失败，请检查文件格式或仅重试此文件"}
                    failed.append(failure)
                    with db.connection() as conn:
                        conn.execute("UPDATE archive_preview_items SET status='failed',reason=? WHERE id=?", (failure["error"], item["id"]))
            except OSError as exc:
                if isinstance(exc.__cause__, PathSecurityError):
                    raise _storage_error() from exc
                failure = {"file_id": item["id"], "relative_path": item["relative_path"], "error_code": "CONVERSION_FAILED", "error": "文件转换失败，请检查文件格式或仅重试此文件"}
                failed.append(failure)
                with db.connection() as conn:
                    conn.execute("UPDATE archive_preview_items SET status='failed',reason=? WHERE id=?", (failure["error"], item["id"]))
            except ArchiveError as exc:
                if exc.code == _ARCHIVE_STORAGE_ERROR:
                    raise
                failure = {"file_id": item["id"], "relative_path": item["relative_path"], "error_code": exc.code, "error": exc.message}
                failed.append(failure)
                with db.connection() as conn:
                    conn.execute("UPDATE archive_preview_items SET status='failed',reason=? WHERE id=?", (failure["error"], item["id"]))
            except Exception as exc:
                failure = {"file_id": item["id"], "relative_path": item["relative_path"], "error_code": "CONVERSION_FAILED", "error": "文件转换失败，请检查文件格式或仅重试此文件"}
                failed.append(failure)
                with db.connection() as conn:
                    conn.execute("UPDATE archive_preview_items SET status='failed',reason=? WHERE id=?", (failure["error"], item["id"]))
            finally:
                if target:
                    _remove_file(target, destination, missing_ok=True)
        ignored = [{"file_id": item["id"], "relative_path": item["relative_path"], "status": item["status"], "reason": item["reason"]} for item in items if item["status"] in {"ignored", "rejected"}]
        preview_status = "partial" if failed else "imported"
        with db.connection() as conn:
            conn.execute("UPDATE archive_previews SET status=? WHERE id=?", (preview_status, preview_id))
        if not failed:
            _remove_tree(preview_directory, _trusted_root(), missing_ok=True)
        return {"preview_id": preview_id, "status": preview_status, "archive_source_id": archive_source_id, "imported": imported, "duplicates": duplicates, "failed": failed, "retryable_file_ids": [item["file_id"] for item in failed], "ignored": ignored}
    finally:
        _remove_tree(destination, preview_directory, missing_ok=True)


def discard_preview(workspace_id: str, preview_id: str) -> bool:
    row = db.row("archive_previews", "id", preview_id)
    if not row or row.get("workspace_id") != workspace_id:
        raise ArchiveError("ARCHIVE_PREVIEW_EXPIRED", "ZIP 预览不存在或已过期")
    directory = _validated_preview_directory(preview_id, allow_missing=True)
    _remove_tree(directory, _trusted_root(), missing_ok=True)
    with db.connection() as conn:
        conn.execute("UPDATE archive_previews SET status='cancelled' WHERE id=?", (preview_id,))
    return True


def cleanup_expired_previews() -> None:
    now = _now()
    with db.connection() as conn:
        rows = [dict(row) for row in conn.execute("SELECT id,expires_at,storage_path,status FROM archive_previews WHERE status IN ('ready','partial')").fetchall()]
        for row in rows:
            try:
                expired = datetime.fromisoformat(row["expires_at"]) <= now
            except (TypeError, ValueError):
                expired = True
            if expired:
                try:
                    directory = _validated_preview_directory(str(row["id"]), allow_missing=True)
                    _remove_tree(directory, _trusted_root(), missing_ok=True)
                except ArchiveError:
                    # A compromised preview path is never traversed or removed.
                    # Leave its row for an operator-visible, fail-closed retry.
                    continue
                conn.execute("UPDATE archive_previews SET status='expired' WHERE id=?", (row["id"],))
