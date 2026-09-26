"""Local replacement for the product-settings API/QSettings adapter."""
from __future__ import annotations

import copy
import math
import re

from .storage import AccountJSON, StorageError
from .themes import DEFAULT_CLASSROOM_THEME, CLASSROOM_THEMES

DEFAULTS = {
    "classroom_theme": DEFAULT_CLASSROOM_THEME,
    "free_chat_prompt": "",
    "analogy_keywords": "",
    "task_colors": {},
    "font_scale": 1.0,
    "compact_mode": False,
    "show_calendar": True,
}
_MIGRATED_DEFAULTS = {key: DEFAULTS[key] for key in ("font_scale", "compact_mode", "show_calendar")}


class PreferenceStore:
    def __init__(self, account_id, data_root):
        self.account_id = account_id
        self._file = AccountJSON(account_id, data_root, "preferences.json")

    @staticmethod
    def validate(values):
        if not isinstance(values, dict) or set(values) != set(DEFAULTS):
            raise StorageError("偏好设置格式无效。")
        if not isinstance(values['classroom_theme'], str) or values['classroom_theme'] not in CLASSROOM_THEMES:
            raise StorageError("请选择有效的课堂主题。")
        for key, limit in [('free_chat_prompt', 6000), ('analogy_keywords', 1000)]:
            if not isinstance(values[key], str) or len(values[key]) > limit:
                raise StorageError("提示词或类比关键词过长。")
        colors = values['task_colors']
        if (not isinstance(colors, dict) or len(colors) > 64 or any(
                not isinstance(k, str) or not k.strip() or len(k) > 80
                or not isinstance(v, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', v)
                for k, v in colors.items())):
            raise StorageError("请为每个任务标记填写名称并选择有效颜色。")
        scale = values['font_scale']
        if (isinstance(scale, bool) or type(scale) not in (int, float)
                or not math.isfinite(scale) or not 0.85 <= scale <= 1.5):
            raise StorageError("界面字号比例需在 0.85–1.5 之间。")
        if type(values['compact_mode']) is not bool or type(values['show_calendar']) is not bool:
            raise StorageError("界面显示偏好格式无效。")
        return copy.deepcopy(values)

    @staticmethod
    def _migrate(values):
        if not isinstance(values, dict):
            raise StorageError("偏好设置格式无效。")
        migrated = copy.deepcopy(values)
        for key, default in _MIGRATED_DEFAULTS.items():
            migrated.setdefault(key, copy.deepcopy(default))
        return migrated

    def load(self):
        data = self._file.read({'version': 1, 'values': DEFAULTS})
        if set(data) != {'version', 'values'} or data['version'] != 1:
            raise StorageError("偏好设置文件版本无效。")
        return self.validate(self._migrate(data['values']))

    def save(self, values):
        result = self.validate(self._migrate(values))
        self._file.write({'version': 1, 'values': result})
        return result
