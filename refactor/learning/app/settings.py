"""Account-level learning preferences for the new window (TEACH-01, TEACH-05, SYS-04, UI-00).

Teacher presets follow the account; each plan keeps its own preset binding.
chat_persona remains independent and never enters classroom prompts.
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


def legacy_preset(values):
    return dict(id="teacher-1", name="预设一", persona="", style=values.get("teacher_style", "warm"),
                detail=values.get("detail", "brief"), examples=values.get("examples", "normal"),
                analogies=list(values.get("analogy_domains", ["life"])))

DEFAULTS = {
    "version": 1,
    "theme": "sakura",
    "font_scale": 1.0,
    "reduce_motion": False,
    "teacher_style": "warm",
    "detail": "brief",
    "examples": "normal",
    "analogy_domains": ["life"],
    "refresh_time": "04:00",
    "reminder_enabled": False,
    "reminder_time": "20:00",
    "chat_persona": "",
}

_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
DEFAULTS.update(teacher_presets=[legacy_preset(DEFAULTS)], default_teacher_preset_id="teacher-1")


def validate_presets(presets, default_id):
    if not isinstance(presets, list) or not 1 <= len(presets) <= 4:
        raise BridgeError("TEACHER_PRESETS_INVALID", "请保留 1 至 4 个老师预设。最多 4 个预设。")
    result, ids, names = [], set(), set()
    for row in presets:
        if not isinstance(row, dict) or set(row) != {"id", "name", "persona", "style", "detail", "examples", "analogies"}:
            raise BridgeError("TEACHER_PRESETS_INVALID", "老师预设格式无效。")
        identifier, name, persona = row['id'], row['name'], row['persona']
        domains = row['analogies']
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", identifier)
                or identifier in ids or not isinstance(name, str) or not name.strip() or name.strip() in names
                or not isinstance(persona, str) or len(persona) > 2000
                or row['style'] not in TEACHER_STYLES or row['detail'] not in DETAIL_LEVELS
                or row['examples'] not in EXAMPLE_AMOUNTS
                or not isinstance(domains, list) or not 1 <= len(domains) <= len(ANALOGY_DOMAINS)
                or any(d not in ANALOGY_DOMAINS for d in domains) or len(set(domains)) != len(domains)):
            raise BridgeError("TEACHER_PRESETS_INVALID", "名称不能为空或重复；人设最多 2000 字；请使用已有选项。")
        ids.add(identifier); names.add(name.strip())
        result.append(dict(row, name=name.strip(), analogies=list(domains)))
    if not isinstance(default_id, str) or default_id not in ids:
        raise BridgeError("TEACHER_PRESETS_INVALID", "请选择一个现有预设作为默认。")
    return dict(presets=result, default_id=default_id)


def resolve_teacher_preset(settings, plan):
    """Also accepts legacy load-only settings adapters used outside the window."""
    value = settings.load() if settings is not None else copy.deepcopy(DEFAULTS)
    group = validate_presets(value.get('teacher_presets', [legacy_preset(value)]),
                             value.get('default_teacher_preset_id', 'teacher-1'))
    identifier = plan.get('teacher_preset_id') or group['default_id']
    return next((p for p in group['presets'] if p['id'] == identifier),
                next(p for p in group['presets'] if p['id'] == group['default_id']))


def teaching_preferences(settings, store, plan_id):
    row = resolve_teacher_preset(settings, store.get_plan(plan_id))
    return dict(teacher_style=row['style'], detail=row['detail'], examples=row['examples'],
                analogy_domains=row['analogies'], persona=row['persona'])


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
        isinstance(merged["chat_persona"], str) and len(merged["chat_persona"]) <= 6000,
        merged["version"] == 1,
    )
    if not all(checks):
        raise BridgeError("SETTINGS_INVALID", "有设置项的取值无效，请重新选择。")
    merged["font_scale"] = round(float(merged["font_scale"]), 2)
    if 'teacher_presets' not in values:
        merged['teacher_presets'] = [legacy_preset(merged)]
        merged['default_teacher_preset_id'] = 'teacher-1'
    group = validate_presets(merged['teacher_presets'], merged['default_teacher_preset_id'])
    merged.update(teacher_presets=group['presets'], default_teacher_preset_id=group['default_id'])
    return merged


class AppSettings:
    def __init__(self, account_id: str, data_root, *, store_provider=None):
        self._file = AccountJSON(account_id, data_root, "app-settings.json")
        self._store_provider = store_provider

    def bind_store(self, store):
        self._store_provider = lambda: store

    def load(self) -> dict:
        try:
            data = self._file.read(copy.deepcopy(DEFAULTS))
            value = validate(data)
        except (StorageError, BridgeError):
            # A damaged file never blocks learning; defaults are shown and the
            # next save replaces it.
            value = copy.deepcopy(DEFAULTS)
        if self._store_provider is not None:
            store = self._store_provider()
            group = store.load_account_setting('teacher_presets')
            if group is None:
                group = dict(presets=value['teacher_presets'], default_id=value['default_teacher_preset_id'])
                store.save_account_setting('teacher_presets', group)
            group = validate_presets(group['presets'], group['default_id'])
            value.update(teacher_presets=group['presets'], default_teacher_preset_id=group['default_id'])
        return value

    def save(self, changes: dict) -> dict:
        if not isinstance(changes, dict):
            raise BridgeError("SETTINGS_INVALID", "设置格式无效。")
        if set(changes) & {'teacher_presets', 'default_teacher_preset_id'}:
            raise BridgeError('SETTINGS_INVALID', '请通过老师预设入口管理预设。')
        current = self.load()
        # Keep the legacy settings API usable; the preset UI saves the full group.
        if set(changes) & {'teacher_style', 'detail', 'examples', 'analogy_domains'}:
            row = next(p for p in current['teacher_presets'] if p['id'] == current['default_teacher_preset_id'])
            for old, new in (('teacher_style','style'), ('detail','detail'), ('examples','examples'), ('analogy_domains','analogies')):
                if old in changes: row[new] = changes[old]
        current.update(changes)
        value = validate(current)
        try:
            self._file.write(value)
        except StorageError as exc:
            raise BridgeError("SETTINGS_SAVE_FAILED", str(exc)) from None
        if self._store_provider is not None:
            self._store_provider().save_account_setting('teacher_presets',
                dict(presets=value['teacher_presets'], default_id=value['default_teacher_preset_id']))
        return value

    def teacher_presets(self):
        value = self.load()
        return dict(presets=value['teacher_presets'], default_id=value['default_teacher_preset_id'])

    def save_teacher_presets(self, presets, default_id, store, *, confirm_delete=False):
        group = validate_presets(presets, default_id)
        self.bind_store(store)
        previous = self.teacher_presets()
        deleted = {p['id'] for p in previous['presets']} - {p['id'] for p in group['presets']}
        if previous['default_id'] in deleted:
            raise BridgeError('DEFAULT_PRESET', '默认预设不能删，请先把另一个预设设为默认。')
        plans = [p for p in store.list_plans(include_archived=True) if p.get('teacher_preset_id') in deleted]
        if plans and confirm_delete is not True:
            return dict(confirmation_required=True, usage_count=len(plans),
                        message=f"有 {len(plans)} 个计划在用，删除后它们改用默认预设。确定删除吗？")
        # The authoritative synced group and all affected bindings commit together.
        with store.transaction():
            for plan in plans: store.set_teacher_preset(plan['id'], group['default_id'])
            store.save_account_setting('teacher_presets', group)
        return dict(group, confirmation_required=False)

    @staticmethod
    def defaults_copy() -> dict:
        return copy.deepcopy(DEFAULTS)

    @staticmethod
    def options() -> dict:
        return {"themes": list(THEMES), "teacher_styles": list(TEACHER_STYLES), "detail_levels": list(DETAIL_LEVELS),
                "example_amounts": list(EXAMPLE_AMOUNTS), "analogy_domains": list(ANALOGY_DOMAINS)}
