"""Read-only original lookup, split from account_vault/portability.

Only this authenticated account's new materials and old vault indexes are
queried. Legacy databases are opened mode=ro and never restored or initialized.
Server SHA256 and byte count, not filename or a cached availability flag, prove
the selected original. An explicit relink lasts for this window only.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Event
from urllib.parse import quote

from .file_io import _native_path, assert_no_reparse_ancestors, canonical_io_path, open_resource_source
from .plan_models import PlanError, PlanResource, PlanScope
from .store import material_source_path


@dataclass(frozen=True)
class OriginalStatus:
    resource: PlanResource
    status: str
    message: str
    path: Path | None = None


class PlanResourceResolver:
    def __init__(self, *, data_root: Path, scope: PlanScope, legacy_data_root: Path | None = None):
        self.scope = scope
        self.root = canonical_io_path(data_root) / "learning" / "accounts" / scope.account_id
        self.legacy_root = None
        if legacy_data_root is not None:
            hashes = [hashlib.sha256(value.encode()).hexdigest() for value in (scope.issuer, scope.subject_id)]
            self.legacy_root = canonical_io_path(legacy_data_root) / "accounts" / hashes[0] / hashes[1]
        self._relinked = {}
        self._closed = Event()

    def close(self):
        self._closed.set()
        self._relinked.clear()

    def require_current(self):
        if self._closed.is_set():
            raise PlanError("CANCELLED", "资料读取已取消。")

    @contextmanager
    def _read_db(self, path: Path):
        assert_no_reparse_ancestors(path, Path(path.anchor))
        if _native_path(path).stat().st_nlink != 1:
            raise ValueError("database hardlink")
        # Percent-encoded native filename preserves Windows extended-length
        # syntax without turning its '?' into a URI query or UNC authority.
        connection = sqlite3.connect("file:" + quote(str(_native_path(path)), safe="") + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _safe_original(self, path):
        path = canonical_io_path(path)
        if path.is_relative_to(self.root.parent) and not path.is_relative_to(self.root):
            raise PlanError("CROSS_ACCOUNT_PATH", "关联路径指向其他账号，未读取。")
        if self.legacy_root:
            accounts = self.legacy_root.parent.parent
            if path.is_relative_to(accounts) and not path.is_relative_to(self.legacy_root):
                raise PlanError("CROSS_ACCOUNT_PATH", "旧资料路径指向其他账号，未读取。")
        return path

    def _candidates(self, resource):
        candidates = []
        if resource.resource_id in self._relinked:
            yield self._relinked[resource.resource_id]
        database = self.root / "materials.sqlite3"
        if _native_path(database).exists():
            with self._read_db(database) as connection:
                owners = connection.execute("SELECT account_id FROM owner").fetchall()
                if len(owners) != 1 or owners[0][0] != self.scope.account_id:
                    raise PlanError("SCOPE_MISMATCH", "本机资料库的账号归属不一致。")
                rows = connection.execute("SELECT material_id FROM materials WHERE sha256=? AND size_bytes=?", (resource.sha256, resource.size_bytes)).fetchall()
                for row in rows:
                    try:
                        candidates.append(material_source_path(self.root, row[0]))
                    except ValueError as exc:
                        raise ValueError("invalid material key") from exc
        yield from candidates
        candidates.clear()
        if self.legacy_root:
            database = self.legacy_root / "metadata" / "vault.sqlite"
            if _native_path(database).exists():
                with self._read_db(database) as connection:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if "local_resource_bindings" in tables:
                        rows = connection.execute("SELECT local_path FROM local_resource_bindings WHERE resource_id=? AND fingerprint_sha256=?", (resource.resource_id, resource.sha256)).fetchall()
                        candidates.extend(Path(row[0]) for row in rows if row[0])
                    if "local_assets" in tables:
                        rows = connection.execute("SELECT import_mode,object_relative_path,linked_path FROM local_assets WHERE sha256=? AND size_bytes=?", (resource.sha256, resource.size_bytes)).fetchall()
                        for row in rows:
                            if row[0] == "MANAGED":
                                relative = Path(row[1])
                                if relative.is_absolute() or ".." in relative.parts:
                                    raise ValueError("invalid managed asset path")
                                candidates.append(self.legacy_root / relative)
                            elif row[0] == "LINKED" and row[2]:
                                candidates.append(Path(row[2]))
            yield from dict.fromkeys(candidates)
            # active_teaching._connect uses the account-scoped ORM database;
            # uploads need not appear in vault.sqlite's local_assets index.
            database = self.legacy_root / "data" / "mentorflow_v2.db"
            if _native_path(database).exists():
                with self._read_db(database) as connection:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if "active_materials" in tables:
                        binding = connection.execute("SELECT scope FROM product_sync_binding WHERE singleton=1").fetchone() if "product_sync_binding" in tables else None
                        if binding is None or json.loads(binding[0]) != self.scope.snapshot_scope():
                            raise PlanError("LEGACY_SCOPE_UNCONFIRMED", "旧资料库的账号/空间归属未确认；请手动重新关联原件。")
                        rows = connection.execute("SELECT stored_path FROM active_materials WHERE id=? AND sha256=? AND size_bytes=?", (resource.resource_id, resource.sha256, resource.size_bytes)).fetchall()
                        for row in rows:
                            if row[0]:
                                yield Path(row[0])

    def verify(self, resource, path: Path, *, cancelled=None):
        self.require_current()
        digest, count = hashlib.sha256(), 0
        with open_resource_source(self._safe_original(path)) as (_, stream):
            while block := stream.read(1024 * 1024):
                self.require_current()
                if cancelled is not None and cancelled.is_set():
                    raise PlanError("CANCELLED", "资料读取已取消。")
                count += len(block)
                if count > resource.size_bytes:
                    raise PlanError("ORIGINAL_CHANGED", "原件大小与服务器记录不同。")
                digest.update(block)
        if count != resource.size_bytes or digest.hexdigest() != resource.sha256:
            raise PlanError("ORIGINAL_CHANGED", "原件内容与服务器指纹不同。")
        return path

    def resolve(self, resource: PlanResource, *, cancelled=None) -> OriginalStatus:
        self.require_current()
        failures = []
        try:
            for candidate in self._candidates(resource):
                try:
                    self.verify(resource, candidate, cancelled=cancelled)
                    return OriginalStatus(resource, "available", "已校验本机原件", candidate)
                except FileNotFoundError:
                    continue
                except PlanError as exc:
                    if exc.code == "CANCELLED":
                        raise
                    failures.append(str(exc))
                except (OSError, ValueError):
                    failures.append("原件无法安全读取")
        except PlanError:
            raise
        except Exception:
            return OriginalStatus(resource, "error", "本机原件索引无法读取，未视为无资料")
        if failures:
            return OriginalStatus(resource, "error", failures[0])
        return OriginalStatus(resource, "missing", "本机缺少原件；服务器只有记录，可重新关联原文件")

    def relink(self, resource, path):
        path = canonical_io_path(path)
        self.verify(resource, path)
        self._relinked[resource.resource_id] = path
        return OriginalStatus(resource, "available", "已校验重新关联的原件（本次会话）", path)
