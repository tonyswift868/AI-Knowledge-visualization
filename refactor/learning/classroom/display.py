"""Extracted MF native atmosphere, themes and teacher/learner message bubbles.

Source: desktop/classroom_page.py. No DesktopApi, backend, webview or media runtime.
"""
from __future__ import annotations
import html
import os
import re
from copy import deepcopy
from typing import Any
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
    QLabel, QLayout, QPushButton, QScrollArea, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget)
from .theme import DEFAULT_CLASSROOM_THEME, classroom_theme, normalize_classroom_theme

BLOCK_LABELS = {
    "objective": "学习目标",
    "explanation": "核心讲解",
    "analogy": "结构类比",
    "worked_example": "完整示例",
    "correction": "针对性纠错",
    "alternate_example": "另一示例",
    "prerequisite": "前置补救",
}


def _transcript_stylesheet(theme_key: str) -> str:
    theme = classroom_theme(theme_key)
    bubble_style = str(theme.get("bubbleStyle", "soft-card"))
    shadow_name = str(theme.get("shadow", "soft-petal"))
    bubble_padding = int(theme.get("bubble_padding", 12)) + (1 if bubble_style in {"glass", "watercolor"} else 0)
    bubble_edge = int(theme.get("bubble_edge_width", 1))
    shadow_width = int(theme.get("bubble_shadow_width", 2))
    shadow_color = str(theme.get("shadow_color", theme["border"])) if shadow_name else theme["border"]
    bubble_shape = str(theme.get("bubble_shape", "rounded"))
    shape_radius = int(theme["bubble_radius"])
    if bubble_shape in {"minimal", "pixel", "terminal"}:
        shape_radius = min(shape_radius, 5)
    elif bubble_shape in {"pill", "glass"}:
        shape_radius = max(shape_radius, 14)
    return f"""
    QScrollArea#classroomTranscript {{ border: 0; border-radius: {theme['panel_radius']}px;
        background: {theme['surface_alt']}; }}
    QScrollArea#classroomTranscript QWidget#classroomTranscriptContent {{ background: transparent; }}
    QScrollArea#classroomTranscript QScrollBar:vertical {{ width: {theme['scrollbar_width']}px; margin: 5px 2px;
        background: transparent; border: 0; border-radius: {theme['control_radius']}px; }}
    QScrollArea#classroomTranscript QScrollBar::handle:vertical {{ min-height: 56px; border-radius: {theme['control_radius']}px;
        background: {theme['scrollbar']}; }}
    QScrollArea#classroomTranscript QScrollBar::add-line:vertical,
    QScrollArea#classroomTranscript QScrollBar::sub-line:vertical {{ height: 0; background: transparent; }}
    QTextBrowser#transcriptMessageBody {{ border: 0; background: transparent; color: {theme['text']};
        padding: 0; font-family: {theme['font_family']}; font-size: {theme['font_size']}px; }}
    QLabel#teacherMessageRole {{ color: {theme['accent_dark']}; font-size: 12px; font-weight: 800; }}
    QLabel#learnerMessageRole {{ color: {theme['accent_dark']}; font-size: 12px; font-weight: 800; }}
    QLabel#teacherAvatarBadge {{ color: white; background: {theme['accent']}; border-radius: {theme['bubble_radius']}px;
        min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; font-weight: 900; }}
    QLabel#learnerAvatarBadge {{ color: white; background: {theme['accent_dark']}; border-radius: {theme['bubble_radius']}px;
        min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; font-weight: 900; }}
    QPushButton#messageCopyButton {{ color: {theme['muted']}; border: 1px solid {theme['border']};
        border-radius: {theme['control_radius']}px; padding: 2px 8px; min-height: 24px; background: {theme['surface_elevated']}; }}
    QPushButton#messageCopyButton:hover {{ color: {theme['accent_dark']}; border-color: {theme['accent']};
        background: {theme['accent_soft']}; }}
    QLabel#messageAttachments {{ color: {theme['muted']}; font-size: 12px; padding-top: 3px; }}
    QFrame#teacherMessageBubble {{ background: {theme['teacher_bubble']}; border: {bubble_edge}px {theme['border_style']} {theme['teacher_border']};
        border-radius: {shape_radius}px; padding: {bubble_padding}px; border-bottom: {shadow_width}px solid {shadow_color}; }}
    QFrame#learnerMessageBubble {{ background: {theme['learner_bubble']}; border: {bubble_edge}px {theme['border_style']} {theme['learner_border']};
        border-radius: {shape_radius}px; padding: {bubble_padding}px; border-bottom: {shadow_width}px solid {shadow_color}; }}
    """


def _message_document_style(theme_key: str) -> str:
    theme = classroom_theme(theme_key)
    shape = str(theme.get("bubble_shape", "rounded"))
    code_background = str(theme["surface_alt"] if shape in {"terminal", "minimal", "pixel"} else "#24333a")
    return (
        f"body{{font-family:{theme['font_family']};"
        f"color:{theme['text']};margin:0;font-size:{theme['font_size']}px;line-height:1.62;}}"
        ".teacher-paragraph{margin:0 0 10px;}"
        f".teacher-example{{margin:10px 0;padding:10px 12px;background:{theme['accent_soft']};"
        f"border-radius:{theme['control_radius']}px;color:{theme['text']};}}"
        f".teacher-question{{margin:12px 0 6px;padding:11px 13px;background:{theme['status_bg']};"
        f"border-left:3px solid {theme['accent']};border-radius:{theme['control_radius']}px;font-weight:700;}}"
        f".teacher-invite{{margin:7px 0 0;color:{theme['accent_dark']};font-weight:700;}}"
        f".concept-heading{{color:{theme['accent_dark']};margin:0 0 10px;font-size:18px;}}"
        ".hard-focus{color:#b42318;font-weight:700;}"
        "a{color:#246b9b;text-decoration:underline;}"
        f"pre{{background:{code_background};color:{theme['text']};padding:10px;border-radius:{theme['control_radius']}px;white-space:pre-wrap;}}"
    )


class ClassroomAtmosphere(QWidget):
    """Low-cost animated backdrop; learning state remains completely separate."""

    def __init__(self, theme_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme_key = normalize_classroom_theme(theme_key)
        self._phase = 0.0
        self._ambient_timer = QTimer(self)
        self._ambient_timer.setInterval(55)
        self._ambient_timer.timeout.connect(self._tick_ambient)
        self._ambient_timer.start()

    def set_theme(self, theme_key: str) -> None:
        self._theme_key = normalize_classroom_theme(theme_key)
        self.update()

    def _tick_ambient(self) -> None:
        if not self.isVisible():
            return
        if os.getenv("MENTORFLOW_REDUCED_MOTION", "").strip().lower() in {"1", "true", "yes", "on"}:
            return
        theme = classroom_theme(self._theme_key)
        intensity = str(theme.get("motionIntensity", theme.get("motion_intensity", "gentle")))
        phase_step = {"quiet": 0.004, "gentle": 0.010, "active": 0.020}.get(intensity, 0.010)
        self._phase = (self._phase + phase_step) % 1000
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        theme = classroom_theme(self._theme_key)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if theme.get("background_direction") == "diagonal":
            gradient = QLinearGradient(0, 0, self.width(), self.height())
        else:
            gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, QColor(str(theme["background_top"])))
        gradient.setColorAt(1.0, QColor(str(theme["background_bottom"])))
        painter.fillRect(self.rect(), gradient)
        colors = tuple(str(value) for value in theme.get("ambientOverlay", theme["ambient"]))
        pattern = str(theme.get("backgroundPattern", theme.get("background_pattern", "")))
        pattern_color = QColor(str(theme.get("pattern_color", colors[0])))
        pattern_color.setAlpha(38)
        painter.setPen(pattern_color)
        width, height = self.width(), self.height()
        # Each named background family has its own small geometric grammar.
        # The renderer is deterministic for screenshot acceptance and cheap
        # enough for a classroom backdrop.
        if pattern == "petals":
            for i in range(14):
                x, y = (i * 137) % max(1, width), (i * 83) % max(1, height)
                painter.save(); painter.translate(x, y); painter.rotate(i * 27 + self._phase * 8)
                painter.drawEllipse(-13, -4, 26, 8); painter.restore()
        elif pattern in {"paper", "ruled-paper"}:
            step = 23 if pattern == "paper" else 20
            for y in range(step, height, step):
                painter.drawLine(0, y, width, y)
            if pattern == "ruled-paper":
                painter.drawLine(54, 0, 54, height)
        elif pattern == "clouds":
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(pattern_color)
            for i in range(6):
                x, y = (i * 211) % max(1, width), 70 + (i * 97) % max(1, height // 2 or 1)
                painter.drawEllipse(x, y, 130, 34); painter.drawEllipse(x + 25, y - 18, 66, 52)
        elif pattern == "stars":
            for i in range(32):
                x, y = (i * 173) % max(1, width), (i * 277) % max(1, height)
                radius = 2 + i % 3
                painter.drawLine(x - radius, y, x + radius, y); painter.drawLine(x, y - radius, x, y + radius)
        elif pattern == "chalk-grid":
            for x in range(0, width, 34): painter.drawLine(x, 0, x, height)
            for y in range(0, height, 30): painter.drawLine(0, y, width, y)
        elif pattern == "woodgrain":
            for offset in range(-height, width, 30):
                painter.drawArc(offset, -18, max(40, height // 2), height + 36, 0, 180 * 16)
        elif pattern == "window-light":
            painter.save()
            sun = QColor(str(colors[0])); sun.setAlpha(92)
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(sun)
            painter.drawEllipse(max(0, width - 260), 46, 126, 126)
            painter.restore()
            painter.setPen(pattern_color)
            for x in range(48, width, 180):
                painter.drawRect(x, 0, 4, height); painter.drawRect(x + 86, 0, 3, height)
            for y in range(80, height, 150): painter.drawLine(0, y, width, y)
            # Long diagonal desk/window shadows make the sunset composition
            # visibly different from Sakura instead of merely recolouring it.
            for offset in range(-height, width, 190):
                painter.drawLine(offset, 0, offset + height, height)
        elif pattern == "raindrops":
            painter.save()
            lamp = QColor(str(colors[2])); lamp.setAlpha(58)
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(lamp)
            painter.drawEllipse(max(0, width - 330), max(0, height - 300), 250, 250)
            painter.restore()
            mullion = QColor(str(theme.get("border", colors[0]))); mullion.setAlpha(105)
            painter.setPen(mullion)
            painter.drawLine(width // 2, 0, width // 2, height)
            painter.drawLine(0, height // 2, width, height // 2)
            painter.setPen(pattern_color)
            for i in range(28):
                x, y = (i * 97) % max(1, width), (i * 149) % max(1, height)
                painter.drawLine(x, y, x - 4, y + 18 + i % 8)
        elif pattern == "wash":
            painter.setPen(Qt.PenStyle.NoPen)
            for i, color_value in enumerate(colors):
                color = QColor(color_value); color.setAlpha(42); painter.setBrush(color)
                painter.drawEllipse((i * 237) % max(1, width), (i * 131) % max(1, height), 220, 110)
        elif pattern == "ruled-paper":
            for y in range(20, height, 20): painter.drawLine(0, y, width, y)
            painter.drawLine(54, 0, 54, height)
        elif pattern == "circuit":
            for i in range(11):
                x, y = 30 + (i * 113) % max(1, width - 30), 30 + (i * 71) % max(1, height - 30)
                painter.drawLine(x, y, min(width, x + 52), y); painter.drawLine(min(width, x + 52), y, min(width, x + 52), y + 28)
                painter.drawEllipse(min(width, x + 46), y - 3, 7, 7)
        elif pattern == "whiteboard":
            for x in range(0, width, 70): painter.drawLine(x, 0, x, height)
            for y in range(0, height, 70): painter.drawLine(0, y, width, y)
        elif pattern == "pixel-grid":
            for x in range(0, width, 16):
                for y in range(0, height, 16):
                    if (x // 16 + y // 16) % 3 == 0: painter.drawRect(x, y, 4, 4)
        elif pattern in {"leaves", "coffee-rings"}:
            for i in range(12):
                x, y = (i * 139) % max(1, width), (i * 91) % max(1, height)
                if pattern == "coffee-rings": painter.drawEllipse(x, y, 32 + i % 4 * 8, 16 + i % 3 * 5)
                else:
                    painter.save(); painter.translate(x, y); painter.rotate(i * 23); painter.drawEllipse(-14, -5, 28, 10); painter.restore()
        # Decorative layers are a second, named pass over the background.
        decorative_name = str(theme.get("decorativeLayer", "marker-dots"))
        family = str(theme.get("decorative_family", "dot"))
        if decorative_name in {"scanline", "scanlines"}:
            family = "scanline"
        descriptor = theme.get("decorative_descriptor") or {}
        count = int(descriptor.get("count", 12))
        motion = float(descriptor.get("motion", 0.004))
        for i in range(count):
            x = (i * 173 + 27) % max(1, width)
            y = int(((i * 257 + 31) + self._phase * motion * max(1, height)) % max(1, height))
            color = QColor(colors[i % len(colors)]); color.setAlpha(24 if self._theme_key != "night" else 40)
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(color)
            if family in {"petal", "leaf"}:
                painter.save(); painter.translate(x, y); painter.rotate(i * 31 + self._phase * 8); painter.drawEllipse(-12, -4, 24, 8); painter.restore()
            elif family == "star":
                painter.setPen(color); painter.drawLine(x - 7, y, x + 7, y); painter.drawLine(x, y - 7, x, y + 7)
            elif family == "cloud": painter.drawEllipse(x, y, 48 + i % 3 * 18, 15)
            elif family == "rain": painter.setPen(color); painter.drawLine(x, y, x - 5, y + 28)
            elif family == "ray":
                painter.drawLine(x, y, x + 80, y + 18); painter.drawLine(x + 80, y + 18, x + 110, y + 90)
            elif family == "wash": painter.drawEllipse(x, y, 70 + i % 3 * 20, 32 + i % 4 * 10)
            elif family == "scanline": painter.drawRect(0, y, width, 1 + i % 2)
            elif family == "steam": painter.setPen(color); painter.drawArc(x, y, 24, 46, 45 * 16, 240 * 16)
            elif family == "note": painter.drawRect(x, y, 20, 14)
            else: painter.drawEllipse(x, y, 4 + i % 3, 4 + i % 3)
        # Texture is intentionally subtle: it differentiates fibres, CRT,
        # frost and carbon without competing with the transcript.
        texture_name = str(theme.get("texture", "paper-grain"))
        texture_family = str(theme.get("texture_family", "grain"))
        texture = theme.get("texture_descriptor") or {}
        texture_color = QColor(str(theme.get("pattern_color", colors[0]))); texture_color.setAlpha(int(texture.get("alpha", 14)))
        painter.setPen(texture_color)
        step = max(6, int(texture.get("step", 20)))
        if texture_name == "crt":
            texture_family = "scanline"
        elif texture_name == "lined-paper":
            texture_family = "lines"
        if texture_family in {"lines", "scanline"}:
            for y in range(0, height, step): painter.drawLine(0, y, width, y)
        elif texture_family == "crosshatch":
            for offset in range(-height, width, step): painter.drawLine(offset, 0, offset + height, height)
        elif texture_family == "carbon":
            for x in range(0, width, step): painter.drawLine(x, 0, x + height, height)
        elif texture_family in {"grain", "fibres", "frost", "erase", "mist", "velvet", "chalk", "wash"}:
            for i in range(max(8, width // step)):
                x, y = (i * 83) % max(1, width), (i * 47) % max(1, height)
                painter.drawPoint(x, y); painter.drawPoint((x + 17) % max(1, width), (y + 11) % max(1, height))
        painter.end()
        super().paintEvent(event)


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


def message_copy_text(message: dict[str, Any]) -> str:
    """Return the plain-text body used by a per-message copy action."""

    turn = message.get("teacher_turn")
    if isinstance(turn, dict):
        text = teacher_turn_text(turn)
    else:
        text = str(message.get("content") or "").strip()
    # Focus markers are an internal state-machine representation.  The
    # rendered message exposes their visible label and value, so copied text
    # must do the same without leaking ``[[...]]`` syntax.
    return re.sub(
        r"\[\[(重难点|难点|重点)\s*[:：]\s*([^\]]+)\]\]",
        lambda match: f"{match.group(1)}：{match.group(2).strip()}",
        text,
    )


def _safe_link(value: object) -> str:
    raw = str(value or "").strip()
    if raw.startswith(("https://", "http://")):
        return raw
    return ""


def _inline_teacher_html(value: object) -> str:
    """Render a small safe Markdown subset; raw HTML is always escaped."""
    raw = str(value or "")
    # Shared complete() returns text. Preserve fenced examples without requiring
    # the old structured-provider schema or letting markup inside code execute.
    segments = re.split(r"(```[^\n`]*\n[\s\S]*?```)", raw)
    if len(segments) > 1:
        rendered = []
        for index, segment in enumerate(segments):
            if index % 2:
                code = segment.split("\n", 1)[1][:-3]
                rendered.append("<pre><code>" + html.escape(code) + "</code></pre>")
            else:
                rendered.append(_inline_teacher_html(segment))
        return "".join(rendered)
    text = html.escape(raw, quote=False)
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
        blocks.append(f'<h3 class="concept-heading">{html.escape(str(name))}</h3>')
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
        if block_type == "worked_example":
            blocks.append(f'<div class="teacher-example">{content_html}</div>')
        elif block_type in {"correction", "alternate_example", "prerequisite"}:
            blocks.append(f'<section class="teacher-block"><h4>{title}</h4><div>{content_html}</div></section>')
        else:
            # The turn remains structured in state and copy/export data, but
            # the visible classroom should read like a conversation rather
            # than exposing every internal block label to the learner.
            blocks.append(f'<p class="teacher-paragraph">{content_html}</p>')
    check = turn.get("check") or {}
    if isinstance(check, dict) and check.get("prompt"):
        blocks.append(f'<div class="teacher-question">{_inline_teacher_html(check["prompt"])}</div>')
    expected = turn.get("expected_learner_action") or turn.get("expected_action")
    check_prompt = str(check.get("prompt") or "").strip() if isinstance(check, dict) else ""
    if expected and str(expected).strip() != check_prompt:
        blocks.append(f'<p class="teacher-invite">{_inline_teacher_html(expected)}</p>')
    return "".join(blocks) or "<p>教师暂时没有生成可展示的内容。</p>"


class ClassroomTranscriptView(QScrollArea):
    """Scrollable native message bubbles for the classroom transcript.

    QTextDocument's HTML renderer does not consistently honor ``border-radius``
    on tables across Qt platforms.  Each message is therefore a real QFrame,
    while its safe rich-text body remains a QTextBrowser so links, selection,
    and the existing teacher formatting continue to work.
    """

    def __init__(self, theme_key: str = DEFAULT_CLASSROOM_THEME, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme_key = normalize_classroom_theme(theme_key)
        self._last_session: dict[str, Any] = {}
        self._rendered_message_count = 0
        self._bubble_animations: list[QPropertyAnimation] = []
        self.setObjectName("classroomTranscript")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet(_transcript_stylesheet(self._theme_key))
        self._content = QWidget(self)
        self._content.setObjectName("classroomTranscriptContent")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(14, 14, 14, 14)
        self._layout.setSpacing(12)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.setWidget(self._content)
        self._has_rendered_session = False

    def set_theme(self, theme_key: str) -> None:
        normalized = normalize_classroom_theme(theme_key)
        if normalized == self._theme_key:
            return
        self._theme_key = normalized
        self.setStyleSheet(_transcript_stylesheet(normalized))
        # Restyle existing bodies in place. Rebuilding a long transcript here
        # used to destroy/recreate every bubble and could make a simple skin
        # preview look frozen while also resetting scroll/focus state.
        document_style = _message_document_style(normalized)
        for body in self.findChildren(QTextBrowser, "transcriptMessageBody"):
            content = str(body.property("mentorflowMessageHtml") or "")
            if content:
                body.setHtml("<style>" + document_style + "</style>" + content)
                body.document().setDocumentMargin(0)

    def _clear_messages(self) -> None:
        for animation in self._bubble_animations:
            animation.stop()
        self._bubble_animations.clear()
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
        body.setProperty("mentorflowMessageHtml", content)
        body.setHtml(
            "<style>"
            + _message_document_style(self._theme_key)
            + "</style>"
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
        header = QHBoxLayout()
        header.setSpacing(6)
        free_chat = str(message.get("action") or "") in {"free_chat", "free_chat_response"}
        role = QLabel(
            ("教师 · 自由聊天" if teacher else "你 · 自由聊天")
            if free_chat
            else ("教师" if teacher else "你"),
            bubble,
        )
        role.setObjectName("teacherMessageRole" if teacher else "learnerMessageRole")
        avatar = QLabel("师" if teacher else "我", bubble)
        avatar.setObjectName("teacherAvatarBadge" if teacher else "learnerAvatarBadge")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if teacher:
            header.addWidget(avatar)
            header.addWidget(role)
            header.addStretch(1)
        else:
            role.setAlignment(Qt.AlignmentFlag.AlignRight)
            header.addStretch(1)
            header.addWidget(role)
            header.addWidget(avatar)
        copy_button = QPushButton("复制", bubble)
        copy_button.setObjectName("messageCopyButton")
        copy_button.setToolTip("复制这条消息的纯文本")
        copy_button.clicked.connect(
            lambda _checked=False, selected=message, button=copy_button: self._copy_message(
                selected, button
            )
        )
        header.addWidget(copy_button, 0, Qt.AlignmentFlag.AlignTop)
        bubble_layout.addLayout(header)
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

    def _animate_bubble(self, bubble: QFrame) -> None:
        effect = QGraphicsOpacityEffect(bubble)
        effect.setOpacity(0.12)
        bubble.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", bubble)
        animation.setDuration(260)
        animation.setStartValue(0.12)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: bubble.setGraphicsEffect(None))
        animation.finished.connect(lambda: self._bubble_animations.remove(animation) if animation in self._bubble_animations else None)
        self._bubble_animations.append(animation)
        animation.start()

    @staticmethod
    def _copy_message(message: dict[str, Any], button: QPushButton) -> None:
        """Copy one message from the Qt GUI thread without exposing markup."""

        QApplication.clipboard().setText(message_copy_text(message))
        button.setText("已复制")

    def _fit_bodies(self) -> None:
        available = max(180, self.viewport().width() - 22)
        for bubble in self._content.findChildren(QFrame, options=Qt.FindChildOption.FindDirectChildrenOnly):
            body = bubble.findChild(QTextBrowser)
            if body is None:
                continue
            role = str(bubble.property("messageRole") or "teacher")
            # Structured teaching turns are materially denser than ordinary
            # chat.  Give them a calm, readable content column inspired by
            # mature role-based chat layouts instead of squeezing them into a
            # narrow form-like card.  Learner replies stay a little shorter so
            # the left/right speaking rhythm remains immediately visible.
            max_width = 960 if role == "teacher" else 760
            ratio = 0.80 if role == "teacher" else 0.70
            width = max(150, min(max_width, int(available * ratio) - 32))
            body.setFixedWidth(width)
            body.document().setTextWidth(width)
            # adjustSize() resets QTextDocument's wrapping width to its guessed
            # ideal size, causing a short sentence to wrap inside a wide bubble.
            height = max(34, int(body.document().size().height()) + 4)
            body.setFixedHeight(height)
        self._layout.invalidate()
        self._content.setMinimumHeight(self._layout.sizeHint().height())
        self._content.updateGeometry()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        QTimer.singleShot(0, self, self._fit_bodies)

    def _fit_and_scroll_to_latest(self, *, scroll_to_latest: bool = True) -> None:
        self._fit_bodies()
        if scroll_to_latest:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        else:
            # Keep the opening at its beginning so a long structured lesson is
            # scannable; later replies intentionally follow the newest turn.
            self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())

    def set_session(self, session: dict[str, Any]) -> None:
        self._last_session = deepcopy(session) if isinstance(session, dict) else {}
        self._render_session(self._last_session, animate_from=self._rendered_message_count)

    def _render_session(self, session: dict[str, Any], *, animate_from: int) -> None:
        scroll_to_latest = self._has_rendered_session
        self._clear_messages()
        messages = _current_point_messages(session)
        for index, message in enumerate(messages):
            bubble = self._make_bubble(message)
            alignment = Qt.AlignmentFlag.AlignLeft if message.get("role") == "teacher" else Qt.AlignmentFlag.AlignRight
            self._layout.addWidget(bubble, 0, alignment)
            if index >= animate_from:
                self._animate_bubble(bubble)
        if not messages:
            empty = QLabel("准备好了就点击下方“开始上课”。老师会从当前知识点带你学起。", self._content)
            empty.setObjectName("messageAttachments")
            empty.setWordWrap(True)
            self._layout.addWidget(empty)
        self._layout.addStretch(1)
        self._scroll_to_latest = scroll_to_latest
        QTimer.singleShot(0, self, self._finish_layout)
        self._has_rendered_session = True
        self._rendered_message_count = len(messages)

    def _finish_layout(self) -> None:
        self._fit_and_scroll_to_latest(scroll_to_latest=self._scroll_to_latest)

    def setHtml(self, content: str) -> None:
        """Compatibility fallback for callers that still provide one HTML document."""

        self._clear_messages()
        self._last_session = {}
        self._rendered_message_count = 0
        bubble = QFrame(self._content)
        bubble.setObjectName("teacherMessageBubble")
        bubble.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(16, 12, 16, 13)
        bubble_layout.addWidget(self._make_body(content))
        self._layout.addWidget(bubble, 0, Qt.AlignmentFlag.AlignLeft)
        self._layout.addStretch(1)
        QTimer.singleShot(0, self, self._fit_and_scroll_to_latest)
        # Compatibility content (including the exit message) is not a lesson
        # turn; the next real session should still open at its beginning.
        self._has_rendered_session = False
