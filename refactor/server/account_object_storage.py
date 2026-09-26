"""Durable account-scoped original-file object store.

This module has no database or UI dependency. The backend adapter supplies
authenticated actor and tenant values and exposes the bytes over its API.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil

_HEX = re.compile(r"^[a-f0-9]{64}$")
MAX_OBJECT_BYTES = 8 * 1024 * 1024


class AccountObjectNotFound(FileNotFoundError):
    pass


class AccountObjectStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def scope_key(actor, tenant) -> str:
        raw = "\x1f".join(str(getattr(actor, k, "") or "") for k in ("issuer", "subject_id"))
        raw += "\x1f" + "\x1f".join(str(getattr(tenant, k, "") or "") for k in ("organization_id", "workspace_id"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _path(self, actor, tenant, digest: str) -> Path:
        if not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise ValueError("invalid object digest")
        return self.root / self.scope_key(actor, tenant) / digest[:2] / digest

    @staticmethod
    def _scope_manifest(actor, tenant) -> dict:
        return {
            "schema": "mentorflow.object-scope.v1",
            "issuer": str(getattr(actor, "issuer", "") or ""),
            "subject_id": str(getattr(actor, "subject_id", "") or ""),
            "organization_id": str(getattr(tenant, "organization_id", "") or ""),
            "workspace_id": str(getattr(tenant, "workspace_id", "") or ""),
        }

    def _ensure_scope_manifest(self, scope_dir: Path, actor, tenant) -> None:
        """Persist non-secret scope identity so closure can safely purge by subject.

        The directory name is intentionally a one-way hash for isolation.  A small
        manifest gives the closure path a way to locate all organization/workspace
        scopes for an issuer/subject without guessing or deleting unrelated data.
        """
        manifest = scope_dir / ".scope.json"
        expected = self._scope_manifest(actor, tenant)
        if manifest.exists():
            try:
                current = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ValueError("object scope manifest is invalid")
            if current != expected:
                raise ValueError("object scope manifest mismatch")
            return
        tmp = manifest.with_name(manifest.name + "." + os.urandom(8).hex() + ".pending")
        try:
            with tmp.open("x", encoding="utf-8", newline="") as stream:
                json.dump(expected, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, manifest)
        finally:
            tmp.unlink(missing_ok=True)

    def put(self, actor, tenant, digest: str, data: bytes) -> int:
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ValueError("object data must be bytes")
        data = bytes(data)
        if len(data) > MAX_OBJECT_BYTES or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("object size or digest mismatch")
        target = self._path(actor, tenant, digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_scope_manifest(target.parent.parent, actor, tenant)
        replace_existing = False
        if target.exists():
            # A previous interrupted write or a damaged volume must not make a
            # later retry report success while leaving an unreadable object in
            # place.  A valid immutable object is safely deduplicated.
            try:
                existing = target.read_bytes()
                replace_existing = (len(existing) != len(data)
                                    or hashlib.sha256(existing).hexdigest() != digest)
            except (OSError, ValueError):
                replace_existing = True
            if not replace_existing:
                return len(data)
        if replace_existing or not target.exists():
            tmp = target.with_name(target.name + "." + os.urandom(8).hex() + ".pending")
            try:
                with tmp.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
        return len(data)

    def get(self, actor, tenant, digest: str) -> bytes:
        target = self._path(actor, tenant, digest)
        if not target.is_file():
            raise AccountObjectNotFound(digest)
        data = target.read_bytes()
        if len(data) > MAX_OBJECT_BYTES or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("object integrity failure")
        return data

    def purge_scope(self, actor, tenant) -> int:
        """Delete one exact issuer/subject/org/workspace object scope."""
        scope_dir = self.root / self.scope_key(actor, tenant)
        if not scope_dir.is_dir():
            return 0
        manifest = scope_dir / ".scope.json"
        if manifest.exists():
            try:
                if json.loads(manifest.read_text(encoding="utf-8")) != self._scope_manifest(actor, tenant):
                    raise ValueError("object scope manifest mismatch")
            except (OSError, ValueError):
                raise ValueError("object scope manifest is invalid")
        count = sum(1 for item in scope_dir.rglob("*") if item.is_file() and item.name != ".scope.json")
        shutil.rmtree(scope_dir)
        return count

    def purge_subject(self, issuer: str, subject_id: str, *, organization_id: str | None = None,
                      workspace_id: str | None = None) -> int:
        """Delete all recorded scopes owned by issuer/subject.

        Closure normally has no tenant context, so it uses manifests to match every
        organization/workspace scope for the subject. Optional tenant filters make
        this the same primitive usable by a tenant-scoped cleanup hook.
        """
        issuer, subject_id = str(issuer or ""), str(subject_id or "")
        removed = 0
        if not self.root.is_dir():
            return 0
        for scope_dir in tuple(self.root.iterdir()):
            if not scope_dir.is_dir():
                continue
            manifest = scope_dir / ".scope.json"
            if not manifest.is_file():
                continue
            try:
                value = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if value.get("schema") != "mentorflow.object-scope.v1":
                continue
            if value.get("issuer") != issuer or value.get("subject_id") != subject_id:
                continue
            if organization_id is not None and value.get("organization_id") != str(organization_id):
                continue
            if workspace_id is not None and value.get("workspace_id") != str(workspace_id):
                continue
            removed += sum(1 for item in scope_dir.rglob("*") if item.is_file() and item.name != ".scope.json")
            shutil.rmtree(scope_dir)
        return removed
