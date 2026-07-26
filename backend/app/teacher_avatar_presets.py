"""Curated, redistributable VRM presets for the teacher avatar roster."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .teacher_avatar_registry import MAX_MODEL_BYTES, TeacherAvatarError, import_model, list_models


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRESET_ROOT = PROJECT_ROOT / "content" / "teacher_avatar_presets" / "1.0.0"
PRESET_MANIFEST = PRESET_ROOT / "manifest.json"
REMOTE_CACHE_ROOT = PROJECT_ROOT / "data" / "teacher_avatar_cache"
_HASH_CACHE: dict[str, tuple[int, int, str]] = {}
_REMOTE_LOCKS_GUARD = Lock()
_REMOTE_LOCKS: dict[str, Lock] = {}
_REMOTE_DOWNLOAD_SLOTS = BoundedSemaphore(3)
_TRUSTED_REMOTE_REPOSITORIES = {
    ("ExpiredPopsicle", "SnekStudio"),
    ("gatosyocora", "vrm-avatar-editor"),
    ("iamenahs", "xlunar-ai-avatar"),
    ("madjin", "vrm-samples"),
    ("kyoki-railway", "kosys-character-3d"),
    ("neonglitch86", "BunkerBunnies"),
}


def _safe_path(relative: str) -> Path:
    path = (PRESET_ROOT / relative).resolve()
    if PRESET_ROOT.resolve() not in path.parents or not path.is_file():
        raise TeacherAvatarError("PRESET_FILE_MISSING", "教师预设模型文件缺失。", 500)
    return path


def _load_manifest() -> dict[str, Any]:
    try:
        payload = json.loads(PRESET_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TeacherAvatarError("PRESET_MANIFEST_INVALID", "教师预设清单损坏。", 500) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 2 or not isinstance(payload.get("models"), list):
        raise TeacherAvatarError("PRESET_MANIFEST_INVALID", "教师预设清单格式无效。", 500)
    ids = [str(item.get("preset_id") or "") for item in payload["models"] if isinstance(item, dict)]
    hashes = [str(item.get("sha256") or "") for item in payload["models"] if isinstance(item, dict)]
    if len(ids) != len(payload["models"]) or not all(ids) or len(ids) != len(set(ids)) or len(hashes) != len(set(hashes)):
        raise TeacherAvatarError("PRESET_MANIFEST_INVALID", "教师预设清单包含重复或无效条目。", 500)
    return payload


def _preset(preset_id: str) -> dict[str, Any]:
    item = next((value for value in _load_manifest()["models"] if value.get("preset_id") == preset_id), None)
    if item is None:
        raise TeacherAvatarError("PRESET_NOT_FOUND", "教师模型预设不存在。", 404)
    return item


def _verify(item: dict[str, Any]) -> tuple[Path | None, Path]:
    preview = _safe_path(str(item.get("preview_file", "")))
    artifact = item.get("artifact") or {}
    conditions = item.get("license_snapshot") or {}
    if (
        conditions.get("avatar_use") != "allow"
        or conditions.get("redistribution") != "allow"
        or conditions.get("modification") not in {"allow", "allowModificationRedistribution"}
    ):
        raise TeacherAvatarError("PRESET_LICENSE_INVALID", "教师预设缺少可使用或可再分发授权。", 500)
    expected_sha = str(item.get("sha256") or "").lower()
    if len(expected_sha) != 64 or any(value not in "0123456789abcdef" for value in expected_sha):
        raise TeacherAvatarError("PRESET_ARTIFACT_INVALID", "教师预设缺少有效内容哈希。", 500)
    if str(artifact.get("sha256") or "").lower() != expected_sha:
        raise TeacherAvatarError("PRESET_ARTIFACT_INVALID", "教师预设内容标识与清单不一致。", 500)

    source_kind = item.get("source_kind")
    if source_kind == "bundled" and artifact.get("mode") == "bundled":
        model = _safe_path(str(item.get("model_file", "")))
        stat = model.stat()
        if int(artifact.get("size_bytes") or -1) != stat.st_size:
            raise TeacherAvatarError("PRESET_ARTIFACT_INVALID", "内置预设文件大小与清单不一致。", 500)
        cache_key = str(model)
        cached = _HASH_CACHE.get(cache_key)
        if cached and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            digest = cached[2]
        else:
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            _HASH_CACHE[cache_key] = (stat.st_size, stat.st_mtime_ns, digest)
        if digest != expected_sha:
            raise TeacherAvatarError("PRESET_CHECKSUM_MISMATCH", "教师预设模型校验失败。", 500)
        return model, preview

    if source_kind == "remote_redistributable" and artifact.get("mode") == "remote":
        url = str(artifact.get("url") or "")
        parsed = urlparse(url)
        size = int(artifact.get("size_bytes") or 0)
        path_parts = parsed.path.strip("/").split("/")
        commit = path_parts[2] if len(path_parts) >= 4 else ""
        if (
            parsed.scheme != "https"
            or parsed.hostname != "raw.githubusercontent.com"
            or parsed.username
            or parsed.password
            or tuple(path_parts[:2]) not in _TRUSTED_REMOTE_REPOSITORIES
            or len(commit) != 40
            or any(value not in "0123456789abcdefABCDEF" for value in commit)
        ):
            raise TeacherAvatarError("PRESET_REMOTE_URL_INVALID", "远程预设地址不在受信任的内容源中。", 500)
        if size <= 0 or size > MAX_MODEL_BYTES:
            raise TeacherAvatarError("PRESET_ARTIFACT_INVALID", "远程预设文件大小无效。", 500)
        return None, preview

    raise TeacherAvatarError("PRESET_ARTIFACT_INVALID", "教师预设来源类型无效。", 500)


def _remote_model_content(item: dict[str, Any]) -> bytes:
    artifact = item["artifact"]
    expected_sha = str(item["sha256"]).lower()
    with _REMOTE_LOCKS_GUARD:
        model_lock = _REMOTE_LOCKS.setdefault(expected_sha, Lock())
    with model_lock:
        return _remote_model_content_locked(item, expected_sha)


def _remote_model_content_locked(item: dict[str, Any], expected_sha: str) -> bytes:
    artifact = item["artifact"]
    expected_size = int(artifact["size_bytes"])
    cache = REMOTE_CACHE_ROOT / f"{expected_sha}.vrm"
    if cache.is_file():
        cached = cache.read_bytes()
        if len(cached) == expected_size and hashlib.sha256(cached).hexdigest() == expected_sha:
            _validate_remote_embedded_license(item, cached)
            return cached
        cache.unlink()

    REMOTE_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = REMOTE_CACHE_ROOT / f"{expected_sha}.{os.getpid()}.{id(item)}.tmp"
    digest = hashlib.sha256()
    downloaded = 0
    try:
        with _REMOTE_DOWNLOAD_SLOTS:
            with httpx.stream("GET", str(artifact["url"]), timeout=60.0, follow_redirects=False) as response:
                if response.status_code != 200:
                    raise TeacherAvatarError("PRESET_DOWNLOAD_FAILED", "远程教师模型下载失败。", 502)
                declared = int(response.headers.get("content-length") or 0)
                if declared and declared != expected_size:
                    raise TeacherAvatarError("PRESET_DOWNLOAD_INVALID", "远程教师模型大小与审核记录不一致。", 502)
                with temporary.open("xb") as target:
                    for chunk in response.iter_bytes():
                        downloaded += len(chunk)
                        if downloaded > expected_size or downloaded > MAX_MODEL_BYTES:
                            raise TeacherAvatarError("PRESET_DOWNLOAD_INVALID", "远程教师模型大小与审核记录不一致。", 502)
                        digest.update(chunk)
                        target.write(chunk)
        if downloaded != expected_size or digest.hexdigest() != expected_sha:
            raise TeacherAvatarError("PRESET_DOWNLOAD_INVALID", "远程教师模型哈希校验失败。", 502)
        payload = temporary.read_bytes()
        _validate_remote_embedded_license(item, payload)
        os.replace(temporary, cache)
        return payload
    except httpx.RequestError as exc:
        raise TeacherAvatarError("PRESET_DOWNLOAD_FAILED", "无法连接教师模型内容源。", 502) from exc
    except OSError as exc:
        raise TeacherAvatarError("PRESET_CACHE_FAILED", "远程教师模型无法写入本地缓存。", 507) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _validate_remote_embedded_license(item: dict[str, Any], content: bytes) -> None:
    try:
        json_length, chunk_type = struct.unpack_from("<II", content, 12)
        if chunk_type != 0x4E4F534A or 20 + json_length > len(content):
            raise ValueError("missing JSON chunk")
        document = json.loads(content[20 : 20 + json_length].decode("utf-8").rstrip(" \t\r\n\0"))
        meta = document["extensions"]["VRM"]["meta"]
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, struct.error) as exc:
        raise TeacherAvatarError("PRESET_EMBEDDED_LICENSE_INVALID", "远程模型缺少可验证的 VRM 许可元数据。", 502) from exc

    if meta.get("allowedUserName") != "Everyone" or meta.get("commercialUssageName") != "Allow":
        raise TeacherAvatarError("PRESET_EMBEDDED_LICENSE_MISMATCH", "远程模型内嵌使用许可与审核记录不一致。", 502)
    license_name = str(meta.get("licenseName") or "")
    expected_license_name = str(item.get("embedded_license_name") or "")
    if not expected_license_name and str(item.get("license_name") or "").startswith("CC0"):
        expected_license_name = "CC0"
    if expected_license_name and license_name != expected_license_name:
        raise TeacherAvatarError("PRESET_EMBEDDED_LICENSE_MISMATCH", "远程模型内嵌许可类型与审核记录不一致。", 502)
    embedded_url = str(meta.get("otherLicenseUrl") or meta.get("licenseUrl") or "")
    expected_url = str(item.get("embedded_license_url") or "")
    if license_name == "CC0":
        return
    if embedded_url != expected_url:
        raise TeacherAvatarError("PRESET_EMBEDDED_LICENSE_MISMATCH", "远程模型内嵌许可地址与审核记录不一致。", 502)
    query = parse_qs(urlparse(embedded_url).query)
    required = {
        "redistribution": "allow",
        "corporate_commercial_use": "allow",
        "modification": "allow",
        "characterization_allowed_user": "everyone",
    }
    if any(query.get(key, [""])[0] != value for key, value in required.items()):
        raise TeacherAvatarError("PRESET_EMBEDDED_LICENSE_MISMATCH", "远程模型内嵌再分发许可与审核记录不一致。", 502)


def list_presets(
    *,
    query: str = "",
    tags: list[str] | None = None,
    limit: int = 48,
    cursor: int = 0,
) -> dict[str, Any]:
    manifest = _load_manifest()
    roster_by_sha = {str(model.get("sha256")): model for model in list_models()["items"]}
    normalized_query = query.strip().casefold()[:100]
    normalized_tags = {value.strip().casefold() for value in (tags or []) if value.strip()}
    all_items: list[dict[str, Any]] = []
    facet_counts: dict[str, int] = {}
    for item in manifest["models"]:
        _verify(item)
        item_tags = [str(value) for value in (item.get("tags") or [])]
        if str(item.get("license_name") or "").startswith("CC0") and "CC0" not in item_tags:
            item_tags.append("CC0")
        if item.get("source_kind") == "remote_redistributable" and "按需下载" not in item_tags:
            item_tags.append("按需下载")
        for tag in item_tags:
            facet_counts[tag] = facet_counts.get(tag, 0) + 1
        searchable = " ".join(
            [
                str(item.get("display_name") or ""),
                str(item.get("description") or ""),
                str(item.get("creator") or ""),
                str(item.get("collection") or ""),
                *item_tags,
            ]
        ).casefold()
        if normalized_query and normalized_query not in searchable:
            continue
        if normalized_tags and not normalized_tags.issubset({value.casefold() for value in item_tags}):
            continue
        active = roster_by_sha.get(str(item["sha256"]))
        all_items.append(
            {
                **{key: value for key, value in item.items() if key not in {"model_file", "preview_file", "artifact_source_url"}},
                "tags": item_tags,
                "preview_url": f"/api/teacher-avatar-presets/{item['preset_id']}/preview",
                "included": item.get("source_kind") == "bundled",
                "active": active is not None,
                "active_model_id": active.get("model_id") if active else None,
            }
        )
    total = len(all_items)
    safe_limit = max(1, min(100, int(limit)))
    safe_cursor = max(0, min(total, int(cursor)))
    items = all_items[safe_cursor : safe_cursor + safe_limit]
    next_cursor = safe_cursor + len(items) if safe_cursor + len(items) < total else None
    return {
        "package_id": manifest.get("package_id"),
        "version": manifest.get("version"),
        "verified_at": manifest.get("verified_at"),
        "items": items,
        "count": len(items),
        "total": total,
        "next_cursor": str(next_cursor) if next_cursor is not None else None,
        "facets": {"tags": [{"value": key, "count": value} for key, value in sorted(facet_counts.items())]},
        "online_catalog": {
            "provider": "VRoid Hub",
            "status": "configured" if list_models()["provider_guidance"]["vroid_hub_configured"] else "credentials_required",
            "application_registration_url": "https://hub.vroid.com/oauth/applications/",
            "catalog_url": "https://hub.vroid.com/en/",
            "note": "配置官方 OAuth 后可搜索并选择用户获准使用的全部 Hub 模型。",
        },
    }


def activate_preset(preset_id: str, *, adult_character_confirmed: bool) -> dict[str, Any]:
    if not adult_character_confirmed:
        raise TeacherAvatarError("ADULT_REQUIRED", "加入教师池前必须确认该角色按成年教师设定使用。")
    item = _preset(preset_id)
    model, _ = _verify(item)
    content = model.read_bytes() if model else _remote_model_content(item)
    source_path = model if model else REMOTE_CACHE_ROOT / f"{item['sha256']}.vrm"
    return import_model(
        model.name if model else f"{preset_id}.vrm",
        content,
        display_name=str(item["display_name"]),
        creator=str(item["creator"]),
        source_url=str(item["source_url"]),
        license_name=str(item["license_name"]),
        license_url=str(item["license_url"]),
        rights_confirmed=True,
        adult_character_confirmed=True,
        source_path=source_path,
    )


def resolve_preview(preset_id: str) -> Path:
    item = _preset(preset_id)
    return _safe_path(str(item.get("preview_file", "")))
