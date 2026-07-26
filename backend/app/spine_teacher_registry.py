"""Secure local registry for licensed Spine 4.1 AI teacher packages."""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_ROOT = PROJECT_ROOT / "data" / "spine_teachers"
REGISTRY_FILE = REGISTRY_ROOT / "registry.json"
MANIFEST_NAME = "teacher.manifest.json"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 350 * 1024 * 1024
MAX_FILES = 160
ALLOWED_SUFFIXES = {".json", ".skel", ".atlas", ".png", ".webp", ".jpg", ".jpeg"}
ANIMATION_STATES = ("idle", "listening", "thinking", "explaining", "encouraging")
_LOCK = Lock()


class SpineTeacherError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _empty_registry() -> dict[str, Any]:
    return {"version": 1, "active_teacher_id": None, "teachers": []}


def _load() -> dict[str, Any]:
    if not REGISTRY_FILE.exists():
        return _empty_registry()
    try:
        payload = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpineTeacherError("SPINE_REGISTRY_INVALID", "Spine 教师登记表损坏。", 500) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("teachers"), list):
        raise SpineTeacherError("SPINE_REGISTRY_INVALID", "Spine 教师登记表格式无效。", 500)
    return payload


def _save(payload: dict[str, Any]) -> None:
    REGISTRY_ROOT.mkdir(parents=True, exist_ok=True)
    temporary = REGISTRY_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(REGISTRY_FILE)


def _safe_member(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SpineTeacherError("SPINE_PACKAGE_UNSAFE_PATH", f"资源包包含不安全路径：{name}", 415)
    return path


def _resolve_package_path(base: PurePosixPath, value: str, *, suffixes: set[str]) -> PurePosixPath:
    path = _safe_member(str(base / value))
    if path.suffix.casefold() not in suffixes:
        raise SpineTeacherError("SPINE_PACKAGE_INVALID_ASSET", f"资源类型不受支持：{value}", 415)
    return path


def _read_manifest(archive: zipfile.ZipFile) -> tuple[dict[str, Any], PurePosixPath]:
    manifest_members = [
        _safe_member(info.filename)
        for info in archive.infolist()
        if not info.is_dir() and PurePosixPath(info.filename.replace("\\", "/")).name == MANIFEST_NAME
    ]
    if len(manifest_members) != 1:
        raise SpineTeacherError("SPINE_MANIFEST_REQUIRED", f"ZIP 必须包含且只能包含一个 {MANIFEST_NAME}。", 415)
    manifest_path = manifest_members[0]
    try:
        manifest = json.loads(archive.read(str(manifest_path)).decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpineTeacherError("SPINE_MANIFEST_INVALID", "Spine 教师清单无法读取。", 415) from exc
    if not isinstance(manifest, dict):
        raise SpineTeacherError("SPINE_MANIFEST_INVALID", "Spine 教师清单必须是 JSON 对象。", 415)
    return manifest, manifest_path.parent


def validate_manifest(manifest: dict[str, Any], *, base: PurePosixPath = PurePosixPath(".")) -> dict[str, Any]:
    if manifest.get("format") != "mentorflow.spine-teacher" or manifest.get("runtime_version") != "4.1":
        raise SpineTeacherError("SPINE_VERSION_UNSUPPORTED", "仅接受 mentorflow.spine-teacher / Spine 4.1 资源包。", 415)
    license_contract = manifest.get("license")
    if (
        not isinstance(license_contract, dict)
        or license_contract.get("spine_editor_license_confirmed") is not True
        or license_contract.get("character_rights_confirmed") is not True
    ):
        raise SpineTeacherError("SPINE_LICENSE_REQUIRED", "必须确认 Spine Editor 授权和角色资源使用权。", 403)
    skeleton = manifest.get("skeleton")
    if not isinstance(skeleton, dict) or not skeleton.get("atlas"):
        raise SpineTeacherError("SPINE_SKELETON_REQUIRED", "资源清单缺少 skeleton.atlas。", 415)
    json_name = str(skeleton.get("json") or "")
    binary_name = str(skeleton.get("binary") or "")
    if bool(json_name) == bool(binary_name):
        raise SpineTeacherError("SPINE_SKELETON_REQUIRED", "skeleton.json 与 skeleton.binary 必须且只能提供一个。", 415)
    skeleton_path = _resolve_package_path(base, json_name or binary_name, suffixes={".json", ".skel"})
    atlas_path = _resolve_package_path(base, str(skeleton["atlas"]), suffixes={".atlas"})
    animations = manifest.get("animations")
    if not isinstance(animations, dict) or any(not str(animations.get(state) or "").strip() for state in ANIMATION_STATES):
        raise SpineTeacherError("SPINE_ANIMATIONS_INCOMPLETE", "资源清单必须映射五种教学动画状态。", 415)
    return {
        **manifest,
        "_skeleton_path": skeleton_path.as_posix(),
        "_atlas_path": atlas_path.as_posix(),
    }


def _atlas_texture_paths(atlas_text: str, *, atlas_parent: PurePosixPath) -> list[PurePosixPath]:
    textures: list[PurePosixPath] = []
    for raw in atlas_text.splitlines():
        line = raw.strip()
        if not line or ":" in line:
            continue
        if PurePosixPath(line).suffix.casefold() in {".png", ".webp", ".jpg", ".jpeg"}:
            textures.append(_resolve_package_path(atlas_parent, line, suffixes={".png", ".webp", ".jpg", ".jpeg"}))
    if not textures:
        raise SpineTeacherError("SPINE_ATLAS_TEXTURE_REQUIRED", "atlas 没有引用任何贴图。", 415)
    return textures


def import_package(
    filename: str,
    content: bytes,
    *,
    display_name: str,
    creator: str,
    spine_editor_license_confirmed: bool,
    character_rights_confirmed: bool,
    adult_character_confirmed: bool,
) -> dict[str, Any]:
    if Path(filename).suffix.casefold() != ".zip":
        raise SpineTeacherError("SPINE_PACKAGE_ZIP_REQUIRED", "Spine 教师资源必须使用 ZIP。", 415)
    if not content or len(content) > MAX_ARCHIVE_BYTES:
        raise SpineTeacherError("SPINE_PACKAGE_SIZE_INVALID", "Spine 教师 ZIP 为空或超过 100 MB。", 413)
    if not all((spine_editor_license_confirmed, character_rights_confirmed, adult_character_confirmed)):
        raise SpineTeacherError("SPINE_IMPORT_CONFIRMATION_REQUIRED", "导入前必须确认 Spine 授权、角色使用权和成年教师设定。", 403)

    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise SpineTeacherError("SPINE_PACKAGE_INVALID", "文件不是有效 ZIP。", 415) from exc

    with archive:
        files = [info for info in archive.infolist() if not info.is_dir()]
        if not files or len(files) > MAX_FILES or sum(info.file_size for info in files) > MAX_EXPANDED_BYTES:
            raise SpineTeacherError("SPINE_PACKAGE_EXPANSION_INVALID", "ZIP 文件数量或展开体积超过安全限制。", 413)
        names: set[str] = set()
        for info in files:
            path = _safe_member(info.filename)
            if info.external_attr >> 16 & 0o170000 == 0o120000:
                raise SpineTeacherError("SPINE_PACKAGE_SYMLINK_REJECTED", "ZIP 不允许符号链接。", 415)
            if path.suffix.casefold() not in ALLOWED_SUFFIXES:
                raise SpineTeacherError("SPINE_PACKAGE_INVALID_ASSET", f"ZIP 包含不支持的文件：{path}", 415)
            names.add(path.as_posix())

        manifest, base = _read_manifest(archive)
        manifest.setdefault("license", {})
        manifest["license"]["spine_editor_license_confirmed"] = spine_editor_license_confirmed
        manifest["license"]["character_rights_confirmed"] = character_rights_confirmed
        validated = validate_manifest(manifest, base=base)
        required = {validated["_skeleton_path"], validated["_atlas_path"]}
        try:
            atlas_text = archive.read(validated["_atlas_path"]).decode("utf-8")
        except (KeyError, UnicodeDecodeError) as exc:
            raise SpineTeacherError("SPINE_ATLAS_INVALID", "atlas 文件缺失或不是 UTF-8。", 415) from exc
        required.update(path.as_posix() for path in _atlas_texture_paths(atlas_text, atlas_parent=PurePosixPath(validated["_atlas_path"]).parent))
        missing = sorted(required - names)
        if missing:
            raise SpineTeacherError("SPINE_PACKAGE_MISSING_ASSET", f"资源包缺少文件：{missing}", 415)

        teacher_id = f"spine-{uuid.uuid4().hex[:12]}"
        target = REGISTRY_ROOT / teacher_id
        temporary = REGISTRY_ROOT / f".tmp-{teacher_id}"
        REGISTRY_ROOT.mkdir(parents=True, exist_ok=True)
        try:
            temporary.mkdir()
            for info in files:
                relative = _safe_member(info.filename)
                output = temporary.joinpath(*relative.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(archive.read(info))
            temporary.replace(target)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise

    now = datetime.now(timezone.utc).isoformat()
    item = {
        "teacher_id": teacher_id,
        "display_name": display_name.strip() or "2D AI 教师",
        "creator": creator.strip() or "用户导入",
        "format": "spine-4.1",
        "manifest_path": str((base / MANIFEST_NAME).as_posix()),
        "skeleton_path": validated["_skeleton_path"],
        "atlas_path": validated["_atlas_path"],
        "animations": {state: str(validated["animations"][state]) for state in ANIMATION_STATES},
        "skin": str(validated.get("skin") or ""),
        "adult_character_confirmed": True,
        "created_at": now,
        "updated_at": now,
    }
    with _LOCK:
        registry = _load()
        registry["teachers"].append(item)
        registry["active_teacher_id"] = teacher_id
        _save(registry)
    return _public(item)


def _public(item: dict[str, Any]) -> dict[str, Any]:
    teacher_id = str(item["teacher_id"])
    return {
        **{key: value for key, value in item.items() if not key.endswith("_path")},
        "manifest_url": f"/api/spine-teachers/{teacher_id}/manifest",
    }


def active_teacher() -> dict[str, Any] | None:
    with _LOCK:
        registry = _load()
        active_id = registry.get("active_teacher_id")
        item = next((entry for entry in registry["teachers"] if entry.get("teacher_id") == active_id), None)
    return _public(item) if item else None


def public_manifest(teacher_id: str) -> dict[str, Any]:
    with _LOCK:
        item = next((entry for entry in _load()["teachers"] if entry.get("teacher_id") == teacher_id), None)
    if not item:
        raise SpineTeacherError("SPINE_TEACHER_NOT_FOUND", "Spine 教师不存在。", 404)
    prefix = f"/api/spine-teachers/{teacher_id}/assets/"
    skeleton: dict[str, str] = {"atlas_url": prefix + item["atlas_path"]}
    skeleton_key = "json_url" if str(item["skeleton_path"]).casefold().endswith(".json") else "binary_url"
    skeleton[skeleton_key] = prefix + item["skeleton_path"]
    return {
        "format": "mentorflow.spine-teacher",
        "runtime_version": "4.1",
        "license": {
            "spine_editor_license_confirmed": True,
            "character_rights_confirmed": True,
        },
        "skeleton": skeleton,
        "animations": item["animations"],
        **({"skin": item["skin"]} if item.get("skin") else {}),
    }


def resolve_asset(teacher_id: str, asset_name: str) -> tuple[Path, str]:
    with _LOCK:
        item = next((entry for entry in _load()["teachers"] if entry.get("teacher_id") == teacher_id), None)
    if not item:
        raise SpineTeacherError("SPINE_TEACHER_NOT_FOUND", "Spine 教师不存在。", 404)
    relative = _safe_member(asset_name)
    root = (REGISTRY_ROOT / teacher_id).resolve()
    path = root.joinpath(*relative.parts).resolve()
    if root not in path.parents or not path.is_file():
        raise SpineTeacherError("SPINE_ASSET_NOT_FOUND", "Spine 教师资源不存在。", 404)
    return path, mimetypes.guess_type(path.name)[0] or "application/octet-stream"
