"""Persistent local roster for editable Koikatsu Sunshine teacher cards."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from .koikatsu_card import KoikatsuCardError, inspect_koikatsu_sunshine_card


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ROOT = PROJECT_ROOT / "data" / "koikatsu_teachers"
REGISTRY = ROOT / "registry.json"
DEFAULT_CARD_CANDIDATES = (
    Path(os.getenv("MENTORFLOW_KKS_DEFAULT_CARD", "")) if os.getenv("MENTORFLOW_KKS_DEFAULT_CARD") else None,
    Path(r"E:\BaiduNetdiskDownload\shuang\C02\[ScrewThisNoise] Koikatsu Sunshine BetterRepack R1\UserData\chara\female\KKS BR-Chan.png"),
)
_LOCK = Lock()


class KoikatsuTeacherError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _load() -> dict[str, Any]:
    if not REGISTRY.exists():
        return {"version": 1, "teachers": []}
    try:
        result = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KoikatsuTeacherError("REGISTRY_INVALID", "恋活教师档案损坏。", 500) from exc
    if not isinstance(result, dict) or not isinstance(result.get("teachers"), list):
        raise KoikatsuTeacherError("REGISTRY_INVALID", "恋活教师档案格式无效。", 500)
    return result


def _save(value: dict[str, Any]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    temporary = REGISTRY.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(REGISTRY)


def _public(value: dict[str, Any]) -> dict[str, Any]:
    teacher_id = str(value["teacher_id"])
    return {**{key: item for key, item in value.items() if key != "card_path"}, "card_url": f"/api/koikatsu-teachers/{teacher_id}/card"}


def default_card() -> Path:
    for candidate in DEFAULT_CARD_CANDIDATES:
        if candidate and candidate.is_file():
            try:
                inspect_koikatsu_sunshine_card(candidate.read_bytes())
                return candidate
            except (OSError, KoikatsuCardError):
                continue
    raise KoikatsuTeacherError("DEFAULT_CARD_MISSING", "未找到默认恋活角色卡。请设置 MENTORFLOW_KKS_DEFAULT_CARD 或导入一张 PNG 卡。", 404)


def list_teachers() -> dict[str, Any]:
    with _LOCK:
        return {"items": [_public(item) for item in _load()["teachers"]]}


def save_teacher(
    content: bytes,
    *,
    teacher_id: str = "",
    display_name: str,
    persona: str,
    teaching_style: str,
) -> dict[str, Any]:
    try:
        inspected = inspect_koikatsu_sunshine_card(content)
    except KoikatsuCardError as exc:
        raise KoikatsuTeacherError("CARD_INVALID", f"无法保存恋活角色卡：{exc}", 415) from exc
    if not display_name.strip():
        raise KoikatsuTeacherError("NAME_REQUIRED", "请填写教师显示名。")
    if not persona.strip():
        raise KoikatsuTeacherError("PERSONA_REQUIRED", "请填写教师人设。")
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", teacher_id).strip("-")
    digest = hashlib.sha256(content).hexdigest()
    if not safe_id:
        safe_id = f"kks-teacher-{digest[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    with _LOCK:
        payload = _load()
        existing = next((item for item in payload["teachers"] if item.get("teacher_id") == safe_id), None)
        item = {
            "teacher_id": safe_id,
            "display_name": display_name.strip(),
            "persona": persona.strip(),
            "teaching_style": teaching_style.strip(),
            "card_path": f"{safe_id}.png",
            "native_summary": inspected["native_summary"],
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / item["card_path"]).write_bytes(content)
        if existing:
            payload["teachers"][payload["teachers"].index(existing)] = item
        else:
            payload["teachers"].append(item)
        _save(payload)
    return _public(item)


def resolve_card(teacher_id: str) -> Path:
    with _LOCK:
        item = next((item for item in _load()["teachers"] if item.get("teacher_id") == teacher_id), None)
    if not item:
        raise KoikatsuTeacherError("TEACHER_NOT_FOUND", "该恋活教师不存在。", 404)
    path = (ROOT / str(item["card_path"])).resolve()
    if ROOT.resolve() not in path.parents or not path.is_file():
        raise KoikatsuTeacherError("CARD_MISSING", "该教师的角色卡文件缺失。", 404)
    return path
