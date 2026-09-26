"""Small durable local material store, explicitly scoped to an authenticated ID.

The shell supplies its existing mfa1 account_id and data_root. This stores only
new imports; it does not enumerate, migrate or change legacy/server records.
The former workspace-bound HTTP upload handler is replaced with direct local
I/O so this feature does not start LocalService or require a learning plan.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
from threading import Event, RLock
from uuid import uuid4

from .file_io import (
    PlainFileCopyError, _native_path, assert_no_reparse_ancestors,
    canonical_io_path, open_resource_source,
)
from .text_import import MAX_FILE_BYTES, PREVIEW_CHARACTERS, SUPPORTED_SUFFIXES, parse_text


_SAFE_MATERIAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")
_DIRECT_MATERIAL_ID = re.compile(r"material_[0-9a-f]{32}")
_PRODUCT_CLASSIFICATIONS = frozenset({"USER_FILE", "MODEL", "OTHER", "UNKNOWN"})
# Structured sync carries a bounded excerpt so a new device can use the
# associated plan material even when the original file is not present there.
# Full originals are synced as account-owned cloud objects; the local copy is
# retained as an offline cache.
SYNC_EXCERPT_CHARACTERS = 8_000


def _product_classification(value: object) -> str:
    """Normalize old local labels to the server resource contract."""
    if not isinstance(value, str) or value == "learning_material":
        return "USER_FILE"
    return value if value in _PRODUCT_CLASSIFICATIONS else "UNKNOWN"


def material_source_path(root: Path, material_id: str) -> Path:
    """Return the account-local source path for any supported material ID.

    Newly imported materials use a 32-hex ``material_`` ID and keep that ID
    in the filename.  Restored server records can use older, shorter IDs (or
    another safe stable key); those are deliberately hashed into a neutral
    filename.  Keeping this mapping in one place prevents the plan resolver
    and the material store from disagreeing about the same restored record.
    """
    if not isinstance(material_id, str) or _SAFE_MATERIAL_ID.fullmatch(material_id) is None:
        raise ValueError("资料标识无效")
    filename = (
        material_id
        if _DIRECT_MATERIAL_ID.fullmatch(material_id)
        else "remote_" + hashlib.sha256(material_id.encode()).hexdigest()
    )
    return Path(root) / "originals" / f"{filename}.source"


class MaterialError(RuntimeError):
    """An actionable error suitable for the native material page."""

    def __init__(self, message: str, *, material: Material | None = None):
        super().__init__(message)
        self.material = material


@dataclass(frozen=True)
class Material:
    material_id: str
    filename: str
    suffix: str
    size_bytes: int
    sha256: str
    created_at: str
    status: str
    error: str
    encoding: str
    character_count: int
    availability: str

    @property
    def storage_scope(self) -> str:
        """Describe where usable bytes live without hiding local-only files.

        Direct imports keep their original bytes on this device.  A restored
        row marked ``云端保留正文`` starts with a bounded account excerpt;
        once the verified account object is downloaded, the local copy is an
        account-object cache and can be used on this device without relinking.
        """
        if self.encoding == "云端保留正文":
            return "account_object_cache" if self.availability == "available" else "server_excerpt"
        return "local_only"

    @property
    def status_label(self) -> str:
        if self.availability == "missing":
            return "原件缺失 · 记录保留"
        if self.availability == "unreadable":
            return "原件无法读取"
        if self.availability == "changed":
            return "原件内容已变化 · 可重新关联"
        return "可查看" if self.status == "ready" else "解析失败 · 原件保留"


@dataclass(frozen=True)
class MaterialPreview:
    material: Material
    text: str
    notice: str


class MaterialStore:
    """One immutable account scope per window; no tokens or global active user.

    close() cancels unpublished work. Already committed imports remain in the
    original account. GUI completions must also be discarded after disposal.
    Connections are short-lived and belong to the thread performing each call.
    """

    def __init__(self, data_root: Path, account_id: str):
        if not isinstance(account_id, str) or not re.fullmatch(r"mfa1_[0-9a-f]{64}", account_id):
            raise ValueError("学习资料需要账号层提供已验证的稳定 account_id")
        self._account_id = account_id
        self._root = canonical_io_path(data_root) / "learning" / "accounts" / account_id
        self._closed = Event()
        self._publication_lock = RLock()

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def root(self) -> Path:
        return self._root

    def close(self) -> None:
        with self._publication_lock:
            self._closed.set()

    def _check_active(self) -> None:
        if self._closed.is_set():
            raise MaterialError("此账号资料会话已关闭，请重新登录后打开")

    def _check_path(self, path: Path) -> None:
        assert_no_reparse_ancestors(path, Path(path.anchor))

    @contextmanager
    def _connection(self):
        self._check_active()
        connection = None
        try:
            self._check_path(self.root)
            _native_path(self.root).mkdir(parents=True, exist_ok=True)
            self._check_path(self.root)
            database = self.root / "materials.sqlite3"
            for path in (database, Path(str(database) + "-journal")):
                self._check_path(path)
                if _native_path(path).exists() and _native_path(path).stat().st_nlink != 1:
                    raise MaterialError("资料记录路径不是独立文件，已停止读取")
            connection = sqlite3.connect(str(_native_path(database)), timeout=10)
            connection.row_factory = sqlite3.Row
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise MaterialError("资料记录版本较新，请使用对应版本的程序打开")
            with connection:
                # Serialize account-local reads and publication across page
                # sessions. On Windows, an open reader can block os.replace.
                connection.execute("BEGIN IMMEDIATE")
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables:
                    connection.execute("CREATE TABLE owner (account_id TEXT PRIMARY KEY)")
                    connection.execute("INSERT INTO owner VALUES (?)", (self.account_id,))
                elif "owner" not in tables:
                    raise MaterialError("资料记录的账号归属未知，已停止读取；未将旧资料认作当前账号数据")
                owners = connection.execute("SELECT account_id FROM owner").fetchall()
                if len(owners) != 1 or owners[0][0] != self.account_id:
                    raise MaterialError("资料记录的账号归属不一致，已停止读取")
                connection.execute("""CREATE TABLE IF NOT EXISTS materials (
                    material_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    suffix TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('ready','failed')),
                    error TEXT NOT NULL,
                    encoding TEXT NOT NULL,
                    character_count INTEGER NOT NULL,
                    preview TEXT NOT NULL
                )""")
                columns = {row[1] for row in connection.execute("PRAGMA table_info(materials)")}
                if "media_type" not in columns:
                    connection.execute("ALTER TABLE materials ADD COLUMN media_type TEXT NOT NULL DEFAULT ''")
                if "product_classification" not in columns:
                    connection.execute("ALTER TABLE materials ADD COLUMN product_classification TEXT NOT NULL DEFAULT 'USER_FILE'")
                connection.execute("PRAGMA user_version = 1")
                yield connection
        except MaterialError:
            raise
        except (OSError, sqlite3.Error, PlainFileCopyError) as exc:
            raise MaterialError("本机资料存取失败；请检查资料目录权限、磁盘空间或记录文件是否损坏。记录未被清空。") from exc
        finally:
            if connection is not None:
                connection.close()

    def _original(self, material_id: str) -> Path:
        try:
            return material_source_path(self.root, material_id)
        except ValueError as exc:
            raise MaterialError(str(exc)) from exc

    def original_path(self, material_id: str) -> Path:
        return self._original(material_id)

    def original_bytes(self, material_id: str) -> bytes:
        with self._connection() as connection:
            row = self._row(connection, material_id)
            availability, raw = self._original_state(row)
            if availability != "available":
                raise MaterialError("资料原件当前不可上传")
            # Availability only proves that a readable file exists.  The
            # sync layer also needs to know that it is the exact revision
            # represented by the metadata; otherwise a locally edited file
            # could be ACKed against an unchanged cloud record.
            if len(raw) != int(row["size_bytes"] or 0) or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise MaterialError("资料原件内容已变化，请重新关联后再同步")
            return raw

    def restore_original_bytes(self, material_id: str, data: bytes) -> None:
        with self._connection() as connection:
            row = self._row(connection, material_id)
            if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise MaterialError("云端资料原件完整性校验失败")
            target = self._original(material_id)
        self._publish_bytes(target, data)

    def restore_metadata(self, records):
        """Restore validated server metadata/excerpts; never claim a whole file."""
        with self._connection() as connection:
            for row in records:
                self._original(row["material_id"])
                if (not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
                        or type(row["size_bytes"]) is not int or row["size_bytes"] < 0):
                    raise MaterialError("云端资料指纹无效，未恢复记录")
                previous = connection.execute("SELECT * FROM materials WHERE material_id=?", (row["material_id"],)).fetchone()
                if previous and (previous["sha256"], previous["size_bytes"]) != (row["sha256"], row["size_bytes"]):
                    raise MaterialError("同一资料标识的内容不一致，已保留原记录")
                # ``text`` is accepted only for backwards-compatible local
                # restore callers; sync callers deliberately omit it.  Never
                # persist a source path or file bytes from a wire record.
                text = row.get("text") or ""
                media_type = row.get("media_type") or self._media_type(row["filename"], Path(row["filename"]).suffix.lower())
                classification = _product_classification(row.get("product_classification"))
                connection.execute("""INSERT OR IGNORE INTO materials
                    (material_id,filename,suffix,size_bytes,sha256,created_at,status,error,
                     encoding,character_count,preview,media_type,product_classification)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    row["material_id"], row["filename"], Path(row["filename"]).suffix.lower(), row["size_bytes"], row["sha256"],
                    row.get("created_at") or "", "ready", "", "云端保留正文", len(text), text[:PREVIEW_CHARACTERS],
                    media_type, classification))
                # A device may already have the same remote identity from a
                # previous sync. Keep the content fingerprint immutable while
                # allowing descriptive metadata and a later cloud excerpt to
                # converge across devices. A local, byte-identical original
                # remains authoritative; only a missing/changed/unreadable
                # original is promoted to the explicitly marked cloud excerpt.
                connection.execute("""UPDATE materials SET filename=?, media_type=?,
                    product_classification=? WHERE material_id=? AND sha256=? AND size_bytes=?""",
                    (row["filename"], media_type, classification, row["material_id"],
                     row["sha256"], row["size_bytes"]))
                if previous is not None and text:
                    use_cloud_excerpt = previous["encoding"] == "云端保留正文"
                    if not use_cloud_excerpt:
                        try:
                            availability, _ = self._original_state(previous)
                        except (OSError, ValueError, PlainFileCopyError, MaterialError):
                            availability = "unreadable"
                        use_cloud_excerpt = availability != "available"
                    if use_cloud_excerpt:
                        connection.execute("""UPDATE materials SET encoding=?,
                            character_count=?, preview=? WHERE material_id=?
                            AND sha256=? AND size_bytes=?""",
                            ("云端保留正文", len(text), text[:PREVIEW_CHARACTERS],
                             row["material_id"], row["sha256"], row["size_bytes"]))
            self._check_active()

    @staticmethod
    def _media_type(filename: str, suffix: str) -> str:
        guessed, _ = mimetypes.guess_type(filename or ("file" + suffix))
        return guessed or "application/octet-stream"

    def sync_metadata(self) -> list[dict[str, object]]:
        """Return account-scoped material metadata suitable for structured sync.

        This projection carries a bounded text excerpt in addition to stable
        descriptive fields.  It excludes source bytes and every local path.
        """
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT material_id,filename,suffix,size_bytes,sha256,media_type,product_classification,preview FROM materials "
                "ORDER BY material_id"
            ).fetchall()
            self._check_active()
            return [{
                "material_id": row["material_id"],
                "filename": row["filename"],
                "media_type": row["media_type"] or self._media_type(row["filename"], row["suffix"]),
                "size": row["size_bytes"],
                "sha256": row["sha256"],
                "product_classification": _product_classification(row["product_classification"]),
                "text": (row["preview"] or "")[:SYNC_EXCERPT_CHARACTERS],
            } for row in rows]

    def apply_sync_metadata(self, values: dict[str, object]) -> None:
        """Restore one remote metadata record without materializing its file."""
        required = {"material_id", "filename", "media_type", "size", "sha256",
                    "product_classification", "text"}
        if not isinstance(values, dict) or set(values) != required:
            raise MaterialError("云端资料元数据字段无效")
        if not isinstance(values["material_id"], str) or not isinstance(values["filename"], str):
            raise MaterialError("云端资料元数据标识无效")
        if (type(values["size"]) is not int or values["size"] < 0
                or not re.fullmatch(r"[a-f0-9]{64}", str(values["sha256"]))
                or not isinstance(values["media_type"], str)
                or values["product_classification"] not in _PRODUCT_CLASSIFICATIONS
                or not isinstance(values["text"], str)
                or len(values["text"]) > SYNC_EXCERPT_CHARACTERS):
            raise MaterialError("云端资料元数据指纹无效")
        # restore_metadata validates the fingerprint and writes only a local
        # metadata row; passing no text ensures no content crosses the wire.
        self.restore_metadata([{
            "material_id": values["material_id"], "filename": values["filename"],
            "size_bytes": values["size"], "sha256": values["sha256"],
            "media_type": values["media_type"],
            "product_classification": values["product_classification"],
            "text": values["text"],
        }])

    def _original_state(self, row: sqlite3.Row) -> tuple[str, bytes]:
        """Extracted revision check from LocalAssetLifecycle._availability.

        Existence alone does not prove availability. Never follow a link or
        accept changed bytes as the original represented by a stable record.
        """
        path = self._original(row["material_id"])
        try:
            with open_resource_source(path) as (_, handle):
                raw = handle.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return "missing", b""
        except (OSError, ValueError, PlainFileCopyError):
            return "unreadable", b""
        if len(raw) > MAX_FILE_BYTES or len(raw) != row["size_bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            return "changed", b""
        return "available", raw

    def _material(self, row: sqlite3.Row, availability: str | None = None) -> Material:
        if availability is None:
            availability, _ = self._original_state(row)
        return Material(**{key: row[key] for key in Material.__dataclass_fields__ if key != "availability"}, availability=availability)

    def _row(self, connection, material_id: str) -> sqlite3.Row:
        self._original(material_id)
        row = connection.execute("SELECT * FROM materials WHERE material_id=?", (material_id,)).fetchone()
        if row is None:
            raise MaterialError("当前账号中没有这份资料记录")
        return row

    def list_materials(self) -> list[Material]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM materials ORDER BY created_at DESC, material_id DESC").fetchall()
            materials = []
            for row in rows:
                self._check_active()
                materials.append(self._material(row))
            self._check_active()
            return materials

    def import_file(self, source: str | Path) -> Material:
        self._check_active()
        try:
            with open_resource_source(source) as (path, handle):
                suffix = path.suffix.lower()
                if suffix not in SUPPORTED_SUFFIXES:
                    raise MaterialError("目前支持 TXT、Markdown、CSV 和 JSON；此文件尚未导入")
                raw = handle.read(MAX_FILE_BYTES + 1)
                filename = path.name
        except MaterialError:
            raise
        except FileNotFoundError as exc:
            raise MaterialError("所选文件不存在或已移动，请重新选择") from exc
        except (ValueError, OSError, PlainFileCopyError) as exc:
            raise MaterialError("所选文件无法安全读取，请选择可访问的本机普通文件") from exc
        if len(raw) > MAX_FILE_BYTES:
            raise MaterialError(f"单份资料上限为 {MAX_FILE_BYTES // (1024 * 1024)} MiB；此文件尚未导入")
        text, encoding, error = "", "", ""
        try:
            text, encoding = parse_text(raw, suffix)
        except ValueError as exc:
            error = str(exc)
        self._check_active()
        material_id = "material_" + uuid4().hex
        target = self._original(material_id)
        staging = target.with_suffix(".pending")
        # Publish the complete source before its database row. A crash cannot
        # leave a success row pointing at a partially written original.
        published = False
        committed = False
        with self._connection() as connection:
            try:
                self._check_path(target.parent)
                _native_path(target.parent).mkdir(parents=True, exist_ok=True)
                self._check_path(target.parent)
                with _native_path(staging).open("xb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                with self._publication_lock:
                    self._check_active()
                    os.replace(_native_path(staging), _native_path(target))
                    published = True
                    with connection:
                        connection.execute("""INSERT INTO materials
                            (material_id,filename,suffix,size_bytes,sha256,created_at,status,error,
                             encoding,character_count,preview,media_type,product_classification)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                            material_id, filename, suffix, len(raw), hashlib.sha256(raw).hexdigest(),
                            datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                            "failed" if error else "ready", error, encoding, len(text), text[:PREVIEW_CHARACTERS],
                            self._media_type(filename, suffix), "USER_FILE",
                        ))
                        self._check_active()
                    committed = True
                row = connection.execute("SELECT * FROM materials WHERE material_id=?", (material_id,)).fetchone()
                return self._material(row)
            finally:
                # Only files created by this invocation are eligible for cleanup.
                _native_path(staging).unlink(missing_ok=True)
                if published and not committed:
                    _native_path(target).unlink(missing_ok=True)

    def preview(self, material_id: str) -> MaterialPreview:
        self._original(material_id)
        with self._connection() as connection:
            row = self._row(connection, material_id)
            material = self._material(row)
            notice = ""
            if material.availability == "missing":
                notice = "此设备的原件缺失。记录仍在；下方为这份资料保留的正文预览，不能替代完整原件。"
            elif material.availability == "unreadable":
                if material.encoding != "云端保留正文":
                    raise MaterialError("原件路径无法安全读取；资料记录仍保留", material=material)
                notice = "此设备的原件无法安全读取；下面只显示已同步的正文片段，不能替代完整原件。"
            elif material.availability == "changed":
                if material.encoding != "云端保留正文":
                    raise MaterialError("已保存原件内容发生变化；未将旧预览当作当前正文，请重新关联原文件", material=material)
                notice = "此设备的原件内容已变化；下面只显示服务器保留的旧正文片段，完整内容请重新关联原文件。"
            if material.encoding == "云端保留正文" and material.availability != "available":
                notice += ("\n" if notice else "") + "账号云端保留这份资料的正文片段；完整原件下载后会保存在本机缓存。"
            elif material.encoding == "云端保留正文" and material.availability == "available":
                notice += ("\n" if notice else "") + "完整原件已从账号对象存储恢复，并已在本机校验保存。"
            elif material.error:
                notice += ("\n" if notice else "") + "解析失败：" + material.error
            if material.character_count > PREVIEW_CHARACTERS:
                ending = "如需完整内容，请重新关联原件。" if material.availability != "available" else "完整原件已保存。"
                notice += ("\n" if notice else "") + f"正文共 {material.character_count:,} 字符，当前显示前 {PREVIEW_CHARACTERS:,} 字符；" + ending
            self._check_active()
            return MaterialPreview(material, row["preview"], notice)

    def _publish_bytes(self, target: Path, raw: bytes) -> None:
        """Durable staged publication, adapted from lifecycle._publish_file.

        All destinations are constructed inside this account. Only this
        invocation's staging file is cleaned up; external sources are read-only.
        """
        self._check_path(target)
        _native_path(target.parent).mkdir(parents=True, exist_ok=True)
        self._check_path(target)
        staging = target.with_name(f"{target.name}.{uuid4().hex}.pending")
        created = False
        try:
            with _native_path(staging).open("xb") as handle:
                created = True
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            with self._publication_lock:
                self._check_active()
                self._check_path(target)
                if _native_path(target).exists() and _native_path(target).stat().st_nlink != 1:
                    raise MaterialError("原件路径不是独立文件，已停止恢复")
                os.replace(_native_path(staging), _native_path(target))
        finally:
            if created:
                _native_path(staging).unlink(missing_ok=True)

    def relink(self, material_id: str, source: str | Path) -> Material:
        """Explicitly restore one known record from byte-identical local bytes.

        Managed-import semantics are preserved: copy into the same account's
        original, keeping its ID, timestamp, parse result and preview unchanged.
        No legacy database is discovered or adopted; no external path is owned.
        """
        self._original(material_id)
        with self._connection() as connection:
            row = self._row(connection, material_id)
            try:
                with open_resource_source(source) as (_, handle):
                    raw = handle.read(MAX_FILE_BYTES + 1)
            except (OSError, ValueError, PlainFileCopyError) as exc:
                raise MaterialError("重新关联的文件无法安全读取，请选择可访问的本机普通文件") from exc
            if len(raw) > MAX_FILE_BYTES or len(raw) != row["size_bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise MaterialError("所选文件与原资料内容不一致，未更改记录或原件；新版本请通过“添加资料”导入")
            self._publish_bytes(self._original(material_id), raw)
            material = self._material(row)
            self._check_active()
            return material

    def prepare_original_open(self, material_id: str) -> Path:
        """Return a verified, full-size account-local copy with a usable suffix.

        The established .source storage stays compatible. System applications
        receive a separate copy so editing it cannot change the saved original.
        These copies remain account-scoped across page disposal for late readers.
        """
        self._original(material_id)
        with self._connection() as connection:
            row = self._row(connection, material_id)
            availability, raw = self._original_state(row)
            if availability != "available":
                raise MaterialError(
                    "原件缺失、已变化或无法读取，不能打开；请重新关联原件",
                    material=self._material(row, availability),
                )
            if row["suffix"] not in SUPPORTED_SUFFIXES:
                raise MaterialError("原件格式未通过校验，未交给系统打开")
            target = self.root / "opened-originals" / (self._original(material_id).stem + row["suffix"])
            self._publish_bytes(target, raw)
            self._check_active()
            return target
