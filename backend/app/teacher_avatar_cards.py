"""Small, local teacher-character cards.

The card stores parameters and asset references only.  The actual body model
is resolved from the local unified avatar registry, which keeps cards small
and makes missing optional assets explicit instead of silently distorting a
random imported VRM.
"""

from __future__ import annotations

import json
import struct
from typing import Any

from .koikatsu_card import KoikatsuCardError, parse_koikatsu_sunshine_card
from .teacher_avatar_registry import TeacherAvatarError, list_models


CARD_FORMAT = "mentorflow.teacher-card"
CARD_VERSION = 1
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_CARD_KEY = b"mentorflow.card"
MORPH_KEYS = {
    "height",
    "head_scale",
    "upper_chest",
    "shoulder_width",
    "waist_width",
    "hip_width",
    "arm_length",
    "leg_length",
    "smile",
    "blink",
}


def _error(code: str, message: str, status: int = 400) -> TeacherAvatarError:
    return TeacherAvatarError(code, message, status)


def _decode_png_card(content: bytes) -> dict[str, Any]:
    if not content.startswith(PNG_SIGNATURE):
        raise _error("CARD_FORMAT_INVALID", "角色卡不是有效的 JSON 或 PNG 文件。", 415)
    offset = len(PNG_SIGNATURE)
    while offset + 12 <= len(content):
        length = struct.unpack_from(">I", content, offset)[0]
        chunk_type = content[offset + 4 : offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        if chunk_end + 4 > len(content):
            break
        chunk = content[chunk_start:chunk_end]
        if chunk_type == b"tEXt":
            key, _, value = chunk.partition(b"\0")
            if key == PNG_CARD_KEY:
                try:
                    return json.loads(value.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise _error("CARD_JSON_INVALID", "PNG 角色卡中的数据不是有效 JSON。", 415) from exc
        if chunk_type == b"iTXt":
            parts = chunk.split(b"\0", 5)
            if parts and parts[0] == PNG_CARD_KEY and len(parts) == 6:
                try:
                    return json.loads(parts[5].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise _error("CARD_JSON_INVALID", "PNG 角色卡中的数据不是有效 JSON。", 415) from exc
        offset = chunk_end + 4
        if chunk_type == b"IEND":
            break
    raise _error("CARD_DATA_MISSING", "PNG 中没有 MentorFlow 角色卡数据。", 415)


def _decode(filename: str, content: bytes) -> tuple[dict[str, Any], str]:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "json":
        try:
            return json.loads(content.decode("utf-8")), "json"
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _error("CARD_JSON_INVALID", "角色卡 JSON 无法解析。", 415) from exc
    if suffix == "png":
        try:
            return _decode_png_card(content), "png"
        except TeacherAvatarError as exc:
            if exc.code != "CARD_DATA_MISSING":
                raise
            try:
                return parse_koikatsu_sunshine_card(content), "koikatsu_sunshine"
            except KoikatsuCardError as native_exc:
                raise _error(
                    "CARD_DATA_MISSING",
                    "PNG 中没有 MentorFlow 或 Koikatsu Sunshine 角色卡数据。",
                    415,
                ) from native_exc
    raise _error("CARD_FORMAT_UNSUPPORTED", "角色卡仅支持 JSON 或带内嵌数据的 PNG。", 415)


def validate_card(filename: str, content: bytes) -> dict[str, Any]:
    raw, source_format = _decode(filename, content)
    if not isinstance(raw, dict):
        raise _error("CARD_SCHEMA_INVALID", "角色卡根数据必须是对象。", 422)
    if raw.get("format") != CARD_FORMAT:
        raise _error("CARD_SCHEMA_INVALID", f"不支持的角色卡格式：{raw.get('format') or '未声明'}。", 422)
    if int(raw.get("version") or 0) != CARD_VERSION:
        raise _error("CARD_VERSION_UNSUPPORTED", f"仅支持角色卡版本 {CARD_VERSION}。", 422)
    models = list_models()["items"]
    native_card = source_format == "koikatsu_sunshine"
    template_id = str(raw.get("body_template_id") or "").strip()
    if not template_id and native_card and models:
        template_id = str(models[0]["model_id"])
    if not template_id:
        raise _error("BODY_TEMPLATE_REQUIRED", "角色卡缺少统一身体模板 ID。", 422)
    template = next((item for item in models if item["model_id"] == template_id), None)
    if not template:
        raise _error("BODY_TEMPLATE_MISSING", "本机没有角色卡所需的统一身体模板，请先导入模板资源。", 409)
    morph = raw.get("morph") or {}
    if not isinstance(morph, dict):
        raise _error("CARD_MORPH_INVALID", "角色卡的 morph 必须是对象。", 422)
    normalized_morph: dict[str, float] = {}
    for key, value in morph.items():
        if key not in MORPH_KEYS:
            continue
        try:
            normalized_morph[key] = float(value)
        except (TypeError, ValueError) as exc:
            raise _error("CARD_MORPH_INVALID", f"角色卡参数 {key} 不是数字。", 422) from exc
    assets = raw.get("assets") or {}
    if not isinstance(assets, dict):
        raise _error("CARD_ASSETS_INVALID", "角色卡的 assets 必须是对象。", 422)
    dependencies = raw.get("dependencies") or []
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise _error("CARD_DEPENDENCIES_INVALID", "角色卡 dependencies 必须是字符串数组。", 422)
    return {
        "format": CARD_FORMAT,
        "version": CARD_VERSION,
        "character": raw.get("character") if isinstance(raw.get("character"), dict) else {},
        "body_template_id": template_id,
        "morph": normalized_morph,
        "face": raw.get("face") if isinstance(raw.get("face"), dict) else {},
        "assets": {str(key): str(value) for key, value in assets.items() if value is not None},
        "dependencies": dependencies,
        "missing_dependencies": list(dependencies) if native_card else [],
        "source_format": source_format,
        "native_summary": raw.get("native_summary") if isinstance(raw.get("native_summary"), dict) else None,
        "resolved_template": {
            "model_id": template["model_id"],
            "display_name": template["display_name"],
            "creator": template["creator"],
            "model_url": template["model_url"],
        },
    }
