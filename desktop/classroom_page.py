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

from PySide6.QtCore import QCoreApplication, QEvent, QMicrophonePermission, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .web_links import install_external_link_handler
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QLayout,
    QScrollArea,
    QSplitter,
    QSizePolicy,
    QStackedWidget,
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


_CLASSROOM_PHASE_LABELS = {
    "understand": "理解知识点",
    "recall": "回忆知识点",
    "guided_practice": "引导练习",
    "independent_application": "独立应用",
    "manual_skip": "已跳过（不计掌握）",
    "self_attested_completion": "已完成",
    "review": "复习",
    "daily_briefing": "学习导入",
    "waiting_for_answer": "等待回答",
    "v1_waiting_for_answer": "等待回答",
    "concept_check": "理解检查",
    "in_progress": "进行中",
    "completed": "已完成",
}


def current_knowledge_point_summary(session: dict[str, Any]) -> dict[str, Any]:
    """Return display-only information for the point currently in the lesson.

    The API has carried this information in a few nested state shapes while
    the canonical task route evolved.  Resolve the teacher turn first, then
    fall back to the current memory/task title.  Internal identifiers are used
    only to find the active task and are never returned for presentation.
    """

    if not isinstance(session, dict):
        return {"name": "", "phase": "", "route": "", "review": ""}
    projection = session.get("current_point") or session.get("current_point_projection")
    projection = projection if isinstance(projection, dict) else {}
    state = session.get("state")
    state = state if isinstance(state, dict) else {}
    turn = state.get("teacher_turn_v1")
    turn = turn if isinstance(turn, dict) else {}
    concept = turn.get("concept")
    concept = concept if isinstance(concept, dict) else {}
    turn_state = turn.get("state")
    turn_state = turn_state if isinstance(turn_state, dict) else {}

    active_task: dict[str, Any] = {}
    active_task_id = str(state.get("active_task_id") or "")
    for candidate in (
        state.get("active_task"),
        state.get("active_task_metadata"),
        state.get("current_task"),
        state.get("task"),
        session.get("active_task"),
    ):
        if isinstance(candidate, dict):
            active_task = dict(candidate)
            break
    today_plan = state.get("today_plan")
    if isinstance(today_plan, dict):
        tasks = today_plan.get("tasks") or []
        if isinstance(tasks, (list, tuple)):
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                if active_task_id and str(task.get("id") or "") == active_task_id:
                    active_task = dict(task)
                    break
                if (
                    not active_task
                    and str(task.get("knowledge_point_id") or "")
                    == str(state.get("current_knowledge_point_id") or "")
                ):
                    active_task = dict(task)

    name = ""
    for candidate in (
        projection.get("name"),
        concept.get("name"),
        concept.get("title"),
        state.get("current_knowledge_point_name"),
        (state.get("current_knowledge_memory") or {}).get("knowledge_point_name")
        if isinstance(state.get("current_knowledge_memory"), dict)
        else "",
        active_task.get("title"),
        (state.get("navigation_context") or {}).get("to_knowledge_point_name")
        if isinstance(state.get("navigation_context"), dict)
        else "",
    ):
        value = str(candidate or "").strip()
        if value:
            name = value
            break

    phase_raw = str(
        turn_state.get("phase")
        or state.get("current_teaching_step")
        or active_task.get("learning_phase")
        or ""
    ).strip()
    phase_key = phase_raw.removeprefix("v1_")
    phase = _CLASSROOM_PHASE_LABELS.get(phase_raw) or _CLASSROOM_PHASE_LABELS.get(phase_key)
    if not phase and phase_key:
        phase = phase_key.replace("_", " ").replace("-", " ").strip().capitalize()

    def _positive_int(*values: object) -> int:
        for value in values:
            try:
                parsed = int(value or 0)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                return parsed
        return 0

    progress = session.get("knowledge_point_progress")
    progress = progress if isinstance(progress, dict) else {}
    point_ordinal = _positive_int(
        projection.get("ordinal"), progress.get("ordinal"), state.get("current_point_ordinal")
    )
    point_total = _positive_int(
        projection.get("total"), progress.get("total"), state.get("current_point_total")
    )
    if point_ordinal and point_total:
        route = f"知识点 {point_ordinal}/{point_total}"
    else:
        route_length = _positive_int(state.get("route_length"), today_plan.get("task_count") if isinstance(today_plan, dict) else 0)
        route_index = _positive_int(state.get("route_index")) + 1
        if route_length:
            route_index = min(max(1, route_index), route_length)
            route = f"路线 {route_index}/{route_length}"
        else:
            route = ""

    review_metadata = state.get("review_metadata")
    review_metadata = review_metadata if isinstance(review_metadata, dict) else {}
    task_type = str(
        active_task.get("task_type")
        or state.get("task_type")
        or turn_state.get("task_type")
        or review_metadata.get("task_type")
        or ""
    ).strip().casefold()
    review_cycle = _positive_int(
        active_task.get("review_cycle"),
        active_task.get("review_round"),
        state.get("review_cycle"),
        state.get("review_round"),
        turn_state.get("review_cycle"),
        turn_state.get("review_round"),
        (state.get("active_review") or {}).get("review_cycle")
        if isinstance(state.get("active_review"), dict)
        else 0,
        (state.get("active_review") or {}).get("review_round")
        if isinstance(state.get("active_review"), dict)
        else 0,
        (state.get("review_metadata") or {}).get("review_cycle")
        if isinstance(state.get("review_metadata"), dict)
        else 0,
        (state.get("review_metadata") or {}).get("review_round")
        if isinstance(state.get("review_metadata"), dict)
        else 0,
        (state.get("current_review") or {}).get("review_cycle")
        if isinstance(state.get("current_review"), dict)
        else 0,
        (state.get("current_review") or {}).get("review_round")
        if isinstance(state.get("current_review"), dict)
        else 0,
    )
    review = ""
    if task_type == "review" or review_cycle:
        review = f"复习第 {review_cycle} 轮" if review_cycle else "复习任务"
    return {"name": name, "phase": phase or "", "route": route, "review": review}


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


def _current_point_messages(session: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only the active point conversation.

    Unscoped historical messages are audit history, not display content.  They
    can belong to a different point, so showing even the latest one is unsafe.
    The backend materialises a fresh current-point turn when an old session is
    resumed.
    """

    projection = session.get("current_point") or session.get("current_point_projection")
    messages = projection.get("messages") if isinstance(projection, dict) else None
    if isinstance(messages, list) and messages:
        return [item for item in messages if isinstance(item, dict)]
    # Historical sessions can predate point IDs on message rows.  Their state
    # still carries the current structured teacher turn, so render that single
    # turn instead of either a blank classroom or the entire old transcript.
    state = session.get("state") if isinstance(session.get("state"), dict) else {}
    turn = state.get("teacher_turn_v1") if isinstance(state, dict) else None
    if isinstance(turn, dict) and turn:
        return [
            {
                "role": "teacher",
                "content": str(turn.get("plain_text") or ""),
                "teacher_turn": turn,
                "attachments": [],
            }
        ]
    historical = session.get("messages") or []
    if not isinstance(projection, dict):
        return [item for item in historical if isinstance(item, dict)]
    return []


def session_transcript(session: dict[str, Any]) -> str:
    rendered: list[str] = []
    messages = _current_point_messages(session)
    for message in messages:
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
        ".teacher-message{max-width:92%;margin:10px 0 14px;padding:13px 15px;background:#fff3f6;border:1px solid #edc2d3;border-radius:18px 18px 18px 7px;box-shadow:0 4px 14px #8d3f6218;}"
        ".learner-message{max-width:82%;margin:10px 0 14px auto;padding:11px 14px;background:#edf7f0;border:1px solid #c7dfcf;border-radius:18px 18px 7px 18px;}"
        ".message-role{font-size:12px;font-weight:800;color:#a94f79;margin-bottom:7px;}"
        ".learner-message .message-role{color:#5b806b;text-align:right;}"
        ".teacher-block{margin:8px 0;padding:9px 11px;background:#fff0f5;border-radius:10px;}"
        ".teacher-block h4{margin:0 0 5px;color:#a94f79;} .concept-heading{color:#8d3f62;}"
        ".hard-focus{color:#b42318;font-weight:700;} .expected-action{font-weight:700;}"
        "a{color:#246b9b;text-decoration:underline;} pre{background:#24333a;color:#f7fbf9;padding:10px;border-radius:8px;white-space:pre-wrap;}"
        "</style>"
    ]
    messages = _current_point_messages(session)
    for message in messages:
        if not isinstance(message, dict):
            continue
        teacher = message.get("role") == "teacher"
        turn = message.get("teacher_turn")
        body = teacher_turn_html(turn) if isinstance(turn, dict) else _inline_teacher_html(message.get("content") or "")
        attachments = message.get("attachments") or []
        attachment_html = ""
        if attachments:
            names = "、".join(
                html.escape(str(item.get("original_filename") or item.get("id") or "附件"))
                for item in attachments
                if isinstance(item, dict)
            )
            if names:
                attachment_html = f'<p class="message-attachments">附件：{names}</p>'
        if teacher:
            rendered.append(f'<div class="teacher-message"><div class="message-role">教师</div>{body}{attachment_html}</div>')
        else:
            rendered.append(f'<div class="learner-message"><div class="message-role">你</div>{body}{attachment_html}</div>')
    return "".join(rendered) or "<p>进入课堂后，结构化讲解会显示在这里。</p>"


class ClassroomTranscriptView(QScrollArea):
    """Scrollable native message bubbles for the classroom transcript.

    QTextDocument's HTML renderer does not consistently honor ``border-radius``
    on tables across Qt platforms.  Each message is therefore a real QFrame,
    while its safe rich-text body remains a QTextBrowser so links, selection,
    and the existing teacher formatting continue to work.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("classroomTranscript")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet(
            "QScrollArea#classroomTranscript { border: 1px solid #eadde2; border-radius: 18px; background: #fffdfb; }"
            "QScrollArea#classroomTranscript QWidget#classroomTranscriptContent { background: #fffdfb; }"
            "QScrollArea#classroomTranscript QScrollBar:vertical { width: 18px; margin: 5px 2px; background: #f4e9ee; border: 0; border-radius: 8px; }"
            "QScrollArea#classroomTranscript QScrollBar::handle:vertical { min-height: 56px; border-radius: 8px; background: #b887a0; }"
            "QScrollArea#classroomTranscript QScrollBar::handle:vertical:hover { background: #b887a0; }"
            "QScrollArea#classroomTranscript QScrollBar::add-line:vertical, QScrollArea#classroomTranscript QScrollBar::sub-line:vertical { height: 0; background: transparent; }"
            "QScrollArea#classroomTranscript QScrollBar::add-page:vertical, QScrollArea#classroomTranscript QScrollBar::sub-page:vertical { background: transparent; }"
            "QTextBrowser#transcriptMessageBody { border: 0; background: transparent; color: #38423d; padding: 0; }"
            "QLabel#teacherMessageRole { color: #9e4a72; font-size: 12px; font-weight: 800; }"
            "QLabel#learnerMessageRole { color: #4f7c62; font-size: 12px; font-weight: 800; }"
            "QLabel#messageAttachments { color: #6d6870; font-size: 12px; padding-top: 3px; }"
            "QFrame#teacherMessageBubble { background: #fff3f6; border: 1px solid #edc2d3; border-radius: 18px; }"
            "QFrame#learnerMessageBubble { background: #edf7f0; border: 1px solid #c7dfcf; border-radius: 18px; }"
        )
        self._content = QWidget(self)
        self._content.setObjectName("classroomTranscriptContent")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(14, 14, 14, 14)
        self._layout.setSpacing(12)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.setWidget(self._content)
        self._has_rendered_session = False

    def _clear_messages(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _message_body(message: dict[str, Any]) -> str:
        turn = message.get("teacher_turn")
        if isinstance(turn, dict):
            return teacher_turn_html(turn)
        return _inline_teacher_html(message.get("content") or "") or "<p>（空回答）</p>"

    def _make_body(self, content: str) -> QTextBrowser:
        body = QTextBrowser(self._content)
        body.setObjectName("transcriptMessageBody")
        body.setReadOnly(True)
        body.setOpenExternalLinks(True)
        body.setOpenLinks(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        body.setHtml(
            "<style>"
            "body{font-family:'Segoe UI','Microsoft YaHei',sans-serif;color:#38423d;margin:0;}"
            ".teacher-block{margin:8px 0;padding:9px 11px;background:#fff0f5;border-radius:10px;}"
            ".teacher-block h4{margin:0 0 5px;color:#a94f79;} .concept-heading{color:#8d3f62;margin:0 0 8px;}"
            ".hard-focus{color:#b42318;font-weight:700;} .expected-action{font-weight:700;}"
            "a{color:#246b9b;text-decoration:underline;}"
            "pre{background:#24333a;color:#f7fbf9;padding:10px;border-radius:8px;white-space:pre-wrap;}"
            "</style>"
            + content
        )
        body.document().setDocumentMargin(0)
        return body

    def _make_bubble(self, message: dict[str, Any]) -> QFrame:
        teacher = message.get("role") == "teacher"
        bubble = QFrame(self._content)
        bubble.setObjectName("teacherMessageBubble" if teacher else "learnerMessageBubble")
        bubble.setProperty("messageRole", "teacher" if teacher else "learner")
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(16, 12, 16, 13)
        bubble_layout.setSpacing(5)
        role = QLabel("教师" if teacher else "你", bubble)
        role.setObjectName("teacherMessageRole" if teacher else "learnerMessageRole")
        if not teacher:
            role.setAlignment(Qt.AlignmentFlag.AlignRight)
        bubble_layout.addWidget(role)
        bubble_layout.addWidget(self._make_body(self._message_body(message)))
        attachments = message.get("attachments") or []
        names = "、".join(
            str(item.get("original_filename") or item.get("id") or "附件")
            for item in attachments
            if isinstance(item, dict)
        )
        if names:
            attachments_label = QLabel("附件：" + names, bubble)
            attachments_label.setObjectName("messageAttachments")
            attachments_label.setWordWrap(True)
            bubble_layout.addWidget(attachments_label)
        return bubble

    def _fit_bodies(self) -> None:
        available = max(180, self.viewport().width() - 22)
        for bubble in self._content.findChildren(QFrame, options=Qt.FindChildOption.FindDirectChildrenOnly):
            body = bubble.findChild(QTextBrowser)
            if body is None:
                continue
            role = str(bubble.property("messageRole") or "teacher")
            max_width = 760 if role == "teacher" else 640
            ratio = 0.94 if role == "teacher" else 0.82
            width = max(150, min(max_width, int(available * ratio) - 32))
            body.setFixedWidth(width)
            body.document().setTextWidth(width)
            body.document().adjustSize()
            height = max(34, int(body.document().size().height()) + 4)
            body.setFixedHeight(height)
        self._layout.invalidate()
        self._content.setMinimumHeight(self._layout.sizeHint().height())
        self._content.updateGeometry()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        QTimer.singleShot(0, self._fit_bodies)

    def _fit_and_scroll_to_latest(self, *, scroll_to_latest: bool = True) -> None:
        self._fit_bodies()
        if scroll_to_latest:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        else:
            # Keep the opening at its beginning so a long structured lesson is
            # scannable; later replies intentionally follow the newest turn.
            self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())

    def set_session(self, session: dict[str, Any]) -> None:
        scroll_to_latest = self._has_rendered_session
        self._clear_messages()
        messages = _current_point_messages(session)
        for message in messages:
            bubble = self._make_bubble(message)
            alignment = Qt.AlignmentFlag.AlignLeft if message.get("role") == "teacher" else Qt.AlignmentFlag.AlignRight
            self._layout.addWidget(bubble, 0, alignment)
        if not messages:
            empty = QLabel("进入课堂后，结构化讲解会显示在这里。", self._content)
            empty.setObjectName("messageAttachments")
            empty.setWordWrap(True)
            self._layout.addWidget(empty)
        self._layout.addStretch(1)
        QTimer.singleShot(
            0,
            lambda: self._fit_and_scroll_to_latest(scroll_to_latest=scroll_to_latest),
        )
        self._has_rendered_session = True

    def setHtml(self, content: str) -> None:
        """Compatibility fallback for callers that still provide one HTML document."""

        self._clear_messages()
        bubble = QFrame(self._content)
        bubble.setObjectName("teacherMessageBubble")
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(16, 12, 16, 13)
        bubble_layout.addWidget(self._make_body(content))
        self._layout.addWidget(bubble, 0, Qt.AlignmentFlag.AlignLeft)
        self._layout.addStretch(1)
        QTimer.singleShot(0, self._fit_and_scroll_to_latest)
        # Compatibility content (including the exit message) is not a lesson
        # turn; the next real session should still open at its beginning.
        self._has_rendered_session = False


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


def plan_landing_items(items: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Normalize the active-teaching index for the classroom landing page.

    The landing page deliberately consumes the canonical workspace index only.
    Archived workspaces, setup-only rows, and malformed rows are kept out of
    the entry cards so a click can never start an ambiguous classroom.
    """

    normalized: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict) or bool(raw.get("archived")):
            continue
        workspace_id = str(raw.get("workspace_id") or "").strip()
        plan_id = str(raw.get("plan_id") or "").strip()
        if not workspace_id or not plan_id:
            continue
        today = raw.get("today_plan")
        if not isinstance(today, dict):
            today = {}
        task_rows = [item for item in today.get("tasks") or [] if isinstance(item, dict)]
        total = max(0, int(raw.get("knowledge_point_count") or raw.get("task_count") or 0))
        completed = max(
            0,
            int(
                raw.get("completed_knowledge_point_count")
                if raw.get("completed_knowledge_point_count") is not None
                else raw.get("completed_task_count") or 0
            ),
        )
        if total:
            completed = min(completed, total)
        normalized.append(
            {
                **raw,
                "workspace_id": workspace_id,
                "plan_id": plan_id,
                "title": str(raw.get("title") or workspace_id),
                "today_plan": today,
                "today_tasks": task_rows,
                "today_task_count": max(0, int(today.get("task_count") or len(task_rows))),
                "today_planned_minutes": max(0, int(today.get("planned_task_minutes") or 0)),
                "today_completed_minutes": max(0, int(today.get("completed_task_minutes") or 0)),
                "progress_total": total,
                "progress_completed": completed,
            }
        )
    return normalized


class _PlanLandingCard(QFrame):
    """One selectable plan card shown before the native classroom session."""

    def __init__(
        self,
        item: dict[str, Any],
        on_start: Callable[[str], None],
        on_details: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workspace_id = str(item.get("workspace_id") or "")
        self.setObjectName("classroomPlanCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            "QFrame#classroomPlanCard { background: #fffdfb; border: 1px solid #eadde2; "
            "border-radius: 18px; }"
            "QFrame#classroomPlanCard:hover { border-color: #c58aa7; background: #fff9fc; }"
            "QLabel#classroomPlanTitle { color: #553449; font-size: 19px; font-weight: 800; }"
            "QLabel#classroomPlanMeta { color: #756b72; font-size: 13px; }"
            "QLabel#classroomPlanTopics { color: #665b62; font-size: 13px; }"
            "QProgressBar#classroomPlanProgress { height: 9px; border: 0; border-radius: 4px; "
            "background: #f0e5ea; text-align: center; }"
            "QProgressBar#classroomPlanProgress::chunk { border-radius: 4px; background: #bd7699; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        title_row = QHBoxLayout()
        title = QLabel(str(item.get("title") or self.workspace_id), self)
        title.setObjectName("classroomPlanTitle")
        title.setWordWrap(True)
        title_row.addWidget(title, 1)
        status = QLabel("学习中" if str(item.get("status") or "") == "learning" else "已激活", self)
        status.setStyleSheet("color: #9e4a72; font-weight: 700; padding: 3px 9px; background: #fff0f5; border-radius: 10px;")
        title_row.addWidget(status, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(title_row)

        today_count = int(item.get("today_task_count") or 0)
        today_minutes = int(item.get("today_planned_minutes") or 0)
        completed_minutes = int(item.get("today_completed_minutes") or 0)
        overdue_count = max(0, int((item.get("today_plan") or {}).get("overdue_task_count") or 0))
        overdue_minutes = max(0, int((item.get("today_plan") or {}).get("overdue_task_minutes") or 0))
        layout.addWidget(
            QLabel(
                f"今日学习  ·  {today_count} 个任务  ·  {today_minutes} 分钟"
                + (f"  ·  已完成 {min(completed_minutes, today_minutes)} 分钟" if today_minutes else ""),
                self,
            )
        )
        meta = layout.itemAt(layout.count() - 1).widget()
        if meta is not None:
            meta.setObjectName("classroomPlanMeta")
        if overdue_count:
            overdue = QLabel(f"待补学习  ·  {overdue_count} 个任务  ·  {overdue_minutes} 分钟", self)
            overdue.setObjectName("classroomPlanMeta")
            overdue.setStyleSheet("color: #a35c39; font-size: 13px; font-weight: 700;")
            layout.addWidget(overdue)

        total = int(item.get("progress_total") or 0)
        completed = int(item.get("progress_completed") or 0)
        progress_text = f"总进度  {completed}/{total} 个知识点" if total else "总进度  暂无可统计知识点"
        progress = QProgressBar(self)
        progress.setObjectName("classroomPlanProgress")
        progress.setRange(0, 100)
        progress.setValue(round(completed * 100 / total) if total else 0)
        progress.setFormat(progress_text + (f"  ·  {progress.value()}%" if total else ""))
        progress.setTextVisible(True)
        layout.addWidget(progress)

        topics = [str(task.get("title") or task.get("knowledge_point_name") or "").strip() for task in item.get("today_tasks") or []]
        topics = [topic for topic in topics if topic]
        if len(topics) > 3:
            topics = [*topics[:3], f"还有 {len(topics) - 3} 个任务"]
        topics_label = QLabel("今日主题：" + ("、".join(topics) if topics else "暂未安排知识点"), self)
        topics_label.setObjectName("classroomPlanTopics")
        topics_label.setWordWrap(True)
        layout.addWidget(topics_label)

        actions = QHBoxLayout()
        actions.addStretch()
        details = QPushButton("查看/调整计划", self)
        details.setObjectName("secondaryButton")
        details.clicked.connect(lambda: on_details(self.workspace_id))
        actions.addWidget(details)
        start = QPushButton("开始今日课程", self)
        start.setObjectName("primaryButton")
        start.clicked.connect(lambda: on_start(self.workspace_id))
        actions.addWidget(start)
        layout.addLayout(actions)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            primary = self.findChild(QPushButton, "primaryButton")
            if primary is not None:
                primary.click()
        super().mousePressEvent(event)


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
        self._evidence_panel: QTextBrowser | None = None
        self._evidence_dialog: QDialog | None = None
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
        self._active_learning_seconds = 0
        self._last_learning_activity_at = time.monotonic()
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(1_000)
        self._activity_timer.timeout.connect(self._track_active_learning_second)
        self._activity_timer.start()
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        data_root = Path(os.getenv("MENTORFLOW_USER_DATA_ROOT", r"E:\MentorFlowData"))
        self._voice_temp_root = data_root / "temp" / "voice"
        self._workspace_items: list[dict[str, Any]] = []
        self._landing_items: list[dict[str, Any]] = []
        self._view_stack: QStackedWidget | None = None
        self._landing_cards_layout: QVBoxLayout | None = None
        self._landing_status: QLabel | None = None
        self._landing_summary: QLabel | None = None
        self._build()
        # Keep construction usable with small/fake API clients used by native
        # tests; production navigation refreshes again when this page opens.
        self.refresh_workspaces()

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        """Notice real classroom interaction without counting background idle time."""

        if event.type() in {
            QEvent.Type.KeyPress,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.Wheel,
            QEvent.Type.TouchBegin,
        }:
            belongs_to_classroom = watched is self
            if isinstance(watched, QWidget):
                belongs_to_classroom = belongs_to_classroom or self.isAncestorOf(watched)
            if belongs_to_classroom:
                self._last_learning_activity_at = time.monotonic()
        return super().eventFilter(watched, event)

    def _track_active_learning_second(self) -> None:
        if not self.session_id or not self.isVisible() or not self.window().isActiveWindow():
            return
        # Reading and thinking are learning activity, but an abandoned window
        # must not silently accumulate hours. A fresh classroom interaction
        # opens a bounded two-minute active interval.
        if time.monotonic() - self._last_learning_activity_at <= 120:
            self._active_learning_seconds += 1

    def _activity_payload(self) -> tuple[float, str]:
        if self._active_learning_seconds <= 0:
            return 0.0, ""
        return round(self._active_learning_seconds / 60.0, 3), f"desktop-{uuid.uuid4().hex}"

    def _commit_activity(self) -> None:
        self._active_learning_seconds = 0
        self._last_learning_activity_at = time.monotonic()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._view_stack = QStackedWidget(self)
        outer.addWidget(self._view_stack, 1)
        landing = self._build_plan_landing()
        self._view_stack.addWidget(landing)
        classroom_panel = QWidget(self)
        layout = QVBoxLayout(classroom_panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        def compact_button(button: QPushButton, height: int = 36) -> None:
            button.setMinimumHeight(height)
            button.setStyleSheet(f"QPushButton {{ min-height: {height}px; padding: 0 11px; }}")

        heading_row = QHBoxLayout()
        heading = QLabel("原生课堂")
        heading.setObjectName("pageHeading")
        heading_row.addWidget(heading)
        heading_row.addStretch()
        stage_button = QPushButton("展开教师面板")
        stage_button.setToolTip("在课堂右侧展开 Live2D 教师面板；默认不占用对话区。")
        stage_button.clicked.connect(self.open_teacher_stage)
        compact_button(stage_button)
        self._teacher_stage_button = stage_button
        heading_row.addWidget(stage_button)
        layout.addLayout(heading_row)
        subtitle = QLabel("教师进入概念后自动讲解、举例并提出最小检查；回答错误时按状态机纠错，不由模型擅自宣布掌握。")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        controls = QGridLayout()
        controls.setHorizontalSpacing(8)
        controls.setVerticalSpacing(8)
        controls.addWidget(QLabel("学习任务"), 0, 0)
        self.workspace = QComboBox()
        controls.addWidget(self.workspace, 0, 1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh_workspaces)
        compact_button(refresh)
        controls.addWidget(refresh, 0, 2)
        evidence_button = QPushButton("依据")
        evidence_button.setToolTip("在单独窗口查看当前知识点的资料依据，不占用课堂对话空间。")
        evidence_button.clicked.connect(self.open_evidence)
        compact_button(evidence_button)
        self._evidence_button = evidence_button
        controls.addWidget(evidence_button, 0, 3)
        controls.addWidget(QLabel("课堂管理"), 1, 0)
        exit_classroom = QPushButton("退出课堂")
        exit_classroom.setToolTip("离开当前课堂并返回任务选择；不会删除学习记录。")
        exit_classroom.clicked.connect(self.exit_classroom)
        compact_button(exit_classroom)
        controls.addWidget(exit_classroom, 1, 1)
        teacher_library = QPushButton("更换教师")
        teacher_library.setToolTip("从教师库选择并应用另一位教师")
        teacher_library.clicked.connect(self.open_teacher_library)
        compact_button(teacher_library)
        controls.addWidget(teacher_library, 1, 2)
        modeling = QPushButton("编辑当前教师")
        modeling.setToolTip("编辑当前教师人设；保存后可应用到本计划")
        modeling.clicked.connect(self.open_teacher_modeling)
        compact_button(modeling)
        controls.addWidget(modeling, 1, 3)
        controls.setColumnStretch(1, 1)
        layout.addLayout(controls)

        self.state = QLabel("请选择已激活计划的任务。")
        self.state.setWordWrap(True)
        layout.addWidget(self.state)

        current_point_card = QFrame(classroom_panel)
        current_point_card.setObjectName("currentKnowledgePointCard")
        current_point_card.setMinimumHeight(72)
        current_point_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        current_point_card.setStyleSheet(
            "QFrame#currentKnowledgePointCard { background: #fff0f5; border: 1px solid #e7b7ca; "
            "border-radius: 16px; }"
            "QLabel#currentKnowledgePointKicker { color: #9e4a72; font-size: 12px; font-weight: 850; }"
            "QLabel#currentKnowledgePointName { color: #553449; font-size: 20px; font-weight: 850; }"
            "QLabel#currentKnowledgePointMeta { color: #725b68; font-size: 12px; }"
            "QLabel#currentKnowledgePointReview { color: #8d3f62; font-size: 12px; font-weight: 800; }"
        )
        current_point_layout = QVBoxLayout(current_point_card)
        current_point_layout.setContentsMargins(14, 9, 14, 9)
        current_point_layout.setSpacing(3)
        current_point_header = QHBoxLayout()
        current_point_kicker = QLabel("当前知识点", current_point_card)
        current_point_kicker.setObjectName("currentKnowledgePointKicker")
        current_point_header.addWidget(current_point_kicker)
        current_point_header.addStretch()
        self.current_point_review = QLabel("", current_point_card)
        self.current_point_review.setObjectName("currentKnowledgePointReview")
        self.current_point_review.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        current_point_header.addWidget(self.current_point_review)
        current_point_layout.addLayout(current_point_header)
        current_point_body = QHBoxLayout()
        current_point_body.setSpacing(12)
        self.current_point_name = QLabel("进入课堂后显示", current_point_card)
        self.current_point_name.setObjectName("currentKnowledgePointName")
        self.current_point_name.setWordWrap(True)
        self.current_point_name.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        current_point_body.addWidget(self.current_point_name, 1)
        self.current_point_meta = QLabel("", current_point_card)
        self.current_point_meta.setObjectName("currentKnowledgePointMeta")
        self.current_point_meta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.current_point_meta.setWordWrap(True)
        self.current_point_meta.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        current_point_body.addWidget(self.current_point_meta)
        current_point_layout.addLayout(current_point_body)
        self.current_point_card = current_point_card
        layout.addWidget(current_point_card)

        teacher_strip = QFrame()
        teacher_strip.setObjectName("explanationCard")
        teacher_strip_layout = QVBoxLayout(teacher_strip)
        teacher_strip_layout.setContentsMargins(12, 8, 12, 8)
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("今日 AI 教师"))
        title_row.addStretch()
        teacher_strip_layout.addLayout(title_row)
        teacher_hint = QLabel("教师会先讲解、举例和检查，再等待你的回答。")
        teacher_hint.setWordWrap(True)
        teacher_strip_layout.addWidget(teacher_hint)
        self._evidence_panel = QTextBrowser(teacher_strip)
        self._evidence_panel.setObjectName("classroomEvidencePanel")
        self._evidence_panel.setReadOnly(True)
        self._evidence_panel.setOpenExternalLinks(True)
        self._evidence_panel.setOpenLinks(True)
        self._evidence_panel.setMaximumHeight(180)
        self._evidence_panel.setHtml(self._evidence_html)
        self._evidence_panel.setVisible(False)
        layout.addWidget(teacher_strip)

        conversation = QFrame()
        conversation.setObjectName("classroomConversation")
        conversation_layout = QVBoxLayout(conversation)
        conversation_layout.setContentsMargins(0, 0, 0, 0)
        conversation_layout.setSpacing(8)
        self.transcript = ClassroomTranscriptView()
        conversation_layout.addWidget(self.transcript, 1)
        self.answer = QPlainTextEdit()
        self.answer.setMinimumHeight(56)
        self.answer.setMaximumHeight(88)
        self.answer.setPlaceholderText("写下你的解释、答案或具体不理解的地方……")
        conversation_layout.addWidget(self.answer)
        self.pending_attachments = QLabel("未选择附件。")
        self.pending_attachments.setWordWrap(True)
        conversation_layout.addWidget(self.pending_attachments)
        answer_actions = QVBoxLayout()
        answer_actions.setSpacing(6)
        answer_inputs = QHBoxLayout()
        answer_inputs.setSpacing(6)
        attach = QPushButton("添加截图/文档")
        attach.clicked.connect(self.choose_attachments)
        answer_inputs.addWidget(attach)
        transcribe = QPushButton("🎙 语音")
        transcribe.setToolTip("点击后申请麦克风权限并开始语音输入；再次点击结束。")
        transcribe.clicked.connect(self.toggle_voice_recording)
        self.voice_button = transcribe
        answer_inputs.addWidget(transcribe)
        answer_inputs.addStretch()
        answer_actions.addLayout(answer_inputs)
        completion_actions = QHBoxLayout()
        completion_actions.setSpacing(6)
        completion_actions.addStretch()
        self_assessed = QPushButton("我已掌握，完成此知识点")
        self_assessed.setToolTip(
            "直接完成当前知识点并进入下一知识点；当前进度会立即保存。"
        )
        self_assessed.clicked.connect(self.skip_to_next_point)
        completion_actions.addWidget(self_assessed)
        send = QPushButton("提交回答")
        send.setObjectName("primaryButton")
        send.clicked.connect(self.send_answer)
        completion_actions.addWidget(send)
        answer_actions.addLayout(completion_actions)
        for button in (attach, transcribe, self_assessed, send):
            compact_button(button)
        conversation_layout.addLayout(answer_actions)
        # The teacher stage is a sidecar window attached to the main window,
        # not a splitter child.  Keep the classroom conversation full width.
        layout.addWidget(conversation, 1)
        self._view_stack.addWidget(classroom_panel)
        self._view_stack.setCurrentIndex(0)

    def _build_plan_landing(self) -> QWidget:
        landing = QWidget(self)
        landing.setObjectName("classroomPlanLanding")
        landing.setStyleSheet(
            "QWidget#classroomPlanLanding { background: #fffafc; }"
            "QLabel#classroomLandingHeading { color: #553449; font-size: 30px; font-weight: 850; }"
            "QLabel#classroomLandingSubtitle { color: #756b72; font-size: 14px; }"
            "QLabel#classroomLandingSummary { color: #9e4a72; font-size: 14px; font-weight: 700; }"
            "QLabel#classroomLandingEmpty { color: #756b72; font-size: 15px; padding: 26px; }"
        )
        layout = QVBoxLayout(landing)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(10)
        heading_row = QHBoxLayout()
        heading = QLabel("全部计划 · 今日学习", landing)
        heading.setObjectName("classroomLandingHeading")
        heading_row.addWidget(heading)
        heading_row.addStretch()
        refresh = QPushButton("刷新计划", landing)
        refresh.setObjectName("secondaryButton")
        refresh.clicked.connect(self.refresh_workspaces)
        refresh.setMinimumHeight(36)
        heading_row.addWidget(refresh)
        layout.addLayout(heading_row)
        subtitle = QLabel("先选择今天要学习的计划，再进入教师课堂。已有课堂记录会在退出后保留。", landing)
        subtitle.setObjectName("classroomLandingSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self._landing_summary = QLabel("正在读取今日计划……", landing)
        self._landing_summary.setObjectName("classroomLandingSummary")
        layout.addWidget(self._landing_summary)
        self._landing_status = QLabel("", landing)
        self._landing_status.setObjectName("classroomLandingSubtitle")
        self._landing_status.setWordWrap(True)
        self._landing_status.hide()
        layout.addWidget(self._landing_status)

        scroll = QScrollArea(landing)
        scroll.setObjectName("classroomPlanCards")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 4, 0, 4)
        content_layout.setSpacing(12)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._landing_cards_layout = content_layout
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return landing

    def _render_plan_landing(self, items: list[dict[str, Any]], *, error: str = "") -> None:
        self._landing_items = plan_landing_items(items)
        if self._landing_summary is not None:
            count = len(self._landing_items)
            self._landing_summary.setText(f"今天有 {count} 个可学习计划" if count else "今天还没有可进入的已激活计划")
        if self._landing_status is not None:
            self._landing_status.setText(error)
            self._landing_status.setVisible(bool(error))
        if self._landing_cards_layout is None:
            return
        while self._landing_cards_layout.count():
            item = self._landing_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._landing_items:
            empty = QLabel(
                "还没有可进入的已激活学习计划。请先在“学习计划”中完成计划生成/激活；"
                "如果刚刚完成设置，可以点击右上角刷新。",
                self,
            )
            empty.setObjectName("classroomLandingEmpty")
            empty.setWordWrap(True)
            self._landing_cards_layout.addWidget(empty)
            open_plans = QPushButton("查看/调整学习计划", self)
            open_plans.setObjectName("secondaryButton")
            open_plans.clicked.connect(lambda: self._open_plan_details(""))
            self._landing_cards_layout.addWidget(open_plans, 0, Qt.AlignmentFlag.AlignLeft)
            self._landing_cards_layout.addStretch(1)
            return
        for item in self._landing_items:
            card = _PlanLandingCard(item, self.start_session, self._open_plan_details)
            self._landing_cards_layout.addWidget(card)
        self._landing_cards_layout.addStretch(1)

    def _open_plan_details(self, workspace_id: str) -> None:
        """Route the secondary card action to the existing plan editor when available."""

        host = self.window()
        handler = getattr(host, "_open_workspace_detail", None)
        if workspace_id and callable(handler):
            handler(workspace_id)
            return
        if not workspace_id:
            navigation = getattr(host, "navigation", None)
            if navigation is not None and hasattr(navigation, "setCurrentRow"):
                navigation.setCurrentRow(4)
                return
        if not workspace_id:
            self.state.setText("请先在学习计划页创建并激活一个计划。")
            return
        try:
            plan = self.api.current_mastery_plan(workspace_id)
        except (ApiError, AttributeError) as exc:
            message = exc.message if isinstance(exc, ApiError) else str(exc)
            self.state.setText(f"无法读取计划：{message}")
            return
        tasks = plan.get("execution_tasks") or plan.get("tasks") or []
        detail = QDialog(self)
        detail.setWindowTitle("学习计划 · 查看/调整")
        detail.resize(760, 560)
        detail_layout = QVBoxLayout(detail)
        detail_layout.addWidget(QLabel(str(plan.get("title") or plan.get("goal") or "当前学习计划")))
        overview = QPlainTextEdit(detail)
        overview.setReadOnly(True)
        lines = []
        for task in tasks:
            if isinstance(task, dict):
                lines.append(
                    f"{task.get('day_date') or '未排期'}  ·  {task.get('title') or '未命名任务'}  ·  "
                    f"{task.get('estimated_task_minutes') or 0} 分钟  ·  {task.get('status') or 'planned'}"
                )
        overview.setPlainText("\n".join(lines) or "当前计划尚未生成可执行任务。")
        detail_layout.addWidget(overview, 1)
        close = QPushButton("返回")
        close.clicked.connect(detail.accept)
        detail_layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        detail.exec()

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
        web.setPage(install_external_link_handler(QWebEnginePage(profile, web)))
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
        """Show current-point evidence without expanding the classroom layout."""

        if self._evidence_dialog is not None and self._evidence_dialog.isVisible():
            self._evidence_dialog.raise_()
            self._evidence_dialog.activateWindow()
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("课堂依据")
        dialog.resize(760, 460)
        content = QVBoxLayout(dialog)
        hint = QLabel("当前知识点的资料依据")
        hint.setObjectName("cardHeading")
        content.addWidget(hint)
        browser = QTextBrowser(dialog)
        browser.setOpenExternalLinks(True)
        browser.setOpenLinks(True)
        browser.setHtml(self._evidence_html)
        content.addWidget(browser, 1)
        close = QPushButton("关闭", dialog)
        close.clicked.connect(dialog.close)
        content.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self._evidence_dialog = dialog
        dialog.finished.connect(lambda _result: setattr(self, "_evidence_dialog", None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _toggle_evidence_panel(self, visible: bool) -> None:
        # Kept for compatibility with older callers that sent a toggled
        # signal.  Evidence now lives in a compact dialog, never in the
        # conversation's vertical layout.
        if visible:
            self.open_evidence()
        elif self._evidence_dialog is not None:
            self._evidence_dialog.close()

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
        web.setPage(install_external_link_handler(QWebEnginePage(profile, web)))
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
            payload = self.api.active_workspaces()
            items = payload.get("items", []) if isinstance(payload, dict) else []
            if not isinstance(items, (list, tuple)):
                items = []
        except ApiError as exc:
            self.state.setText(f"读取任务失败：{exc.code}：{exc.message}")
            self._render_plan_landing(self._workspace_items, error=f"读取计划失败：{exc.code}：{exc.message}")
            return
        except (AttributeError, TypeError) as exc:
            # Small API doubles and partially initialized local runtimes can
            # still open the landing shell with a clear, recoverable state.
            self._render_plan_landing(self._workspace_items, error=f"暂时无法读取计划：{exc}")
            return
        if self.session_id:
            # Refresh the authoritative session snapshot as well, so a point
            # advanced elsewhere is reflected immediately in the native card.
            try:
                session = self.api.teaching_session(self.session_id)
            except (ApiError, AttributeError, TypeError):
                session = None
            if isinstance(session, dict):
                self._render_session(session)
        self._workspace_items = [dict(item) for item in items if isinstance(item, dict)]
        self.workspace.blockSignals(True)
        self.workspace.clear()
        for item in self._workspace_items:
            workspace_id = str(item.get("workspace_id") or "")
            if workspace_id:
                title = str(item.get("title") or workspace_id)
                self.workspace.addItem(title, workspace_id)
                self.workspace.setItemData(self.workspace.count() - 1, title, Qt.ItemDataRole.ToolTipRole)
        if selected:
            index = self.workspace.findData(selected)
            if index >= 0:
                self.workspace.setCurrentIndex(index)
        self.workspace.blockSignals(False)
        self._render_plan_landing(self._workspace_items)

    def start_session(self, workspace_id: str | None = None) -> None:
        requested_workspace = str(workspace_id or "").strip()
        if requested_workspace:
            index = self.workspace.findData(requested_workspace)
            if index < 0:
                self.state.setText("该计划已不可用，请刷新今日计划后重试。")
                return
            self.workspace.setCurrentIndex(index)
        workspace_id = str(self.workspace.currentData() or "")
        if not workspace_id:
            self.state.setText("请先从今日计划中选择一个计划。")
            return
        try:
            session = self.api.start_teaching_session(workspace_id, self.provider_session())
        except ApiError as exc:
            self.state.setText(f"无法进入课堂：{exc.code}：{exc.message}")
            return
        except (AttributeError, TypeError) as exc:
            self.state.setText(f"无法进入课堂：{exc}")
            return
        self.session_id = str(session.get("session_id") or "")
        self._commit_activity()
        self._load_teacher_stage(str(self.workspace.currentData() or ""))
        self._render_session(session)
        if self._view_stack is not None:
            self._view_stack.setCurrentIndex(1)

    def exit_classroom(self) -> None:
        """Leave the current classroom view without deleting any learning data."""
        self._cancel_voice_recording()
        self._commit_activity()
        self.session_id = ""
        self.current_session = {}
        self._render_current_point({})
        self.pending_attachment_ids.clear()
        self.answer.clear()
        self.pending_attachments.setText("未选择附件。")
        self.transcript.setHtml("<p>已退出本次课堂。请选择任务后重新进入，学习记录不会被删除。</p>")
        self._evidence_html = "<p>当前回合没有可展示的引用。</p>"
        self._collapse_teacher_stage()
        self.state.setText("已退出本次课堂。请选择学习任务后重新进入；已有学习记录仍会保留。")
        if self._view_stack is not None:
            self._view_stack.setCurrentIndex(0)
        self.refresh_workspaces()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        # Switching to another left-nav page must also hide the sidecar; it is
        # a classroom companion, never a floating overlay for other pages.
        self._cancel_voice_recording()
        self._collapse_teacher_stage()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._cancel_voice_recording()
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
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
        web.setPage(install_external_link_handler(QWebEnginePage(profile, web)))
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
        elapsed, event_id = self._activity_payload()
        try:
            session = self.api.advance_teaching_session(
                self.session_id,
                message=message,
                provider_session_id=self.provider_session(),
                attachment_ids=self.pending_attachment_ids,
                actual_elapsed_minutes=elapsed,
                activity_event_id=event_id,
            )
        except ApiError as exc:
            self.state.setText(f"教师回合失败：{exc.code}：{exc.message}")
            return
        self._commit_activity()
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
            QMessageBox.information(self, "先进入课堂", "语音会作为课堂回答转成文字。请先从计划卡进入课堂。")
            return
        permission = QMicrophonePermission()
        app = QCoreApplication.instance()
        if app is None:
            self.state.setText("无法读取麦克风权限状态，请重新打开软件后重试。")
            return
        permission_status = app.checkPermission(permission)
        if permission_status == Qt.PermissionStatus.Undetermined:
            self.voice_button.setText("正在申请麦克风权限…")
            self.voice_button.setEnabled(False)
            self.state.setText("请在系统提示中允许 MentorFlow 使用麦克风。")
            app.requestPermission(permission, self, self._microphone_permission_result)
            return
        if permission_status == Qt.PermissionStatus.Denied:
            self._show_microphone_permission_help()
            return
        self._start_voice_recording()

    def _microphone_permission_result(self, permission: QMicrophonePermission) -> None:
        self.voice_button.setEnabled(True)
        self.voice_button.setText("🎙 语音")
        app = QCoreApplication.instance()
        if app is not None and app.checkPermission(permission) == Qt.PermissionStatus.Granted:
            self._start_voice_recording()
        else:
            self._show_microphone_permission_help()

    def _show_microphone_permission_help(self) -> None:
        self.state.setText("麦克风权限未开启。可以前往 Windows 设置允许桌面应用访问麦克风。")
        message = QMessageBox(self)
        message.setWindowTitle("需要麦克风权限")
        message.setText("MentorFlow 需要麦克风权限才能把语音转成文字。")
        message.setInformativeText("你可以打开 Windows 麦克风设置，允许桌面应用访问麦克风，然后回来重试。")
        open_settings = message.addButton("打开麦克风设置", QMessageBox.ButtonRole.AcceptRole)
        message.addButton("稍后", QMessageBox.ButtonRole.RejectRole)
        message.exec()
        if message.clickedButton() is open_settings:
            QDesktopServices.openUrl(QUrl("ms-settings:privacy-microphone"))

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
        self.voice_button.setText("🎙 语音")
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
            self.voice_button.setText("🎙 语音")

    def skip_to_next_point(self) -> None:
        if not self.session_id:
            self.state.setText("请先进入课堂。")
            return
        elapsed, event_id = self._activity_payload()
        try:
            session = self.api.advance_teaching_session(
                self.session_id,
                message="学习者自我确认已掌握，完成当前知识点并进入下一知识点。",
                provider_session_id=self.provider_session(),
                force_complete=True,
                actual_elapsed_minutes=elapsed,
                activity_event_id=event_id,
            )
        except ApiError as exc:
            self.state.setText(f"无法进入下一知识点：{exc.code}：{exc.message}")
            return
        self._commit_activity()
        self._render_session(session)

    def _render_current_point(self, session: dict[str, Any]) -> None:
        """Refresh the compact current-point summary before the transcript."""

        summary = current_knowledge_point_summary(session)
        name = str(summary.get("name") or "").strip() or "进入课堂后显示"
        phase = str(summary.get("phase") or "").strip()
        route = str(summary.get("route") or "").strip()
        meta = " · ".join(item for item in (f"阶段：{phase}" if phase else "", route) if item)
        self.current_point_name.setText(name)
        self.current_point_meta.setText(meta)
        self.current_point_review.setText(str(summary.get("review") or ""))
        self.current_point_review.setVisible(bool(summary.get("review")))

    def _render_session(self, session: dict[str, Any]) -> None:
        self.current_session = session
        self._render_current_point(session)
        self.transcript.set_session(session)
        state = session.get("state") or {}
        turn = state.get("teacher_turn_v1") or {}
        turn_state = turn.get("state") or {}
        phase_labels = {
            "understand": "理解知识点",
            "recall": "回忆知识点",
            "guided_practice": "引导练习",
            "independent_application": "独立应用",
            "manual_skip": "已跳过（不计掌握）",
            "self_attested_completion": "已完成",
            "review": "复习",
        }
        status_labels = {
            "manual_skipped": "已跳过（不计掌握）",
            "self_attested": "已完成",
            "awaiting_answer": "等待回答",
            "in_progress": "进行中",
            "completed": "已完成",
        }
        phase = str(turn_state.get("phase") or state.get("current_teaching_step") or "未知")
        status = str(turn_state.get("status") or "in_progress")
        summary = current_knowledge_point_summary(session)
        progress_text = str(summary.get("route") or "").strip()
        state_bits = [
            f"阶段：{phase_labels.get(phase, _CLASSROOM_PHASE_LABELS.get(phase, phase))}",
            f"状态：{status_labels.get(status, status)}",
        ]
        if progress_text:
            state_bits.append(progress_text)
        self.state.setText(
            "课堂状态：已连接教师\n"
            + "　".join(state_bits)
            + "\n"
            f"预计任务：{state.get('estimated_task_minutes') or 0} 分钟；"
            f"实际累计：{session.get('actual_elapsed_minutes') or 0} 分钟"
        )
        citations: list[dict[str, Any]] = []
        projection = session.get("current_point") or session.get("current_point_projection")
        if isinstance(projection, dict):
            citations = [
                item for item in projection.get("evidence") or []
                if isinstance(item, dict)
            ]
        if not citations:
            citations = [
                item for item in session.get("current_point_evidence") or []
                if isinstance(item, dict)
            ]
        messages = session.get("messages") or []
        if not citations and messages and isinstance(messages[-1], dict):
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
        self._evidence_html = "".join(lines) if lines else "<p>当前知识点没有可展示的引用。</p>"
        if self._evidence_panel is not None:
            self._evidence_panel.setHtml(self._evidence_html)
