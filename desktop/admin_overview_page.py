"""SUPER_ADMIN-only aggregate account overview page."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .api import ApiError


def admin_role_visible(user, transport) -> bool:
    """UI gate mirrors the server role and requires an authenticated transport."""
    value = user or {}
    return (str(value.get("role") or value.get("application_role") or "").upper()
            == "SUPER_ADMIN" and transport is not None)


class AdminOverviewPage(QWidget):
    REFRESH_SECONDS = 60

    def __init__(self, api):
        super().__init__()
        self.api = api
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mentorflow-admin-overview")
        self.future: Future | None = None
        self.labels: dict[str, QLabel] = {}
        root = QVBoxLayout(self)
        title = QLabel("管理员概览")
        title.setObjectName("pageHeading")
        root.addWidget(title)
        self.status = QLabel("正在读取…")
        self.status.setObjectName("inlineStatus")
        root.addWidget(self.status)
        grid = QGridLayout()
        fields = (
            ("registered_human_users", "注册用户"), ("registrations_today", "今日新增"),
            ("registrations_7d", "近 7 日新增"), ("registrations_30d", "近 30 日新增"),
            ("enabled_users", "启用账号"), ("disabled_users", "禁用账号"),
            ("email_verified_users", "邮箱已验证"), ("email_unverified_users", "邮箱未验证"),
            ("required_action_users", "待处理操作"), ("backend_account_count", "Backend 账号"),
            ("workspace_member_count", "Workspace 成员"),
            ("identity_account_mismatch_count", "身份不一致"),
        )
        for index, (key, caption) in enumerate(fields):
            cell = QVBoxLayout()
            cell.addWidget(QLabel(caption))
            value = QLabel("—")
            value.setObjectName("metricValue")
            cell.addWidget(value)
            self.labels[key] = value
            grid.addLayout(cell, index // 4, index % 4)
        root.addLayout(grid)
        root.addWidget(QLabel("服务状态"))
        for name in ("keycloak", "backend", "database", "email"):
            value = QLabel(f"{name}: —")
            self.labels["service_" + name] = value
            root.addWidget(value)
        actions = QPushButton("立即刷新")
        actions.clicked.connect(self.refresh)
        root.addWidget(actions)
        self.last_success = ""
        self.timer = QTimer(self)
        self.timer.setInterval(self.REFRESH_SECONDS * 1000)
        self.timer.timeout.connect(self.refresh)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self.timer.start()

    def hideEvent(self, event) -> None:
        self.timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        if self.future is not None and not self.future.done():
            return
        self.future = self.executor.submit(self.api.admin_overview)
        QTimer.singleShot(20, self._poll)

    def _poll(self) -> None:
        future = self.future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(40, self._poll)
            return
        self.future = None
        try:
            payload = future.result()
            for key in self.labels:
                if key.startswith("service_"):
                    name = key[8:]
                    service = payload.get("services", {}).get(name, {})
                    status = {'healthy': '正常', 'unavailable': '不可用', 'unknown': '尚未观测'}.get(service.get('status'), '尚未观测')
                    self.labels[key].setText(f"{name}: {status}（{service.get('code', 'NOT_OBSERVED')}）")
                elif key in payload:
                    self.labels[key].setText('—' if payload[key] is None else str(payload[key]))
            if payload.get('data_status') == 'current':
                self.last_success = str(payload.get("refreshed_at") or "")
                self.status.setText(f"最后刷新：{self.last_success}")
            else:
                code = payload.get('identity_statistics', {}).get('code', 'AGGREGATE_UNAVAILABLE')
                self.status.setText(f"部分统计暂不可用（{code}）；观测时间：{payload.get('observed_at', '—')}")
        except Exception as exc:
            detail = exc.code if isinstance(exc, ApiError) else type(exc).__name__
            suffix = f"；最后成功：{self.last_success}" if self.last_success else ""
            self.status.setText(f"数据暂不可用（stale/unavailable：{detail}）{suffix}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.timer.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)


__all__ = ["AdminOverviewPage", "admin_role_visible"]
