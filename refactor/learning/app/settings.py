"""Account-level learning preferences for the new window (TEACH-01, TEACH-05, SYS-04, UI-00).

Preferences are chosen from presets, never typed prompts (TEACH-01). They
apply to every plan of the account; mistakes and profiles stay per plan.
"""
from __future__ import annotations

import copy
import re

from ..preferences.storage import AccountJSON, StorageError
from .bridge import BridgeError

THEMES = ("sakura", "aurora")
TEACHER_STYLES = ("warm", "strict", "humorous", "calm")
DETAIL_LEVELS = ("brief", "normal", "detailed")
EXAMPLE_AMOUNTS = ("few", "normal", "many")
ANALOGY_DOMAINS = ("life", "games", "programming", "sports", "music", "cooking", "film", "business", "nature")

DEFAULTS = {
    "version": 1,
    "theme": "sakura",
    "font_scale": 1.0,
    "reduce_motion": False,
    "teacher_style": "warm",
    "detail": "normal",
    "examples": "normal",
    "analogy_domains": ["life"],
    "refresh_time": "04:00",
    "reminder_enabled": False,
    "reminder_time": "20:00",
}

_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")


def validate(values: dict) -> dict:
    if not isinstance(values, dict):
        raise BridgeError("SETTINGS_INVALID", "设置格式无效。")
    merged = copy.deepcopy(DEFAULTS)
    unknown = set(values) - set(DEFAULTS)
    if unknown:
        raise BridgeError("SETTINGS_INVALID", "设置里有无法识别的项目。")
    merged.update(values)
    checks = (
        merged["theme"] in THEMES,
        type(merged["font_scale"]) in (int, float) and 0.85 <= merged["font_scale"] <= 1.4,
        type(merged["reduce_motion"]) is bool,
        merged["teacher_style"] in TEACHER_STYLES,
        merged["detail"] in DETAIL_LEVELS,
        merged["examples"] in EXAMPLE_AMOUNTS,
        isinstance(merged["analogy_domains"], list) and 1 <= len(merged["analogy_domains"]) <= len(ANALOGY_DOMAINS)
        and all(d in ANALOGY_DOMAINS for d in merged["analogy_domains"])
        and len(set(merged["analogy_domains"])) == len(merged["analogy_domains"]),
        isinstance(merged["refresh_time"], str) and bool(_TIME.fullmatch(merged["refresh_time"])),
        type(merged["reminder_enabled"]) is bool,
        isinstance(merged["reminder_time"], str) and bool(_TIME.fullmatch(merged["reminder_time"])),
        merged["version"] == 1,
    )
    if not all(checks):
        raise BridgeError("SETTINGS_INVALID", "有设置项的取值无效，请重新选择。")
    merged["font_scale"] = round(float(merged["font_scale"]), 2)
    return merged


class AppSettings:
    def __init__(self, account_id: str, data_root):
        self._file = AccountJSON(account_id, data_root, "app-settings.json")

    def load(self) -> dict:
        try:
            data = self._file.read(copy.deepcopy(DEFAULTS))
            return validate(data)
        except (StorageError, BridgeError):
            # A damaged file never blocks learning; defaults are shown and the
            # next save replaces it.
            return copy.deepcopy(DEFAULTS)

    def save(self, changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise BridgeError("SETTINGS_INVALID", "设置格式无效。")
        current = self.load()
        current.update(changes)
        value = validate(current)
        try:
            self._file.write(value)
        except StorageError as exc:
            raise BridgeError("SETTINGS_SAVE_FAILED", str(exc)) from None
        return value

    @staticmethod
    def defaults_copy() -> dict:
        return copy.deepcopy(DEFAULTS)

    @staticmethod
    def options() -> dict:
        return {"themes": list(THEMES), "teacher_styles": list(TEACHER_STYLES), "detail_levels": list(DETAIL_LEVELS),
                "example_amounts": list(EXAMPLE_AMOUNTS), "analogy_domains": list(ANALOGY_DOMAINS)}
