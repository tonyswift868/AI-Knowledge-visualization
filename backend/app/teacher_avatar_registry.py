"""Licensed avatar registry for the rotating AI teacher surface.

The learning persona and course memory deliberately do not live in this
module.  A teacher avatar is only a presentation layer: a user-supplied VRM
or GLB file, its usage terms, and a small set of model-agnostic rig controls.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import io
import struct
import zipfile
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from .runtime_resources import project_resource_root
from threading import Lock
from typing import Any, Callable, Protocol, TypeVar

from .credential_store import get_secret
from .enterprise.contracts_v1 import TenantMode
from .koikatsu_paths import KKS_RUNTIME_ROOT, TEACHER_AVATAR_REGISTRY_ROOT

PROJECT_ROOT = project_resource_root()
REGISTRY_ROOT = TEACHER_AVATAR_REGISTRY_ROOT
REGISTRY_FILE = REGISTRY_ROOT / "registry.json"
KKS_CHARACTER_PACK_ROOT = KKS_RUNTIME_ROOT / "character-packs"
KKS_RUNTIME_PACK_ROOT = KKS_RUNTIME_ROOT / "runtime-packs"
MAX_MODEL_BYTES = 100 * 1024 * 1024
MAX_BATCH_MODELS = 50
MAX_BATCH_BYTES = 500 * 1024 * 1024
ALLOWED_SUFFIXES = {".vrm", ".glb", ".zip"}
KOIKATSU_EXPORT_SUFFIXES = {".pmx", ".png", ".jpg", ".jpeg", ".bmp", ".tga", ".dds", ".json"}
MAX_KOIKATSU_EXPORT_BYTES = 500 * 1024 * 1024
KKS_CHARACTER_PACK_MANIFEST = "mentorflow-kks-pack.json"
KKS_CHARACTER_PACK_SCHEMA = "mentorflow.kks-character-pack.v2"
KKS_MODEL_PROVENANCE_PATH = "model.provenance.json"
KKS_MODEL_PROVENANCE_SCHEMA = "mentorflow.kks-web-model-provenance.v1"
KKS_APPEARANCE_RECEIPT_PATH = "appearance-receipt.json"
KKS_APPEARANCE_RECEIPT_SCHEMA = "mentorflow.kks-appearance-receipt.v1"
KKS_RUNTIME_PACK_MANIFEST = "mentorflow-kks-runtime-pack.json"
KKS_RUNTIME_PACK_SCHEMA = "mentorflow.kks-runtime-pack.v1"
MOTION_LIBRARY = ("idle", "explain", "question", "encourage", "quiz", "celebrate", "redirect")
_LOCK = Lock()
_SecretResult = TypeVar("_SecretResult")


class TeacherAvatarError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class VroidHubCredentialSource(str, Enum):
    LEGACY_PERSONAL_LOCAL_COMPATIBILITY = "LEGACY_PERSONAL_LOCAL_COMPATIBILITY"
    ENTERPRISE_PROVIDER_GOVERNANCE = "ENTERPRISE_PROVIDER_GOVERNANCE"
    ENTERPRISE_CREDENTIAL_REFERENCE_REQUIRED = "ENTERPRISE_CREDENTIAL_REFERENCE_REQUIRED"


@dataclass(frozen=True, slots=True)
class VroidHubCredentialSnapshot:
    """Secret-free status projected into Teacher Avatar API metadata."""

    configured: bool
    source: VroidHubCredentialSource
    status: str
    revision: int | None = None

    def to_public_dict(self) -> dict[str, object]:
        return {
            "vroid_hub_configured": self.configured,
            "credential_source": self.source.value,
            "credential_status": self.status,
            "credential_revision": self.revision,
        }


class CredentialResolverPort(Protocol):
    """Resolve secret material only for an already fenced execution."""

    def __call__(self, credential_reference: str) -> str: ...


class ProviderGovernancePort(Protocol):
    """Narrow provider-governance seam used by the VRoid Hub adapter."""

    def get_internal(self, config_id: str) -> Any: ...

    def authorize_new_session(self, config_id: str, model: str) -> Any: ...

    def validate_session(self, grant: Any) -> Any: ...


class LegacyLocalVroidHubCompatibilityAdapter:
    """The only permitted legacy plaintext environment compatibility boundary."""

    @staticmethod
    def credential_snapshot() -> VroidHubCredentialSnapshot:
        configured = bool(
            os.getenv("VROID_HUB_CLIENT_ID", "").strip()
            and os.getenv("VROID_HUB_CLIENT_SECRET", "")
        )
        return VroidHubCredentialSnapshot(
            configured=configured,
            source=VroidHubCredentialSource.LEGACY_PERSONAL_LOCAL_COMPATIBILITY,
            status="ACTIVE" if configured else "MISSING_LEGACY_CREDENTIAL",
        )


@dataclass(frozen=True, slots=True)
class EnterpriseVroidHubCredentialAdapter:
    """Reference-only Enterprise adapter with revision-fenced secret execution."""

    governance: ProviderGovernancePort
    config_id: str
    model: str = "vroid-hub-api"

    def __post_init__(self) -> None:
        if not str(self.config_id or "").strip():
            raise ValueError("config_id must not be empty")
        if not str(self.model or "").strip():
            raise ValueError("model must not be empty")

    def credential_snapshot(self) -> VroidHubCredentialSnapshot:
        try:
            config = self.governance.get_internal(self.config_id)
        except RuntimeError as exc:
            if getattr(exc, "code", None) != "PROVIDER_CONFIG_NOT_FOUND":
                raise
            return VroidHubCredentialSnapshot(
                configured=False,
                source=VroidHubCredentialSource.ENTERPRISE_CREDENTIAL_REFERENCE_REQUIRED,
                status="MISSING_CREDENTIAL_REFERENCE",
            )
        status = str(getattr(config.status, "value", config.status))
        return VroidHubCredentialSnapshot(
            configured=status == "ACTIVE",
            source=VroidHubCredentialSource.ENTERPRISE_PROVIDER_GOVERNANCE,
            status=status,
            revision=config.revision,
        )

    def authorize_new_session(self) -> Any:
        """Revoked, rotating, or invalid configs are rejected by governance."""

        return self.governance.authorize_new_session(self.config_id, self.model)

    def execute_with_secret(
        self,
        grant: Any,
        operation: Callable[[str], _SecretResult],
        *,
        resolver: CredentialResolverPort = get_secret,
    ) -> _SecretResult:
        """Resolve plaintext after fencing and expose it only to the provider call."""

        config = self.governance.validate_session(grant)
        credential_reference = config.secret_reference.value
        try:
            secret = resolver(credential_reference)
        except Exception:
            raise TeacherAvatarError(
                "VROID_HUB_CREDENTIAL_UNAVAILABLE",
                "VRoid Hub credential is unavailable.",
                503,
            ) from None
        if not secret:
            raise TeacherAvatarError(
                "VROID_HUB_CREDENTIAL_UNAVAILABLE",
                "VRoid Hub credential is unavailable.",
                503,
            )
        try:
            return operation(secret)
        except Exception:
            raise TeacherAvatarError(
                "VROID_HUB_EXECUTION_FAILED",
                "VRoid Hub provider execution failed.",
                502,
            ) from None
        finally:
            secret = ""


def validate_kks_appearance_receipt(
    receipt: Any,
    *,
    card_fingerprint: str,
    model_sha256: str,
    source_card_sha256: str,
    provenance_sha256: str,
) -> dict[str, Any]:
    """Validate the visual acceptance evidence bound to one card and GLB."""

    if not isinstance(receipt, dict) or receipt.get("schema") != KKS_APPEARANCE_RECEIPT_SCHEMA:
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS appearance receipt schema is invalid.", 415)
    if receipt.get("status") != "ready" or receipt.get("decision") != "accepted":
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS appearance receipt is not approved.", 415)
    expected = {
        "card_fingerprint": card_fingerprint,
        "model_sha256": model_sha256,
        "source_card_sha256": source_card_sha256,
        "provenance_sha256": provenance_sha256,
    }
    for key, value in expected.items():
        if str(receipt.get(key) or "").casefold() != value.casefold():
            raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", f"KKS appearance receipt {key} does not match.", 415)
    if receipt.get("model_kind") != "kkbp-web":
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS appearance receipt model kind is invalid.", 415)
    reviewer = receipt.get("reviewer")
    if (
        not isinstance(reviewer, dict)
        or not str(reviewer.get("kind") or "").strip()
        or not str(reviewer.get("id") or "").strip()
        or not str(receipt.get("reviewed_at") or "").strip()
    ):
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS appearance receipt reviewer is missing.", 415)
    checks = receipt.get("checks")
    required_checks = (
        "identity_verified",
        "face_features_visible",
        "materials_separated",
        "full_body_visible",
        "browser_visual_review",
    )
    if not isinstance(checks, dict) or any(checks.get(key) is not True for key in required_checks):
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS appearance receipt checks are incomplete.", 415)
    evidence = receipt.get("evidence")
    screenshot_sha = str(evidence.get("browser_screenshot_sha256") or "").casefold() if isinstance(evidence, dict) else ""
    if not re.fullmatch(r"[0-9a-f]{64}", screenshot_sha):
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS appearance receipt lacks browser screenshot evidence.", 415)
    return {
        "status": "ready",
        "card_fingerprint": card_fingerprint,
        "model_sha256": model_sha256,
        "source_card_sha256": source_card_sha256,
        "provenance_sha256": provenance_sha256,
        "model_kind": "kkbp-web",
        "browser_screenshot_sha256": screenshot_sha,
    }


def _read_verified_kks_character_pack(archive: zipfile.ZipFile) -> dict[str, Any]:
    """Read a v2 pack and verify every hash before it may be listed or installed."""

    manifest = json.loads(archive.read(KKS_CHARACTER_PACK_MANIFEST).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != KKS_CHARACTER_PACK_SCHEMA:
        raise TeacherAvatarError("KKS_PACK_INVALID", "KKS character pack schema is unsupported.", 415)
    fingerprint = str(manifest.get("card_fingerprint") or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise TeacherAvatarError("KKS_PACK_INVALID", "KKS character pack fingerprint is invalid.", 415)
    model = manifest.get("model")
    model_path = str(model.get("path") or "") if isinstance(model, dict) else ""
    if model_path != "model.glb":
        raise TeacherAvatarError("KKS_PACK_INVALID", "KKS character pack model path is invalid.", 415)
    model_content = archive.read(model_path)
    model_sha = hashlib.sha256(model_content).hexdigest()
    if str(model.get("sha256") or "").casefold() != model_sha:
        raise TeacherAvatarError("KKS_PACK_INVALID", "KKS character pack model checksum failed.", 415)
    provenance_contract = manifest.get("provenance")
    provenance_path = str(provenance_contract.get("path") or "") if isinstance(provenance_contract, dict) else ""
    provenance_sha = str(provenance_contract.get("sha256") or "").casefold() if isinstance(provenance_contract, dict) else ""
    if provenance_path != KKS_MODEL_PROVENANCE_PATH or not re.fullmatch(r"[0-9a-f]{64}", provenance_sha):
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS model provenance contract is invalid.", 415)
    provenance_content = archive.read(provenance_path)
    provenance = json.loads(provenance_content.decode("utf-8"))
    if (
        hashlib.sha256(provenance_content).hexdigest() != provenance_sha
        or not isinstance(provenance, dict)
        or provenance.get("schema") != KKS_MODEL_PROVENANCE_SCHEMA
        or provenance.get("identity_verified") is not True
        or str(provenance.get("model_sha256") or "").casefold() != model_sha
    ):
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS model provenance verification failed.", 415)
    appearance = manifest.get("appearance_contract")
    source_card_sha = str(appearance.get("source_card_sha256") or "").casefold() if isinstance(appearance, dict) else ""
    receipt_path = str(appearance.get("receipt_path") or "") if isinstance(appearance, dict) else ""
    receipt_sha = str(appearance.get("receipt_hash") or "").casefold() if isinstance(appearance, dict) else ""
    if (
        not isinstance(appearance, dict)
        or appearance.get("status") != "ready"
        or appearance.get("model_kind") != "kkbp-web"
        or str(appearance.get("card_fingerprint") or "").casefold() != fingerprint
        or str(appearance.get("model_sha256") or "").casefold() != model_sha
        or str(provenance.get("source_card_sha256") or "").casefold() != source_card_sha
        or receipt_path != KKS_APPEARANCE_RECEIPT_PATH
        or not re.fullmatch(r"[0-9a-f]{64}", source_card_sha)
        or not re.fullmatch(r"[0-9a-f]{64}", receipt_sha)
    ):
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS appearance contract is invalid.", 415)
    try:
        receipt_content = archive.read(receipt_path)
    except KeyError as exc:
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS appearance receipt is missing.", 415) from exc
    if hashlib.sha256(receipt_content).hexdigest() != receipt_sha:
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS appearance receipt checksum failed.", 415)
    receipt = json.loads(receipt_content.decode("utf-8"))
    validated_appearance = validate_kks_appearance_receipt(
        receipt,
        card_fingerprint=fingerprint,
        model_sha256=model_sha,
        source_card_sha256=source_card_sha,
        provenance_sha256=provenance_sha,
    )
    return {
        "manifest": manifest,
        "fingerprint": fingerprint,
        "model_content": model_content,
        "model_sha256": model_sha,
        "provenance_content": provenance_content,
        "receipt_content": receipt_content,
        "appearance_contract": {
            **validated_appearance,
            "receipt_hash": receipt_sha,
        },
    }


def _empty_registry() -> dict[str, Any]:
    return {"version": 1, "models": []}


def _load() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        return _empty_registry()
    try:
        payload = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TeacherAvatarError("REGISTRY_INVALID", "教师模型登记表损坏，无法安全加载。", 500) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise TeacherAvatarError("REGISTRY_INVALID", "教师模型登记表格式无效。", 500)
    return payload


def _save(payload: dict[str, Any]) -> None:
    REGISTRY_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = REGISTRY_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(REGISTRY_FILE)


def _public(item: dict[str, Any]) -> dict[str, Any]:
    model_id = str(item["model_id"])
    model_url = None if item.get("asset_profile") == "kks-runtime-pack" else f"/api/teacher-avatars/{model_id}/file"
    if item.get("format") == "pmx":
        model_url = f"/api/teacher-avatars/{model_id}/file/{Path(str(item['file_path'])).name}"
    public = {
        **{key: value for key, value in item.items() if key != "file_path"},
        "model_url": model_url,
        "motions": list(MOTION_LIBRARY) if item.get("format") != "pmx" else ["idle"],
    }
    if item.get("asset_profile") == "kks-runtime-pack":
        public["runtime_manifest_url"] = f"/api/koikatsu-runtime-packs/{item.get('runtime_pack_id')}/manifest"
    return public


def list_models(
    *,
    tenant_mode: TenantMode = TenantMode.PERSONAL_LOCAL,
    enterprise_credentials: EnterpriseVroidHubCredentialAdapter | None = None,
) -> dict[str, Any]:
    if not isinstance(tenant_mode, TenantMode):
        raise TeacherAvatarError(
            "TENANT_MODE_REQUIRED",
            "A canonical TenantMode is required for VRoid Hub credential selection.",
        )
    if tenant_mode is TenantMode.ENTERPRISE_SERVER:
        credential_snapshot = (
            enterprise_credentials.credential_snapshot()
            if enterprise_credentials is not None
            else VroidHubCredentialSnapshot(
                configured=False,
                source=VroidHubCredentialSource.ENTERPRISE_CREDENTIAL_REFERENCE_REQUIRED,
                status="MISSING_CREDENTIAL_REFERENCE",
            )
        )
    else:
        if enterprise_credentials is not None:
            raise TeacherAvatarError(
                "ENTERPRISE_CREDENTIAL_MODE_REQUIRED",
                "Enterprise VRoid Hub credentials require Enterprise Server mode.",
            )
        credential_snapshot = LegacyLocalVroidHubCompatibilityAdapter.credential_snapshot()
    with _LOCK:
        items = [_public(item) for item in _load()["models"] if item.get("enabled", True)]
    return {
        "items": items,
        "count": len(items),
        "accepted_formats": [".vrm", ".glb", ".zip (MentorFlow KKS character pack, native runtime pack, or KKBP export)"],
        "motion_library": list(MOTION_LIBRARY),
        "provider_guidance": {
            "recommended": "VRM 1.0 / VRoid Hub authorized model",
            "vroid_hub_api": "https://developer.vroid.com/en/api/",
            "vroid_hub_application_registration": "https://hub.vroid.com/oauth/applications/",
            **credential_snapshot.to_public_dict(),
            "oauth_requirements": ["OAuth 2.0", "PKCE S256", "X-Api-Version: 11", "per-model usage conditions"],
            "license_required": True,
        },
    }


def _glb_document(content: bytes) -> dict[str, Any]:
    if len(content) < 20 or content[:4] != b"glTF":
        raise TeacherAvatarError("MODEL_BINARY_INVALID", "模型不是有效的 GLB/VRM 2.0 二进制文件。", 415)
    version, declared_length = struct.unpack_from("<II", content, 4)
    if version != 2 or declared_length != len(content):
        raise TeacherAvatarError("MODEL_BINARY_INVALID", "模型 GLB 头信息无效或文件不完整。", 415)
    json_length, json_type = struct.unpack_from("<II", content, 12)
    if json_type != 0x4E4F534A or 20 + json_length > len(content):
        raise TeacherAvatarError("MODEL_BINARY_INVALID", "模型缺少有效的 GLB JSON 数据块。", 415)
    try:
        document = json.loads(content[20 : 20 + json_length].decode("utf-8").rstrip(" \t\r\n\0"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TeacherAvatarError("MODEL_BINARY_INVALID", "模型 GLB JSON 数据块损坏。", 415) from exc
    if not isinstance(document, dict):
        raise TeacherAvatarError("MODEL_BINARY_INVALID", "模型 GLB JSON 数据块格式无效。", 415)
    return document


def _validate_humanoid_rig(content: bytes) -> None:
    """Reject generic GLB files that cannot use the shared teacher motion rig."""

    document = _glb_document(content)

    extensions = document.get("extensions") or {}
    required = {"head", "leftUpperArm", "leftLowerArm", "rightUpperArm", "rightLowerArm"}
    vrm0 = extensions.get("VRM") or {}
    vrm1 = extensions.get("VRMC_vrm") or {}
    vrm0_bones = {
        item.get("bone")
        for item in ((vrm0.get("humanoid") or {}).get("humanBones") or [])
        if isinstance(item, dict) and isinstance(item.get("node"), int)
    }
    vrm1_bones = {
        name
        for name, item in (((vrm1.get("humanoid") or {}).get("humanBones") or {}).items())
        if isinstance(item, dict) and isinstance(item.get("node"), int)
    }
    missing = required - (vrm0_bones | vrm1_bones)
    if not (vrm0 or vrm1) or missing:
        raise TeacherAvatarError(
            "HUMANOID_RIG_REQUIRED",
            "模型必须包含可用于通用动作的 VRM 人形骨架（头部与双臂骨骼）。",
            415,
        )


def _validate_kks_character_glb(content: bytes) -> dict[str, int]:
    """Validate the retained full-rig KKS Web profile used by card packs."""

    document = _glb_document(content)
    extensions = set(document.get("extensionsUsed") or [])
    missing_extensions = {"EXT_meshopt_compression", "EXT_texture_webp"} - extensions
    if missing_extensions:
        raise TeacherAvatarError(
            "KKS_PACK_MODEL_INVALID",
            f"恋活资源包模型缺少网页压缩扩展：{sorted(missing_extensions)}",
            415,
        )
    nodes = document.get("nodes") or []
    joints = {
        int(index)
        for skin in document.get("skins") or []
        for index in skin.get("joints") or []
        if isinstance(index, int)
    }
    joint_names = {
        str(nodes[index].get("name") or "")
        for index in joints
        if 0 <= index < len(nodes) and isinstance(nodes[index], dict)
    }
    required = {
        "cf_s_head",
        "cf_s_arm01_L",
        "cf_s_forearm01_L",
        "cf_s_arm01_R",
        "cf_s_forearm01_R",
    }
    missing_bones = sorted(required - joint_names)
    if len(joint_names) < 800 or missing_bones:
        raise TeacherAvatarError(
            "KKS_PACK_RIG_INVALID",
            f"恋活资源包必须保留完整骨架（当前{len(joint_names)}个关节，缺失{missing_bones}）。",
            415,
        )
    morph_count = sum(
        len(primitive.get("targets") or [])
        for mesh in document.get("meshes") or []
        for primitive in mesh.get("primitives") or []
    )
    materials = document.get("materials") or []
    light_maps = sum(
        1
        for material in materials
        if (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
    )
    dark_maps = sum(1 for material in materials if material.get("emissiveTexture"))
    if morph_count < 1 or light_maps < 1 or dark_maps < 1:
        raise TeacherAvatarError(
            "KKS_PACK_MATERIAL_INVALID",
            "恋活资源包缺少形态键或KKS明暗材质贴图。",
            415,
        )
    return {
        "joint_count": len(joint_names),
        "morph_target_count": morph_count,
        "light_map_count": light_maps,
        "dark_map_count": dark_maps,
    }


def list_kks_character_packs() -> dict[str, Any]:
    """List bounded, publisher-provided packs without extracting model bytes."""

    items: list[dict[str, Any]] = []
    installed = {
        str(item.get("card_fingerprint") or "")
        for item in _load().get("models", [])
        if item.get("asset_profile") == "kks-character-pack"
        and item.get("appearance_contract", {}).get("status") == "ready"
    }
    if KKS_CHARACTER_PACK_ROOT.is_dir():
        # Preserve category/name subdirectories on disk; the URL still uses
        # the unique file stem so the route remains simple and safe.
        for path in sorted(
            KKS_CHARACTER_PACK_ROOT.rglob("*.zip"),
            key=lambda item: item.relative_to(KKS_CHARACTER_PACK_ROOT).as_posix().casefold(),
        )[:100]:
            try:
                with zipfile.ZipFile(path) as archive:
                    info = archive.getinfo(KKS_CHARACTER_PACK_MANIFEST)
                    if info.file_size > 64 * 1024:
                        continue
                    manifest = json.loads(archive.read(info).decode("utf-8"))
                fingerprint = str(manifest.get("card_fingerprint") or "").casefold()
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("schema") != KKS_CHARACTER_PACK_SCHEMA
                    or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                ):
                    continue
                items.append({
                    "pack_id": str(manifest.get("pack_id") or path.stem),
                    "display_name": str(manifest.get("display_name") or path.stem),
                    "category": str(manifest.get("category") or "未分类"),
                    "category_path": str(manifest.get("category_path") or "未分类"),
                    "creator": str(manifest.get("creator") or ""),
                    "card_fingerprint": fingerprint,
                    "size_bytes": path.stat().st_size,
                    "installed": fingerprint in installed,
                    "download_url": f"/api/teacher-avatar-character-packs/{str(manifest.get('pack_id') or path.stem)}/download",
                })
            except (OSError, KeyError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError):
                continue
    """
    # A categorized rebuild may coexist with an older flat pack.  Expose one
    # download entry per native card fingerprint, preferring the categorized
    # package so the customer catalog does not show duplicates.
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.get("card_fingerprint") or item.get("pack_id") or "")
        previous = unique.get(key)
        if previous is None or (
            str(previous.get("category") or "未分类") == "未分类"
            and str(item.get("category") or "未分类") != "未分类"
        ):
            unique[key] = item
    items = sorted(unique.values(), key=lambda item: (str(item.get("category_path") or "").casefold(), str(item.get("display_name") or "").casefold()))
    return {"items": items, "count": len(items)}
    """


def list_kks_character_packs() -> dict[str, Any]:
    """List categorized packs and deduplicate legacy flat entries."""
    items: list[dict[str, Any]] = []
    installed = {
        str(item.get("card_fingerprint") or "")
        for item in _load().get("models", [])
        if item.get("asset_profile") == "kks-character-pack"
        and item.get("appearance_contract", {}).get("status") == "ready"
    }
    root = KKS_CHARACTER_PACK_ROOT.resolve()
    if not root.is_dir():
        return {"items": [], "count": 0}
    catalog_path = root / "catalog.json"
    # The catalog is only a presentation cache and cannot prove model identity
    # or visual acceptance. Enumerate and verify the ZIPs below instead.
    catalog_trusted = False
    if catalog_trusted and catalog_path.is_file():
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            for raw in catalog.get("items") or []:
                if not isinstance(raw, dict) or not raw.get("card_fingerprint"):
                    continue
                pack_id = str(raw.get("pack_id") or f"{raw.get('card_id')}-{str(raw['card_fingerprint'])[:12]}")
                items.append({
                    "pack_id": pack_id,
                    "display_name": str(raw.get("display_name") or pack_id),
                    "category": str(raw.get("category") or "未分类"),
                    "category_path": str(raw.get("category_path") or "未分类"),
                    "creator": str(raw.get("creator") or ""),
                    "card_fingerprint": str(raw.get("card_fingerprint") or "").casefold(),
                    "size_bytes": int(raw.get("size_bytes") or 0),
                    "installed": str(raw.get("card_fingerprint") or "").casefold() in installed,
                    "download_url": f"/api/teacher-avatar-character-packs/{pack_id}/download",
                })
                if raw.get("asset_profile"):
                    items[-1]["asset_profile"] = str(raw["asset_profile"])
                if raw.get("runtime_manifest_url"):
                    items[-1]["runtime_manifest_url"] = str(raw["runtime_manifest_url"])
            if items:
                return {"items": items, "count": len(items)}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            pass
    for path in sorted(root.rglob("*.zip"), key=lambda item: item.relative_to(root).as_posix().casefold())[:100]:
        try:
            with zipfile.ZipFile(path) as archive:
                info = archive.getinfo(KKS_CHARACTER_PACK_MANIFEST)
                if info.file_size > 64 * 1024:
                    continue
                verified = _read_verified_kks_character_pack(archive)
                manifest = verified["manifest"]
                fingerprint = str(verified["fingerprint"])
            pack_id = str(manifest.get("pack_id") or path.stem)
            items.append({
                "pack_id": pack_id,
                "display_name": str(manifest.get("display_name") or path.stem),
                "category": str(manifest.get("category") or "未分类"),
                "category_path": str(manifest.get("category_path") or "未分类"),
                "creator": str(manifest.get("creator") or ""),
                "card_fingerprint": fingerprint,
                "size_bytes": path.stat().st_size,
                "installed": fingerprint in installed,
                "download_url": f"/api/teacher-avatar-character-packs/{pack_id}/download",
            })
        except (
            OSError,
            KeyError,
            zipfile.BadZipFile,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TeacherAvatarError,
        ):
            continue
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.get("card_fingerprint") or item.get("pack_id") or "")
        previous = unique.get(key)
        if previous is None or (previous.get("category") == "未分类" and item.get("category") != "未分类"):
            unique[key] = item
    items = sorted(unique.values(), key=lambda item: (str(item.get("category_path") or "").casefold(), str(item.get("display_name") or "").casefold()))
    return {"items": items, "count": len(items)}


def resolve_kks_character_pack(pack_id: str) -> Path:
    value = str(pack_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise TeacherAvatarError("KKS_PACK_NOT_FOUND", "未找到指定的恋活角色资源包。", 404)
    root = KKS_CHARACTER_PACK_ROOT.resolve()
    matches = [path for path in root.rglob(f"{value}.zip") if path.is_file()]
    if False:
        path = root
    if False:
        raise TeacherAvatarError("KKS_PACK_NOT_FOUND", "未找到指定的恋活角色资源包。", 404) from exc
    if not matches:
        raise TeacherAvatarError("KKS_PACK_NOT_FOUND", "未找到指定的恋活角色资源包。", 404)
    return sorted(matches, key=lambda item: item.as_posix().casefold())[0]


def resolve_kks_character_pack(pack_id: str) -> Path:
    """Resolve a pack by filename stem while preserving categorized folders."""
    value = str(pack_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise TeacherAvatarError("KKS_PACK_NOT_FOUND", "invalid character pack id", 404)
    root = KKS_CHARACTER_PACK_ROOT.resolve()
    manifest_matches: list[Path] = []
    if root.is_dir():
        for path in root.rglob("*.zip"):
            try:
                with zipfile.ZipFile(path) as archive:
                    # The categorized offline builder publishes the newer
                    # self-contained runtime-pack manifest, while older
                    # customer packs use the character-pack manifest. Both
                    # are valid downloadable resources and must resolve via
                    # the same download endpoint.
                    manifest_name = next(
                        (
                            name
                            for name in (KKS_CHARACTER_PACK_MANIFEST, KKS_RUNTIME_PACK_MANIFEST)
                            if name in archive.namelist()
                        ),
                        None,
                    )
                    if manifest_name is None:
                        continue
                    manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
                if str(manifest.get("pack_id") or "") == value:
                    manifest_matches.append(path)
            except (OSError, KeyError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError):
                continue
    if manifest_matches:
        return sorted(manifest_matches, key=lambda item: item.as_posix().casefold())[0]
    matches = [path for path in root.rglob(f"{value}.zip") if path.is_file()]
    if not matches:
        raise TeacherAvatarError("KKS_PACK_NOT_FOUND", "character pack not found", 404)
    return sorted(matches, key=lambda item: item.as_posix().casefold())[0]


def _is_kks_character_pack(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            return KKS_CHARACTER_PACK_MANIFEST in archive.namelist()
    except zipfile.BadZipFile:
        return False


def _is_kks_runtime_pack(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            manifest = json.loads(archive.read(KKS_RUNTIME_PACK_MANIFEST).decode("utf-8"))
        return isinstance(manifest, dict) and manifest.get("schema") == KKS_RUNTIME_PACK_SCHEMA
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _import_kks_runtime_pack(
    filename: str,
    content: bytes,
    *,
    display_name: str,
    creator: str,
    source_url: str,
    license_name: str,
    license_url: str,
) -> dict[str, Any]:
    """Install a self-contained cache-ready native runtime pack."""
    if not content or len(content) > MAX_KOIKATSU_EXPORT_BYTES:
        raise TeacherAvatarError("RUNTIME_PACK_SIZE_INVALID", "runtime pack is empty or too large", 413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        manifest = json.loads(archive.read(KKS_RUNTIME_PACK_MANIFEST).decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TeacherAvatarError("RUNTIME_PACK_INVALID", "runtime pack manifest is invalid", 415) from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != KKS_RUNTIME_PACK_SCHEMA:
        raise TeacherAvatarError("RUNTIME_PACK_INVALID", "unsupported runtime pack schema", 415)
    pack_id = str(manifest.get("pack_id") or "").strip()
    fingerprint = str(manifest.get("card_fingerprint") or "").casefold()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", pack_id) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise TeacherAvatarError("RUNTIME_PACK_INVALID", "runtime pack identity is invalid", 415)
    required = {"card.png", "runtime/manifest.json"}
    declared = manifest.get("assets")
    if not isinstance(declared, list):
        raise TeacherAvatarError("RUNTIME_PACK_INVALID", "runtime pack assets are missing", 415)
    entries: dict[str, dict[str, Any]] = {}
    for raw in declared:
        if not isinstance(raw, dict):
            raise TeacherAvatarError("RUNTIME_PACK_INVALID", "runtime pack asset entry is invalid", 415)
        name = str(raw.get("path") or "").replace("\\", "/")
        path = Path(name)
        if not name or path.is_absolute() or ".." in path.parts or name != path.as_posix():
            raise TeacherAvatarError("RUNTIME_PACK_INVALID", "runtime pack asset path is unsafe", 415)
        entries[name] = raw
    entries.update({name: {} for name in required})
    if len(entries) > 5000:
        raise TeacherAvatarError("RUNTIME_PACK_INVALID", "runtime pack contains too many files", 413)
    try:
        total = sum(archive.getinfo(name).file_size for name in entries)
    except KeyError as exc:
        raise TeacherAvatarError("RUNTIME_PACK_INVALID", "runtime pack is missing a declared file", 415) from exc
    if total > MAX_KOIKATSU_EXPORT_BYTES:
        raise TeacherAvatarError("RUNTIME_PACK_SIZE_INVALID", "runtime pack expands beyond 500MB", 413)
    model_id = f"kks-runtime-pack-{fingerprint[:12]}"
    target = (KKS_RUNTIME_PACK_ROOT / pack_id).resolve()
    root = KKS_RUNTIME_PACK_ROOT.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise TeacherAvatarError("RUNTIME_PACK_INVALID", "runtime pack destination is unsafe", 415) from exc
    with _LOCK:
        payload = _load()
        existing = next((entry for entry in payload["models"] if entry.get("asset_profile") == "kks-runtime-pack" and (entry.get("card_fingerprint") == fingerprint or entry.get("runtime_pack_id") == pack_id)), None)
        if existing:
            return _public(existing)
        if target.exists():
            raise TeacherAvatarError("RUNTIME_PACK_EXISTS", "a runtime pack with this id already exists", 409)
        temporary = root / f".{pack_id}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir(parents=True, exist_ok=False)
        try:
            for name in entries:
                destination = (temporary / name).resolve()
                destination.relative_to(temporary.resolve())
                destination.parent.mkdir(parents=True, exist_ok=True)
                data = archive.read(name)
                declared_item = entries.get(name) or {}
                expected = str(declared_item.get("sha256") or "").casefold()
                if expected and expected != hashlib.sha256(data).hexdigest():
                    raise TeacherAvatarError("RUNTIME_PACK_INVALID", f"checksum mismatch for {name}", 415)
                destination.write_bytes(data)
            (temporary / KKS_RUNTIME_PACK_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, target)
            item = {
                "model_id": model_id,
                "display_name": display_name.strip() or str(manifest.get("display_name") or Path(filename).stem),
                "creator": creator.strip() or str(manifest.get("creator") or "未填写"),
                "format": "runtime-pack",
                "asset_profile": "kks-runtime-pack",
                "runtime_pack_id": pack_id,
                "runtime_manifest_url": f"/api/koikatsu-runtime-packs/{pack_id}/manifest",
                "card_fingerprint": fingerprint,
                "file_path": f"{pack_id}/runtime/manifest.json",
                "source_url": source_url.strip() or str(manifest.get("source_url") or ""),
                "license_name": license_name.strip() or str(manifest.get("license_name") or ""),
                "license_url": license_url.strip() or str(manifest.get("license_url") or ""),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "adult_character_confirmed": True,
                "rights_confirmed": True,
                "enabled": True,
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "morph": {"height": 1.0, "head_scale": 1.0, "upper_chest": 1.0, "shoulder_width": 1.0, "waist_width": 1.0, "hip_width": 1.0, "arm_length": 1.0, "leg_length": 1.0, "smile": 0.0, "blink": 1.0},
            }
            payload["models"].append(item)
            _save(payload)
        except Exception:
            if temporary.exists():
                import shutil
                shutil.rmtree(temporary, ignore_errors=True)
            if target.exists() and not any(entry.get("runtime_pack_id") == pack_id for entry in payload.get("models", [])):
                import shutil
                shutil.rmtree(target, ignore_errors=True)
            raise
    return _public(item)


def resolve_runtime_pack_manifest(pack_id: str) -> Path:
    value = str(pack_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise TeacherAvatarError("RUNTIME_PACK_NOT_FOUND", "invalid runtime pack id", 404)
    root = KKS_RUNTIME_PACK_ROOT.resolve()
    path = (root / value / "runtime" / "manifest.json").resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise TeacherAvatarError("RUNTIME_PACK_NOT_FOUND", "runtime pack path is unsafe", 404) from exc
    if not path.is_file():
        raise TeacherAvatarError("RUNTIME_PACK_NOT_FOUND", "runtime pack not found", 404)
    return path


def resolve_runtime_pack_asset(pack_id: str, asset_path: str) -> Path:
    value = str(pack_id).strip()
    relative = Path(str(asset_path).replace("\\", "/").lstrip("/"))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value) or relative.is_absolute() or ".." in relative.parts:
        raise TeacherAvatarError("RUNTIME_PACK_ASSET_NOT_FOUND", "invalid runtime pack asset path", 404)
    root = KKS_RUNTIME_PACK_ROOT.resolve()
    path = (root / value / "assets" / relative).resolve()
    try:
        path.relative_to(root / value)
    except ValueError as exc:
        raise TeacherAvatarError("RUNTIME_PACK_ASSET_NOT_FOUND", "runtime pack asset path is unsafe", 404) from exc
    if not path.is_file():
        raise TeacherAvatarError("RUNTIME_PACK_ASSET_NOT_FOUND", "runtime pack asset not found", 404)
    return path


def _import_kks_character_pack(
    filename: str,
    content: bytes,
    *,
    display_name: str,
    creator: str,
    source_url: str,
    license_name: str,
    license_url: str,
) -> dict[str, Any]:
    if not content or len(content) > MAX_KOIKATSU_EXPORT_BYTES:
        raise TeacherAvatarError("MODEL_SIZE_INVALID", "恋活角色资源包必须非空且不超过500MB。", 413)
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        verified_pack = _read_verified_kks_character_pack(archive)
        manifest = verified_pack["manifest"]
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TeacherAvatarError("KKS_PACK_INVALID", "恋活角色资源包清单无效。", 415) from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != KKS_CHARACTER_PACK_SCHEMA:
        raise TeacherAvatarError("KKS_PACK_INVALID", "恋活角色资源包版本不受支持。", 415)
    fingerprint = str(manifest.get("card_fingerprint") or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise TeacherAvatarError("KKS_PACK_INVALID", "恋活角色资源包缺少有效的角色卡指纹。", 415)
    model = manifest.get("model")
    model_path = str(model.get("path") or "") if isinstance(model, dict) else ""
    relative = Path(model_path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.suffix.casefold() != ".glb"
        or len(relative.parts) != 1
    ):
        raise TeacherAvatarError("KKS_PACK_INVALID", "恋活角色资源包模型路径无效。", 415)
    try:
        model_content = archive.read(model_path)
    except KeyError as exc:
        raise TeacherAvatarError("KKS_PACK_INVALID", "恋活角色资源包缺少模型文件。", 415) from exc
    expected_sha = str(model.get("sha256") or "").casefold()
    model_sha = hashlib.sha256(model_content).hexdigest()
    if expected_sha != model_sha:
        raise TeacherAvatarError("KKS_PACK_INVALID", "恋活角色资源包模型校验失败。", 415)
    provenance_contract = manifest.get("provenance")
    provenance_path = str(provenance_contract.get("path") or "") if isinstance(provenance_contract, dict) else ""
    provenance_sha = str(provenance_contract.get("sha256") or "").casefold() if isinstance(provenance_contract, dict) else ""
    if provenance_path != KKS_MODEL_PROVENANCE_PATH or not re.fullmatch(r"[0-9a-f]{64}", provenance_sha):
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS model provenance contract is invalid.", 415)
    try:
        provenance_content = archive.read(provenance_path)
        provenance = json.loads(provenance_content.decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS model provenance is missing or invalid.", 415) from exc
    if (
        hashlib.sha256(provenance_content).hexdigest() != provenance_sha
        or not isinstance(provenance, dict)
        or provenance.get("schema") != KKS_MODEL_PROVENANCE_SCHEMA
        or provenance.get("identity_verified") is not True
        or str(provenance.get("model_sha256") or "").casefold() != model_sha
    ):
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS model provenance verification failed.", 415)
    appearance = manifest.get("appearance_contract")
    if not isinstance(appearance, dict) or appearance.get("status") != "ready":
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS character pack has no approved appearance contract.", 415)
    source_card_sha = str(appearance.get("source_card_sha256") or "").casefold()
    receipt_path = str(appearance.get("receipt_path") or "")
    receipt_sha = str(appearance.get("receipt_hash") or "").casefold()
    if (
        receipt_path != KKS_APPEARANCE_RECEIPT_PATH
        or not re.fullmatch(r"[0-9a-f]{64}", source_card_sha)
        or not re.fullmatch(r"[0-9a-f]{64}", receipt_sha)
        or str(appearance.get("card_fingerprint") or "").casefold() != fingerprint
        or str(appearance.get("model_sha256") or "").casefold() != model_sha
        or appearance.get("model_kind") != "kkbp-web"
        or str(provenance.get("source_card_sha256") or "").casefold() != source_card_sha
    ):
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS character pack appearance contract is invalid.", 415)
    try:
        receipt_content = archive.read(receipt_path)
        receipt = json.loads(receipt_content.decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS appearance receipt is missing or invalid.", 415) from exc
    if hashlib.sha256(receipt_content).hexdigest() != receipt_sha:
        raise TeacherAvatarError("KKS_APPEARANCE_UNVERIFIED", "KKS appearance receipt checksum failed.", 415)
    validated_appearance = validate_kks_appearance_receipt(
        receipt,
        card_fingerprint=fingerprint,
        model_sha256=model_sha,
        source_card_sha256=source_card_sha,
        provenance_sha256=provenance_sha,
    )
    profile = _validate_kks_character_glb(model_content)
    pack_sha = hashlib.sha256(content).hexdigest()
    model_id = f"kks-pack-{fingerprint[:12]}-{model_sha[:8]}"
    stored_dir = REGISTRY_ROOT / model_id
    stored_name = stored_dir / "model.glb"
    item = {
        "model_id": model_id,
        "display_name": display_name.strip() or str(manifest.get("display_name") or Path(filename).stem),
        "creator": creator.strip() or str(manifest.get("creator") or "未填写"),
        "format": "glb",
        "asset_profile": "kks-character-pack",
        "card_fingerprint": fingerprint,
        "file_path": f"{model_id}/model.glb",
        "source_url": source_url.strip(),
        "license_name": license_name.strip(),
        "license_url": license_url.strip(),
        "sha256": pack_sha,
        "model_sha256": model_sha,
        "appearance_contract": {
            **validated_appearance,
            "receipt_hash": receipt_sha,
        },
        "size_bytes": len(model_content),
        "kks_profile": profile,
        "adult_character_confirmed": True,
        "rights_confirmed": True,
        "enabled": True,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "morph": {
            "height": 1.0,
            "head_scale": 1.0,
            "upper_chest": 1.0,
            "shoulder_width": 1.0,
            "waist_width": 1.0,
            "hip_width": 1.0,
            "arm_length": 1.0,
            "leg_length": 1.0,
            "smile": 0.0,
            "blink": 1.0,
        },
    }
    with _LOCK:
        payload = _load()
        existing = next((
            entry
            for entry in payload["models"]
            if entry.get("card_fingerprint") == fingerprint
            and entry.get("model_sha256") == model_sha
        ), None)
        if existing:
            return _public(existing)
        stored_dir.mkdir(parents=True, exist_ok=False)
        try:
            stored_name.write_bytes(model_content)
            (stored_dir / KKS_MODEL_PROVENANCE_PATH).write_bytes(provenance_content)
            (stored_dir / KKS_APPEARANCE_RECEIPT_PATH).write_bytes(receipt_content)
            (stored_dir / KKS_CHARACTER_PACK_MANIFEST).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            payload["models"].append(item)
            _save(payload)
        except Exception:
            for child in stored_dir.iterdir():
                if child.is_file():
                    child.unlink(missing_ok=True)
            stored_dir.rmdir()
            raise
    return _public(item)


def _import_koikatsu_export(
    filename: str,
    content: bytes,
    *,
    display_name: str,
    creator: str,
    source_url: str,
    license_name: str,
    license_url: str,
) -> dict[str, Any]:
    """Store a KKBP export intact so MMDLoader can resolve its textures.

    KKBP exports the *assembled* KKS character as a PMX plus texture files.
    Keeping the directory layout is essential: PMX texture paths are relative
    to the PMX file and cannot be reconstructed from a character-card PNG.
    """
    if not content or len(content) > MAX_KOIKATSU_EXPORT_BYTES:
        raise TeacherAvatarError("MODEL_SIZE_INVALID", "恋活导出包必须非空且不超过 500MB。", 413)
    try:
        archive = zipfile.ZipFile(__import__("io").BytesIO(content))
        entries = [info for info in archive.infolist() if not info.is_dir()]
    except zipfile.BadZipFile as exc:
        raise TeacherAvatarError("KOIKATSU_EXPORT_INVALID", "恋活导出包不是有效的 ZIP 文件。", 415) from exc
    if not entries or len(entries) > 2000:
        raise TeacherAvatarError("KOIKATSU_EXPORT_INVALID", "恋活导出包为空或包含过多文件。", 415)
    if sum(info.file_size for info in entries) > MAX_KOIKATSU_EXPORT_BYTES:
        raise TeacherAvatarError("MODEL_SIZE_INVALID", "恋活导出包解压后超过 500MB。", 413)
    unsafe = [info.filename for info in entries if Path(info.filename).is_absolute() or ".." in Path(info.filename).parts]
    if unsafe:
        raise TeacherAvatarError("KOIKATSU_EXPORT_INVALID", "恋活导出包包含不安全的文件路径。", 415)
    pmx_entries = [info for info in entries if Path(info.filename).suffix.lower() == ".pmx"]
    if len(pmx_entries) != 1:
        raise TeacherAvatarError("KOIKATSU_EXPORT_INVALID", "恋活导出包必须且只能包含一个 .pmx 模型文件。", 415)
    unsupported = [info.filename for info in entries if Path(info.filename).suffix.lower() not in KOIKATSU_EXPORT_SUFFIXES]
    if unsupported:
        raise TeacherAvatarError("KOIKATSU_EXPORT_INVALID", "恋活导出包包含不支持的附带文件。", 415)
    digest = hashlib.sha256(content).hexdigest()
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(filename).stem).strip("-")[:40] or "koikatsu"
    model_id = f"kks-{safe_stem}-{digest[:10]}"
    pmx_name = Path(pmx_entries[0].filename).name
    stored_dir = REGISTRY_ROOT / model_id
    item = {
        "model_id": model_id,
        "display_name": display_name.strip() or Path(filename).stem,
        "creator": creator.strip() or "未填写",
        "format": "pmx",
        "file_path": f"{model_id}/{pmx_name}",
        "source_url": source_url.strip(),
        "license_name": license_name.strip(),
        "license_url": license_url.strip(),
        "sha256": digest,
        "size_bytes": len(content),
        "adult_character_confirmed": True,
        "rights_confirmed": True,
        "enabled": True,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "morph": {"height": 1.0, "head_scale": 1.0, "upper_chest": 1.0, "shoulder_width": 1.0, "waist_width": 1.0, "hip_width": 1.0, "arm_length": 1.0, "leg_length": 1.0, "smile": 0.18, "blink": 1.0},
    }
    with _LOCK:
        payload = _load()
        existing = next((entry for entry in payload["models"] if entry.get("sha256") == digest), None)
        if existing:
            return _public(existing)
        stored_dir.mkdir(parents=True, exist_ok=False)
        try:
            for entry in entries:
                relative = Path(entry.filename)
                target = (stored_dir / relative).resolve()
                if stored_dir.resolve() not in target.parents:
                    raise TeacherAvatarError("KOIKATSU_EXPORT_INVALID", "恋活导出包路径无效。", 415)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
            payload["models"].append(item)
            _save(payload)
        except Exception:
            for child in stored_dir.glob("*"):
                child.unlink(missing_ok=True)
            stored_dir.rmdir()
            raise
    return _public(item)


def _build_koikatsu_local_export_archive(card_id: str) -> bytes:
    """Create a deterministic ZIP containing the local KK export directory.

    A dedicated route can then reuse the same validated importer as manual
    uploads, while keeping runtime dependencies (model/texture sidecars) in
    the same layout expected by the runtime PMX loader.
    """
    from .koikatsu_teacher_registry import KoikatsuTeacherError, resolve_local_export_asset

    model_path = resolve_local_export_asset(card_id)
    export_root = model_path.parent
    if not export_root.is_dir():
        raise TeacherAvatarError("LOCAL_EXPORT_MISSING", "找不到对应的恋活导出目录，无法构建导入包", 404)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(export_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(export_root).as_posix()
            # KK export directories may contain metadata sidecars and nested
            # asset packs. Keep only known file kinds so the same validator can
            # enforce compatibility.
            if Path(relative).suffix.lower() not in KOIKATSU_EXPORT_SUFFIXES:
                raise TeacherAvatarError(
                    "KOIKATSU_EXPORT_INVALID",
                    "恋活导出目录包含不支持的文件类型，请重新导出为官方 KKBP 导出包",
                    415,
                )
            archive.write(path, relative)
    return buffer.getvalue()


def import_local_koikatsu_card(
    card_id: str,
    *,
    display_name: str,
    creator: str,
    source_url: str,
    license_name: str,
    license_url: str,
    rights_confirmed: bool,
    adult_character_confirmed: bool,
) -> dict[str, Any]:
    if not card_id or not str(card_id).strip():
        raise TeacherAvatarError("LOCAL_CARD_REQUIRED", "需要指定要导入的恋活角色卡片", 400)
    if not rights_confirmed:
        raise TeacherAvatarError("RIGHTS_REQUIRED", "恋活导入前需要确认已获得模型与资源使用授权", 415)
    if not adult_character_confirmed:
        raise TeacherAvatarError("ADULT_REQUIRED", "恋活模型角色必须确认符合成人内容使用合规要求", 415)

    payload = _build_koikatsu_local_export_archive(str(card_id).strip())
    return _import_koikatsu_export(
        f"{str(card_id).strip()}.zip",
        payload,
        display_name=display_name,
        creator=creator,
        source_url=source_url,
        license_name=license_name,
        license_url=license_url,
    )


def import_model(
    filename: str,
    content: bytes,
    *,
    display_name: str,
    creator: str,
    source_url: str,
    license_name: str,
    license_url: str,
    rights_confirmed: bool,
    adult_character_confirmed: bool,
    source_path: Path | None = None,
) -> dict[str, Any]:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise TeacherAvatarError("FORMAT_UNSUPPORTED", "仅支持 VRM、GLB 或 KKBP 恋活导出 ZIP。", 415)
    if not rights_confirmed:
        raise TeacherAvatarError("RIGHTS_REQUIRED", "导入前必须确认拥有模型使用授权。")
    if not adult_character_confirmed:
        raise TeacherAvatarError("ADULT_REQUIRED", "教师模型必须明确为成年角色。")
    if not source_url.strip() or not license_name.strip():
        raise TeacherAvatarError("PROVENANCE_REQUIRED", "必须填写模型来源和许可名称。")
    if suffix == ".zip":
        if _is_kks_runtime_pack(content):
            return _import_kks_runtime_pack(
                filename,
                content,
                display_name=display_name,
                creator=creator,
                source_url=source_url,
                license_name=license_name,
                license_url=license_url,
            )
        if _is_kks_character_pack(content):
            return _import_kks_character_pack(
                filename,
                content,
                display_name=display_name,
                creator=creator,
                source_url=source_url,
                license_name=license_name,
                license_url=license_url,
            )
        return _import_koikatsu_export(filename, content, display_name=display_name, creator=creator, source_url=source_url, license_name=license_name, license_url=license_url)
    if not content or len(content) > MAX_MODEL_BYTES:
        raise TeacherAvatarError("MODEL_SIZE_INVALID", "模型必须非空且不超过 100MB。", 413)
    _validate_humanoid_rig(content)

    digest = hashlib.sha256(content).hexdigest()
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(filename).stem).strip("-")[:40] or "avatar"
    model_id = f"vrm-{safe_stem}-{digest[:10]}"
    stored_name = f"{model_id}{suffix}"
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "model_id": model_id,
        "display_name": display_name.strip() or Path(filename).stem,
        "creator": creator.strip() or "未填写",
        "format": suffix.removeprefix("."),
        "file_path": stored_name,
        "source_url": source_url.strip(),
        "license_name": license_name.strip(),
        "license_url": license_url.strip(),
        "sha256": digest,
        "size_bytes": len(content),
        "adult_character_confirmed": True,
        "rights_confirmed": True,
        "enabled": True,
        "imported_at": now,
        "morph": {
            "height": 1.0,
            "head_scale": 1.0,
            "upper_chest": 1.0,
            "shoulder_width": 1.0,
            "waist_width": 1.0,
            "hip_width": 1.0,
            "arm_length": 1.0,
            "leg_length": 1.0,
            "smile": 0.18,
            "blink": 1.0,
        },
    }
    with _LOCK:
        payload = _load()
        existing = next((entry for entry in payload["models"] if entry.get("sha256") == digest), None)
        if existing:
            return _public(existing)
        REGISTRY_ROOT.mkdir(parents=True, exist_ok=True)
        destination = REGISTRY_ROOT / stored_name
        linked = False
        if source_path is not None and source_path.is_file():
            try:
                os.link(source_path.resolve(), destination)
                linked = True
            except OSError:
                linked = False
        if not linked:
            destination.write_bytes(content)
        payload["models"].append(item)
        _save(payload)
    return _public(item)


def import_model_batch(
    files: list[tuple[str, bytes]],
    *,
    creator: str,
    source_url: str,
    license_name: str,
    license_url: str,
    rights_confirmed: bool,
    adult_character_confirmed: bool,
) -> dict[str, Any]:
    """Import a user-selected collection without making a server-side path scan.

    Files arrive through the browser's file/folder picker, so the service never
    receives an arbitrary local path.  Each file keeps the same validation as a
    one-at-a-time import; a bad asset is reported without discarding valid ones.
    """

    if not files:
        raise TeacherAvatarError("BATCH_EMPTY", "请至少选择一个 VRM 或 GLB 模型。")
    if len(files) > MAX_BATCH_MODELS:
        raise TeacherAvatarError("BATCH_LIMIT", f"一次最多导入 {MAX_BATCH_MODELS} 个模型。", 413)
    total = sum(len(content) for _, content in files)
    if total > MAX_BATCH_BYTES:
        raise TeacherAvatarError("BATCH_SIZE_LIMIT", "本次导入的模型总大小超过 500MB。", 413)

    imported: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for filename, content in files:
        try:
            imported.append(
                import_model(
                    filename,
                    content,
                    display_name=Path(filename).stem,
                    creator=creator,
                    source_url=source_url,
                    license_name=license_name,
                    license_url=license_url,
                    rights_confirmed=rights_confirmed,
                    adult_character_confirmed=adult_character_confirmed,
                )
            )
        except TeacherAvatarError as exc:
            rejected.append({"filename": filename or "unknown", "code": exc.code, "message": exc.message})
    return {"items": imported, "imported_count": len(imported), "rejected": rejected, "rejected_count": len(rejected)}


def resolve_model_file(model_id: str, asset_name: str | None = None) -> Path:
    with _LOCK:
        item = next((entry for entry in _load()["models"] if entry.get("model_id") == model_id), None)
    if not item:
        raise TeacherAvatarError("MODEL_NOT_FOUND", "教师模型不存在。", 404)
    stored = REGISTRY_ROOT / str(item["file_path"])
    if item.get("format") == "pmx" and asset_name:
        relative = Path(str(asset_name).replace("\\", "/"))
        stored = stored.parent / relative
    path = stored.resolve()
    model_root = (REGISTRY_ROOT / str(item["model_id"])).resolve()
    if model_root not in path.parents and path != model_root:
        raise TeacherAvatarError("MODEL_FILE_MISSING", "model asset is outside its model directory", 404)
    if not path.is_file():
        raise TeacherAvatarError("MODEL_FILE_MISSING", "教师模型文件缺失。", 404)
    return path


def today_model(workspace_id: str = "default", day: date | None = None) -> dict[str, Any]:
    items = [item for item in list_models()["items"] if item.get("asset_profile") != "kks-runtime-pack"]
    if not items:
        return {"item": None, "requires_import": True, "rotation_key": (day or date.today()).isoformat()}
    selected_day = day or date.today()
    rotation_key = selected_day.isoformat()
    # Keep each workspace's starting offset stable, then advance exactly one
    # roster position per day.  Independent daily hashes can select the same
    # model on consecutive days, which breaks the promised daily rotation.
    digest = hashlib.sha256(workspace_id.encode("utf-8")).digest()
    index = (int.from_bytes(digest[:4], "big") + selected_day.toordinal()) % len(items)
    return {"item": items[index], "requires_import": False, "rotation_key": rotation_key, "roster_size": len(items)}


def update_morph(model_id: str, values: dict[str, Any]) -> dict[str, Any]:
    ranges = {
        "height": (0.50, 2.00),
        "head_scale": (0.10, 10.00),
        "upper_chest": (0.10, 10.00),
        "shoulder_width": (0.10, 10.00),
        "waist_width": (0.10, 10.00),
        "hip_width": (0.10, 10.00),
        "arm_length": (0.10, 10.00),
        "leg_length": (0.10, 10.00),
        "smile": (0.0, 1.0),
        "blink": (0.0, 1.0),
    }
    with _LOCK:
        payload = _load()
        item = next((entry for entry in payload["models"] if entry.get("model_id") == model_id), None)
        if not item:
            raise TeacherAvatarError("MODEL_NOT_FOUND", "教师模型不存在。", 404)
        morph = dict(item.get("morph") or {})
        for key, raw in values.items():
            if key not in ranges:
                continue
            low, high = ranges[key]
            try:
                morph[key] = max(low, min(high, float(raw)))
            except (TypeError, ValueError):
                raise TeacherAvatarError("MORPH_INVALID", f"{key} 不是有效数值。")
        item["morph"] = morph
        _save(payload)
        return _public(item)
