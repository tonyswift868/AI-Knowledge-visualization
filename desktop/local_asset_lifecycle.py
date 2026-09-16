"""Current-account local asset lifecycle without leaking device paths to the server."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
from urllib.request import urlopen
from backend.app.plain_file_copy import _native_path

from .account_portability import (
    RECOVERY_INDEX_MEMBER,
    _read_verified_archive,
    _safe_filename,
    account_identity_fingerprint,
)
from .account_vault import (
    AccountVault,
    AssetAvailability,
    AssetRecord,
    ImportMode,
    LocalResourceBinding,
    _regular_source,
)


_OVERLAY_SCHEMA = "mentorflow.local-asset-lifecycle.v1"
_OVERLAY_NAME = "local-asset-lifecycle.v1.json"
_SHA256_LENGTH = 64
_CHUNK_SIZE = 1024 * 1024
_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024


class LocalAssetLifecycleError(RuntimeError):
    """A fail-closed lifecycle failure with a safe, user-facing message."""

    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(code)
        self.code = code
        self.user_message = user_message


@dataclass(frozen=True, slots=True)
class LocalAssetView:
    local_asset_id: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    source_kind: str
    availability: AssetAvailability
    portable: bool
    verified_source_url: str | None
    actions: tuple[str, ...]

    @property
    def labels(self) -> tuple[str, ...]:
        return ("LINKED", "NOT_PORTABLE") if self.source_kind == "LINKED" else ("MANAGED",)

    def public_projection(self) -> dict[str, Any]:
        """Return display data only; device paths are deliberately absent."""

        return {
            "local_asset_id": self.local_asset_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "source_kind": self.source_kind,
            "availability": self.availability.value,
            "portable": self.portable,
            "verified_source_url": self.verified_source_url,
            "actions": list(self.actions),
            "labels": list(self.labels),
        }


Downloader = Callable[[str, Path], None]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with _native_path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_sha(value: object) -> str:
    checksum = str(value or "").strip().casefold()
    if len(checksum) != _SHA256_LENGTH or any(char not in "0123456789abcdef" for char in checksum):
        raise LocalAssetLifecycleError("INVALID_REVISION", "资料 revision 无效，未更改本地文件。")
    return checksum


def _verified_https_url(value: object) -> str | None:
    if value in {None, ""}:
        return None
    text = str(value).strip()
    parsed = urlsplit(text)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
    ):
        raise LocalAssetLifecycleError(
            "SOURCE_NOT_VERIFIED",
            "下载来源未通过 HTTPS 安全校验，请重新关联文件或使用恢复包。",
        )
    return text


def _regular_file(path: Path) -> bool:
    try:
        _regular_source(path)
    except (OSError, ValueError):
        return False
    return True


def _matches_revision(path: Path, expected_sha256: str) -> bool:
    if not _regular_file(path):
        return False
    try:
        return secrets.compare_digest(_sha256(path), expected_sha256)
    except OSError:
        return False


def _actions(availability: AssetAvailability) -> tuple[str, ...]:
    if availability is AssetAvailability.AVAILABLE_LOCAL:
        return ()
    if availability is AssetAvailability.REDOWNLOAD_AVAILABLE:
        return ("redownload", "relink")
    if availability is AssetAvailability.RESTORABLE_FROM_PACKAGE:
        return ("restore", "relink")
    return ("relink",)


class LocalAssetLifecycle:
    """Resolve and recover assets only within one already-mounted account Vault."""

    def __init__(
        self,
        vault: AccountVault | None,
        *,
        account_identity: Mapping[str, Any] | str,
        downloader: Downloader | None = None,
    ) -> None:
        if vault is None or vault.closed:
            raise LocalAssetLifecycleError("VAULT_NOT_MOUNTED", "当前账号 Vault 尚未挂载。")
        self._vault = vault
        self._account_fingerprint = account_identity_fingerprint(account_identity)
        self._downloader = downloader or self._download_https
        self._overlay_path = vault.root / "metadata" / _OVERLAY_NAME
        self._overlay = self._load_overlay()

    @property
    def account_key(self) -> str | None:
        return None if self._vault.closed else self._vault.profile.account_id

    @property
    def vault_root(self) -> Path:
        return self._vault.root

    def _blank_overlay(self) -> dict[str, Any]:
        return {
            "schema": _OVERLAY_SCHEMA,
            "account_identity_fingerprint": self._account_fingerprint,
            "device_id": "device_" + secrets.token_hex(16),
            "assets": {},
        }

    def _load_overlay(self) -> dict[str, Any]:
        if not self._overlay_path.exists():
            return self._blank_overlay()
        try:
            parsed = json.loads(self._overlay_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as exc:
            raise LocalAssetLifecycleError(
                "LOCAL_METADATA_INVALID",
                "当前账号的本地资料索引无法验证，未显示不确定内容。",
            ) from exc
        if (
            not isinstance(parsed, dict)
            or parsed.get("schema") != _OVERLAY_SCHEMA
            or parsed.get("account_identity_fingerprint") != self._account_fingerprint
            or not isinstance(parsed.get("assets"), dict)
            or not str(parsed.get("device_id") or "").startswith("device_")
        ):
            raise LocalAssetLifecycleError(
                "LOCAL_METADATA_ACCOUNT_MISMATCH",
                "本地资料索引不属于当前账号，已停止加载。",
            )
        return parsed

    def _write_overlay(self, overlay: Mapping[str, Any] | None = None) -> None:
        content = dict(overlay or self._overlay)
        temporary = self._overlay_path.with_name(
            f"{self._overlay_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(
                    content,
                    stream,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._overlay_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _metadata(self, local_asset_id: str) -> dict[str, Any]:
        raw = self._overlay["assets"].get(local_asset_id)
        return dict(raw) if isinstance(raw, dict) else {}

    def _save_metadata(self, local_asset_id: str, metadata: Mapping[str, Any]) -> None:
        self._overlay["assets"][local_asset_id] = dict(metadata)
        self._write_overlay()

    def _record(self, local_asset_id: str) -> AssetRecord:
        for asset in self._vault.list_assets():
            if secrets.compare_digest(asset.local_asset_id, str(local_asset_id)):
                return asset
        raise LocalAssetLifecycleError("ASSET_NOT_FOUND", "当前账号中没有这份资料。")

    def import_managed(
        self,
        source: Path | str,
        *,
        verified_source_url: str | None = None,
    ) -> LocalAssetView:
        safe_url = _verified_https_url(verified_source_url)
        asset = self._vault.import_managed(source)
        self._save_metadata(
            asset.local_asset_id,
            {
                "revision_sha256": _valid_sha(asset.sha256),
                "verified_source_url": safe_url,
                "restorable_from_package": False,
            },
        )
        return self.resolve(asset.local_asset_id)

    def import_linked(self, source: Path | str) -> LocalAssetView:
        source_path = Path(source)
        if not _regular_file(source_path):
            raise LocalAssetLifecycleError("SOURCE_UNAVAILABLE", "所选文件不可用，请重新选择普通文件。")
        try:
            checksum = _sha256(source_path)
        except OSError as exc:
            raise LocalAssetLifecycleError(
                "SOURCE_UNAVAILABLE",
                "读取所选文件时发生变化，请重新选择。",
            ) from exc
        asset = self._vault.import_linked(source_path)
        self._save_metadata(
            asset.local_asset_id,
            {
                "revision_sha256": checksum,
                "linked_path": str(source_path.absolute()),
                "verified_source_url": None,
                "restorable_from_package": False,
            },
        )
        return self.resolve(asset.local_asset_id)

    def register_recovery_options(
        self,
        local_asset_id: str,
        *,
        verified_source_url: str | None = None,
        restorable_from_package: bool = False,
    ) -> LocalAssetView:
        self._record(local_asset_id)
        metadata = self._metadata(local_asset_id)
        metadata["verified_source_url"] = _verified_https_url(verified_source_url)
        metadata["restorable_from_package"] = bool(restorable_from_package)
        self._save_metadata(local_asset_id, metadata)
        return self.resolve(local_asset_id)

    def _expected_sha(self, asset: AssetRecord, metadata: Mapping[str, Any]) -> str:
        return _valid_sha(metadata.get("revision_sha256") or asset.sha256)

    def _availability(self, asset: AssetRecord, metadata: Mapping[str, Any]) -> AssetAvailability:
        expected_sha = self._expected_sha(asset, metadata)
        if asset.import_mode is ImportMode.LINKED:
            linked = Path(str(metadata.get("linked_path") or asset.linked_path or ""))
            if _matches_revision(linked, expected_sha):
                return AssetAvailability.AVAILABLE_LOCAL
            return AssetAvailability.LINKED_FILE_MISSING
        target = self._vault.managed_object_path(asset)
        if _matches_revision(target, expected_sha):
            return AssetAvailability.AVAILABLE_LOCAL
        if metadata.get("verified_source_url"):
            return AssetAvailability.REDOWNLOAD_AVAILABLE
        if bool(metadata.get("restorable_from_package")):
            return AssetAvailability.RESTORABLE_FROM_PACKAGE
        return AssetAvailability.MISSING_ON_DEVICE

    def _local_path(self, asset: AssetRecord, metadata: Mapping[str, Any]) -> Path:
        if asset.import_mode is ImportMode.LINKED:
            return Path(str(metadata.get("linked_path") or asset.linked_path or ""))
        return self._vault.managed_object_path(asset)

    def _record_implicit_binding(
        self,
        asset: AssetRecord,
        metadata: Mapping[str, Any],
        *,
        fingerprint_sha256: str,
        availability: AssetAvailability,
    ) -> LocalResourceBinding:
        state = (
            AssetAvailability.AVAILABLE_LOCAL
            if availability is AssetAvailability.AVAILABLE_LOCAL
            else AssetAvailability.MISSING_ON_THIS_DEVICE
        )
        return self._vault.upsert_resource_binding(
            resource_id=asset.local_asset_id,
            local_path=self._local_path(asset, metadata).absolute(),
            fingerprint_sha256=fingerprint_sha256,
            availability=state,
        )

    def resolve(self, local_asset_id: str) -> LocalAssetView:
        asset = self._record(local_asset_id)
        metadata = self._metadata(local_asset_id)
        checksum = self._expected_sha(asset, metadata)
        availability = self._availability(asset, metadata)
        view = LocalAssetView(
            local_asset_id=asset.local_asset_id,
            filename=asset.display_name,
            mime_type=asset.mime_type,
            size_bytes=asset.size_bytes,
            sha256=checksum,
            source_kind=asset.import_mode.value,
            availability=availability,
            portable=asset.import_mode is ImportMode.MANAGED,
            verified_source_url=_verified_https_url(metadata.get("verified_source_url")),
            actions=_actions(availability),
        )
        self._record_implicit_binding(
            asset,
            metadata,
            fingerprint_sha256=checksum,
            availability=availability,
        )
        return view

    def list_assets(self) -> list[LocalAssetView]:
        return [self.resolve(asset.local_asset_id) for asset in self._vault.list_assets()]

    def server_manifest_dto(self, local_asset_id: str) -> dict[str, Any]:
        """Project pathless metadata without syncing device availability state."""

        asset = self.resolve(local_asset_id)
        dto: dict[str, Any] = {
            "local_asset_id": asset.local_asset_id,
            "filename": asset.filename,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "sha256": asset.sha256,
            "import_mode": asset.source_kind,
            "portability": "PORTABLE" if asset.portable else "NONPORTABLE",
        }
        if asset.verified_source_url:
            dto["verified_source_url"] = asset.verified_source_url
        if asset.source_kind == "LINKED":
            dto["device_id"] = self._overlay["device_id"]
        return dto

    def register_missing_resource(
        self,
        resource_id: str,
        *,
        fingerprint_sha256: str,
    ) -> LocalResourceBinding:
        """Create a pathless Device-2 binding without copying server metadata."""

        return self._vault.upsert_resource_binding(
            resource_id=resource_id,
            local_path=None,
            fingerprint_sha256=_valid_sha(fingerprint_sha256),
            availability=AssetAvailability.MISSING_ON_THIS_DEVICE,
        )

    def bind_resource(
        self,
        resource_id: str,
        source: Path | str,
        *,
        fingerprint_sha256: str,
    ) -> LocalResourceBinding:
        source_path = Path(source)
        expected = _valid_sha(fingerprint_sha256)
        if not _regular_file(source_path):
            raise LocalAssetLifecycleError(
                "RESOURCE_SOURCE_UNAVAILABLE",
                "所选本机资源不可用。",
            )
        try:
            observed = _sha256(source_path)
        except OSError as exc:
            raise LocalAssetLifecycleError(
                "RESOURCE_SOURCE_UNAVAILABLE",
                "读取本机资源时发生变化，请重新选择。",
            ) from exc
        if not secrets.compare_digest(observed, expected):
            raise LocalAssetLifecycleError(
                "RESOURCE_FINGERPRINT_MISMATCH",
                "所选文件不是同一份资料，本机关联未更改。",
            )
        return self._vault.upsert_resource_binding(
            resource_id=resource_id,
            local_path=source_path.absolute(),
            fingerprint_sha256=expected,
            availability=AssetAvailability.AVAILABLE_LOCAL,
        )

    def bind_resource_to_asset(
        self,
        resource_id: str,
        local_asset_id: str,
    ) -> LocalResourceBinding:
        asset = self._record(local_asset_id)
        metadata = self._metadata(local_asset_id)
        view = self.resolve(local_asset_id)
        return self._vault.upsert_resource_binding(
            resource_id=resource_id,
            local_path=self._local_path(asset, metadata).absolute(),
            fingerprint_sha256=view.sha256,
            availability=(
                AssetAvailability.AVAILABLE_LOCAL
                if view.availability is AssetAvailability.AVAILABLE_LOCAL
                else AssetAvailability.MISSING_ON_THIS_DEVICE
            ),
        )

    def resolve_resource_binding(self, resource_id: str) -> LocalResourceBinding:
        binding = self._vault.get_resource_binding(resource_id)
        if binding is None:
            raise LocalAssetLifecycleError(
                "RESOURCE_BINDING_NOT_FOUND",
                "当前账号在本机没有这份资源的关联记录。",
            )
        state = (
            AssetAvailability.AVAILABLE_LOCAL
            if binding.local_path is not None
            and _matches_revision(binding.local_path, binding.fingerprint_sha256)
            else AssetAvailability.MISSING_ON_THIS_DEVICE
        )
        return self._vault.upsert_resource_binding(
            resource_id=binding.resource_id,
            local_path=binding.local_path,
            fingerprint_sha256=binding.fingerprint_sha256,
            availability=state,
        )

    def list_resource_bindings(self) -> list[LocalResourceBinding]:
        return [
            self.resolve_resource_binding(binding.resource_id)
            for binding in self._vault.list_resource_bindings()
        ]

    def relink_resource(
        self,
        resource_id: str,
        source: Path | str,
    ) -> LocalResourceBinding:
        binding = self._vault.get_resource_binding(resource_id)
        if binding is None:
            raise LocalAssetLifecycleError(
                "RESOURCE_BINDING_NOT_FOUND",
                "当前账号在本机没有这份资源的关联记录。",
            )
        return self.bind_resource(
            binding.resource_id,
            source,
            fingerprint_sha256=binding.fingerprint_sha256,
        )

    def relink(self, local_asset_id: str, source: Path | str) -> LocalAssetView:
        asset = self._record(local_asset_id)
        metadata = self._metadata(local_asset_id)
        source_path = Path(source)
        if not _regular_file(source_path):
            raise LocalAssetLifecycleError("RELINK_SOURCE_UNAVAILABLE", "重新关联的文件不可用。")
        if not _matches_revision(source_path, self._expected_sha(asset, metadata)):
            raise LocalAssetLifecycleError(
                "REVISION_MISMATCH",
                "所选文件不是同一 revision，未更改当前资料。",
            )
        if asset.import_mode is ImportMode.LINKED:
            metadata["linked_path"] = str(source_path.absolute())
            self._save_metadata(local_asset_id, metadata)
        else:
            self._publish_file(asset, source_path)
        return self.resolve(local_asset_id)

    def _publish_file(self, asset: AssetRecord, source: Path) -> None:
        expected = _valid_sha(
            asset.sha256
            or self._metadata(asset.local_asset_id).get("revision_sha256")
        )
        if not _matches_revision(source, expected):
            raise LocalAssetLifecycleError("REVISION_MISMATCH", "恢复内容与原 revision 不一致。")
        target = self._vault.managed_object_path(asset)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._vault.root / "managed-assets" / "staging" / f"{uuid.uuid4().hex}.part"
        try:
            with source.open("rb") as reader, temporary.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=_CHUNK_SIZE)
                writer.flush()
                os.fsync(writer.fileno())
            if _sha256(temporary) != expected:
                raise LocalAssetLifecycleError("REVISION_MISMATCH", "恢复内容与原 revision 不一致。")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _download_https(url: str, destination: Path, *, expected_size=None, cancel=None, progress=None) -> None:
        limit = expected_size if expected_size is not None else _MAX_DOWNLOAD_BYTES
        with urlopen(url, timeout=30) as response, destination.open("xb") as writer:  # noqa: S310
            _verified_https_url(response.geturl())
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > limit:
                raise LocalAssetLifecycleError("DOWNLOAD_TOO_LARGE", "下载文件超过本机资料大小上限。")
            total = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise LocalAssetLifecycleError('DOWNLOAD_CANCELLED', '下载已取消，可以重试。')
                chunk = response.read(_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise LocalAssetLifecycleError("DOWNLOAD_TOO_LARGE", "下载文件超过本机资料大小上限。")
                writer.write(chunk)
                if progress is not None:
                    progress(total, limit)
            if expected_size is not None and total != expected_size:
                raise LocalAssetLifecycleError('DOWNLOAD_SIZE_MISMATCH', '下载大小与可信清单不一致。')
            writer.flush()
            os.fsync(writer.fileno())

    def redownload(self, local_asset_id: str) -> LocalAssetView:
        asset = self._record(local_asset_id)
        if asset.import_mode is not ImportMode.MANAGED:
            raise LocalAssetLifecycleError("REDOWNLOAD_NOT_MANAGED", "Linked 资料请使用重新关联。")
        url = _verified_https_url(self._metadata(local_asset_id).get("verified_source_url"))
        if not url:
            raise LocalAssetLifecycleError("SOURCE_NOT_VERIFIED", "没有已验证的下载来源。")
        temporary = self._vault.root / "managed-assets" / "staging" / f"{uuid.uuid4().hex}.download"
        try:
            self._downloader(url, temporary)
            if not _regular_file(temporary):
                raise LocalAssetLifecycleError("DOWNLOAD_FAILED", "下载未产生可用文件。")
            self._publish_file(asset, temporary)
        except LocalAssetLifecycleError:
            raise
        except Exception as exc:
            raise LocalAssetLifecycleError("DOWNLOAD_FAILED", "下载未完成，请稍后重试或重新关联。") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return self.resolve(local_asset_id)

    def reacquire_resource(
        self,
        resource_id: str,
        *,
        fingerprint_sha256: str,
        verified_source_url: str,
        verified_cache: Path | None = None,
    ) -> LocalResourceBinding:
        """Reacquire one missing server resource through the existing importer."""

        expected = _valid_sha(fingerprint_sha256)
        url = _verified_https_url(verified_source_url)
        if not url:
            raise LocalAssetLifecycleError(
                "SOURCE_NOT_VERIFIED",
                "没有已验证的重新获取来源。",
            )
        temporary = (
            self._vault.root
            / "managed-assets"
            / "staging"
            / f"{uuid.uuid4().hex}.reacquire"
        )
        try:
            if verified_cache is not None:
                if not _regular_file(verified_cache) or not _matches_revision(verified_cache, expected):
                    raise LocalAssetLifecycleError('RESOURCE_FINGERPRINT_MISMATCH', '本机缓存校验失败。')
                shutil.copyfile(verified_cache, temporary)
            else:
                self._downloader(url, temporary)
            if not _regular_file(temporary) or not _matches_revision(
                temporary,
                expected,
            ):
                raise LocalAssetLifecycleError(
                    "RESOURCE_FINGERPRINT_MISMATCH",
                    "重新获取的资料与原 revision 不一致，本机关联未更改。",
                )
            # Reacquiring an existing resource (including a local model) must
            # not register the device-local ID as another USER_FILE on server.
            asset = self._vault.import_managed(temporary, publish_metadata=False)
            self._save_metadata(asset.local_asset_id, {
                'revision_sha256': expected, 'verified_source_url': url,
                'restorable_from_package': False,
            })
            return self.bind_resource_to_asset(resource_id, asset.local_asset_id)
        except LocalAssetLifecycleError:
            raise
        except Exception as exc:
            raise LocalAssetLifecycleError(
                "DOWNLOAD_FAILED",
                "资料未能重新获取，请稍后重试或选择本机文件。",
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def validate_model_cache(selected):
        from backend.app.plain_file_copy import assert_no_reparse_ancestors
        path = Path(selected).absolute()
        assert_no_reparse_ancestors(path, Path(path.anchor))
        if not path.is_dir():
            raise ValueError('MODEL_CACHE_DIRECTORY_REQUIRED')
        return path

    def install_public_model(self, resource_id, *, fingerprint_sha256, cancel=None, progress=None, cache_root=None):
        """Install the byte-pinned public model tree and bind its stable resource.

        Restart reuses verified members. Private weights have no catalog entry.
        The returned resource is local-only; models never publish USER_FILE rows.
        """
        from backend.app import model_material_catalog as catalog
        material = catalog.material_for_hash(fingerprint_sha256)
        if material is None:
            raise LocalAssetLifecycleError('MODEL_SOURCE_UNTRUSTED', 'No trusted public model material')
        if cache_root is not None:
            cache_root = self.validate_model_cache(cache_root)
        self._vault._require_open()
        root = self._vault.root / 'models' / catalog.TRUSTED_MODEL_REVISION
        root.mkdir(parents=True, exist_ok=True)
        for name, (size, digest) in catalog.TRUSTED_MODEL_FILES.items():
            if cancel is not None and cancel.is_set():
                raise LocalAssetLifecycleError('DOWNLOAD_CANCELLED', 'Download cancelled; retry is available')
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if _regular_file(target) and target.stat().st_size == size and _matches_revision(target, digest):
                continue
            temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.part')
            try:
                cached = Path(cache_root) / name if cache_root is not None else None
                if cached is not None and _regular_file(cached) and cached.stat().st_size == size and _matches_revision(cached, digest):
                    with cached.open('rb') as reader, temporary.open('xb') as writer:
                        while chunk := reader.read(_CHUNK_SIZE):
                            if cancel is not None and cancel.is_set():
                                raise LocalAssetLifecycleError('DOWNLOAD_CANCELLED', 'Download cancelled; retry is available')
                            writer.write(chunk)
                        writer.flush(); os.fsync(writer.fileno())
                else:
                    self._download_https(catalog.material_url(name), temporary, expected_size=size, cancel=cancel, progress=progress)
                if not _regular_file(temporary) or temporary.stat().st_size != size or not _matches_revision(temporary,digest):
                    raise LocalAssetLifecycleError('RESOURCE_FINGERPRINT_MISMATCH', 'Model material verification failed')
                os.replace(temporary,target)
            finally:
                temporary.unlink(missing_ok=True)
        if cancel is not None and cancel.is_set():
            raise LocalAssetLifecycleError('DOWNLOAD_CANCELLED', 'Download cancelled; retry is available')
        prior = self._vault.get_resource_binding(resource_id)
        if prior and prior.fingerprint_sha256 == fingerprint_sha256:
            current = self.resolve_resource_binding(resource_id)
            if current.availability is AssetAvailability.AVAILABLE_LOCAL:
                return current
        return self.reacquire_resource(resource_id, fingerprint_sha256=fingerprint_sha256,
            verified_source_url=catalog.material_url(material[0]), verified_cache=root / material[0])

    def restore_resource_from_package(
        self, resource_id: str, package: Path | str, *, fingerprint_sha256: str, cancel=None,
    ) -> LocalResourceBinding:
        """Restore a server-known resource into an already rehydrated device.

        The authenticated snapshot supplies ID/hash. The V1 package supplies
        only matching bytes; no packaged database, owner or settings are applied.
        Device A's local asset ID need not exist on device B.
        """
        self._vault._require_open()
        def check_cancel():
            if cancel is not None and cancel.is_set():
                raise LocalAssetLifecycleError('RESTORE_CANCELLED', '恢复已取消，可重试。')
            self._vault._require_open()
        check_cancel()
        expected = _valid_sha(fingerprint_sha256)
        package_path = Path(package)
        if package_path.suffix.casefold() != '.mentorflowpack' or not _regular_file(package_path):
            raise LocalAssetLifecycleError('PACKAGE_UNAVAILABLE', '请选择可用的 .mentorflowpack 恢复包。')
        try:
            manifest, payloads, _ = _read_verified_archive(package_path)
            check_cancel()
            if manifest.get('account_identity_fingerprint') != self._account_fingerprint:
                raise LocalAssetLifecycleError('PACKAGE_ACCOUNT_MISMATCH', '恢复包不属于当前账号。')
            recovery = json.loads(payloads[RECOVERY_INDEX_MEMBER].decode('utf-8'))
            rows = recovery.get('assets', [])
            matches = [row for row in rows if isinstance(row, dict) and row.get('asset_id') == resource_id]
            if len(matches) != 1 or matches[0].get('storage_kind') != 'MANAGED':
                raise LocalAssetLifecycleError('PACKAGE_ASSET_MISSING', '恢复包中没有这份资料的原文件。')
            row = matches[0]
            filename = _safe_filename(row.get('filename'), field='filename')
            member = str(row.get('package_member') or '')
            raw = payloads.get(member)
            if (member != f"raw-assets/{row.get('original_local_asset_id')}/{filename}"
                    or not isinstance(raw, bytes) or row.get('sha256') != expected
                    or hashlib.sha256(raw).hexdigest() != expected or len(raw) != row.get('size_bytes')):
                raise LocalAssetLifecycleError('PACKAGE_REVISION_MISMATCH', '恢复包与当前资料版本不一致。')
            prior = self._vault.get_resource_binding(resource_id)
            if prior:
                if prior.fingerprint_sha256 != expected:
                    raise LocalAssetLifecycleError('RESOURCE_REVISION_CONFLICT', '本机已关联另一版本，未覆盖。')
                current = self.resolve_resource_binding(resource_id)
                if current.availability is AssetAvailability.AVAILABLE_LOCAL:
                    return current
            with tempfile.TemporaryDirectory(dir=_native_path(self._vault.root / 'managed-assets' / 'staging')) as folder:
                source = Path(folder) / filename
                _native_path(source).write_bytes(raw)
                check_cancel()
                # This restores bytes for an existing server resource. Publishing
                # the new device-local asset ID would duplicate cloud metadata.
                asset = self._vault.import_managed(source, publish_metadata=False)
            return self.bind_resource_to_asset(resource_id, asset.local_asset_id)
        except LocalAssetLifecycleError:
            raise
        except Exception as exc:
            raise LocalAssetLifecycleError('PACKAGE_INVALID', '恢复包未通过校验，未恢复资料。') from exc

    def restore_from_package(self, local_asset_id: str, package: Path | str) -> LocalAssetView:
        asset = self._record(local_asset_id)
        if asset.import_mode is not ImportMode.MANAGED:
            raise LocalAssetLifecycleError("RESTORE_NOT_MANAGED", "Linked 资料不能从便携包恢复。")
        package_path = Path(package)
        if package_path.suffix.casefold() != ".mentorflowpack" or not _regular_file(package_path):
            raise LocalAssetLifecycleError("PACKAGE_UNAVAILABLE", "请选择可用的 .mentorflowpack 恢复包。")
        try:
            manifest, payloads, _manifest_sha = _read_verified_archive(package_path)
            if manifest.get("account_identity_fingerprint") != self._account_fingerprint:
                raise LocalAssetLifecycleError("PACKAGE_ACCOUNT_MISMATCH", "恢复包不属于当前账号。")
            recovery = json.loads(payloads[RECOVERY_INDEX_MEMBER].decode("utf-8"))
            rows = recovery.get("assets") if isinstance(recovery, dict) else None
            row = next(
                (
                    item
                    for item in rows or []
                    if isinstance(item, dict)
                    and item.get("original_local_asset_id") == local_asset_id
                ),
                None,
            )
            if not row or row.get("storage_kind") != "MANAGED":
                raise LocalAssetLifecycleError("PACKAGE_ASSET_MISSING", "恢复包中没有这份资料。")
            member = str(row.get("package_member") or "")
            expected_prefix = f"raw-assets/{local_asset_id}/"
            expected = self._expected_sha(asset, self._metadata(local_asset_id))
            raw = payloads.get(member)
            if (
                not member.startswith(expected_prefix)
                or Path(member).name != asset.display_name
                or not isinstance(raw, bytes)
                or str(row.get("sha256") or "").casefold() != expected
                or hashlib.sha256(raw).hexdigest() != expected
                or len(raw) != asset.size_bytes
            ):
                raise LocalAssetLifecycleError("PACKAGE_REVISION_MISMATCH", "恢复包中的资料 revision 不匹配。")
            temporary = (
                self._vault.root
                / "managed-assets"
                / "staging"
                / f"{uuid.uuid4().hex}.restore"
            )
            try:
                with temporary.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._publish_file(asset, temporary)
            finally:
                temporary.unlink(missing_ok=True)
        except LocalAssetLifecycleError:
            raise
        except Exception as exc:
            raise LocalAssetLifecycleError(
                "PACKAGE_INVALID",
                "恢复包未通过完整性校验，未更改当前资料。",
            ) from exc
        return self.resolve(local_asset_id)


__all__ = [
    "Downloader",
    "LocalAssetLifecycle",
    "LocalAssetLifecycleError",
    "LocalAssetView",
]
