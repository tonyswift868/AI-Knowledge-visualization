"""Small built-in character-card roster.

Only three parameter cards are shipped with the app.  Users can import any
other native KKS PNG or MentorFlow JSON through the card importer; the game
directory and its assets remain outside this project.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .teacher_avatar_cards import validate_card
from .teacher_avatar_registry import TeacherAvatarError
from .koikatsu_paths import KKS_CARD_PRESET_ROOT


CARD_ROOT = KKS_CARD_PRESET_ROOT
MANIFEST = CARD_ROOT / "manifest.json"


def _load_manifest() -> dict[str, Any]:
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TeacherAvatarError("CARD_PRESET_MANIFEST_INVALID", "内置角色卡清单无法读取。", 500) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list):
        raise TeacherAvatarError("CARD_PRESET_MANIFEST_INVALID", "内置角色卡清单格式无效。", 500)
    return payload


def _card_path(relative: str) -> Path:
    path = (CARD_ROOT / relative).resolve()
    if CARD_ROOT.resolve() not in path.parents or not path.is_file():
        raise TeacherAvatarError("CARD_PRESET_MISSING", "内置角色卡文件缺失。", 500)
    return path


def _read_card(card_id: str) -> dict[str, Any]:
    item = next((item for item in _load_manifest()["cards"] if item.get("card_id") == card_id), None)
    if not isinstance(item, dict):
        raise TeacherAvatarError("CARD_PRESET_NOT_FOUND", "内置角色卡不存在。", 404)
    path = _card_path(str(item.get("file") or ""))
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise TeacherAvatarError("CARD_PRESET_MISSING", "内置角色卡无法读取。", 500) from exc
    return {"manifest": item, "card": validate_card(path.name, payload)}


def list_card_presets() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in _load_manifest()["cards"]:
        try:
            result = _read_card(str(item.get("card_id") or ""))
        except TeacherAvatarError as exc:
            # These legacy MentorFlow presets reference an old VRM body
            # template.  The current teacher page is driven by the native KKS
            # runtime roster; an unavailable optional preset must not turn the
            # whole page's background request into a 409 error.
            if exc.code == "BODY_TEMPLATE_MISSING":
                continue
            raise
        card = result["card"]
        items.append(
            {
                "card_id": str(item["card_id"]),
                "display_name": str(item.get("display_name") or card["character"].get("display_name") or "角色卡"),
                "creator": str(item.get("creator") or card["character"].get("creator") or "未署名本地卡"),
                "description": str(item.get("description") or ""),
                "source_format": card["source_format"],
                "body_template_id": card["body_template_id"],
                "morph": card["morph"],
                "assets": card["assets"],
                "dependencies": card["dependencies"],
                "missing_dependencies": card["missing_dependencies"],
                "native_summary": card.get("native_summary"),
            }
        )
    return {"items": items, "count": len(items), "total": len(items), "source": "local-card-roster"}


def get_card_preset(card_id: str) -> dict[str, Any]:
    return _read_card(card_id)["card"]

