"""Bridge methods for the first release of the new window.

Every handler validates its own parameters; the page is not trusted. Handlers
that touch Qt widgets are registered with ``ui=True`` (GUI thread); the rest run
on the bridge worker pool.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import sys
import time as monotonic

from .bridge import BridgeError, CallContext
from .settings import AppSettings

APP_VERSION = "2.0-preview"
if getattr(sys, "frozen", False):
    from refactor.desktop.delivery.version import load_version_manifest
    APP_VERSION = load_version_manifest()["version"]


def _text(params, key, *, limit, required=True, default=None):
    value = params.get(key, default)
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise BridgeError("PARAM_INVALID", "输入内容无效，请检查后重试。")
    return value


def _plain(value):
    """JSON-safe copy (Decimal -> string, nested)."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(k) if not isinstance(k, tuple) else "/".join(map(str, k)): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _local_study_day(settings: dict):
    from ..course.ledger import study_day
    now = datetime.now(timezone.utc).astimezone()
    hour, minute = map(int, settings["refresh_time"].split(":"))
    return study_day(now, time(hour, minute), now.tzinfo)


def register(bridge, window):
    """``window`` provides account info, Qt actions and the bound services."""

    def services():
        current = window.services
        if current is None:
            raise BridgeError("NOT_READY", "账号服务还在连接，请稍候。")
        if hasattr(current, 'bind_model_status'):
            current.bind_model_status(lambda data: bridge.push('model_status', data))
        return current

    def report_model_error(error, method, params):
        from ..connections.faults import present_error
        return present_error(services().gateway, error, purpose=method, plan_id=params.get('plan_id',''))
    bridge.model_error_handler = report_model_error

    # ---------------------------------------------------------------- app
    def bootstrap(params, ctx: CallContext):
        current = window.services
        if current is not None and hasattr(current, 'bind_model_status'):
            current.bind_model_status(lambda data: bridge.push('model_status', data))
        settings = current.settings.load() if current else AppSettings.defaults_copy()
        return {"version": APP_VERSION, "account": window.account_info(), "settings": settings,
                "options": AppSettings.options(), "notice": window.session_notice,
                "ready": current is not None}

    def account(params, ctx):
        action = params.get("action")
        if action not in {"switch", "logout", "verify", "account_settings"}:
            raise BridgeError("PARAM_INVALID", "未知的账号操作。")
        window.request_account_action(action)
        return {"ok": True}

    def window_action(params, ctx):
        action = params.get("action")
        if action in {"teacher_open", "teacher_close"}:
            return {"teacher": window.window_action(action, **{k: v for k, v in params.items() if k != "action"})}
        if action not in {"toggle_fullscreen", "exit_fullscreen"}:
            raise BridgeError("PARAM_INVALID", "未知的窗口操作。")
        return {"fullscreen": window.window_action(action)}

    bridge.register("app.bootstrap", bootstrap, ui=True)
    bridge.register("app.account", account, ui=True)
    bridge.register("app.window", window_action, ui=True)
    def update_action(action, params):
        from PySide6.QtWidgets import QApplication
        if params:
            raise BridgeError("PARAM_INVALID", "更新操作不接受地址或文件参数。")
        updater = getattr(QApplication.instance(), "_mentorflow_updates", None)
        if updater is None:
            return {"available": False, "busy": False, "state": "UNPACKAGED",
                    "version": APP_VERSION, "message": "开发版本；请使用安装版进行自动更新。"}
        value = updater.snapshot() if action == "status" else updater.run(action, window)
        return {**value, "version": APP_VERSION}

    for action in ("status", "check", "automatic", "defer", "install"):
        bridge.register("update." + action, lambda p, c, action=action: update_action(action, p), ui=True)
    bridge.register("sync.status", lambda p, c: window.sync_status(), ui=True)
    bridge.register("sync.now", lambda p, c: window.sync_now(), ui=True)

    # ----------------------------------------------------------- settings
    def settings_get(params, ctx):
        return {"settings": services().settings.load(), "options": AppSettings.options()}

    def settings_save(params, ctx):
        changes = params.get("changes")
        saved = services().settings.save(changes)
        window.settings_changed(saved)
        return {"settings": saved}

    bridge.register("settings.get", settings_get)
    bridge.register("settings.save", settings_save)

    def teacher_presets(params, ctx):
        current = services()
        current.settings.bind_store(current.store)
        return current.settings.teacher_presets()

    def save_teacher_presets(params, ctx):
        current = services()
        result = current.settings.save_teacher_presets(params.get('presets'), params.get('default_id'), current.store,
                                                      confirm_delete=params.get('confirm_delete', False))
        if not result.get('confirmation_required'):
            window.settings_changed(current.settings.load())
        return result

    def set_teacher_preset(params, ctx):
        current = services()
        current.settings.bind_store(current.store)
        group = current.settings.teacher_presets()
        preset_id = _text(params, 'preset_id', limit=80)
        preset = next((row for row in group['presets'] if row['id'] == preset_id), None)
        if preset is None: raise BridgeError('TEACHER_PRESET_NOT_FOUND', '这个老师预设已不存在，请重新选择。')
        current.store.set_teacher_preset(_text(params, 'plan_id', limit=240), preset_id)
        return {'teacher_preset': {k: preset[k] for k in ('id', 'name')}}

    bridge.register('settings.teacher_presets', teacher_presets)
    bridge.register('settings.save_teacher_presets', save_teacher_presets)
    bridge.register('plan.set_teacher_preset', set_teacher_preset)

    # ------------------------------------------------------------- models
    def providers(params, ctx):
        from ..connections.providers import REGISTRY
        items = []
        for spec in REGISTRY.values():
            search = spec.search
            items.append({"id": spec.id, "name": spec.display_name, "default_base_url": spec.default_base_url,
                          "default_model": spec.default_model, "search_supported": search is not None,
                          "search_verified": bool(search and search.verified),
                          "search_note": search.notes if search else ""})
        return {"providers": items}

    def model_list(params, ctx):
        gateway = services().gateway
        active = gateway.get_configuration()
        return {"presets": list(gateway.list_presets()), "active_id": active["id"] if active else None,
                "search_supported": gateway.search_supported() if active else False}

    def model_save(params, ctx):
        gateway = services().gateway
        provider_id = _text(params, "provider_id", limit=32)
        kwargs = {}
        for key, limit in (("display_name", 80), ("base_url", 2048), ("model", 256)):
            value = _text(params, key, limit=limit, required=False)
            if value is not None:
                kwargs[key] = value.strip()
        api_key = params.get("api_key")
        if api_key is not None:
            if not isinstance(api_key, str) or len(api_key) > 1024:
                raise BridgeError("PARAM_INVALID", "API Key 格式无效。")
            if api_key.strip():
                kwargs["api_key"] = api_key.strip()
        preset_id = params.get("preset_id")
        if preset_id is not None:
            kwargs["preset_id"] = _text(params, "preset_id", limit=128)
        for key, low, high in (("temperature", 0, 2), ("timeout", 1, 300)):
            if key in params:
                value = params[key]
                if value is None and key == "temperature":
                    kwargs[key] = None
                elif type(value) in (int, float) and low <= value <= high:
                    kwargs[key] = float(value)
                else:
                    raise BridgeError("PARAM_INVALID", "温度须为 0–2，请求时限须为 1–300 秒。")
        saved = gateway.save_preset(provider_id, **kwargs)
        return {"preset": saved, **model_list({}, ctx)}

    def model_select(params, ctx):
        services().gateway.select_preset(_text(params, "preset_id", limit=128))
        return model_list({}, ctx)

    def model_delete(params, ctx):
        services().gateway.delete_preset(_text(params, "preset_id", limit=128))
        return model_list({}, ctx)

    def model_test(params, ctx):
        started = monotonic.monotonic()
        result = services().gateway.test_connection()
        return {"ok": True, "latency_ms": int((monotonic.monotonic() - started) * 1000),
                "model": getattr(result, "model", ""), "reply": (getattr(result, "text", "") or "")[:80]}

    def model_balance(params, ctx):
        refresh = params.get("refresh", False)
        if type(refresh) is not bool:
            raise BridgeError("PARAM_INVALID", "刷新参数无效。")
        value = services().gateway.balance(_text(params, "preset_id", limit=128),
                                           refresh=refresh, cancel=ctx.cancel)
        return {"balance": value}

    bridge.register("models.providers", providers)
    bridge.register("models.list", model_list)
    bridge.register("models.save", model_save)
    bridge.register("models.select", model_select)
    bridge.register("models.delete", model_delete)
    bridge.register("models.test", model_test)
    bridge.register('models.status', lambda p,c: services().model_status())
    bridge.register("models.balance", model_balance)

    # -------------------------------------------------------------- usage
    def usage_summary(params, ctx):
        days = params.get("days", 7)
        group_by = params.get("group_by", "purpose")
        if days not in (1, 7, 30, 365) or group_by not in ("purpose", "model", "day"):
            raise BridgeError("PARAM_INVALID", "用量统计范围无效。")
        end = datetime.now(timezone.utc) + timedelta(minutes=1)
        start = end - timedelta(days=days)
        return _plain(services().gateway.usage_log.summary(start, end, group_by=group_by, tokens_only=True))

    bridge.register("usage.summary", usage_summary)

    # -------------------------------------------------------------- plans
    def plan_list(params, ctx):
        from ..course.ledger import study_day
        current = services()
        store = current.store
        settings = current.settings.load()
        # One instant for the whole list, even if a refresh boundary is crossed
        # while reading different plans. Other handlers keep their own clock.
        now = datetime.now(timezone.utc).astimezone()
        hour, minute = map(int, settings['refresh_time'].split(':'))
        refresh = time(hour, minute)
        today = study_day(now, refresh, now.tzinfo)
        items = []
        for plan in store.list_plans(include_archived=bool(params.get("include_archived"))):
            item = {"id": plan["id"], "title": plan.get("title") or "", "goal": plan.get("goal", ""), "final_goal": plan.get("final_goal", ""),
                    "state": plan.get("state"), "archived": plan.get("archived", False),
                    "module_count": plan.get("module_count", 0), "window": plan.get("window"),
                    "total_minutes": plan.get("total_minutes", 0), "today": None}
            if plan.get("window") and plan.get("state") in ("active", "checking"):
                try:
                    item["today"] = store.today_status(plan['id'], now=now, refresh=refresh, tz=now.tzinfo)
                except Exception:  # noqa: BLE001 - one broken plan must not hide the others
                    item["today"] = None
            items.append(item)
        return {"plans": items, "study_day": today.isoformat()}

    bridge.register("plans.list", plan_list)

    from . import chat
    chat.register(bridge, services)
    from . import rag
    rag.register(bridge, services, window)
    from ..course import self_test
    self_test.register(bridge, services, window)

    from . import plan_flow
    plan_flow.register(bridge, services, window, _local_study_day, fetcher=getattr(window, "research_fetcher", None))

    from . import classroom
    def presence(active, plan_id=None):
        reminder = getattr(window, "reminder", None)  # no reminder is due while in class (SYS-04)
        if reminder is not None:
            reminder.class_started(plan_id) if active else reminder.class_ended()
    # Classroom calls study_day() with no arguments (as the simulator wires it);
    # passing the settings-taking helper itself made entering a class fail.
    classroom.register(bridge, services, lambda: _local_study_day(services().settings.load()), presence=presence)
    from ..connections.balance import register as register_balance
    register_balance(bridge, services)
