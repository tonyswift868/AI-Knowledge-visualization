"""Native teacher-led classroom UI using structured TeacherTurnV1 data."""

from __future__ import annotations

import html
import os
import re
import time
import uuid
import wave
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QTimer, Qt, QUrl
from PySide6.QtGui import QTextCursor
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .api import ApiError, DesktopApi


BLOCK_LABELS = {
    "objective": "学习目标",
    "explanation": "核心讲解",
    "analogy": "结构类比",
    "worked_example": "完整示例",
    "correction": "针对性纠错",
    "alternate_example": "另一示例",
    "prerequisite": "前置补救",
}


def teacher_turn_text(turn: dict[str, Any]) -> str:
    lines: list[str] = []
    concept = turn.get("concept") or {}
    concept_name = concept.get("name") if isinstance(concept, dict) else concept
    if concept_name:
        lines.extend([f"当前概念：{concept_name}", ""])
    for block in turn.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "内容")
        title = str(block.get("title") or BLOCK_LABELS.get(block_type) or block_type)
        content = str(block.get("content") or block.get("text") or "").strip()
        if content:
            lines.extend([f"【{title}】", content, ""])
    check = turn.get("check") or {}
    if isinstance(check, dict) and check.get("prompt"):
        lines.extend(["【最小检查】", str(check["prompt"]), ""])
    expected = turn.get("expected_learner_action") or turn.get("expected_action")
    if expected:
        lines.append("请你做：" + str(expected))
    return "\n".join(lines).strip()


def session_transcript(session: dict[str, Any]) -> str:
    rendered: list[str] = []
    for message in session.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = "教师" if message.get("role") == "teacher" else "你"
        turn = message.get("teacher_turn")
        content = teacher_turn_text(turn) if isinstance(turn, dict) else str(message.get("content") or "")
        rendered.extend([f"──────── {role} ────────", content.strip(), ""])
        attachments = message.get("attachments") or []
        if attachments:
            rendered.append("附件：" + "、".join(str(item.get("original_filename") or item.get("id")) for item in attachments))
            rendered.append("")
    return "\n".join(rendered).strip()


def _safe_link(value: object) -> str:
    raw = str(value or "").strip()
    if raw.startswith(("https://", "http://", "file:///")):
        return raw
    return ""


def _inline_teacher_html(value: object) -> str:
    """Render a small safe Markdown subset; raw HTML is always escaped."""
    text = html.escape(str(value or ""), quote=False)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*|__([^_\n]+)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = re.sub(r"~~([^~\n]+)~~", r"<del>\1</del>", text)
    # Explicit teacher focus markers are emitted by the state machine.
    text = re.sub(r"\[\[重难点[:：]([^\]]+)\]\]", r'<span class="hard-focus">重难点：\1</span>', text)
    text = re.sub(r"\[\[难点[:：]([^\]]+)\]\]", r"<u>难点：\1</u>", text)
    text = re.sub(r"\[\[重点[:：]([^\]]+)\]\]", r"<strong>重点：\1</strong>", text)
    text = re.sub(r"(?<![\w])重难点[:：]([^\n<]+)", r'<span class="hard-focus">重难点：\1</span>', text)
    text = re.sub(r"(?<![\w])难点[:：]([^\n<]+)", r"<u>难点：\1</u>", text)
    text = re.sub(r"(?<![\w])重点[:：]([^\n<]+)", r"<strong>重点：\1</strong>", text)
    links: list[str] = []
    def link_replacement(match: re.Match[str]) -> str:
        label = match.group(1)
        url = _safe_link(html.unescape(match.group(2)))
        if not url:
            return f"{label}（链接不可用）"
        links.append(url)
        return f'<a href="{html.escape(url, quote=True)}">{label}</a>'
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_replacement, text)
    return text.replace("\n", "<br>")


def teacher_turn_html(turn: dict[str, Any]) -> str:
    blocks: list[str] = []
    concept = turn.get("concept") or {}
    name = concept.get("name") if isinstance(concept, dict) else concept
    if name:
        blocks.append(f'<h3 class="concept-heading">当前概念：{html.escape(str(name))}</h3>')
    for block in turn.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "内容")
        title = html.escape(str(block.get("title") or BLOCK_LABELS.get(block_type) or block_type))
        content = str(block.get("content") or block.get("text") or "").strip()
        if not content:
            continue
        if block_type in {"code", "worked_example"} and ("```" in content or block_type == "code"):
            content_html = f"<pre><code>{html.escape(content.replace('```', '').strip())}</code></pre>"
        else:
            content_html = _inline_teacher_html(content)
        blocks.append(f'<section class="teacher-block"><h4>{title}</h4><div>{content_html}</div></section>')
    check = turn.get("check") or {}
    if isinstance(check, dict) and check.get("prompt"):
        blocks.append(f'<section class="teacher-block check-block"><h4>最小检查</h4><div>{_inline_teacher_html(check["prompt"])}</div></section>')
    expected = turn.get("expected_learner_action") or turn.get("expected_action")
    if expected:
        blocks.append(f'<p class="expected-action">请你做：{_inline_teacher_html(expected)}</p>')
    return "".join(blocks) or "<p>教师暂时没有生成可展示的内容。</p>"


def session_transcript_html(session: dict[str, Any]) -> str:
    rendered: list[str] = [
        "<style>"
        "body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;color:#38423d;background:#fffdf9;margin:4px 2px 16px;}"
        ".teacher-message{max-width:92%;margin:10px 0 14px;padding:13px 15px;background:#fff8fb;border:1px solid #edc2d3;border-radius:7px 16px 16px 16px;box-shadow:0 4px 14px #8d3f6218;}"
        ".learner-message{max-width:82%;margin:10px 0 14px auto;padding:11px 14px;background:#e8f3eb;border:1px solid #c7dfcf;border-radius:16px 7px 16px 16px;}"
        ".message-role{font-size:12px;font-weight:800;color:#a94f79;margin-bottom:7px;}"
        ".learner-message .message-role{color:#5b806b;text-align:right;}"
        ".teacher-block{margin:8px 0;padding:9px 11px;background:#fff0f5;border-radius:8px;}"
        ".teacher-block h4{margin:0 0 5px;color:#a94f79;} .concept-heading{color:#8d3f62;}"
        ".hard-focus{color:#b42318;font-weight:700;} .expected-action{font-weight:700;}"
        "a{color:#246b9b;text-decoration:underline;} pre{background:#24333a;color:#f7fbf9;padding:10px;border-radius:8px;white-space:pre-wrap;}"
        "</style>"
    ]
    for message in session.get("messages") or []:
        if not isinstance(message, dict):
            continue
        teacher = message.get("role") == "teacher"
        turn = message.get("teacher_turn")
        body = teacher_turn_html(turn) if isinstance(turn, dict) else _inline_teacher_html(message.get("content") or "")
        # QTextBrowser supports a conservative HTML subset.  Tables with
        # explicit borders/backgrounds are more reliable than CSS bubbles in
        # Qt's rich-text renderer, so keep each turn visually separated even
        # when the native client is using the fallback document engine.
        if teacher:
            rendered.append(
                '<table width="92%" border="1" bordercolor="#edc2d3" '
                'cellspacing="0" cellpadding="10" bgcolor="#fff8fb">'
                '<tr><td><font color="#a94f79"><b>教师：</b></font>'
                f"{body}</td></tr></table>"
            )
        else:
            rendered.append(
                '<table width="100%" border="0" cellspacing="0" cellpadding="0">'
                '<tr><td align="right"><table width="82%" border="1" '
                'bordercolor="#c7dfcf" cellspacing="0" cellpadding="9" bgcolor="#e8f3eb">'
                '<tr><td align="right"><font color="#5b806b"><b>你：</b></font>'
                f"{body}</td></tr></table></td></tr></table>"
            )
    return "".join(rendered) or "<p>进入课堂后，结构化讲解会显示在这里。</p>"


class TeacherStageWindow(QWidget):
    """A non-topmost, frameless sidecar window attached to the main window.

    The stage is deliberately not a child widget of the classroom content.  It
    starts at the host window's right frame edge and mirrors the host's outer
    height, so the 2D teacher has a dedicated portrait canvas without stealing
    width from the conversation.  Move/resize events are observed through an
    event filter; no always-on-top flag is used, so other applications retain
    normal focus behavior.
    """

    def __init__(self, page: "ClassroomPage") -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.page = page
        self.host: QWidget | None = None
        self.setObjectName("teacherStageWindow")
        self.setWindowTitle("MentorFlow · 教师建模")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setMinimumWidth(460)
        self.resize(500, 820)

    def attach_to(self, host: QWidget | None) -> None:
        if self.host is host:
            return
        if self.host is not None:
            self.host.removeEventFilter(self)
        self.host = host
        if self.host is not None:
            self.host.installEventFilter(self)

    def sync_to_host(self) -> None:
        host = self.host
        if host is None or not host.isVisible() or host.isMinimized():
            return
        frame = host.frameGeometry()
        # A tiny overlap avoids a visible seam between the two frameless
        # surfaces while keeping the sidecar anchored to the host boundary.
        x = frame.x() + frame.width() - 4
        width = max(460, self.width())
        self.setGeometry(x, frame.y(), width, frame.height())

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if watched is self.host:
            event_type = event.type()
            if event_type in {QEvent.Type.Move, QEvent.Type.Resize}:
                if self.isVisible():
                    QTimer.singleShot(0, self.sync_to_host)
            elif event_type == QEvent.Type.WindowStateChange:
                if self.host.isMinimized():
                    self.hide()
                elif self.isVisible():
                    QTimer.singleShot(0, self.sync_to_host)
            elif event_type == QEvent.Type.Close:
                self.hide()
        return super().eventFilter(watched, event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()
        self.hide()
        self.page._on_teacher_stage_hidden()


class ClassroomPage(QWidget):
    def __init__(self, api: DesktopApi, provider_session: Callable[[], str], origin: str = "") -> None:
        super().__init__()
        self.api = api
        self.provider_session = provider_session
        self.origin = origin.rstrip("/")
        self.session_id = ""
        self.current_session: dict[str, Any] = {}
        self.pending_attachment_ids: list[str] = []
        self._evidence_html = "<p>当前回合没有可展示的引用。</p>"
        self._teacher_stage_workspace_id = ""
        self._teacher_stage_panel: QFrame | None = None
        self._teacher_stage_panel_layout: QVBoxLayout | None = None
        self._teacher_stage_placeholder: QLabel | None = None
        self._teacher_stage_web: QWebEngineView | None = None
        self._teacher_stage_profile: QWebEngineProfile | None = None
        self._teacher_stage_button: QPushButton | None = None
        self._teacher_stage_window: TeacherStageWindow | None = None
        self._voice_source: QAudioSource | None = None
        self._audio_io: Any | None = None
        self._audio_buffer = bytearray()
        self._voice_recording = False
        self._voice_started_at = 0.0
        self._voice_base_text = ""
        self._voice_preview_text = ""
        self._voice_future: Future[dict[str, Any]] | None = None
        self._voice_future_path: Path | None = None
        self._voice_future_final = False
        self._voice_final_pending = False
        self._voice_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mentorflow-live-asr",
        )
        self._voice_timer = QTimer(self)
        self._voice_timer.setInterval(900)
        self._voice_timer.timeout.connect(self._voice_tick)
        data_root = Path(os.getenv("MENTORFLOW_USER_DATA_ROOT", r"E:\MentorFlowData"))
        self._voice_temp_root = data_root / "temp" / "voice"
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(38, 32, 38, 32)
        heading = QLabel("原生课堂")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)
        subtitle = QLabel("教师进入概念后自动讲解、举例并提出最小检查；回答错误时按状态机纠错，不由模型擅自宣布掌握。")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("学习任务"))
        self.workspace = QComboBox()
        controls.addWidget(self.workspace, 1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_workspaces)
        controls.addWidget(refresh)
        start = QPushButton("进入/继续课堂")
        start.setObjectName("primaryButton")
        start.clicked.connect(self.start_session)
        controls.addWidget(start)
        exit_classroom = QPushButton("退出课堂")
        exit_classroom.setToolTip("离开当前课堂并返回任务选择；不会删除学习记录。")
        exit_classroom.clicked.connect(self.exit_classroom)
        controls.addWidget(exit_classroom)
        teacher_library = QPushButton("更换教师")
        teacher_library.setToolTip("从教师库选择并应用另一位教师")
        teacher_library.clicked.connect(self.open_teacher_library)
        controls.addWidget(teacher_library)
        modeling = QPushButton("编辑当前教师")
        modeling.setToolTip("编辑当前教师人设；保存后可应用到本计划")
        modeling.clicked.connect(self.open_teacher_modeling)
        controls.addWidget(modeling)
        layout.addLayout(controls)

        self.state = QLabel("请选择已激活计划的任务。")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)

        teacher_strip = QFrame()
        teacher_strip.setObjectName("explanationCard")
        teacher_strip_layout = QHBoxLayout(teacher_strip)
        teacher_strip_layout.setContentsMargins(16, 12, 16, 12)
        teacher_intro = QVBoxLayout()
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("今日 AI 教师"))
        title_row.addStretch()
        title_row.addWidget(QLabel("对话优先 · 右侧教师面板按需展开"))
        teacher_intro.addLayout(title_row)
        teacher_hint = QLabel("教师会先讲解、举例和检查，再等待你的回答。需要看形象时展开右侧面板；引用仍按需查看，不打断当前对话。")
        teacher_hint.setWordWrap(True)
        teacher_intro.addWidget(teacher_hint)
        teacher_strip_layout.addLayout(teacher_intro, 1)
        stage_button = QPushButton("展开教师面板")
        stage_button.setToolTip("在课堂右侧展开 Live2D 教师面板；默认不占用对话区。")
        stage_button.clicked.connect(self.open_teacher_stage)
        self._teacher_stage_button = stage_button
        teacher_strip_layout.addWidget(stage_button)
        evidence_button = QPushButton("查看本回合依据")
        evidence_button.setToolTip("引用默认收起，点击后按需查看资料位置和摘录。")
        evidence_button.clicked.connect(self.open_evidence)
        teacher_strip_layout.addWidget(evidence_button)
        layout.addWidget(teacher_strip)

        conversation = QFrame()
        conversation_layout = QVBoxLayout(conversation)
        self.transcript = QTextBrowser()
        self.transcript.setReadOnly(True)
        self.transcript.setOpenExternalLinks(True)
        self.transcript.setOpenLinks(True)
        self.transcript.setPlaceholderText("进入课堂后，结构化讲解会显示在这里。")
        conversation_layout.addWidget(self.transcript, 1)
        self.answer = QPlainTextEdit()
        self.answer.setMaximumHeight(120)
        self.answer.setPlaceholderText("写下你的解释、答案或具体不理解的地方……")
        conversation_layout.addWidget(self.answer)
        self.pending_attachments = QLabel("未选择附件。")
        self.pending_attachments.setWordWrap(True)
        conversation_layout.addWidget(self.pending_attachments)
        answer_actions = QVBoxLayout()
        answer_actions.setSpacing(8)
        answer_inputs = QHBoxLayout()
        answer_inputs.setSpacing(8)
        attach = QPushButton("添加截图/文档")
        attach.clicked.connect(self.choose_attachments)
        answer_inputs.addWidget(attach)
        transcribe = QPushButton("🎙 开始录音")
        transcribe.setToolTip("点击开始录音并实时转写；再次点击结束，文字可继续编辑或直接提交。")
        transcribe.clicked.connect(self.toggle_voice_recording)
        self.voice_button = transcribe
        answer_inputs.addWidget(transcribe)
        confused = QPushButton("我没理解，请针对纠错")
        confused.clicked.connect(self.send_confusion)
        answer_inputs.addWidget(confused)
        answer_inputs.addStretch()
        answer_actions.addLayout(answer_inputs)
        completion_actions = QHBoxLayout()
        completion_actions.setSpacing(8)
        completion_actions.addStretch()
        self_assessed = QPushButton("我已掌握，进入下一知识点")
        self_assessed.setToolTip(
            "按你的自我判断直接前进；系统会记录为自行跳过，不会伪装成教师验证掌握。"
        )
        self_assessed.clicked.connect(self.skip_to_next_point)
        completion_actions.addWidget(self_assessed)
        verify_mastery = QPushButton("✓ 我已掌握，请教师验证")
        verify_mastery.setToolTip("请先写下你的解释或答案；教师验证通过后才记录掌握并进入下一点。")
        verify_mastery.clicked.connect(self.verify_mastery)
        completion_actions.addWidget(verify_mastery)
        send = QPushButton("提交回答")
        send.setObjectName("primaryButton")
        send.clicked.connect(self.send_answer)
        completion_actions.addWidget(send)
        answer_actions.addLayout(completion_actions)
        for button in (attach, transcribe, confused, self_assessed, verify_mastery, send):
            button.setMinimumHeight(42)
        conversation_layout.addLayout(answer_actions)
        # The teacher stage is a sidecar window attached to the main window,
        # not a splitter child.  Keep the classroom conversation full width.
        layout.addWidget(conversation, 1)

    def _build_teacher_stage_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("teacherStagePanel")
        panel.setStyleSheet(
            "QFrame#teacherStagePanel { border: 1px solid #edc2d3; border-radius: 16px; "
            "background: #fff8fb; }"
        )
        panel.setMinimumWidth(460)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(8)
        self._teacher_stage_panel_layout = panel_layout
        header = QHBoxLayout()
        title = QLabel("Live2D 教师")
        title.setObjectName("cardHeading")
        header.addWidget(title)
        header.addStretch()
        collapse = QPushButton("收起")
        collapse.setToolTip("收起右侧教师面板，回到完整对话区。")
        collapse.clicked.connect(self._collapse_teacher_stage)
        collapse.setMinimumHeight(42)
        header.addWidget(collapse)
        panel_layout.addLayout(header)
        hint = QLabel("教师形象仅作为课堂辅助；对话和学习记录仍以左侧课堂为准。")
        hint.setWordWrap(True)
        panel_layout.addWidget(hint)
        self._teacher_stage_placeholder = QLabel("正在准备教师舞台……")
        self._teacher_stage_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._teacher_stage_placeholder.setWordWrap(True)
        panel_layout.addWidget(self._teacher_stage_placeholder, 1)
        return panel

    def _load_teacher_stage(self, workspace_id: str) -> None:
        self._teacher_stage_workspace_id = workspace_id
        if self._teacher_stage_web is not None:
            self._load_teacher_stage_url()

    def _ensure_teacher_stage_window(self) -> None:
        if self._teacher_stage_window is not None:
            return
        window = TeacherStageWindow(self)
        window_layout = QVBoxLayout(window)
        window_layout.setContentsMargins(0, 0, 0, 0)
        panel = self._build_teacher_stage_panel()
        window_layout.addWidget(panel)
        self._teacher_stage_window = window
        self._teacher_stage_panel = panel

    def open_teacher_stage(self) -> None:
        if not self.origin:
            QMessageBox.information(self, "2D 教师", "当前服务没有可用的教师舞台页面。")
            return
        if self._teacher_stage_window is not None and self._teacher_stage_window.isVisible():
            self._collapse_teacher_stage()
            return
        self._ensure_teacher_stage_window()
        if self._teacher_stage_window is None:
            return
        host = self.window()
        if host is self:
            host = None
        self._teacher_stage_window.attach_to(host)
        self._teacher_stage_window.sync_to_host()
        self._ensure_teacher_stage_web()
        self._teacher_stage_window.show()
        self._teacher_stage_window.raise_()
        if self._teacher_stage_button is not None:
            self._teacher_stage_button.setText("收起教师面板")
            self._teacher_stage_button.setToolTip("收起右侧教师面板，回到完整对话区。")

    def _ensure_teacher_stage_web(self) -> None:
        if self._teacher_stage_web is not None or self._teacher_stage_panel_layout is None:
            return
        panel = self._teacher_stage_panel
        if panel is None:
            return
        web = QWebEngineView(panel)
        profile = QWebEngineProfile(f"mentorflow_teacher_stage_panel_{id(self)}", self)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        web.setPage(QWebEnginePage(profile, web))
        web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        token = self.api.session_token
        if token:
            cookie = QNetworkCookie(b"mentorflow_session", token.encode("utf-8"))
            cookie.setHttpOnly(True)
            cookie.setPath("/")
            profile.cookieStore().setCookie(cookie, QUrl(self.origin))
        if self._teacher_stage_placeholder is not None:
            self._teacher_stage_panel_layout.removeWidget(self._teacher_stage_placeholder)
            self._teacher_stage_placeholder.deleteLater()
            self._teacher_stage_placeholder = None
        self._teacher_stage_panel_layout.addWidget(web, 1)
        self._teacher_stage_web = web
        self._teacher_stage_profile = profile
        self._load_teacher_stage_url()

    def _load_teacher_stage_url(self) -> None:
        if self._teacher_stage_web is None or not self.origin:
            return
        workspace_id = str(self._teacher_stage_workspace_id or self.workspace.currentData() or "")
        query = f"&workspaceId={QUrl.toPercentEncoding(workspace_id).data().decode('ascii')}" if workspace_id else ""
        self._teacher_stage_web.load(QUrl(f"{self.origin}/?tool=teacher-stage{query}"))

    def _collapse_teacher_stage(self) -> None:
        if self._teacher_stage_window is not None:
            self._teacher_stage_window.hide()
        if self._teacher_stage_button is not None:
            self._teacher_stage_button.setText("展开教师面板")
            self._teacher_stage_button.setToolTip("从主窗口右边界展开等高的教师建模面板。")

    def _on_teacher_stage_hidden(self) -> None:
        if self._teacher_stage_button is not None:
            self._teacher_stage_button.setText("展开教师面板")
            self._teacher_stage_button.setToolTip("从主窗口右边界展开等高的教师建模面板。")

    def open_evidence(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("原生课堂 · 当前回合依据")
        dialog.resize(760, 620)
        layout = QVBoxLayout(dialog)
        intro = QLabel("引用默认收起；这里展示教师本回合实际使用的资料位置和摘录。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        evidence = QTextBrowser(dialog)
        evidence.setReadOnly(True)
        evidence.setOpenExternalLinks(True)
        evidence.setOpenLinks(True)
        evidence.setHtml(self._evidence_html)
        layout.addWidget(evidence, 1)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def open_teacher_resources(self) -> None:
        if not self.origin:
            QMessageBox.information(self, "教师资源", "当前服务没有可用的资源页面。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("原生课堂 · 准备教师资源")
        dialog.resize(1080, 760)
        layout = QVBoxLayout(dialog)
        hint = QLabel("语音声包和 2D 教师资源可以单独下载，不会混入学习数据迁移包。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        profile = QWebEngineProfile(f"mentorflow_teacher_resources_{id(dialog)}", dialog)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        web = QWebEngineView(dialog)
        web.setPage(QWebEnginePage(profile, web))
        web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        token = self.api.session_token
        if token:
            cookie = QNetworkCookie(b"mentorflow_session", token.encode("utf-8"))
            cookie.setHttpOnly(True)
            cookie.setPath("/")
            profile.cookieStore().setCookie(cookie, QUrl(self.origin))
        web.load(QUrl(f"{self.origin}/?tool=teacher-resources"))
        layout.addWidget(web, 1)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def refresh_workspaces(self) -> None:
        selected = str(self.workspace.currentData() or "")
        try:
            items = self.api.active_workspaces().get("items", [])
        except ApiError as exc:
            self.state.setText(f"读取任务失败：{exc.code}：{exc.message}")
            return
        self.workspace.blockSignals(True)
        self.workspace.clear()
        for item in items:
            workspace_id = str(item.get("workspace_id") or "")
            if workspace_id:
                self.workspace.addItem(str(item.get("title") or workspace_id), workspace_id)
        if selected:
            index = self.workspace.findData(selected)
            if index >= 0:
                self.workspace.setCurrentIndex(index)
        self.workspace.blockSignals(False)

    def start_session(self) -> None:
        workspace_id = str(self.workspace.currentData() or "")
        if not workspace_id:
            self.state.setText("请先选择学习任务。")
            return
        try:
            session = self.api.start_teaching_session(workspace_id, self.provider_session())
        except ApiError as exc:
            self.state.setText(f"无法进入课堂：{exc.code}：{exc.message}")
            return
        self.session_id = str(session.get("session_id") or "")
        self._load_teacher_stage(str(self.workspace.currentData() or ""))
        self._render_session(session)

    def exit_classroom(self) -> None:
        """Leave the current classroom view without deleting any learning data."""
        self._cancel_voice_recording()
        self.session_id = ""
        self.current_session = {}
        self.pending_attachment_ids.clear()
        self.answer.clear()
        self.pending_attachments.setText("未选择附件。")
        self.transcript.setHtml("<p>已退出本次课堂。请选择任务后重新进入，学习记录不会被删除。</p>")
        self._evidence_html = "<p>当前回合没有可展示的引用。</p>"
        self._collapse_teacher_stage()
        self.state.setText("已退出本次课堂。请选择学习任务后重新进入；已有学习记录仍会保留。")

    def hideEvent(self, event) -> None:  # type: ignore[override]
        # Switching to another left-nav page must also hide the sidecar; it is
        # a classroom companion, never a floating overlay for other pages.
        self._cancel_voice_recording()
        self._collapse_teacher_stage()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._cancel_voice_recording()
        self._voice_executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

    def open_teacher_library(self) -> None:
        try:
            items = self.api.teacher_personas().get("items", [])
        except ApiError as exc:
            QMessageBox.warning(self, "教师库不可用", f"{exc.code}：{exc.message}")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("原生课堂 · 教师库")
        dialog.resize(820, 520)
        layout = QVBoxLayout(dialog)
        intro = QLabel("教师人设只影响课堂表达方式；学习计划、资料和掌握记录不会被替换。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        split = QSplitter(Qt.Orientation.Horizontal)
        persona_list = QListWidget()
        detail = QPlainTextEdit()
        detail.setReadOnly(True)
        split.addWidget(persona_list)
        split.addWidget(detail)
        split.setSizes([280, 500])
        layout.addWidget(split, 1)
        for item in items:
            if not isinstance(item, dict):
                continue
            row = QListWidgetItem(str(item.get("name") or "未命名教师"))
            row.setData(Qt.ItemDataRole.UserRole, item)
            persona_list.addItem(row)

        def show_persona(row: QListWidgetItem | None) -> None:
            item = row.data(Qt.ItemDataRole.UserRole) if row else {}
            if not isinstance(item, dict):
                detail.clear()
                return
            detail.setPlainText(
                f"教师名称：{item.get('name') or '未命名'}\n"
                f"教学风格：{item.get('teaching_style') or '未填写'}\n\n"
                f"课堂说明：\n{item.get('prompt') or item.get('description') or '未填写'}\n\n"
                f"已应用任务：{item.get('usage_count') or 0} 个"
            )

        persona_list.currentItemChanged.connect(show_persona)
        if persona_list.count():
            persona_list.setCurrentRow(0)
        actions = QHBoxLayout()
        apply_button = QPushButton("应用到当前任务")
        apply_button.setObjectName("primaryButton")
        actions.addWidget(apply_button)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.reject)
        actions.addWidget(close_button)
        layout.addLayout(actions)

        def apply_selected() -> None:
            workspace_id = str(self.workspace.currentData() or "")
            row = persona_list.currentItem()
            item = row.data(Qt.ItemDataRole.UserRole) if row else {}
            persona_id = str(item.get("id") or "") if isinstance(item, dict) else ""
            if not workspace_id:
                QMessageBox.information(dialog, "尚未选择任务", "请先在课堂顶部选择一个学习任务。")
                return
            if not persona_id:
                QMessageBox.information(dialog, "尚未选择教师", "请先从左侧选择一个教师人设。")
                return
            try:
                self.api.apply_teacher_persona(workspace_id, persona_id)
            except ApiError as exc:
                QMessageBox.warning(dialog, "应用失败", f"{exc.code}：{exc.message}")
                return
            self.state.setText("教师人设已应用到当前任务；重新进入课堂即可使用新的教学风格。")
            dialog.accept()

        apply_button.clicked.connect(apply_selected)
        dialog.exec()

    def open_teacher_modeling(self) -> None:
        if not self.origin:
            QMessageBox.information(self, "教师建模不可用", "当前服务没有提供教师建模页面地址。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("原生课堂 · 教师建模")
        dialog.resize(1180, 820)
        layout = QVBoxLayout(dialog)
        hint = QLabel("教师人设编辑会保存到本地教师库；只有点击应用后才影响当前任务。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        profile = QWebEngineProfile(f"mentorflow_teacher_modeling_{id(dialog)}", dialog)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        web = QWebEngineView(dialog)
        web.setPage(QWebEnginePage(profile, web))
        web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        loaded = False
        def load() -> None:
            nonlocal loaded
            if not loaded:
                loaded = True
                web.load(QUrl(f"{self.origin}/?tool=teachers"))
        token = self.api.session_token
        if token:
            cookie = QNetworkCookie(b"mentorflow_session", token.encode("utf-8"))
            cookie.setHttpOnly(True)
            cookie.setPath("/")
            store = profile.cookieStore()
            store.cookieAdded.connect(lambda added: load() if bytes(added.name()) == b"mentorflow_session" else None)
            store.setCookie(cookie, QUrl(self.origin))
            QTimer.singleShot(1500, load)
        else:
            load()
        layout.addWidget(web, 1)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def send_confusion(self) -> None:
        if not self.answer.toPlainText().strip():
            self.answer.setPlainText("我还没有理解当前概念，请针对我可能混淆的对象和关系重新解释。")
        self.send_answer()

    def send_answer(self) -> None:
        message = self.answer.toPlainText().strip()
        if not self.session_id:
            self.state.setText("请先进入课堂。")
            return
        if not message and not self.pending_attachment_ids:
            self.state.setText("请输入回答、具体疑问，或添加附件。")
            return
        try:
            session = self.api.advance_teaching_session(
                self.session_id,
                message=message,
                provider_session_id=self.provider_session(),
                attachment_ids=self.pending_attachment_ids,
            )
        except ApiError as exc:
            self.state.setText(f"教师回合失败：{exc.code}：{exc.message}")
            return
        self.answer.clear()
        self.pending_attachment_ids.clear()
        self.pending_attachments.setText("未选择附件。")
        self._render_session(session)

    def choose_attachments(self) -> None:
        if not self.session_id:
            self.state.setText("请先进入课堂，再添加附件。")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择课堂截图或文档")
        if not paths:
            return
        names: list[str] = []
        for raw_path in paths:
            try:
                result = self.api.upload_teaching_attachment(self.session_id, raw_path)
            except (ApiError, OSError) as exc:
                message = exc.message if isinstance(exc, ApiError) else str(exc)
                QMessageBox.warning(self, "附件未接受", f"{Path(raw_path).name}：{message}")
                continue
            attachment_id = str(result.get("id") or "")
            if attachment_id and attachment_id not in self.pending_attachment_ids:
                self.pending_attachment_ids.append(attachment_id)
                names.append(str(result.get("original_filename") or Path(raw_path).name))
        if self.pending_attachment_ids:
            self.pending_attachments.setText(
                f"待随下一条回答发送：{len(self.pending_attachment_ids)} 个附件"
                + ("（" + "、".join(names) + "）" if names else "")
            )

    def toggle_voice_recording(self) -> None:
        """Start/stop microphone capture; partial ASR runs while recording."""

        if self._voice_recording:
            self._stop_voice_recording()
            return
        if self._voice_future is not None:
            self.state.setText("正在完成上一段录音的转写，请稍候……")
            return
        if not self.session_id:
            self.state.setText("请先进入课堂，再开始录音。")
            return
        self._start_voice_recording()

    def _start_voice_recording(self) -> None:
        device = QMediaDevices.defaultAudioInput()
        if not device.description():
            self.state.setText("没有检测到可用麦克风，请在 Windows 设置中检查输入设备。")
            return
        audio_format = QAudioFormat()
        selected = False
        for sample_rate in (16_000, 48_000, 44_100):
            audio_format.setSampleRate(sample_rate)
            audio_format.setChannelCount(1)
            audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if device.isFormatSupported(audio_format):
                selected = True
                break
        if not selected:
            self.state.setText("当前麦克风不支持应用需要的录音格式。请切换 Windows 输入设备后重试。")
            return
        self._audio_buffer.clear()
        self._voice_base_text = self.answer.toPlainText().strip()
        self._voice_preview_text = ""
        self._voice_started_at = time.monotonic()
        self._voice_last_submitted_bytes = 0
        self._voice_sample_rate = audio_format.sampleRate()
        self._voice_channels = audio_format.channelCount()
        self._voice_source = QAudioSource(device, audio_format, self)
        self._audio_io = self._voice_source.start()
        if self._audio_io is None:
            self._voice_source = None
            self.state.setText("麦克风启动失败，请检查 Windows 麦克风权限。")
            return
        self._audio_io.readyRead.connect(self._read_voice_audio)
        self._voice_recording = True
        self._voice_final_pending = False
        self.voice_button.setText("■ 结束录音")
        self.voice_button.setObjectName("recordingButton")
        self.voice_button.style().unpolish(self.voice_button)
        self.voice_button.style().polish(self.voice_button)
        self.state.setText("正在录音并实时转写……再次点击“结束录音”即可完成。")
        self._voice_timer.start()

    def _read_voice_audio(self) -> None:
        if self._audio_io is None:
            return
        chunk = bytes(self._audio_io.readAll())
        if chunk:
            self._audio_buffer.extend(chunk)

    def _voice_tick(self) -> None:
        if not self._voice_recording:
            return
        self._read_voice_audio()
        seconds = max(0, int(time.monotonic() - self._voice_started_at))
        self.voice_button.setText(f"■ 结束录音  {seconds} 秒")
        bytes_per_second = max(1, self._voice_sample_rate * self._voice_channels * 2)
        enough_new_audio = len(self._audio_buffer) - self._voice_last_submitted_bytes >= int(
            bytes_per_second * 1.4
        )
        if seconds >= 2 and enough_new_audio and self._voice_future is None:
            self._request_voice_transcription(final=False)

    def _stop_voice_recording(self) -> None:
        self._read_voice_audio()
        if self._voice_source is not None:
            self._voice_source.stop()
        self._voice_source = None
        self._audio_io = None
        self._voice_recording = False
        self._voice_timer.stop()
        self.voice_button.setObjectName("")
        self.voice_button.style().unpolish(self.voice_button)
        self.voice_button.style().polish(self.voice_button)
        self.voice_button.setText("正在完成转写……")
        self.voice_button.setEnabled(False)
        self.state.setText("录音已结束，正在整理最后一段文字……")
        if len(self._audio_buffer) < max(1, self._voice_sample_rate * self._voice_channels):
            self._finish_voice_transcription("", error="录音太短，没有识别到可用语音。")
            return
        if self._voice_future is not None:
            self._voice_final_pending = True
        else:
            self._request_voice_transcription(final=True)

    def _request_voice_transcription(self, *, final: bool) -> None:
        if self._voice_future is not None or not self.session_id:
            if final:
                self._voice_final_pending = True
            return
        self._voice_temp_root.mkdir(parents=True, exist_ok=True)
        snapshot = self._voice_temp_root / f"recording-{uuid.uuid4().hex}.wav"
        with wave.open(str(snapshot), "wb") as output:
            output.setnchannels(self._voice_channels)
            output.setsampwidth(2)
            output.setframerate(self._voice_sample_rate)
            output.writeframes(bytes(self._audio_buffer))
        self._voice_last_submitted_bytes = len(self._audio_buffer)
        self._voice_future_path = snapshot
        self._voice_future_final = bool(final)
        self._voice_future = self._voice_executor.submit(
            self.api.transcribe_teaching_voice,
            self.session_id,
            snapshot,
        )
        QTimer.singleShot(120, self._poll_voice_transcription)

    def _poll_voice_transcription(self) -> None:
        future = self._voice_future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(120, self._poll_voice_transcription)
            return
        snapshot = self._voice_future_path
        final = self._voice_future_final
        self._voice_future = None
        self._voice_future_path = None
        self._voice_future_final = False
        try:
            result = future.result()
            text = str(result.get("text") or result.get("transcript") or "").strip()
            error = ""
        except (ApiError, OSError, RuntimeError) as exc:
            text = ""
            error = exc.message if isinstance(exc, ApiError) else str(exc)
        finally:
            if snapshot is not None:
                snapshot.unlink(missing_ok=True)
        if text:
            self._voice_preview_text = text
            self.answer.setPlainText(
                (self._voice_base_text + "\n" + self._voice_preview_text).strip()
            )
            cursor = self.answer.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.answer.setTextCursor(cursor)
        if final:
            self._finish_voice_transcription(text, error=error)
            return
        if error and self._voice_recording:
            self.state.setText(f"仍在录音；实时转写暂未返回：{error}")
        elif self._voice_recording:
            self.state.setText("正在录音并实时转写；文字已写入输入框，可继续说。")
        if self._voice_final_pending:
            self._voice_final_pending = False
            self._request_voice_transcription(final=True)

    def _finish_voice_transcription(self, text: str, *, error: str = "") -> None:
        self._voice_final_pending = False
        self.voice_button.setEnabled(True)
        self.voice_button.setText("🎙 开始录音")
        if text or self._voice_preview_text:
            self.state.setText("语音已转成文字；可以继续编辑，也可以直接提交回答。")
            self.answer.setFocus()
        elif error:
            self.state.setText(f"语音转写未完成：{error}")
        else:
            self.state.setText("录音中没有识别到可用语音，请重试。")

    def _cancel_voice_recording(self) -> None:
        if self._voice_source is not None:
            self._voice_source.stop()
        self._voice_source = None
        self._audio_io = None
        self._voice_recording = False
        self._voice_final_pending = False
        self._voice_timer.stop()
        if hasattr(self, "voice_button"):
            self.voice_button.setEnabled(True)
            self.voice_button.setText("🎙 开始录音")

    def skip_to_next_point(self) -> None:
        if not self.session_id:
            self.state.setText("请先进入课堂。")
            return
        if QMessageBox.question(
            self,
            "按自我判断前进",
            "将直接进入下一个知识点，并记录为“自行跳过”。这不会伪造教师验证掌握，之后仍可复习。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            session = self.api.advance_teaching_session(
                self.session_id,
                message="学习者自评已掌握，选择进入下一知识点。",
                provider_session_id=self.provider_session(),
                force_complete=True,
            )
        except ApiError as exc:
            self.state.setText(f"无法进入下一知识点：{exc.code}：{exc.message}")
            return
        self._render_session(session)

    def verify_mastery(self) -> None:
        if not self.session_id:
            self.state.setText("请先进入课堂。")
            return
        evidence = self.answer.toPlainText().strip()
        if not evidence:
            QMessageBox.information(
                self,
                "请先给出掌握证据",
                "请在回答框写下你的解释、答案或解题思路。教师验证通过后才会记录掌握并进入下一知识点。",
            )
            self.answer.setFocus()
            return
        answer = QMessageBox.question(
            self,
            "请教师验证掌握",
            "教师会根据你的回答和当前资料验证；只有通过检查才会记录掌握并进入下一知识点。是否提交？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            session = self.api.advance_teaching_session(
                self.session_id,
                message=evidence,
                provider_session_id=self.provider_session(),
                verify_completion=True,
                attachment_ids=self.pending_attachment_ids,
            )
        except ApiError as exc:
            self.state.setText(f"掌握验证失败：{exc.code}：{exc.message}")
            return
        self.answer.clear()
        self.pending_attachment_ids = []
        self.pending_attachments.setText("未选择附件。")
        self._render_session(session)

    def _render_session(self, session: dict[str, Any]) -> None:
        self.current_session = session
        self.transcript.setHtml(session_transcript_html(session))
        scrollbar = self.transcript.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        state = session.get("state") or {}
        turn = state.get("teacher_turn_v1") or {}
        turn_state = turn.get("state") or {}
        phase_labels = {
            "understand": "理解知识点",
            "recall": "回忆知识点",
            "guided_practice": "引导练习",
            "independent_application": "独立应用",
            "manual_skip": "已跳过（不计掌握）",
            "review": "复习",
        }
        status_labels = {
            "manual_skipped": "已跳过（不计掌握）",
            "awaiting_answer": "等待回答",
            "in_progress": "进行中",
            "completed": "已完成",
        }
        phase = str(turn_state.get("phase") or state.get("current_teaching_step") or "未知")
        status = str(turn_state.get("status") or "in_progress")
        self.state.setText(
            "课堂状态：已连接教师\n"
            f"阶段：{phase_labels.get(phase, phase)}　"
            f"状态：{status_labels.get(status, status)}　"
            f"路线：{int(state.get('route_index') or 0) + 1}/{state.get('route_length') or '?'}\n"
            f"预计任务：{state.get('estimated_task_minutes') or 0} 分钟；"
            f"实际累计：{session.get('actual_elapsed_minutes') or 0} 分钟"
        )
        citations: list[dict[str, Any]] = []
        messages = session.get("messages") or []
        if messages and isinstance(messages[-1], dict):
            citations = messages[-1].get("citations") or []
        material_index: dict[str, dict[str, Any]] = {}
        try:
            workspace_id = str(session.get("workspace_id") or self.workspace.currentData() or "")
            if workspace_id:
                material_index = {
                    str(item.get("id") or ""): item
                    for item in self.api.materials(workspace_id)
                    if isinstance(item, dict)
                }
        except ApiError:
            material_index = {}
        lines: list[str] = []
        for index, item in enumerate(citations, 1):
            source = item.get("source_file") or item.get("source_id") or item.get("material_id") or "未知来源"
            location = []
            if item.get("page_start") is not None:
                location.append(f"第 {item.get('page_start')} 页")
            if item.get("char_start") is not None:
                location.append(f"字符 {item.get('char_start')}–{item.get('char_end', '?')}")
            quote = str(item.get("quote") or item.get("text") or "").strip()
            material = material_index.get(str(item.get("material_id") or item.get("source_id") or ""), {})
            link = _safe_link(item.get("url") or item.get("original_url") or item.get("source_url") or material.get("original_url"))
            local_path = item.get("stored_path") or material.get("stored_path")
            if not link and local_path:
                try:
                    link = QUrl.fromLocalFile(str(local_path)).toString()
                except Exception:
                    link = ""
            source_html = html.escape(str(source))
            if link:
                source_html = f'<a href="{html.escape(link, quote=True)}">{source_html}</a>'
            lines.append(f"<p>{index}. {source_html}" + (" · " + "；".join(location) if location else "") + "</p>")
            if quote:
                lines.append(f"<blockquote>{_inline_teacher_html(quote)}</blockquote>")
        self._evidence_html = "".join(lines) if lines else "<p>当前回合没有可展示的引用。</p>"
