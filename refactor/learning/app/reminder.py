"""Account-scoped learning reminders for the running learning window.

The clock owns wall-time policy (refresh boundary, sleep/clock changes, one
reminder per study day and the in-class hold). This module only supplies the
small local query used by that clock and delivers one Windows tray notification
for plans that have work today, no allocated progress, and no actual learning
activity today (including activity that only paid past debt or was revoked).
It has no process, startup task, persistence or network dependency.

The public integration seam is deliberately small:

``settings_changed(settings)`` / ``apply_settings(settings)``
    Call after ``AppSettings.save``. Changes are applied synchronously.
``class_started()`` and ``class_ended()``
    Call when the classroom enters/leaves. An ended class immediately gives a
    due reminder its first safe opportunity.
``dispose()``
    Stops the in-process clock and notification resources.

``notifier`` is injectable for offline tests. A production instance uses a
``QSystemTrayIcon`` message; clicking it restores the window and selects the
home navigation item without sending any course data.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, tzinfo
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon

from ..services.daily_clock import DailyClock
from .settings import AppSettings, DEFAULTS, validate

log = logging.getLogger(__name__)


def _clock_time(value: str, name: str) -> time:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be HH:MM")
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


class _SystemNotifier:
    """Small Qt tray adapter; kept separate so tests never need a tray server."""

    def __init__(self, owner: QObject, window=None):
        self._owner = owner
        self._window = window
        self._tray = None
        self._clicked = None

    def _click(self):
        if self._clicked is not None:
            self._clicked()

    def notify(self, title: str, message: str, clicked: Callable[[], None]) -> bool:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.warning("system tray is unavailable; learning reminder was not shown")
            return False
        if self._tray is None:
            self._tray = QSystemTrayIcon(self._window or self._owner)
            if self._window is not None:
                icon = self._window.windowIcon()
                if not icon.isNull():
                    self._tray.setIcon(icon)
            if self._tray.icon().isNull():
                pixmap = QPixmap(16, 16)
                pixmap.fill(QColor("#d65a86"))
                self._tray.setIcon(QIcon(pixmap))
            self._tray.messageClicked.connect(self._click)
            self._tray.setToolTip("MentorFlow 学习提醒")
        self._clicked = clicked
        self._tray.setVisible(True)
        self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 12_000)
        return True

    __call__ = notify

    def close(self) -> None:
        if self._tray is not None:
            self._clicked = None
            self._tray.hide()
            self._tray.deleteLater()
            self._tray = None


class ReminderService(QObject):
    """Connect account settings and course progress to :class:`DailyClock`."""

    reminder_sent = Signal(str, str)
    study_day_changed = Signal(object)
    _settings_requested = Signal(object)
    _class_requested = Signal(bool)

    def __init__(self, account_id: str, data_root: Path, store_provider: Callable[[], object | None],
                 *, window=None, parent=None, settings: dict | None = None,
                 now: Callable[[], datetime] | None = None, timezone: tzinfo | str | None = None,
                 notifier: Callable[[str, str, Callable[[], None]], bool] | None = None):
        super().__init__(parent)
        self.account_id = account_id
        self.data_root = Path(data_root)
        self._store_provider = store_provider
        self._window = window
        self._in_class = False
        self._disposed = False
        self._candidate_plans = []
        self._candidate_day = None
        self._notifier = notifier or _SystemNotifier(self, window)
        if settings is None:
            settings = AppSettings(account_id, self.data_root).load()
        try:
            current = validate(settings)
        except Exception:  # damaged settings fall back to the same defaults as AppSettings.load
            current = dict(DEFAULTS)
        self._settings = current
        self._settings_requested.connect(self._apply_settings, Qt.ConnectionType.QueuedConnection)
        self._class_requested.connect(self._set_class_state, Qt.ConnectionType.QueuedConnection)

        self.clock = DailyClock(
            self, refresh=_clock_time(current["refresh_time"], "refresh"),
            timezone=timezone, reminder=_clock_time(current["reminder_time"], "reminder")
            if current["reminder_enabled"] else None,
            today_unfinished=self._today_unfinished, in_class=lambda: self._in_class,
            now=now,
        )
        self.clock.reminder_due.connect(self._deliver)
        self.clock.study_day_changed.connect(self.study_day_changed)

    @property
    def in_class(self) -> bool:
        return self._in_class

    @property
    def settings(self) -> dict:
        return dict(self._settings)

    def _unfinished_plans(self) -> list[str]:
        try:
            store = self._store_provider()
        except Exception:  # noqa: BLE001 - a closed account has no reminder work
            log.debug("course store unavailable while checking reminder", exc_info=True)
            return []
        if store is None:
            return []
        try:
            settings = self._settings
            refresh = _clock_time(settings["refresh_time"], "refresh")
            instant = self.clock_now()
            local = instant.astimezone(self.clock.timezone)
            values = []
            for plan in store.list_plans():
                if plan.get("state") not in ("active", "checking") or not plan.get("window"):
                    continue
                try:
                    status = store.today_status(plan["id"], now=instant, refresh=refresh, tz=local.tzinfo)
                except Exception:  # noqa: BLE001 - one malformed plan must not suppress other plans
                    log.warning("could not read today's status for plan %s", plan.get("id"), exc_info=True)
                    continue
                if (status.get("today_target", 0) > 0 and status.get("today_confirmed", 0) == 0
                        and status.get("today_tentative", 0) == 0
                        and not store.has_study_activity(plan["id"], day=self.clock.current_study_day,
                                                         refresh=refresh, tz=local.tzinfo)):
                    label = (plan.get("goal") or plan.get("final_goal") or "未命名计划").strip()
                    label = " ".join(label.split())
                    values.append(label[:80])
            return values
        except Exception:  # noqa: BLE001 - reminders are optional and must not break learning
            log.warning("could not enumerate plans for reminder", exc_info=True)
            return []

    def clock_now(self) -> datetime:
        """Return a coherent instant for stores; replaceable in deterministic tests."""
        source = getattr(self.clock, "_now", None)
        return source() if callable(source) else datetime.now().astimezone()

    def _today_unfinished(self) -> bool:
        self._candidate_day = self.clock.current_study_day
        self._candidate_plans = self._unfinished_plans()
        return bool(self._candidate_plans)

    def _deliver(self) -> None:
        if self._disposed:
            return
        plans = list(self._candidate_plans)
        if self._candidate_day != self.clock.current_study_day:
            plans = self._unfinished_plans()
        self._candidate_plans = []
        if not plans:
            return
        if len(plans) == 1:
            detail = plans[0]
        elif len(plans) == 2:
            detail = "、".join(plans)
        else:
            detail = "、".join(plans[:2]) + f" 等 {len(plans)} 个计划"
        title = "该开始今天的学习了"
        message = f"今天有学习任务尚未开始：{detail}。打开 MentorFlow 开始学习。"
        self.reminder_sent.emit(title, message)
        try:
            notify = getattr(self._notifier, "notify", self._notifier)
            notify(title, message, self._open_home)
        except Exception:  # noqa: BLE001 - a notification failure cannot break the clock
            log.warning("learning reminder notification failed", exc_info=True)

    def _open_home(self) -> None:
        if self._window is None:
            return
        try:
            self._window.showNormal()
            self._window.raise_()
            self._window.activateWindow()
            bridge = getattr(self._window, "bridge", None)
            if bridge is not None:
                bridge.push("navigate", {"route": "home"})
        except (AttributeError, RuntimeError):
            log.debug("learning window was already closing", exc_info=True)

    def settings_changed(self, settings: dict) -> None:
        """Apply an already-saved settings snapshot immediately on the GUI thread."""
        if self._disposed:
            return
        current = validate(settings)
        if QThread.currentThread() != self.thread():
            self._settings_requested.emit(current)
            return
        self._apply_settings(current)

    def _apply_settings(self, current: dict) -> None:
        if self._disposed:
            return
        self._settings = current
        self.clock.configure(
            refresh=_clock_time(current["refresh_time"], "refresh"),
            reminder=_clock_time(current["reminder_time"], "reminder") if current["reminder_enabled"] else None,
        )

    apply_settings = settings_changed

    def class_started(self) -> None:
        if not self._disposed:
            if QThread.currentThread() != self.thread():
                self._class_requested.emit(True)
            else:
                self._set_class_state(True)

    def class_ended(self) -> None:
        if not self._disposed:
            if QThread.currentThread() != self.thread():
                self._class_requested.emit(False)
            else:
                self._set_class_state(False)

    def _set_class_state(self, active: bool) -> None:
        if self._disposed:
            return
        self._in_class = active
        if not active:
            self.clock.check_now()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self.clock.dispose()
        # The window owns this QObject, but explicitly queue its child clock for
        # deletion so a closed account cannot leave a QTimer behind while the
        # next account/window is being constructed.
        for signal, slot in ((self.clock.reminder_due, self._deliver),
                             (self.clock.study_day_changed, self.study_day_changed),
                             (self._settings_requested, self._apply_settings),
                             (self._class_requested, self._set_class_state)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self.clock.deleteLater()
        close = getattr(self._notifier, "close", None)
        if callable(close):
            close()


def register(window):
    """Create an account-window service with a lazy, shared CourseStore."""
    return ReminderService(
        window._account_id, window._data_root,
        lambda: window.services.store if window.services is not None else None,
        window=window, parent=window,
    )
