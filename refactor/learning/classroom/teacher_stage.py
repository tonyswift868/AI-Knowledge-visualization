"""Offline Live2D stage used by the native classroom.

The stage is deliberately a small, local WebEngine surface.  Its scheme
handler serves the already checked-in classroom bundle and the existing public
teacher files; it never starts the old web server or carries account/session
credentials into JavaScript.
"""
from __future__ import annotations

import json
import mimetypes
import urllib.error
import zipfile
from pathlib import Path
from urllib.parse import unquote

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QUrl, Qt, Signal
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget, QFileDialog
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlSchemeHandler,
    QWebEngineUrlRequestJob,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from refactor.desktop.web_schemes import (
    TEACHER_STAGE_SCHEME as _SCHEME_NAME,
    register_teacher_stage_scheme as _register_scheme,
)
from .live2d_assets import (configured_download, download_configured_asset,
                            inspect_live2d_assets, saved_asset_root, save_asset_root)


_MODULE_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ASSET_ROOT = _MODULE_ROOT / "frontend" / "public"
_STAGE_ROOT = Path(__file__).resolve().parent / "stage" / "dist"


def _safe_child(root: Path, relative: str) -> Path | None:
    try:
        candidate = (root / relative.lstrip("/")).resolve()
        if candidate == root.resolve() or root.resolve() not in candidate.parents:
            return None
        return candidate
    except (OSError, RuntimeError, ValueError):
        return None


def _mime(path: Path) -> str:
    known = {
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".css": "text/css",
        ".html": "text/html",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".moc3": "application/octet-stream",
        ".model3": "application/json",
        ".json": "application/json",
    }
    return known.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class _StageSchemeHandler(QWebEngineUrlSchemeHandler):
    """Serve only the stage bundle and explicitly selected teacher assets."""

    def __init__(self, stage_root: Path, asset_root: Path, parent=None):
        super().__init__(parent)
        self.stage_root = stage_root.resolve()
        self.asset_root = asset_root.resolve()
        self._buffers: list[QBuffer] = []

    def _asset_path(self, relative: str) -> Path | None:
        relative = relative.lstrip("/")
        if relative.startswith("vendor/live2d/"):
            target = _safe_child(self.asset_root, relative)
            # The model package belongs to the user; the renderer runtime is
            # an application dependency and may remain in the installed app.
            # This keeps a manually selected model directory small and local.
            return target if target is not None and target.is_file() else _safe_child(_DEFAULT_ASSET_ROOT, relative)
        if relative.startswith("teacher2d/"):
            target = _safe_child(self.asset_root, relative)
            if target is not None and target.is_file():
                return target
            # A manually selected package can be the model directory itself.
            # Map the known teacher package tail into that directory while
            # keeping all paths contained by the selected root.
            prefix = "teacher2d/tsubaki-live2d/"
            if relative.startswith(prefix):
                direct = _safe_child(self.asset_root, relative[len(prefix):])
                if direct is not None and direct.is_file():
                    return direct
            return target
        if relative.startswith("tsubaki-live2d/"):
            return _safe_child(self.asset_root / "teacher2d", relative)
        return None

    def _payload(self, url: QUrl) -> tuple[bytes, str] | None:
        host = url.host()
        relative = unquote(url.path()).lstrip("/")
        if host == "stage":
            target = self._asset_path(relative) if relative.startswith(("teacher2d/", "vendor/live2d/")) else _safe_child(self.stage_root, relative or "index.html")
            if target is None or not target.is_file():
                return None
            return target.read_bytes(), _mime(target)
        if host != "assets":
            return None
        target = self._asset_path(relative)
        if target is None or not target.is_file():
            return None
        if relative == "tsubaki-live2d/manifest.json":
            value = json.loads(target.read_text(encoding="utf-8"))
            base = "mentorflow://assets/tsubaki-live2d/"
            value["skeleton"] = base + "tsubaki.web.model3.json"
            value["textures"] = [
                base + "椿.4096/texture_00.png",
                base + "椿.4096/texture_01.png",
                base + "椿.4096/texture_02.png",
            ]
            value.setdefault("live2d", {})["coreScript"] = "mentorflow://assets/vendor/live2d/live2dcubismcore.min.js"
            return json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json"
        return target.read_bytes(), _mime(target)

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802
        try:
            payload = self._payload(job.requestUrl())
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            payload = None
        if payload is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        data, content_type = payload
        buffer = QBuffer(job)
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._buffers.append(buffer)
        buffer.destroyed.connect(lambda _=None, item=buffer: self._discard(item))
        job.reply(QByteArray(content_type.encode("ascii")), buffer)

    def _discard(self, item: QBuffer) -> None:
        try:
            self._buffers.remove(item)
        except ValueError:
            pass


class TeacherStagePanel(QFrame):
    """Right-side panel with a lazy, disposable local Live2D WebEngine."""

    runtime_changed = Signal(str)
    collapsed_changed = Signal(bool)
    asset_root_changed = Signal(str)

    def __init__(self, asset_root=None, parent=None):
        super().__init__(parent)
        self.setObjectName("teacherStagePanel")
        self._asset_root = Path(asset_root or saved_asset_root() or _DEFAULT_ASSET_ROOT)
        self._web: QWebEngineView | None = None
        self._profile: QWebEngineProfile | None = None
        self._handler: _StageSchemeHandler | None = None
        self._loaded = False
        self._disposed = False
        self._collapsed = False
        self._pending_state = "idle"
        self._pending_theme = "sakura"
        self._pending_reply = ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        title = QLabel("Live2D 教师")
        title.setObjectName("teacherStageTitle")
        header.addWidget(title)
        self.status = QLabel("未打开")
        self.status.setObjectName("teacherStageStatus")
        header.addWidget(self.status)
        header.addStretch()
        self.choose_button = QPushButton("选择模型目录")
        self.choose_button.setAccessibleName("选择 Live2D 模型目录")
        self.choose_button.clicked.connect(self.choose_asset_directory)
        header.addWidget(self.choose_button)
        self.download_button = QPushButton("下载模型")
        self.download_button.setAccessibleName("下载 Live2D 模型")
        self.download_button.clicked.connect(self.download_model)
        # Keep the action visible even when the catalog has no trusted
        # download descriptor.  Hiding it made a missing model look like a
        # UI omission and gave the learner no indication that an explicit
        # local directory can be selected.  A download is enabled only when
        # the catalog supplies a verified URL/hash/size; never invent a URL.
        self._refresh_download_action()
        header.addWidget(self.download_button)
        self.collapse_button = QPushButton("收起教师栏")
        self.collapse_button.setAccessibleName("收起教师栏")
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        header.addWidget(self.collapse_button)
        layout.addLayout(header)
        self.placeholder = QLabel("点击“显示 Live2D 教师”打开本地教师舞台。\n文字课堂可独立使用。")
        self.placeholder.setObjectName("teacherStagePlaceholder")
        self.placeholder.setWordWrap(True)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.placeholder, 1)
        self.setMinimumWidth(280)

    def _refresh_download_action(self) -> None:
        """Reflect whether this catalog entry can be downloaded safely."""
        available = configured_download(self._asset_root) is not None
        self.download_button.setVisible(True)
        self.download_button.setEnabled(available and not self._disposed)
        self.download_button.setToolTip(
            "选择安装目录并下载已校验的模型资源。"
            if available else
            "当前模型清单没有可信下载地址；请使用“选择模型目录”指向已下载的模型。"
        )

    def choose_asset_directory(self) -> bool:
        """Let the user point at an installed model package on this device."""
        selected = QFileDialog.getExistingDirectory(self, "选择已下载的 Live2D 模型目录", str(self._asset_root))
        if not selected:
            return False
        root = save_asset_root(Path(selected))
        status = inspect_live2d_assets(root)
        if not status.available:
            self.status.setText(status.code)
            self.placeholder.setText(status.message + "\n请选择包含 teacher2d/tsubaki-live2d 的目录。")
            self.asset_root_changed.emit(str(root))
            return False
        self.set_asset_root(root)
        self.asset_root_changed.emit(str(root))
        self.status.setText("模型目录已保存；点击打开教师舞台。")
        self.placeholder.setText("模型已就绪，点击打开教师舞台。")
        return True

    def download_model(self) -> bool:
        config = configured_download(self._asset_root)
        if config is None:
            self.status.setText("没有配置可信的模型下载地址，请选择模型目录。")
            self._refresh_download_action()
            return False
        selected = QFileDialog.getExistingDirectory(self, "选择模型安装目录", str(self._asset_root))
        if not selected:
            return False
        try:
            root = download_configured_asset(config, Path(selected))
            save_asset_root(root)
            self.set_asset_root(root)
        except (OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile) as error:
            self.status.setText(f"模型下载失败：{error}")
            return False
        self.status.setText("模型已下载并校验；点击打开教师舞台。")
        self.placeholder.setText("模型已就绪，点击打开教师舞台。")
        self._refresh_download_action()
        return True

    @property
    def web(self):
        return self._web

    @property
    def profile(self):
        return self._profile

    def _ensure_web(self) -> QWebEngineView | None:
        if self._disposed:
            return None
        if self._web is not None:
            return self._web
        if not _STAGE_ROOT.is_dir():
            self.status.setText("舞台 bundle 缺失")
            self.placeholder.setText("本地教师舞台暂不可用；文字课堂可继续使用。")
            return None
        asset_status = inspect_live2d_assets(self._asset_root)
        if not asset_status.available:
            self.status.setText(asset_status.code)
            self.placeholder.setText(asset_status.message + "\n可选择已下载的模型目录；当前未配置可验证的下载地址。")
            return None
        try:
            _register_scheme()
            profile = QWebEngineProfile(self)
            profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
            profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
            handler = _StageSchemeHandler(_STAGE_ROOT, self._asset_root, profile)
            profile.installUrlSchemeHandler(_SCHEME_NAME, handler)
            web = QWebEngineView(self)
            page = QWebEnginePage(profile, web)
            web.setPage(page)
            web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
            web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
            web.loadFinished.connect(self._loaded_result)
            page.titleChanged.connect(self._title_changed)
            self.layout().addWidget(web, 1)
            self._profile, self._handler, self._web = profile, handler, web
            self.placeholder.hide()
            web.load(QUrl("mentorflow://stage/index.html"))
            return web
        except Exception as error:
            self.status.setText("舞台不可用")
            self.placeholder.setText(f"本地教师舞台加载失败：{error}\n文字课堂可继续使用。")
            return None

    def open_stage(self) -> bool:
        if self._disposed:
            return False
        self.show()
        self._collapsed = False
        self.placeholder.hide()
        self.collapse_button.setText("收起教师栏")
        self.collapse_button.setAccessibleName("收起教师栏")
        web = self._ensure_web()
        if web is None:
            self.placeholder.show()
            return False
        web.show()
        self.status.setText("正在加载")
        self._post("mentorflow.teacher-theme", {"theme": self._pending_theme})
        self._post("mentorflow.teacher-state", {"state": self._pending_state})
        self._post("mentorflow.teacher-reply", {"text": self._pending_reply})
        # An existing stage does not emit titleChanged again when reopened.
        self._title_changed(web.title())
        return True

    def close_stage(self) -> None:
        if self._disposed:
            return
        self.hide()
        if self._web is not None:
            self._web.hide()
        self._collapsed = False
        self.collapse_button.setText("收起教师栏")
        self.collapse_button.setAccessibleName("收起教师栏")
        self.status.setText("未打开")

    def set_asset_root(self, asset_root=None) -> None:
        root = Path(asset_root or saved_asset_root() or _DEFAULT_ASSET_ROOT)
        if root == self._asset_root:
            return
        if self._web is not None:
            self._web.stop()
            self._web.page().setUrl(QUrl("about:blank"))
            self._web.deleteLater()
            self._profile and self._profile.deleteLater()
            self._web = None
            self._profile = None
            self._handler = None
            self._loaded = False
        self._asset_root = root
        self._refresh_download_action()

    def toggle_collapsed(self) -> None:
        if self._disposed:
            return
        self._collapsed = not self._collapsed
        if self._web is not None:
            self._web.setVisible(not self._collapsed)
        self.collapse_button.setText("展开教师栏" if self._collapsed else "收起教师栏")
        self.collapse_button.setAccessibleName("展开教师栏" if self._collapsed else "收起教师栏")
        if self._collapsed:
            self.status.setText("已收起")
        elif self._web is not None:
            self._title_changed(self._web.title())
        self.collapsed_changed.emit(self._collapsed)

    def set_state(self, state: str) -> None:
        self._pending_state = str(state or "idle")
        self._post("mentorflow.teacher-state", {"state": self._pending_state})

    def set_theme(self, theme: str) -> None:
        self._pending_theme = str(theme or "sakura")
        self._post("mentorflow.teacher-theme", {"theme": self._pending_theme})

    def set_reply(self, text: str) -> None:
        """Show the latest generated teacher response in the detached stage."""
        self._pending_reply = str(text or "").strip()[:4000]
        self._post("mentorflow.teacher-reply", {"text": self._pending_reply})

    def _post(self, message_type: str, payload: dict) -> None:
        if self._web is None or not self._loaded or self._disposed:
            return
        message = json.dumps({"type": message_type, **payload}, ensure_ascii=False)
        self._web.page().runJavaScript(f"window.postMessage({message}, '*');")

    def _loaded_result(self, ok: bool) -> None:
        if self._disposed:
            return
        self._loaded = bool(ok)
        if ok:
            self.status.setText("正在加载")
            self._post("mentorflow.teacher-theme", {"theme": self._pending_theme})
            self._post("mentorflow.teacher-state", {"state": self._pending_state})
            self._post("mentorflow.teacher-reply", {"text": self._pending_reply})
        else:
            self.status.setText("舞台加载失败")
            self.placeholder.setText("教师舞台资源暂不可用；文字课堂可继续使用。")

    def _title_changed(self, title: str) -> None:
        if self._disposed:
            return
        if title == "ready":
            self.status.setText("就绪")
            self.runtime_changed.emit("ready")
        elif title == "error":
            self.status.setText("资源不可用")
            self.runtime_changed.emit("error")
        elif title == "loading":
            self.status.setText("正在加载")

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if hasattr(self, "download_button"):
            self.download_button.setEnabled(False)
        self._loaded = False
        if self._web is not None:
            self._web.stop()
            self._web.page().setUrl(QUrl("about:blank"))
            self._web.deleteLater()
        if self._profile is not None:
            self._profile.deleteLater()
        self._web = None
        self._profile = None
        self._handler = None


class TeacherStageWindow(QWidget):
    """A reusable, non-topmost companion window hosting one teacher stage."""

    closed = Signal()

    def __init__(self, parent=None):
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        super().__init__(parent, flags)
        self.setObjectName("teacherStageWindow")
        self.setWindowTitle("Live2D 教师")
        self.setMinimumSize(320, 360)
        self.resize(420, 620)
        self._disposed = False
        self._open = False
        self._positioned = False
        self._closing = False
        self._anchor: QWidget | None = None
        self._anchor_geometry = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.stage = TeacherStagePanel(parent=self)
        layout.addWidget(self.stage, 1)
        self.stage.collapsed_changed.connect(self._stage_collapsed)

    def _available_geometry(self, anchor: QWidget | None):
        screen = anchor.screen() if anchor is not None else None
        if screen is None and anchor is not None:
            center = anchor.mapToGlobal(anchor.rect().center())
            screen = QApplication.screenAt(center)
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _place_next_to(self, anchor: QWidget | None) -> None:
        anchor = anchor.window() if anchor is not None else self._anchor
        available = self._available_geometry(anchor)
        if available is None:
            return
        width = min(self.width(), available.width())
        anchor_height = anchor.frameGeometry().height() if anchor is not None else self.height()
        height = min(anchor_height, available.height())
        self.resize(width, height)
        # ``resize`` uses the client rectangle while the anchor comparison
        # uses its native frame.  Match the outer heights after the tool frame
        # has been realized, keeping the teacher flush with the main window.
        frame = self.frameGeometry()
        frame_width_extra = frame.width() - self.width()
        frame_height_extra = frame.height() - self.height()
        if frame_width_extra or frame_height_extra:
            self.resize(max(1, width - frame_width_extra), max(1, height - frame_height_extra))
        # open_stage() has already shown the window, so this includes the
        # native title bar and resize border rather than only the client area.
        frame = self.frameGeometry()
        frame_width, frame_height = frame.width(), frame.height()
        if anchor is None:
            x = available.left() + max(0, (available.width() - frame_width) // 2)
            y = available.top() + max(0, (available.height() - frame_height) // 2)
        else:
            anchor_frame = anchor.window().frameGeometry()
            x = anchor_frame.right() + 1
            # Keep the detached teacher on the screen's right side when the
            # main window already reaches that edge; overlap is preferable to
            # moving the teacher to the main window's left side.
            if x + frame_width > available.right() + 1:
                x = available.right() - frame_width + 1
            y = anchor_frame.top()
        x = max(available.left(), min(x, available.right() - frame_width + 1))
        y = max(available.top(), min(y, available.bottom() - frame_height + 1))
        self.move(x, y)
        # QWidget.move() uses the client origin on some platforms.  Correct
        # any remaining frame offset, then clamp once more using the native
        # frame so no title-bar pixels escape the work area.
        frame = self.frameGeometry()
        self.move(self.x() + x - frame.left(), self.y() + y - frame.top())
        frame = self.frameGeometry()
        dx = max(available.left() - frame.left(), min(available.right() - frame.right(), 0))
        dy = max(available.top() - frame.top(), min(available.bottom() - frame.bottom(), 0))
        if dx or dy:
            self.move(self.x() + dx, self.y() + dy)
        self._anchor_geometry = anchor.frameGeometry() if anchor is not None else None

    def _sync_to_anchor(self) -> None:
        if self._disposed or not self._open or self._anchor is None:
            return
        self._place_next_to(self._anchor)

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self._anchor and event.type() in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.WindowStateChange,
        }:
            self._sync_to_anchor()
        return super().eventFilter(watched, event)

    def open_stage(self, anchor: QWidget | None = None) -> bool:
        if self._disposed:
            return False
        if anchor is not None:
            target = anchor.window()
            if target is not self._anchor:
                if self._anchor is not None:
                    self._anchor.removeEventFilter(self)
                self._anchor = target
                self._anchor.installEventFilter(self)
        if not self._positioned:
            self._open = True
            self.show()
            self._place_next_to(self._anchor)
            self._positioned = True
        else:
            self._open = True
            self.show()
            if self._anchor is not None and self._anchor.frameGeometry() != self._anchor_geometry:
                self._sync_to_anchor()
        self.raise_()
        return self.stage.open_stage()

    def close_stage(self) -> None:
        if self._disposed:
            return
        was_open = self._open or self.isVisible()
        self._open = False
        self.stage.close_stage()
        self.hide()
        if was_open and not self._closing:
            self.closed.emit()

    def _stage_collapsed(self, collapsed: bool) -> None:
        if collapsed:
            self.close_stage()

    def closeEvent(self, event):  # noqa: N802
        event.ignore()
        self.close_stage()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._closing = True
        self._open = False
        if self._anchor is not None:
            try:
                self._anchor.removeEventFilter(self)
            except RuntimeError:
                pass
            self._anchor = None
        try:
            self.stage.collapsed_changed.disconnect(self._stage_collapsed)
        except (RuntimeError, TypeError):
            pass
        self.stage.dispose()
        self.hide()
        self._closing = False


__all__ = ["TeacherStagePanel", "TeacherStageWindow"]
