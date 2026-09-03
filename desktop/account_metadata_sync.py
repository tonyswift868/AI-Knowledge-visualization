"""Account metadata hydration and device-local resource reconciliation.

Shared Workspace, Plan, Progress, and Resource Manifest metadata stays
server-canonical.  This application service only projects that metadata onto
the current account's existing local resource-binding port; it never transfers
resource bytes or sends a device path to the server.
"""

from __future__ import annotations

import copy
import math
import re
import secrets
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .account_identity import AccountIdentity, canonicalize_issuer
from .local_asset_lifecycle import LocalAssetLifecycleError


ACCOUNT_METADATA_SCHEMA_VERSION = "mentorflow.account-core-metadata.v1"
_RESOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_PATH_KEYS = frozenset(
    {
        "local_path",
        "linked_path",
        "local_relative_path",
        "vault_relative_path",
        "object_relative_path",
        "local_folder",
        "local_folder_tree",
        "folder_tree",
        "package_member",
    }
)
_RAW_RESOURCE_KEYS = frozenset(
    {
        "raw_bytes",
        "resource_bytes",
        "file_bytes",
        "content_bytes",
        "content_base64",
        "raw_resource",
        "raw_file",
        "binary_blob",
    }
)


class AccountMetadataSyncError(RuntimeError):
    """Fail-closed metadata or local-binding error with a stable code."""

    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(code)
        self.code = code
        self.user_message = user_message


class ResourceDeviceState(str, Enum):
    AVAILABLE = "AVAILABLE"
    MISSING_ON_THIS_DEVICE = "MISSING_ON_THIS_DEVICE"
    RELINKED = "RELINKED"


class AccountMetadataApi(Protocol):
    def account_metadata(self) -> Mapping[str, Any]: ...

    def put_account_progress(self, payload: dict[str, Any]) -> Mapping[str, Any]: ...


class ResourceBinding(Protocol):
    resource_id: str
    fingerprint_sha256: str
    availability: Any


class LocalResourceLifecycle(Protocol):
    def register_missing_resource(
        self,
        resource_id: str,
        *,
        fingerprint_sha256: str,
    ) -> ResourceBinding: ...

    def resolve_resource_binding(self, resource_id: str) -> ResourceBinding: ...

    def relink_resource(
        self,
        resource_id: str,
        source: Path | str,
    ) -> ResourceBinding: ...


@dataclass(frozen=True, slots=True)
class SyncedResource:
    resource_id: str
    expected_fingerprint_sha256: str
    server_metadata: Mapping[str, Any]
    state: ResourceDeviceState

    def device_projection(self) -> dict[str, Any]:
        """Return pathless server metadata plus the current device state."""

        projection = copy.deepcopy(dict(self.server_metadata))
        projection["resource_id"] = self.resource_id
        projection["fingerprint_sha256"] = self.expected_fingerprint_sha256
        projection["device_state"] = self.state.value
        return projection


@dataclass(frozen=True, slots=True)
class AccountMetadataSnapshot:
    """One server snapshot with a non-server device projection for resources."""

    server_metadata: Mapping[str, Any]
    resources: tuple[SyncedResource, ...]

    @property
    def workspace(self) -> Mapping[str, Any]:
        value = self.server_metadata.get("workspace")
        return value if isinstance(value, Mapping) else {}

    @property
    def plans(self) -> tuple[Mapping[str, Any], ...]:
        return _mapping_tuple(self.server_metadata.get("plans"))

    @property
    def progress(self) -> tuple[Mapping[str, Any], ...]:
        return _mapping_tuple(self.server_metadata.get("progress"))

    @property
    def learning_metadata(self) -> Any:
        return self.server_metadata.get("learning_metadata")

    def resource(self, resource_id: str) -> SyncedResource:
        identity = _resource_id(resource_id)
        for resource in self.resources:
            if secrets.compare_digest(resource.resource_id, identity):
                return resource
        raise AccountMetadataSyncError(
            "RESOURCE_NOT_IN_MANIFEST",
            "服务器 Resource Manifest 中没有这份资源。",
        )

    def device_projection(self) -> dict[str, Any]:
        """Compose a display-safe snapshot; this is never an upload payload."""

        projection = copy.deepcopy(dict(self.server_metadata))
        manifest = projection.get("resource_manifest")
        manifest_projection = dict(manifest) if isinstance(manifest, Mapping) else {}
        manifest_projection["assets"] = [
            resource.device_projection() for resource in self.resources
        ]
        projection["resource_manifest"] = manifest_projection
        return projection


@dataclass(frozen=True, slots=True)
class RelinkResult:
    resource: SyncedResource
    snapshot: AccountMetadataSnapshot


def _mapping_tuple(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _normalized_key(value: str) -> str:
    return re.sub(r"[\s-]+", "_", value.strip().casefold())


def _is_windows_path(value: str) -> bool:
    text = value.strip()
    return bool(_WINDOWS_ABSOLUTE_PATH.match(text)) or text.casefold().startswith(
        "file:"
    )


def _validate_boundary(value: Any, *, path: str = "metadata") -> None:
    """Validate exact JSON scalars without scanning serialized JSON text."""

    if isinstance(value, (bytes, bytearray, memoryview)):
        raise AccountMetadataSyncError(
            "RAW_RESOURCE_BYTES_FORBIDDEN",
            "Account metadata sync 不接受原始资源字节。",
        )
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise AccountMetadataSyncError(
                    "METADATA_NOT_JSON",
                    "Account metadata 字段名必须是字符串。",
                )
            key = _normalized_key(raw_key)
            if key in _PATH_KEYS:
                raise AccountMetadataSyncError(
                    "LOCAL_PATH_FORBIDDEN",
                    "Account metadata sync 不接受设备本地路径。",
                )
            if key in _RAW_RESOURCE_KEYS:
                raise AccountMetadataSyncError(
                    "RAW_RESOURCE_BYTES_FORBIDDEN",
                    "Account metadata sync 不接受原始资源内容。",
                )
            _validate_boundary(item, path=f"{path}.{raw_key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_boundary(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        if _is_windows_path(value):
            raise AccountMetadataSyncError(
                "LOCAL_PATH_FORBIDDEN",
                "Account metadata sync 不接受 Windows 本地路径。",
            )
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise AccountMetadataSyncError(
        "METADATA_NOT_JSON",
        f"Account metadata 不是可同步 JSON：{path}",
    )


def validate_metadata_boundary(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a detached metadata-only JSON object or fail before side effects."""

    if not isinstance(payload, Mapping):
        raise AccountMetadataSyncError(
            "METADATA_INVALID",
            "Account metadata 响应必须是对象。",
        )
    _validate_boundary(payload)
    return copy.deepcopy(dict(payload))


def _resource_id(value: Any) -> str:
    identity = str(value or "").strip()
    if not _RESOURCE_ID.fullmatch(identity):
        raise AccountMetadataSyncError(
            "RESOURCE_ID_INVALID",
            "Resource Manifest 包含无效资源标识。",
        )
    return identity


def _fingerprint(value: Any) -> str:
    fingerprint = str(value or "").strip().casefold()
    if not _SHA256.fullmatch(fingerprint):
        raise AccountMetadataSyncError(
            "RESOURCE_FINGERPRINT_INVALID",
            "Resource Manifest 包含无效 SHA-256 fingerprint。",
        )
    return fingerprint


def _binding_value(binding: ResourceBinding, field: str) -> Any:
    if isinstance(binding, Mapping):
        return binding.get(field)
    return getattr(binding, field, None)


def _availability_value(binding: ResourceBinding) -> str:
    availability = _binding_value(binding, "availability")
    return str(getattr(availability, "value", availability) or "")


class AccountMetadataSyncService:
    """Hydrate server metadata and reconcile only current-device bindings."""

    def __init__(
        self,
        api: AccountMetadataApi,
        lifecycle: LocalResourceLifecycle,
        *,
        account_identity: AccountIdentity | Mapping[str, Any],
    ) -> None:
        self._api = api
        self._lifecycle = lifecycle
        self._issuer, self._subject, self._organization_id, self._workspace_id = (
            self._identity(account_identity)
        )
        self._snapshot: AccountMetadataSnapshot | None = None

    @staticmethod
    def _identity(
        identity: AccountIdentity | Mapping[str, Any],
    ) -> tuple[str, str, str | None, str | None]:
        if isinstance(identity, Mapping):
            issuer_value = identity.get("issuer")
            subject_value = identity.get("subject") or identity.get("subject_id")
            organization = identity.get("organization_id")
            workspace = identity.get("workspace_id")
        else:
            issuer_value = identity.issuer
            subject_value = identity.subject
            organization = identity.organization_id
            workspace = identity.workspace_id
        try:
            issuer = canonicalize_issuer(str(issuer_value or ""))
        except ValueError as exc:
            raise AccountMetadataSyncError(
                "ACCOUNT_IDENTITY_INVALID",
                "当前 Enterprise Account issuer 无效。",
            ) from exc
        subject = str(subject_value or "").strip()
        if not subject:
            raise AccountMetadataSyncError(
                "ACCOUNT_IDENTITY_INVALID",
                "当前 Enterprise Account subject 无效。",
            )
        return (
            issuer,
            subject,
            str(organization).strip() if organization else None,
            str(workspace).strip() if workspace else None,
        )

    @property
    def snapshot(self) -> AccountMetadataSnapshot | None:
        return self._snapshot

    def _validate_server_context(self, payload: Mapping[str, Any]) -> None:
        if payload.get("schema_version") != ACCOUNT_METADATA_SCHEMA_VERSION:
            raise AccountMetadataSyncError(
                "METADATA_SCHEMA_UNSUPPORTED",
                "服务器 Account metadata 版本不受支持。",
            )
        account = payload.get("account")
        tenant = payload.get("tenant")
        if not isinstance(account, Mapping) or not isinstance(tenant, Mapping):
            raise AccountMetadataSyncError(
                "METADATA_CONTEXT_INVALID",
                "服务器 Account metadata 缺少账号或租户上下文。",
            )
        try:
            server_issuer = canonicalize_issuer(str(account.get("issuer") or ""))
        except ValueError as exc:
            raise AccountMetadataSyncError(
                "METADATA_CONTEXT_INVALID",
                "服务器 Account metadata issuer 无效。",
            ) from exc
        server_subject = str(account.get("subject_id") or "").strip()
        if not secrets.compare_digest(server_issuer, self._issuer) or not secrets.compare_digest(
            server_subject, self._subject
        ):
            raise AccountMetadataSyncError(
                "METADATA_ACCOUNT_MISMATCH",
                "服务器 metadata 不属于当前登录账号。",
            )
        for expected, field in (
            (self._organization_id, "organization_id"),
            (self._workspace_id, "workspace_id"),
        ):
            if expected and not secrets.compare_digest(
                str(tenant.get(field) or "").strip(), expected
            ):
                raise AccountMetadataSyncError(
                    "METADATA_TENANT_MISMATCH",
                    "服务器 metadata 不属于当前选择的组织或 Workspace。",
                )
        if not isinstance(payload.get("workspace"), Mapping):
            raise AccountMetadataSyncError(
                "METADATA_SHAPE_INVALID",
                "服务器 metadata 缺少 Workspace。",
            )
        for field in ("plans", "progress"):
            value = payload.get(field)
            if not isinstance(value, list) or any(
                not isinstance(item, Mapping) for item in value
            ):
                raise AccountMetadataSyncError(
                    "METADATA_SHAPE_INVALID",
                    f"服务器 metadata 的 {field} 列表无效。",
                )

    @staticmethod
    def _manifest_rows(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        manifest = payload.get("resource_manifest")
        assets = manifest.get("assets") if isinstance(manifest, Mapping) else None
        if not isinstance(assets, list) or any(
            not isinstance(item, Mapping) for item in assets
        ):
            raise AccountMetadataSyncError(
                "RESOURCE_MANIFEST_INVALID",
                "服务器 Resource Manifest 无效。",
            )
        return tuple(assets)

    @staticmethod
    def _resource_contract(row: Mapping[str, Any]) -> tuple[str, str]:
        resource_value = row.get("resource_id")
        asset_value = row.get("asset_id")
        if resource_value and asset_value and str(resource_value) != str(asset_value):
            raise AccountMetadataSyncError(
                "RESOURCE_ID_INVALID",
                "Resource Manifest 的资源标识不一致。",
            )
        fingerprint_value = row.get("fingerprint_sha256")
        sha_value = row.get("sha256")
        if fingerprint_value and sha_value and str(fingerprint_value).casefold() != str(
            sha_value
        ).casefold():
            raise AccountMetadataSyncError(
                "RESOURCE_FINGERPRINT_INVALID",
                "Resource Manifest 的 fingerprint 不一致。",
            )
        return (
            _resource_id(resource_value or asset_value),
            _fingerprint(fingerprint_value or sha_value),
        )

    def _missing_binding(self, resource_id: str, fingerprint: str) -> ResourceBinding:
        try:
            return self._lifecycle.register_missing_resource(
                resource_id,
                fingerprint_sha256=fingerprint,
            )
        except LocalAssetLifecycleError as exc:
            raise AccountMetadataSyncError(exc.code, exc.user_message) from exc

    def _binding_for(self, resource_id: str, fingerprint: str) -> ResourceBinding:
        try:
            binding = self._lifecycle.resolve_resource_binding(resource_id)
        except LocalAssetLifecycleError as exc:
            if exc.code != "RESOURCE_BINDING_NOT_FOUND":
                raise AccountMetadataSyncError(exc.code, exc.user_message) from exc
            binding = self._missing_binding(resource_id, fingerprint)
        binding_resource_id = _resource_id(_binding_value(binding, "resource_id"))
        binding_fingerprint = _fingerprint(
            _binding_value(binding, "fingerprint_sha256")
        )
        if not secrets.compare_digest(binding_resource_id, resource_id):
            raise AccountMetadataSyncError(
                "RESOURCE_BINDING_MISMATCH",
                "本地资源关联返回了不同的资源标识。",
            )
        if not secrets.compare_digest(binding_fingerprint, fingerprint):
            binding = self._missing_binding(resource_id, fingerprint)
        return binding

    def sync(self) -> AccountMetadataSnapshot:
        """Fetch canonical metadata, then reconcile pathless local state."""

        payload = validate_metadata_boundary(self._api.account_metadata())
        self._validate_server_context(payload)
        rows = self._manifest_rows(payload)
        contracts = [self._resource_contract(row) for row in rows]
        resource_ids = [resource_id for resource_id, _fingerprint_value in contracts]
        if len(resource_ids) != len(set(resource_ids)):
            raise AccountMetadataSyncError(
                "RESOURCE_MANIFEST_DUPLICATE",
                "服务器 Resource Manifest 包含重复资源。",
            )

        resources: list[SyncedResource] = []
        for row, (resource_id, fingerprint) in zip(rows, contracts, strict=True):
            binding = self._binding_for(resource_id, fingerprint)
            state = (
                ResourceDeviceState.AVAILABLE
                if _availability_value(binding) == "AVAILABLE_LOCAL"
                else ResourceDeviceState.MISSING_ON_THIS_DEVICE
            )
            resources.append(
                SyncedResource(
                    resource_id=resource_id,
                    expected_fingerprint_sha256=fingerprint,
                    server_metadata=copy.deepcopy(dict(row)),
                    state=state,
                )
            )
        snapshot = AccountMetadataSnapshot(
            server_metadata=payload,
            resources=tuple(resources),
        )
        self._snapshot = snapshot
        return snapshot

    def relink(
        self,
        *,
        resource_id: str,
        expected_fingerprint_sha256: str,
        selected_file: Path | str,
    ) -> RelinkResult:
        """Relink by canonical hash and change only the device-local binding."""

        if self._snapshot is None:
            raise AccountMetadataSyncError(
                "METADATA_SYNC_REQUIRED",
                "请先同步 Account metadata，再重新关联资源。",
            )
        identity = _resource_id(resource_id)
        expected = _fingerprint(expected_fingerprint_sha256)
        resource = self._snapshot.resource(identity)
        if not secrets.compare_digest(expected, resource.expected_fingerprint_sha256):
            raise AccountMetadataSyncError(
                "RESOURCE_FINGERPRINT_STALE",
                "Resource Manifest 已更新，请同步后重试；本地关联未更改。",
            )
        try:
            binding = self._lifecycle.relink_resource(identity, selected_file)
        except LocalAssetLifecycleError as exc:
            raise AccountMetadataSyncError(exc.code, exc.user_message) from exc
        if (
            _resource_id(_binding_value(binding, "resource_id")) != identity
            or _fingerprint(_binding_value(binding, "fingerprint_sha256")) != expected
            or _availability_value(binding) != "AVAILABLE_LOCAL"
        ):
            raise AccountMetadataSyncError(
                "RESOURCE_BINDING_MISMATCH",
                "本地资源关联未能验证，未更新 Account metadata 视图。",
            )
        relinked = replace(resource, state=ResourceDeviceState.RELINKED)
        resources = tuple(
            relinked if item.resource_id == identity else item
            for item in self._snapshot.resources
        )
        snapshot = replace(self._snapshot, resources=resources)
        self._snapshot = snapshot
        return RelinkResult(resource=relinked, snapshot=snapshot)

    def push_progress(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Send one metadata-only progress update; raw files have no API here."""

        outbound = validate_metadata_boundary(payload)
        response = self._api.put_account_progress(outbound)
        return validate_metadata_boundary(response)


__all__ = [
    "ACCOUNT_METADATA_SCHEMA_VERSION",
    "AccountMetadataSnapshot",
    "AccountMetadataSyncError",
    "AccountMetadataSyncService",
    "RelinkResult",
    "ResourceDeviceState",
    "SyncedResource",
    "validate_metadata_boundary",
]
