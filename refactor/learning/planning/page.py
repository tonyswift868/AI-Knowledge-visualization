"""Offline bundled creation wizard backed by the shared account-local service."""
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .service import PlanService
from .wizard import PlanningWebView


class PlanningPage(QWidget):
    plan_saved = Signal(object)

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.service = context.get("plan_service")
        if self.service is None:
            self.service = PlanService(context)
            context["plan_service"] = self.service
        if self.service.account_id != context["account_id"]:
            raise ValueError("Plan service account mismatch")
        self._disposed = False
        self._bundle_root = Path(context.get("planning_bundle_root") or (Path(__file__).parent / "web" / "dist"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.web = PlanningWebView(self, self.service, self._bundle_root, self)
        self.bridge = self.web.bridge
        self.bridge.plan_saved.connect(self.plan_saved.emit)
        layout.addWidget(self.web)
        if not (self._bundle_root / "index.html").is_file():
            # A missing bundle is an installation problem; never silently
            # replace it with the retired native form or claim it loaded.
            notice = QLabel("计划页面资源暂不可用，请重新安装本地界面资源。")
            notice.setObjectName("planningBundleUnavailable")
            notice.setWordWrap(True)
            layout.addWidget(notice)

    @property
    def busy(self):
        return self.bridge.busy

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        # ``create_page`` keeps the prewarmed page in the shared feature
        # context so a queued navigation callback can reuse it.  Clear that
        # cache when the page is disposed; otherwise a rebind can hand a
        # disposed WebEngine page back to the new account scope.
        context = getattr(self, "_feature_context", None)
        if isinstance(context, dict) and context.get("_planning_page") is self:
            context.pop("_planning_page", None)
        self.web.dispose()
        self.setEnabled(False)

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)


def create_page(context, parent=None):
    # Navigation normally caches feature pages in the shell, but callers can
    # also invoke the factory directly (for example a queued navigation
    # callback racing the first click).  Keep the account context as the
    # authoritative owner so that a second callback reuses the live page
    # instead of constructing another WebEngine/profile pair.
    existing = context.get("_planning_page")
    if isinstance(existing, PlanningPage) and not existing._disposed:
        return existing
    page = PlanningPage(context, parent)
    page._feature_context = context
    context["_planning_page"] = page
    return page
