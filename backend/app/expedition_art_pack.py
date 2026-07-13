"""Secure, versioned art-pack installation for Learning Expedition.

The module deliberately treats upstream art as untrusted input.  It validates
the ZIP twice (preview and install), copies only manifest-listed files, keeps
installed versions immutable, and exposes assets by registry id rather than a
path supplied by the browser.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .expedition_data import HEROES
from .expedition_orm import (
    ExpeditionActiveArtPackORM,
    ExpeditionArtPackORM,
    ExpeditionArtPackVersionORM,
    ExpeditionArtReviewORM,
    ExpeditionHeroAssetORM,
)
from .orm import SessionLocal, init_orm


ART_KINDS = ("portrait", "avatar", "skill_cutin", "skill_icon")
ALLOWED_IMAGE_EXTENSIONS = {".webp": "image/webp", ".png": "image/png"}
MAX_PACK_BYTES = 100 * 1024 * 1024
MAX_FILES = 512
MAX_SINGLE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 300 * 1024 * 1024
MAX_DEPTH = 10
PREVIEW_TTL_SECONDS = 30 * 60
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = PROJECT_ROOT / "content" / "expedition_art_packs"
PREVIEW_ROOT = PROJECT_ROOT / "data" / "expedition_art_pack_previews"
EXPECTED_HERO_IDS = tuple(hero["hero_id"] for hero in HEROES)


class ArtPackError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_member(raw: str) -> str:
    if not raw or "\x00" in raw:
        raise ArtPackError("ART_PACK_UNSAFE_PATH", "美术包包含空路径或空字节")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("//") or re.match(r"^[A-Za-z]:", normalized):
        raise ArtPackError("ART_PACK_UNSAFE_PATH", "美术包不允许绝对路径、盘符路径或 UNC 路径", details={"path": raw})
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ArtPackError("ART_PACK_UNSAFE_PATH", "美术包包含路径穿越", details={"path": raw})
    if len(parts) - 1 > MAX_DEPTH:
        raise ArtPackError("ART_PACK_TOO_DEEP", "美术包目录深度超过限制", details={"path": raw, "max_depth": MAX_DEPTH})
    return "/".join(parts)


def _is_special(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    return kind in {stat.S_IFLNK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFIFO, stat.S_IFSOCK} or bool(kind and kind not in {stat.S_IFREG, stat.S_IFDIR})


def _read_zip_file(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    if info.file_size > MAX_SINGLE_BYTES:
        raise ArtPackError("ART_PACK_FILE_TOO_LARGE", "单个美术文件超过限制", details={"path": info.filename})
    data = zf.read(info)
    if len(data) > MAX_SINGLE_BYTES:
        raise ArtPackError("ART_PACK_FILE_TOO_LARGE", "单个美术文件解压后超过限制", details={"path": info.filename})
    return data


def _root_and_manifest(names: list[str]) -> tuple[str, str]:
    manifests = [name for name in names if name == "manifest.json" or name.endswith("/manifest.json")]
    if len(manifests) != 1:
        raise ArtPackError("ART_PACK_MANIFEST_REQUIRED", "美术包必须且只能包含一个 manifest.json")
    manifest_path = manifests[0]
    return manifest_path.rsplit("/", 1)[0] + "/" if "/" in manifest_path else "", manifest_path


def _relative(root: str, name: str) -> str:
    if root and not name.startswith(root):
        raise ArtPackError("ART_PACK_FILE_OUTSIDE_ROOT", "文件不在美术包根目录内", details={"path": name})
    return name[len(root):] if root else name


def _image_info(data: bytes, extension: str) -> dict[str, Any]:
    """Read dimensions and alpha presence without adding an image dependency."""
    if extension == ".png":
        if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise ArtPackError("ART_PACK_MIME_MISMATCH", "PNG 文件头不匹配")
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        color_type = data[25]
        alpha = color_type in {4, 6} or b"tRNS" in data
        return {"width": width, "height": height, "has_alpha": alpha, "format": "png"}
    if extension == ".webp":
        if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            raise ArtPackError("ART_PACK_MIME_MISMATCH", "WebP 文件头不匹配")
        position = 12
        width = height = 0
        has_alpha = False
        while position + 8 <= len(data):
            kind = data[position:position + 4]
            size = int.from_bytes(data[position + 4:position + 8], "little")
            payload = data[position + 8:position + 8 + size]
            if kind == b"VP8X" and len(payload) >= 10:
                has_alpha = bool(payload[0] & 0x10)
                width = 1 + int.from_bytes(payload[4:7], "little")
                height = 1 + int.from_bytes(payload[7:10], "little")
                break
            position += 8 + size + (size % 2)
        if not width or not height:
            # Lossy/lossless WebP dimension decoding is intentionally limited;
            # reject rather than silently accepting an unverifiable package.
            raise ArtPackError("ART_PACK_IMAGE_METADATA_INVALID", "WebP 缺少可验证的 VP8X 尺寸/透明通道元数据")
        return {"width": width, "height": height, "has_alpha": has_alpha, "format": "webp"}
    raise ArtPackError("ART_PACK_IMAGE_TYPE_UNSUPPORTED", "只允许 WebP 或 PNG 美术资源")


def _validate_metadata(metadata: dict[str, Any], hero_id: str) -> dict[str, Any]:
    if str(metadata.get("hero_id") or hero_id) != hero_id:
        raise ArtPackError("ART_PACK_METADATA_HERO_MISMATCH", "metadata.json 的 hero_id 不匹配", details={"hero_id": hero_id})
    age = str(metadata.get("age_presentation") or "")
    if age not in {"adult", "creature", "mechanical"}:
        raise ArtPackError("ART_PACK_METADATA_INVALID", "age_presentation 必须是 adult、creature 或 mechanical")
    focal = metadata.get("portrait_focal_point") or {}
    try:
        x, y = float(focal.get("x", 0.5)), float(focal.get("y", 0.4))
    except (TypeError, ValueError):
        raise ArtPackError("ART_PACK_METADATA_INVALID", "portrait_focal_point 必须是数字")
    if not 0 <= x <= 1 or not 0 <= y <= 1:
        raise ArtPackError("ART_PACK_METADATA_INVALID", "portrait_focal_point 必须在 0 到 1 之间")
    facing = str(metadata.get("facing") or "right")
    if facing not in {"left", "right"}:
        raise ArtPackError("ART_PACK_METADATA_INVALID", "facing 只能是 left 或 right")
    offset = metadata.get("battle_offset") or {}
    return {**metadata, "hero_id": hero_id, "portrait_focal_point": {"x": x, "y": y}, "battle_offset": {"x": float(offset.get("x", 0)), "y": float(offset.get("y", 0))}, "battle_scale": float(metadata.get("battle_scale", 1.0)), "facing": facing}


def _manifest_assets(manifest: dict[str, Any], root: str, names: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    required = manifest.get("required_hero_ids")
    if sorted(required or []) != sorted(EXPECTED_HERO_IDS):
        raise ArtPackError("ART_PACK_HERO_COVERAGE_INVALID", "manifest 必须准确覆盖当前 24 个 hero_id", details={"expected": list(EXPECTED_HERO_IDS), "actual": required})
    if int(manifest.get("hero_count") or 0) != len(EXPECTED_HERO_IDS):
        raise ArtPackError("ART_PACK_HERO_COUNT_INVALID", "manifest.hero_count 必须为 24")
    if not str(manifest.get("pack_id") or "") or not str(manifest.get("version") or ""):
        raise ArtPackError("ART_PACK_MANIFEST_INVALID", "manifest 缺少 pack_id 或 version")
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        raise ArtPackError("ART_PACK_ASSETS_REQUIRED", "manifest.assets 不能为空")
    assets: dict[str, dict[str, Any]] = {}
    metadata_paths: dict[str, str] = {}
    for raw in raw_assets:
        if not isinstance(raw, dict):
            raise ArtPackError("ART_PACK_ASSET_ENTRY_INVALID", "manifest.assets 中存在无效条目")
        hero_id = str(raw.get("hero_id") or "")
        kind = str(raw.get("asset_kind") or raw.get("asset_type") or "")
        raw_path = str(raw.get("path") or raw.get("relative_path") or "")
        full_path = _safe_member(root + raw_path if root else raw_path)
        if hero_id and hero_id not in EXPECTED_HERO_IDS:
            raise ArtPackError("ART_PACK_UNKNOWN_HERO", "美术包含未知 hero_id", details={"hero_id": hero_id})
        if full_path not in names:
            raise ArtPackError("ART_PACK_ASSET_MISSING", "manifest 引用的资源不存在", details={"path": full_path})
        if full_path in assets:
            raise ArtPackError("ART_PACK_DUPLICATE_ASSET", "manifest 重复引用同一资源", details={"path": full_path})
        if kind == "metadata":
            if not hero_id:
                raise ArtPackError("ART_PACK_METADATA_INVALID", "metadata 资源必须声明 hero_id")
            metadata_paths[hero_id] = full_path
            assets[full_path] = {**raw, "hero_id": hero_id, "asset_kind": kind, "path": full_path}
        elif kind in ART_KINDS:
            if not hero_id:
                raise ArtPackError("ART_PACK_ASSET_ENTRY_INVALID", "角色资源必须声明 hero_id")
            assets[full_path] = {**raw, "hero_id": hero_id, "asset_kind": kind, "path": full_path}
        else:
            # Shared files are allowed only when explicitly listed and are not
            # installed into the hero registry.
            if not (full_path.startswith(root + "shared/") if root else full_path.startswith("shared/")):
                raise ArtPackError("ART_PACK_ASSET_KIND_INVALID", "资源类型不在允许列表中", details={"kind": kind, "path": full_path})
            assets[full_path] = {**raw, "hero_id": "", "asset_kind": kind, "path": full_path}
    for hero_id in EXPECTED_HERO_IDS:
        if hero_id not in metadata_paths:
            raise ArtPackError("ART_PACK_METADATA_MISSING", "24 名角色都必须提供 metadata.json", details={"hero_id": hero_id})
        for kind in ART_KINDS:
            if not any(item.get("hero_id") == hero_id and item.get("asset_kind") == kind for item in assets.values()):
                raise ArtPackError("ART_PACK_ASSET_MISSING", "每个角色必须提供四类资源", details={"hero_id": hero_id, "asset_kind": kind})
    return assets, metadata_paths


def validate_art_pack(data: bytes, archive_name: str = "art-pack.zip") -> dict[str, Any]:
    if len(data) > MAX_PACK_BYTES:
        raise ArtPackError("ART_PACK_TOO_LARGE", "美术包超过 100 MB 限制")
    try:
        zf = zipfile.ZipFile(__import__("io").BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArtPackError("ART_PACK_INVALID_ZIP", "美术包不是有效 ZIP") from exc
    infos: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for raw_info in zf.infolist():
        name = _safe_member(raw_info.filename)
        if _is_special(raw_info):
            raise ArtPackError("ART_PACK_SPECIAL_FILE", "美术包不允许符号链接或特殊文件", details={"path": name})
        if raw_info.is_dir():
            continue
        if name in infos:
            raise ArtPackError("ART_PACK_DUPLICATE_PATH", "美术包存在重复路径", details={"path": name})
        infos[name] = raw_info
        total += int(raw_info.file_size)
        if len(infos) > MAX_FILES:
            raise ArtPackError("ART_PACK_TOO_MANY_FILES", "美术包文件数量超过限制")
        if raw_info.file_size > MAX_SINGLE_BYTES or total > MAX_TOTAL_BYTES:
            raise ArtPackError("ART_PACK_EXPANDED_TOO_LARGE", "美术包解压后超过限制")
    root, manifest_path = _root_and_manifest(list(infos))
    manifest_raw = _read_zip_file(zf, infos[manifest_path])
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtPackError("ART_PACK_MANIFEST_INVALID", "manifest.json 不是有效 UTF-8 JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("source") != "upstream_approved_art":
        raise ArtPackError("ART_PACK_MANIFEST_INVALID", "manifest.source 必须为 upstream_approved_art")
    assets, metadata_paths = _manifest_assets(manifest, root, set(infos))
    allowed = {manifest_path}
    if root:
        allowed.update({root + "attribution.json", root + "SHA256SUMS.json"})
    else:
        allowed.update({"attribution.json", "SHA256SUMS.json"})
    allowed.update(assets)
    extras = sorted(set(infos) - allowed)
    if extras:
        raise ArtPackError("ART_PACK_EXTRA_FILE", "存在未在 manifest 中声明的文件", details={"paths": extras[:20]})
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    metadata_by_hero: dict[str, dict[str, Any]] = {}
    for hero_id, path in metadata_paths.items():
        try:
            metadata = json.loads(_read_zip_file(zf, infos[path]).decode("utf-8"))
            metadata_by_hero[hero_id] = _validate_metadata(metadata, hero_id)
        except (UnicodeDecodeError, json.JSONDecodeError, ArtPackError) as exc:
            errors.append({"path": path, "error": getattr(exc, "message", str(exc))})
    for path, entry in assets.items():
        if entry["asset_kind"] == "metadata":
            continue
        try:
            extension = PurePosixPath(path).suffix.lower()
            if extension not in ALLOWED_IMAGE_EXTENSIONS:
                raise ArtPackError("ART_PACK_IMAGE_TYPE_UNSUPPORTED", "正式资源只能使用 WebP 或 PNG", details={"path": path})
            image = _image_info(_read_zip_file(zf, infos[path]), extension)
            if not image["has_alpha"]:
                raise ArtPackError("ART_PACK_ALPHA_REQUIRED", "角色资源必须包含透明通道", details={"path": path})
            data_hash = _sha256(_read_zip_file(zf, infos[path]))
            expected = entry.get("sha256")
            if isinstance(expected, dict):
                expected = expected.get("value") or expected.get("hash")
            if expected and str(expected).lower() != data_hash:
                raise ArtPackError("ART_PACK_HASH_MISMATCH", "资源 SHA256 不匹配", details={"path": path})
            rows.append({"path": path, "hero_id": entry["hero_id"], "asset_kind": entry["asset_kind"], "mime": ALLOWED_IMAGE_EXTENSIONS[extension], "width": image["width"], "height": image["height"], "has_alpha": True, "sha256": data_hash, "metadata": metadata_by_hero.get(entry["hero_id"], {})})
        except ArtPackError as exc:
            errors.append({"path": path, "error": exc.message, "code": exc.code, "details": exc.details})
    manifest_hash = _sha256(manifest_raw)
    report = {"valid": not errors, "archive_name": archive_name, "pack_id": manifest.get("pack_id"), "version": manifest.get("version"), "manifest": manifest, "manifest_hash": manifest_hash, "root": root, "asset_count": len(rows), "hero_count": len(metadata_by_hero), "assets": rows, "metadata": metadata_by_hero, "errors": errors, "checked_at": _now().isoformat(), "fallback": {"available": True, "source": "generated_svg"}}
    return report


def _preview_paths(preview_id: str) -> tuple[Path, Path]:
    if not re.fullmatch(r"preview_[a-f0-9]{16}", preview_id):
        raise ArtPackError("ART_PACK_PREVIEW_INVALID", "预览 ID 无效")
    directory = PREVIEW_ROOT / preview_id
    return directory / "pack.zip", directory / "report.json"


def preview_bytes(data: bytes, archive_name: str = "art-pack.zip") -> dict[str, Any]:
    report = validate_art_pack(data, archive_name)
    preview_id = _id("preview")
    directory = PREVIEW_ROOT / preview_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "pack.zip").write_bytes(data)
    report["preview_id"] = preview_id
    report["expires_at"] = datetime.fromtimestamp(_now().timestamp() + PREVIEW_TTL_SECONDS, timezone.utc).isoformat()
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _load_preview(preview_id: str) -> tuple[bytes, dict[str, Any]]:
    zip_path, report_path = _preview_paths(preview_id)
    if not zip_path.exists() or not report_path.exists():
        raise ArtPackError("ART_PACK_PREVIEW_NOT_FOUND", "美术包预览不存在或已过期", 404)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if datetime.fromisoformat(report["expires_at"]) < _now():
        shutil.rmtree(zip_path.parent, ignore_errors=True)
        raise ArtPackError("ART_PACK_PREVIEW_EXPIRED", "美术包预览已过期", 410)
    return zip_path.read_bytes(), report


def _version_public(row: ExpeditionArtPackVersionORM, reviews: list[ExpeditionArtReviewORM] | None = None) -> dict[str, Any]:
    approved = sum(review.review_status == "APPROVED" for review in (reviews or []))
    return {"pack_id": row.pack_id, "version": row.version, "status": row.status, "installed_at": row.installed_at.isoformat() if row.installed_at else None, "activated_at": row.activated_at.isoformat() if row.activated_at else None, "manifest_hash": row.manifest_hash, "hero_count": row.hero_count, "source": row.source, "validation_result": row.validation_result, "asset_path": row.asset_path, "previous_active_pack_id": row.previous_active_pack_id, "previous_active_version": row.previous_active_version, "reviewed_assets": approved, "required_reviews": len(EXPECTED_HERO_IDS) * len(ART_KINDS), "reviews": [{"hero_id": review.hero_id, "asset_kind": review.asset_kind, "review_status": review.review_status, "review_note": review.review_note, "reviewed_at": review.reviewed_at.isoformat() if review.reviewed_at else None} for review in (reviews or [])]}


def install_preview(preview_id: str, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        raise ArtPackError("ART_PACK_CONFIRM_REQUIRED", "安装前必须明确确认")
    data, old_report = _load_preview(preview_id)
    report = validate_art_pack(data, old_report.get("archive_name", "art-pack.zip"))
    if not report["valid"]:
        raise ArtPackError("ART_PACK_VALIDATION_FAILED", "美术包重新校验未通过", details={"report": report})
    pack_id, version = str(report["pack_id"]), str(report["version"])
    PACK_ROOT.mkdir(parents=True, exist_ok=True)
    target = PACK_ROOT / pack_id / version
    if target.exists():
        raise ArtPackError("ART_PACK_VERSION_EXISTS", "该美术包版本已经安装")
    staging = PACK_ROOT / f".staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
            names = [_safe_member(info.filename) for info in zf.infolist() if not info.is_dir()]
            root = report["root"]
            allowed = {root + "manifest.json" if root else "manifest.json", "attribution.json", "SHA256SUMS.json"}
            if root:
                allowed.update({root + "attribution.json", root + "SHA256SUMS.json"})
            allowed.update(item["path"] for item in report["assets"])
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = _safe_member(info.filename)
                if name not in allowed:
                    raise ArtPackError("ART_PACK_EXTRA_FILE", "安装时发现未声明文件", details={"path": name})
                relative = _relative(root, name)
                destination = (staging / relative).resolve()
                if staging.resolve() not in destination.parents:
                    raise ArtPackError("ART_PACK_UNSAFE_PATH", "安装目标路径越界")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(_read_zip_file(zf, info))
        target.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(target)
        init_orm()
        with SessionLocal() as session:
            pack = session.get(ExpeditionArtPackORM, pack_id) or ExpeditionArtPackORM(pack_id=pack_id, display_name=str(report["manifest"].get("style_name") or pack_id), source=str(report["manifest"].get("source")))
            session.add(pack)
            previous = session.query(ExpeditionActiveArtPackORM).filter_by(singleton_id="active").first()
            row = ExpeditionArtPackVersionORM(id=_id("artpack"), pack_id=pack_id, version=version, status="INSTALLED", installed_at=_now(), manifest_hash=report["manifest_hash"], hero_count=len(EXPECTED_HERO_IDS), source=str(report["manifest"].get("source")), validation_result=report, asset_path=str(target), previous_active_pack_id=previous.pack_id if previous else "", previous_active_version=previous.version if previous else "")
            session.add(row)
            for item in report["assets"]:
                metadata = item.get("metadata") or {}
                session.add(ExpeditionHeroAssetORM(id=_id("asset"), pack_id=pack_id, pack_version=version, hero_id=item["hero_id"], asset_kind=item["asset_kind"], asset_id=f"art:{pack_id}:{version}:{item['hero_id']}:{item['asset_kind']}", path=item["path"], mime=item["mime"], width=item["width"], height=item["height"], focal_point=metadata.get("portrait_focal_point", {"x": 0.5, "y": 0.4}), scale=float(metadata.get("battle_scale", 1.0)), offset=metadata.get("battle_offset", {"x": 0, "y": 0}), sha256=item["sha256"], source=str(report["manifest"].get("source"))))
            for item in report["assets"]:
                session.add(ExpeditionArtReviewORM(id=_id("review"), pack_id=pack_id, pack_version=version, hero_id=item["hero_id"], asset_kind=item["asset_kind"], review_status="PENDING"))
            session.commit()
            return _version_public(row)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if target.exists() and not any(target.iterdir()):
            shutil.rmtree(target, ignore_errors=True)
        raise


def list_packs() -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        rows = session.query(ExpeditionArtPackVersionORM).order_by(ExpeditionArtPackVersionORM.created_at.desc()).all()
        return {"items": [_version_public(row, session.query(ExpeditionArtReviewORM).filter_by(pack_id=row.pack_id, pack_version=row.version).all()) for row in rows], "fallback": {"source": "generated_svg", "active": not bool(session.query(ExpeditionActiveArtPackORM).filter_by(singleton_id="active").first())}}


def active_pack() -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        active = session.query(ExpeditionActiveArtPackORM).filter_by(singleton_id="active").first()
        if not active or not active.pack_id:
            return {"active": None, "fallback": True}
        version = session.query(ExpeditionArtPackVersionORM).filter_by(pack_id=active.pack_id, version=active.version).first()
        return {"active": _version_public(version, session.query(ExpeditionArtReviewORM).filter_by(pack_id=active.pack_id, pack_version=active.version).all()) if version else None, "fallback": not bool(version)}


def review_asset(pack_id: str, version: str, hero_id: str, asset_kind: str, review_status: str, note: str = "") -> dict[str, Any]:
    if hero_id not in EXPECTED_HERO_IDS or asset_kind not in ART_KINDS:
        raise ArtPackError("ART_PACK_REVIEW_TARGET_INVALID", "审核目标无效")
    if review_status not in {"APPROVED", "NEEDS_REDO", "PENDING"}:
        raise ArtPackError("ART_PACK_REVIEW_STATUS_INVALID", "审核状态无效")
    init_orm()
    with SessionLocal() as session:
        version_row = session.query(ExpeditionArtPackVersionORM).filter_by(pack_id=pack_id, version=version).first()
        if not version_row:
            raise ArtPackError("ART_PACK_VERSION_NOT_FOUND", "美术包版本不存在", 404)
        row = session.query(ExpeditionArtReviewORM).filter_by(pack_id=pack_id, pack_version=version, hero_id=hero_id, asset_kind=asset_kind).first()
        if not row:
            raise ArtPackError("ART_PACK_REVIEW_TARGET_NOT_FOUND", "美术资源不存在", 404)
        row.review_status, row.review_note, row.reviewed_at = review_status, str(note or ""), _now() if review_status != "PENDING" else None
        reviews = session.query(ExpeditionArtReviewORM).filter_by(pack_id=pack_id, pack_version=version).all()
        if all(item.review_status == "APPROVED" for item in reviews):
            version_row.status = "VALIDATED"
        elif version_row.status == "VALIDATED":
            version_row.status = "INSTALLED"
        session.commit()
        return _version_public(version_row, reviews)


def activate_pack(pack_id: str, version: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        row = session.query(ExpeditionArtPackVersionORM).filter_by(pack_id=pack_id, version=version).first()
        if not row:
            raise ArtPackError("ART_PACK_VERSION_NOT_FOUND", "美术包版本不存在", 404)
        reviews = session.query(ExpeditionArtReviewORM).filter_by(pack_id=pack_id, pack_version=version).all()
        if len(reviews) != len(EXPECTED_HERO_IDS) * len(ART_KINDS) or any(item.review_status != "APPROVED" for item in reviews):
            raise ArtPackError("ART_PACK_REVIEW_INCOMPLETE", "全部美术资源审核通过后才能激活", 409)
        active = session.query(ExpeditionActiveArtPackORM).filter_by(singleton_id="active").first() or ExpeditionActiveArtPackORM(singleton_id="active")
        previous_pack, previous_version = active.pack_id, active.version
        if previous_pack and previous_version != version:
            previous = session.query(ExpeditionArtPackVersionORM).filter_by(pack_id=previous_pack, version=previous_version).first()
            if previous and previous.status == "ACTIVE":
                previous.status = "INSTALLED"
        active.previous_pack_id, active.previous_version = previous_pack, previous_version
        active.pack_id, active.version, active.activated_at = pack_id, version, _now()
        row.status, row.activated_at = "ACTIVE", active.activated_at
        session.add(active); session.commit()
        return _version_public(row, reviews)


def rollback_pack(pack_id: str, version: str) -> dict[str, Any]:
    init_orm()
    with SessionLocal() as session:
        target = session.query(ExpeditionArtPackVersionORM).filter_by(pack_id=pack_id, version=version).first()
        active = session.query(ExpeditionActiveArtPackORM).filter_by(singleton_id="active").first()
        if not target or not active or active.pack_id != pack_id or active.version != version:
            raise ArtPackError("ART_PACK_ROLLBACK_TARGET_INVALID", "只能回滚当前激活的美术包版本", 409)
        old_pack, old_version = active.previous_pack_id, active.previous_version
        target.status = "ROLLED_BACK"
        if old_pack and old_version:
            previous = session.query(ExpeditionArtPackVersionORM).filter_by(pack_id=old_pack, version=old_version).first()
            if not previous:
                raise ArtPackError("ART_PACK_PREVIOUS_NOT_FOUND", "上一版本不存在", 409)
            previous.status, previous.activated_at = "ACTIVE", _now()
            active.pack_id, active.version = old_pack, old_version
        else:
            active.pack_id, active.version = "", ""
        active.previous_pack_id, active.previous_version, active.activated_at = "", "", _now()
        session.commit()
        return active_pack()


def hero_assets(hero_id: str) -> dict[str, Any]:
    if hero_id not in EXPECTED_HERO_IDS:
        raise ArtPackError("HERO_NOT_FOUND", "角色不存在", 404)
    active = active_pack().get("active")
    result = {}
    if active:
        init_orm()
        with SessionLocal() as session:
            rows = session.query(ExpeditionHeroAssetORM).filter_by(pack_id=active["pack_id"], pack_version=active["version"], hero_id=hero_id).all()
            for row in rows:
                result[row.asset_kind] = {"asset_id": row.asset_id, "url": f"/api/expedition/art-assets/{row.asset_id}/file", "width": row.width, "height": row.height, "focal_point": row.focal_point, "scale": row.scale, "offset": row.offset, "fallback_used": False, "version": row.pack_version}
    for kind in ART_KINDS:
        result.setdefault(kind, {"asset_id": f"hero:{hero_id}:{kind}", "url": f"/assets/expedition/heroes/{hero_id}/{kind}.svg", "width": 0, "height": 0, "focal_point": {"x": 0.5, "y": 0.4}, "scale": 1.0, "offset": {"x": 0, "y": 0}, "fallback_used": True, "version": "fallback"})
    return {"hero_id": hero_id, "assets": result, "fallback_used": any(item["fallback_used"] for item in result.values())}


def asset_file(asset_id: str) -> Path:
    if any(part in asset_id for part in ("..", "/", "\\")):
        raise ArtPackError("INVALID_ASSET_ID", "资源标识非法")
    init_orm()
    with SessionLocal() as session:
        row = session.query(ExpeditionHeroAssetORM).filter_by(asset_id=asset_id).first()
        if not row:
            raise ArtPackError("ASSET_NOT_FOUND", "正式美术资源不存在", 404)
        root = (PACK_ROOT / row.pack_id / row.pack_version).resolve()
        path = (root / row.path.split("/", 1)[-1] if row.path.startswith(row.pack_id + "/") else root / row.path)
        # The stored path is normalized to the archive root; fallback to the
        # installed root-relative location if the upstream pack used a wrapper.
        path = path.resolve()
        if root not in path.parents or not path.is_file():
            candidates = list(root.rglob(Path(row.path).name))
            path = candidates[0].resolve() if candidates else path
        if root not in path.parents or not path.is_file():
            raise ArtPackError("ASSET_FILE_MISSING", "正式美术文件缺失，应该回退到 fallback", 404)
        return path


def asset_public(asset_id: str) -> dict[str, Any]:
    if any(part in asset_id for part in ("..", "/", "\\")):
        raise ArtPackError("INVALID_ASSET_ID", "资源标识非法")
    init_orm()
    with SessionLocal() as session:
        row = session.query(ExpeditionHeroAssetORM).filter_by(asset_id=asset_id).first()
        if not row:
            raise ArtPackError("ASSET_NOT_FOUND", "正式美术资源不存在", 404)
        return {"asset_id": row.asset_id, "asset_type": row.asset_kind, "character_id": row.hero_id, "path": row.path, "url": f"/api/expedition/art-assets/{row.asset_id}/file", "source": row.source, "mime": row.mime, "width": row.width, "height": row.height, "focal_point": row.focal_point, "scale": row.scale, "offset": row.offset, "sha256": row.sha256, "version": row.pack_version, "fallback_used": False}
