"""Native teacher-led classroom UI using structured TeacherTurnV1 data."""

from __future__ import annotations

import html
import json
import math
import os
import re
import sys
import time
import uuid
import wave
from copy import deepcopy
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QMicrophonePermission,
    QPropertyAnimation,
    QSettings,
    QTimer,
    Qt,
    QUrl,
)
from PySide6.QtGui import QColor, QDesktopServices, QLinearGradient, QPainter, QTextCursor
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
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QRadioButton,
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
from .classroom_theme import (
    DEFAULT_CLASSROOM_THEME,
    classroom_theme,
    normalize_classroom_theme,
)


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


_TEACHER_PRESENTATION_STATES = {
    "idle",
    "greeting",
    "thinking",
    "talking",
    "explaining",
    "questioning",
    "praising",
    "encouraging",
    "confused",
    "shy",
    "playful_annoyed",
    "ending",
}

_TEACHER_PRESENTATION_LABELS = {
    "idle": "待机",
    "greeting": "问候",
    "thinking": "思考中",
    "talking": "对话中",
    "explaining": "讲解中",
    "questioning": "提问中",
    "praising": "表扬",
    "encouraging": "鼓励",
    "confused": "一起梳理",
    "shy": "害羞",
    "playful_annoyed": "等待回答",
    "ending": "下课告别",
}


_TEACHER_ACTION_REVIEW_SETTINGS_KEY = "development/teacher_action_reviews_v1"
_FREE_CHAT_PROMPT_SETTINGS_KEY = "classroom/free_chat_prompt"
_TEACHER_ACTION_REVIEW_LABELS = {
    "pending": "待验收",
    "excellent": "★ 很好",
    "keep": "✓ 保留",
    "weak": "△ 太弱",
    "ineffective": "✕ 没效果",
    "unnatural": "~ 不自然",
}
_TEACHER_ACTION_REVIEW_FILTERS = (
    ("全部", "all"),
    ("待验收", "pending"),
    ("很好", "excellent"),
    ("保留", "keep"),
    ("太弱", "weak"),
    ("没效果", "ineffective"),
    ("不自然", "unnatural"),
)
_TEACHER_ACTION_REVIEW_VALUES = frozenset(_TEACHER_ACTION_REVIEW_LABELS)
_TEACHER_ACTION_FAILED_REVIEWS = frozenset({"weak", "ineffective", "unnatural"})


_TEACHER_ACTION_HUMAN_PROFILES: dict[str, tuple[int, str, str]] = {
    "IDLE_SOFT": (0, "自然待机", "模型身体轻微左右晃动与呼吸；舞台和头部不做周期平移。"),
    "TALK_SOFT": (10, "轻声说话", "轻柔的说话表情与头部变化。"),
    "TALK_ACTIVE": (11, "积极说话", "更明显的说话表情与头部变化。"),
    "THINK": (12, "思考", "进入思考相关表情或姿态。"),
    "QUESTION": (13, "提问", "进入提问相关表情或姿态。"),
    "ALERT": (20, "注意 / 警觉", "眼睛与头部进入注意状态。"),
    "NOD": (30, "点头", "头部做点头变化。"),
    "SHAKE_HEAD": (31, "摇头", "头部做左右摇头变化。"),
    "LOOK_LEFT": (32, "看左边", "视线向左移动。"),
    "LOOK_RIGHT": (33, "看右边", "视线向右移动。"),
    "LOOK_UP": (34, "向上看", "视线向上移动。"),
    "LOOK_DOWN": (35, "向下看", "视线向下移动。"),
    "HEAD_TILT_LEFT": (36, "向左歪头", "头部向左轻微倾斜。"),
    "HEAD_TILT_RIGHT": (37, "向右歪头", "头部向右轻微倾斜。"),
    "BLINK": (50, "眨眼", "双眼完成一次眨眼。"),
    "WINK": (51, "眨单眼", "一只眼睛闭合后恢复。"),
    "BREATH": (52, "呼吸", "呼吸参数发生轻微周期变化。"),
    "SURPRISED": (53, "惊讶", "眼睛和眉毛进入惊讶表情。"),
    "CRY": (59, "完整哭泣", "模型原生哭泣表情与泪水物理组合播放。"),
    "motion:Cry:0": (60, "哭泣", "模型原生哭泣动作，具体表现请播放确认。"),
    "expression:dark-face": (70, "阴沉 / 黑脸", "模型原生阴沉表情。"),
    "expression:blush": (71, "害羞脸红", "模型原生脸红表情。"),
    "expression:heart-eyes": (72, "爱心眼 / 非常开心", "模型原生爱心眼表情。"),
    "expression:no-highlights": (73, "失神 / 眼神失去高光", "模型原生失去眼神高光的表情。"),
    "expression:cry": (74, "哭泣表情", "模型原生哭泣表情。"),
}


def teacher_action_human_profile(action_id: object) -> tuple[int, str, str]:
    """Return truthful, user-facing action copy without inventing motion details."""

    normalized = str(action_id or "").strip()
    known = _TEACHER_ACTION_HUMAN_PROFILES.get(normalized)
    if known is not None:
        return known
    raw_name = normalized
    if normalized.startswith("motion:"):
        raw_name = normalized.split(":", 2)[1] or normalized
    elif normalized.startswith("expression:"):
        raw_name = normalized.split(":", 1)[1] or normalized
    return (999, f"{raw_name or '未命名动作'}（待确认）", "模型已注册该项目，具体表现请播放确认。")


def teacher_action_type_label(action: dict[str, Any]) -> str:
    implementation = str(action.get("implementation") or "").strip()
    if implementation == "registered-motion":
        return "🎬 模型原生动作"
    if implementation == "registered-expression":
        return "😊 模型表情"
    if implementation == "internal-parameters":
        return "🦴 参数驱动动作"
    return "🔧 适配动作"


def _classroom_panel_stylesheet(theme_key: str) -> str:
    theme = classroom_theme(theme_key)
    # Read canonical manifest names here (rather than only their aliases) so
    # every declared structural field participates in the scoped control
    # renderer.
    bubble_style = str(theme.get("bubbleStyle", "soft-card"))
    shadow_name = str(theme.get("shadow", "soft-petal"))
    chrome_name = str(theme.get("chrome", "soft"))
    icon_name = str(theme.get("iconTreatment", "line"))
    border_style = str(theme.get("borderStyle", theme.get("border_style", "solid")))
    if border_style not in {"solid", "dashed", "dotted"}:
        border_style = str(theme.get("border_style", "solid"))
    control_fill = str(theme.get(theme.get("control_fill_role", "surface_elevated"), theme["surface_elevated"]))
    control_border = str(theme.get(theme.get("control_border_role", "border"), theme["border"]))
    shadow_width = int(theme.get("bubble_shadow_width", 2))
    shadow_color = str(theme.get("shadow_color", theme["border"]))
    bubble_padding = int(theme.get("bubble_padding", 12)) + (1 if bubble_style in {"glass", "watercolor"} else 0)
    icon_weight = int(theme.get("icon_weight", 800)) + (50 if icon_name in {"glow", "hologram", "pixel"} else 0)
    icon_letter_spacing = int(theme.get("icon_letter_spacing", 0))
    button_weight = int(theme.get("button_weight", 800))
    focus_width = int(theme.get("focus_width", 2)) + (1 if chrome_name in {"cyber", "retro"} else 0)
    shadow_tint = shadow_color if shadow_name else theme["border"]
    return f"""
    QWidget#classroomSessionPanel {{ background: transparent; color: {theme['text']};
        font-family: {theme['font_family']}; font-size: {theme['font_size']}px; }}
    QWidget#classroomSessionPanel QLabel {{ color: {theme['text']}; background: transparent; }}
    QWidget#classroomSessionPanel QLabel#pageSubtitle {{ color: {theme['muted']}; }}
    QWidget#classroomSessionPanel QLabel#pageHeading {{ color: {theme['text']}; }}
    QWidget#classroomHeaderPanel {{ background: transparent; }}
    QLabel#classroomPageIcon {{ color: {theme['accent_dark']}; font-size: 20px; font-weight: {icon_weight};
        letter-spacing: {icon_letter_spacing}px; background: transparent; }}
    QWidget#classroomManagementPanel {{ background: {theme['surface_elevated']}; border: 1px {theme['border_style']} {theme['border']};
        border-radius: {theme['panel_radius']}px; padding: {theme['spacing']}px; }}
    QLabel#classroomStateLabel {{ color: {theme['muted']}; background: transparent;
        border: 0; padding: 0; }}
    QFrame#explanationCard {{ background: {theme['surface']}; border: 1px {theme['border_style']} {theme['border']};
        border-radius: {theme['panel_radius']}px; }}
    QFrame#classroomConversation {{ background: {theme['surface']}; border: 1px {theme['border_style']} {theme['border']};
        border-radius: {theme['panel_radius']}px; }}
    QFrame#classroomComposer {{ background: {theme['surface_inset']}; border: 1px {theme['border_style']} {theme['border']};
        border-radius: {theme['panel_radius']}px; }}
    QLabel#classroomConversationTitle {{ color: {theme['text']}; font-size: 17px; font-weight: {theme['title_weight']}; }}
    QLabel#classroomConversationStatus {{ color: {theme['accent_dark']}; background: {theme['status_bg']};
        border: 1px {theme['border_style']} {theme['status_border']}; border-radius: {theme['control_radius']}px; padding: 5px 10px; font-weight: 800; }}
    QFrame#classroomConversation QPlainTextEdit {{ color: {theme['text']}; background: {theme['surface']};
        border: 1px {theme['border_style']} {theme['border']}; border-radius: {theme['control_radius']}px; padding: 11px 13px;
        selection-background-color: {theme['accent_soft']}; }}
    QFrame#currentKnowledgePointCard {{ background: {theme['surface_alt']}; border: 1px solid {theme['border']};
        border-radius: {theme['panel_radius']}px; }}
    QLabel#currentKnowledgePointKicker, QLabel#currentKnowledgePointMeta {{ color: {theme['muted']}; }}
    QLabel#currentKnowledgePointName {{ color: {theme['text']}; font-size: 20px; font-weight: 850; }}
    QLabel#currentKnowledgePointReview {{ color: {theme['accent_dark']}; font-weight: 800; }}
    QWidget#classroomSessionPanel QPushButton {{ color: {theme['text']};
        background: {control_fill}; border: 1px {border_style} {control_border};
        border-radius: {theme['control_radius']}px; padding: 0 {bubble_padding}px; min-height: 36px;
        font-weight: {button_weight}; }}
    QWidget#classroomSessionPanel QPushButton:hover {{ color: {theme['accent_dark']};
        background: {theme['accent_soft']}; border-color: {theme['accent']}; }}
    QWidget#classroomSessionPanel QPushButton:pressed {{ background: {theme['status_bg']};
        border-color: {theme['accent_dark']}; }}
    QWidget#classroomSessionPanel QPushButton#classroomExitButton {{ color: #ffffff;
        background: #c73e4c; border: 1px solid #a72f3e; min-width: 104px; max-width: 142px;
        padding: 0 14px; font-weight: 850; }}
    QWidget#classroomSessionPanel QPushButton#classroomExitButton:hover {{ color: #ffffff;
        background: #ad3040; border-color: #8f2433; }}
    QWidget#classroomSessionPanel QPushButton#classroomExitButton:pressed {{ color: #ffffff;
        background: #8f2433; border-color: #7b1d2b; }}
    QWidget#classroomSessionPanel QPushButton:disabled {{ color: {theme['muted']}; background: {theme['surface_alt']};
        border-color: {theme['border']}; }}
    QWidget#classroomSessionPanel QPushButton:focus, QWidget#classroomSessionPanel QComboBox:focus,
    QWidget#classroomSessionPanel QPlainTextEdit:focus {{ border: {focus_width}px solid {theme['accent']}; }}
    QPushButton#teacherStageToggleButton, QPushButton#immersiveToggleButton {{ color: {theme['accent_dark']};
        background: {theme['accent_soft']}; border: 1px solid {theme['status_border']};
        border-radius: {theme['control_radius']}px; font-weight: {button_weight}; }}
    QPushButton#teacherStageToggleButton:hover, QPushButton#immersiveToggleButton:hover {{
        background: {theme['status_bg']}; border-color: {theme['accent']}; }}
    QWidget#classroomSessionPanel QComboBox {{ color: {theme['text']}; background: {control_fill};
        border: 1px {theme['border_style']} {control_border}; border-radius: {theme['control_radius']}px;
        padding: 0 10px; min-height: 36px; selection-background-color: {theme['accent_soft']}; }}
    QWidget#classroomSessionPanel QComboBox QAbstractItemView {{ color: {theme['text']}; background: {theme['surface']};
        border: 1px solid {theme['border']}; selection-background-color: {theme['accent_soft']}; }}
    QWidget#classroomSessionPanel QPlainTextEdit {{ color: {theme['text']}; background: {theme['surface']};
        border: 1px {theme['border_style']} {control_border}; border-radius: {theme['control_radius']}px;
        padding: 10px 12px; selection-background-color: {theme['accent_soft']}; }}
    QWidget#classroomSessionPanel QPlainTextEdit QScrollBar:vertical {{ width: {theme['scrollbar_width']}px;
        background: transparent; border: 0; }}
    QWidget#classroomSessionPanel QPlainTextEdit QScrollBar::handle:vertical {{ background: {theme['scrollbar']};
        border-radius: {theme['control_radius']}px; min-height: 40px; }}
    QWidget#classroomSessionPanel QRadioButton {{ spacing: 8px; color: {theme['text']}; font-weight: 700; padding: 6px 10px;
        border: 1px {theme['border_style']} {theme['border']}; border-radius: {theme['control_radius']}px; background: {theme['surface_elevated']}; }}
    QWidget#classroomSessionPanel QRadioButton:hover {{ border-color: {theme['accent']}; background: {theme['accent_soft']}; }}
    QWidget#classroomSessionPanel QRadioButton::indicator {{ width: 18px; height: 18px; border-radius: 9px;
        border: 2px solid {theme['accent']}; background: {theme['surface']}; }}
    QWidget#classroomSessionPanel QRadioButton::indicator:checked {{ border: none;
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {theme['accent']}, stop:0.31 {theme['accent']}, stop:0.35 {theme['surface']},
        stop:0.66 {theme['surface']}, stop:0.70 {theme['accent']}, stop:1 {theme['accent']}); }}
    QLabel#interactionNotice {{ color: {theme['accent_dark']}; background: {theme['status_bg']};
        border: 1px {theme['border_style']} {theme['status_border']}; border-radius: {theme['control_radius']}px; padding: 6px 9px; }}
    QFrame#classroomConversation, QFrame#classroomComposer, QFrame#currentKnowledgePointCard {{
        border-bottom: {shadow_width}px solid {shadow_tint}; }}
    """


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


def _teacher_stage_stylesheet(theme_key: str) -> str:
    theme = classroom_theme(theme_key)
    stage_name = str(theme.get("teacherStage", "sakura-window"))
    stage_frame = str(theme.get("stage_frame", "window"))
    stage_role = str((theme.get("stage_descriptor") or {}).get("panel_role", "surface_alt"))
    stage_background = str(theme.get(stage_role, theme["surface_alt"]))
    shadow_width = int(theme.get("bubble_shadow_width", 2))
    icon_weight = int(theme.get("icon_weight", 800))
    stage_border_style = "dashed" if stage_frame in {"board", "journal"} or stage_name == "journal-clip" else theme["border_style"]
    stage_radius = max(2, int(theme["panel_radius"]) - (2 if stage_frame in {"crt", "holo", "board"} else 0))
    return f"""
    QFrame#teacherStagePanel {{ border: {max(1, shadow_width // 2)}px {stage_border_style} {theme['border']}; border-radius: {stage_radius}px;
        background: {stage_background}; border-bottom: {shadow_width}px solid {theme['shadow_color']}; }}
    QLabel#teacherStageTitle {{ color: {theme['text']}; font-size: 18px; font-weight: {theme['title_weight']};
        letter-spacing: {int(theme.get('icon_letter_spacing', 0))}px; }}
    QLabel#teacherStageFrameMark {{ color: {theme['accent']}; font-size: 24px; font-weight: {icon_weight}; }}
    QLabel#teacherStageStatus {{ color: {theme['accent_dark']}; background: {theme['status_bg']};
        border: 1px {theme['border_style']} {theme['status_border']}; border-radius: {theme['control_radius']}px; padding: 4px 8px; font-weight: 800; }}
    QFrame#teacherStagePanel QPushButton {{ color: {theme['accent_dark']}; background: {theme['accent_soft']};
        border: 1px {theme['border_style']} {theme['accent']}; border-radius: {theme['control_radius']}px; padding: 5px 10px; font-weight: 800; }}
    QFrame#teacherStagePanel QPushButton:hover {{ background: {theme['status_bg']}; border-color: {theme['accent_dark']}; }}
    """


def _teacher_stage_window_stylesheet(theme_key: str) -> str:
    theme = classroom_theme(theme_key)
    return f"""
    QWidget#teacherStageWindow {{ background: {theme['background_bottom']}; color: {theme['text']};
        border: 1px {theme['border_style']} {theme['border']}; }}
    """


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


def teacher_presentation_state(
    session: dict[str, Any] | None,
    *,
    busy: bool = False,
) -> str:
    """Map durable teaching semantics to a renderer state.

    The sidecar must reflect the real classroom turn instead of remaining in a
    decorative, permanently ``explaining`` pose.  Only workflow semantics are
    inspected; no course title, subject, resource ID, or model-specific action
    is embedded here.
    """

    if busy:
        return "thinking"
    if not isinstance(session, dict) or not session:
        return "idle"
    state = session.get("state")
    state = state if isinstance(state, dict) else {}
    turn = state.get("teacher_turn_v1")
    turn = turn if isinstance(turn, dict) else {}
    free_turn = state.get("latest_free_chat_turn_v1")
    free_turn = free_turn if isinstance(free_turn, dict) else {}
    turn_state = turn.get("state")
    turn_state = turn_state if isinstance(turn_state, dict) else {}
    explicit_state = str(
        free_turn.get("presentation_state")
        or turn.get("presentation_state")
        or state.get("teacher_presentation_state")
        or ""
    ).strip().lower()
    if explicit_state in _TEACHER_PRESENTATION_STATES:
        return explicit_state
    action = str(
        free_turn.get("action")
        or turn.get("action")
        or state.get("last_teacher_action")
        or state.get("next_action")
        or ""
    ).strip().lower()
    phase = str(
        turn_state.get("phase")
        or state.get("current_teaching_step")
        or ""
    ).strip().lower()
    status = str(turn_state.get("status") or state.get("status") or "").strip().lower()

    if status == "completed" or phase == "completed" or re.search(
        r"complete_route|course_complete|lesson_end", action
    ):
        return "ending"
    if re.search(r"daily_briefing|knowledge_jump|lesson_start", action):
        return "greeting"
    if re.search(r"praise|pass|advance_route|resume_main_route|mastered", action):
        return "praising"
    if re.search(r"source_conflict|insufficient_evidence|confus", action):
        return "confused"
    if re.search(r"remediation|encourag|learning_coach", action):
        return "encouraging"
    if re.search(r"question|check|guided_practice|clarify_feedback|invite_questions|point_readiness", action):
        return "questioning"
    if re.search(r"explain|example", action):
        return "explaining"
    if re.search(r"free_chat|grounded_answer|model_general_answer|coach_response", action):
        return "talking"
    if status in {"awaiting_answer", "waiting_for_answer"}:
        return "questioning"
    return "talking" if turn or free_turn else "idle"


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
        free_chat = str(message.get("action") or "") in {"free_chat", "free_chat_response"}
        role = (
            "教师 · 自由聊天" if message.get("role") == "teacher" else "你 · 自由聊天"
        ) if free_chat else ("教师" if message.get("role") == "teacher" else "你")
        turn = message.get("teacher_turn")
        content = teacher_turn_text(turn) if isinstance(turn, dict) else str(message.get("content") or "")
        rendered.extend([f"──────── {role} ────────", content.strip(), ""])
        attachments = message.get("attachments") or []
        if attachments:
            rendered.append("附件：" + "、".join(str(item.get("original_filename") or item.get("id")) for item in attachments))
            rendered.append("")
    return "\n".join(rendered).strip()


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


def session_transcript_html(
    session: dict[str, Any], theme_key: str = DEFAULT_CLASSROOM_THEME
) -> str:
    theme = classroom_theme(theme_key)
    shadow_width = int(theme.get("bubble_shadow_width", 2))
    shape_radius = int(theme.get("bubble_radius", 16))
    rendered: list[str] = [
        "<style>"
        f"body{{font-family:{theme['font_family']};color:{theme['text']};background:{theme['background_top']};margin:4px 2px 16px;}}"
        f".teacher-message{{max-width:92%;margin:10px 0 14px;padding:13px 15px;background:{theme['teacher_bubble']};border:1px {theme['border_style']} {theme['teacher_border']};border-radius:{shape_radius}px;border-bottom:{shadow_width}px solid {theme['shadow_color']};}}"
        f".learner-message{{max-width:82%;margin:10px 0 14px auto;padding:11px 14px;background:{theme['learner_bubble']};border:1px {theme['border_style']} {theme['learner_border']};border-radius:{shape_radius}px;border-bottom:{shadow_width}px solid {theme['shadow_color']};}}"
        f".message-role{{font-size:12px;font-weight:{theme['button_weight']};color:{theme['accent_dark']};margin-bottom:7px;}}"
        f".learner-message .message-role{{color:{theme['accent_dark']};text-align:right;}}"
        f".teacher-block{{margin:8px 0;padding:9px 11px;background:{theme['accent_soft']};border-left:3px solid {theme['accent']};border-radius:{theme['control_radius']}px;}}"
        f".teacher-block h4{{margin:0 0 5px;color:{theme['accent_dark']};}} .concept-heading{{color:{theme['accent_dark']};}}"
        f".teacher-paragraph{{margin:0 0 10px}}.teacher-question{{padding:10px 12px;background:{theme['status_bg']};border-left:3px solid {theme['accent']};border-radius:{theme['control_radius']}px;font-weight:700}}.teacher-invite{{color:{theme['accent_dark']};font-weight:700}}"
        ".hard-focus{color:#b42318;font-weight:700;}"
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
        self._rendered_message_count = len(messages)

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
        # Restore the original compact, host-height sidecar proportions.  The
        # model canvas receives the full remaining height instead of sharing a
        # larger window with an action test console.
        screen = host.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        width = max(460, min(500, self.width()))
        x = frame.x() + frame.width() - 4
        if available is not None:
            if x + width > available.right() + 1:
                x = available.right() - width + 1
            x = max(available.left(), x)
        height = max(360, frame.height())
        if available is not None:
            height = min(height, available.height())
        y = frame.y()
        if available is not None:
            y = min(max(available.top(), y), available.bottom() - height + 1)
        self.setGeometry(x, y, width, height)

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
        self._teacher_stage_status: QLabel | None = None
        self._teacher_stage_splitter: QSplitter | None = None
        self._teacher_action_debug_dialog: QDialog | None = None
        self._teacher_action_debug_rows: QVBoxLayout | None = None
        self._teacher_action_debug_status: QLabel | None = None
        self._teacher_action_debug_notice: QLabel | None = None
        self._teacher_action_debug_filter: QComboBox | None = None
        self._teacher_action_debug_actions: list[dict[str, Any]] = []
        self._teacher_action_debug_played: set[str] = set()
        self._teacher_action_debug_reviews = self._load_teacher_action_reviews()
        self._teacher_action_debug_attempts = 0
        self._teacher_presentation_state = "idle"
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
        self._closed = False
        self._voice_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mentorflow-live-asr",
        )
        self._action_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mentorflow-classroom-actions",
        )
        self._action_future: Future[dict[str, Any]] | None = None
        self._action_generation = 0
        self._action_kind = ""
        self._action_context: dict[str, Any] = {}
        self.send_button: QPushButton | None = None
        self.complete_button: QPushButton | None = None
        self.attach_button: QPushButton | None = None
        self.learning_evidence_radio: QRadioButton | None = None
        self.free_chat_radio: QRadioButton | None = None
        self.interaction_notice: QLabel | None = None
        self.interaction_mode = "learning_evidence"
        self._classroom_theme_key = normalize_classroom_theme(
            QSettings("MentorFlow", "MentorFlow").value(
                "appearance/classroom_theme", DEFAULT_CLASSROOM_THEME
            )
        )
        self._classroom_panel: ClassroomAtmosphere | None = None
        self._conversation_status: QLabel | None = None
        self._typing_step = 0
        self._typing_timer = QTimer(self)
        self._typing_timer.setInterval(360)
        self._typing_timer.timeout.connect(self._advance_typing_indicator)
        # The classroom keeps the transcript and answer controls stable while
        # allowing a one-key presentation mode for focused teaching.  These
        # references are populated by ``_build`` once the upper management
        # shell exists.
        self._immersive_mode = False
        self._immersive_toggle: QPushButton | None = None
        self.immersive_toggle: QPushButton | None = None
        self._immersive_top_widgets: list[QWidget] = []
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
        if (
            not self.session_id
            or self.interaction_mode == "free_chat"
            or not self.isVisible()
            or not self.window().isActiveWindow()
        ):
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
        classroom_panel = ClassroomAtmosphere(self._classroom_theme_key, self)
        classroom_panel.setObjectName("classroomSessionPanel")
        classroom_panel.setStyleSheet(_classroom_panel_stylesheet(self._classroom_theme_key))
        self._classroom_panel = classroom_panel
        layout = QVBoxLayout(classroom_panel)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        def compact_button(button: QPushButton, height: int = 36) -> None:
            button.setMinimumHeight(height)
            # Keep all visual properties in the classroom-scoped stylesheet;
            # a per-button stylesheet would mask the selected skin and fall
            # back to desktop/style.py's Sakura controls.
            button.setProperty("classroomCompact", True)

        heading_row = QHBoxLayout()
        page_icon = QLabel(str(classroom_theme(self._classroom_theme_key).get("symbol", "✿")))
        page_icon.setObjectName("classroomPageIcon")
        page_icon.setAccessibleName("课堂主题图标")
        self._classroom_page_icon = page_icon
        heading_row.addWidget(page_icon)
        heading = QLabel("原生课堂")
        heading.setObjectName("pageHeading")
        heading_row.addWidget(heading)
        heading_row.addStretch()
        stage_button = QPushButton("打开教师舞台")
        stage_button.setObjectName("teacherStageToggleButton")
        stage_button.setAccessibleName("打开教师舞台")
        stage_button.setToolTip("打开可移动的教师舞台；对话区会保持完整宽度。")
        stage_button.clicked.connect(self.open_teacher_stage)
        compact_button(stage_button)
        self._teacher_stage_button = stage_button
        heading_row.addWidget(stage_button)
        action_debug = QPushButton("教师动作调试")
        action_debug.setObjectName("teacherActionDebugButton")
        action_debug.setAccessibleName("教师动作调试")
        action_debug.setToolTip("读取当前 Live2D 舞台注册动作与能力；仅调试当前会话，不保存设置。")
        action_debug.clicked.connect(self.open_teacher_action_debug)
        compact_button(action_debug)
        self._teacher_action_debug_button = action_debug
        debug_actions_enabled = not getattr(sys, "frozen", False) or os.getenv(
            "MENTORFLOW_ENABLE_TEACHER_ACTION_DEBUG", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        action_debug.setVisible(debug_actions_enabled)
        heading_row.addWidget(action_debug)
        heading_panel = QWidget(classroom_panel)
        heading_panel.setObjectName("classroomHeaderPanel")
        heading_panel.setLayout(heading_row)
        layout.addWidget(heading_panel)
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
        management_actions = QHBoxLayout()
        management_actions.setContentsMargins(0, 0, 0, 0)
        management_actions.setSpacing(8)
        management_actions.addStretch(1)
        teacher_library = QPushButton("更换教师")
        teacher_library.setToolTip("从教师库选择并应用另一位教师")
        teacher_library.clicked.connect(self.open_teacher_library)
        compact_button(teacher_library)
        management_actions.addWidget(teacher_library)
        modeling = QPushButton("编辑当前教师")
        modeling.setToolTip("编辑当前教师人设；保存后可应用到本计划")
        modeling.clicked.connect(self.open_teacher_modeling)
        compact_button(modeling)
        management_actions.addWidget(modeling)
        exit_classroom = QPushButton("退出课堂")
        exit_classroom.setObjectName("classroomExitButton")
        exit_classroom.setAccessibleName("退出课堂")
        exit_classroom.setToolTip("离开当前课堂并返回任务选择；不会删除学习记录。")
        exit_classroom.clicked.connect(self.exit_classroom)
        compact_button(exit_classroom)
        management_actions.addWidget(exit_classroom)
        management_actions_widget = QWidget(classroom_panel)
        management_actions_widget.setLayout(management_actions)
        controls.addWidget(management_actions_widget, 1, 1, 1, 3)
        controls.setColumnStretch(1, 1)
        management_panel = QWidget(classroom_panel)
        management_panel.setObjectName("classroomManagementPanel")
        management_panel.setLayout(controls)
        layout.addWidget(management_panel)

        self.state = QLabel("请选择已激活计划的任务。")
        self.state.setObjectName("classroomStateLabel")
        self.state.setWordWrap(True)

        current_point_card = QFrame(classroom_panel)
        current_point_card.setObjectName("currentKnowledgePointCard")
        current_point_card.setMinimumHeight(54)
        current_point_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Keep the status and point summary on one compact row.  The status
        # may grow to use spare width while the summary keeps its phase,
        # route, and review metadata grouped on the right.
        current_point_layout = QHBoxLayout(current_point_card)
        current_point_layout.setContentsMargins(12, 7, 12, 7)
        current_point_layout.setSpacing(16)
        self.state.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        current_point_layout.addWidget(self.state, 1, Qt.AlignmentFlag.AlignVCenter)

        current_point_summary = QWidget(current_point_card)
        current_point_summary.setObjectName("currentKnowledgePointSummary")
        current_point_summary.setAccessibleName("当前知识点摘要")
        current_point_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        current_point_summary_layout = QVBoxLayout(current_point_summary)
        current_point_summary_layout.setContentsMargins(0, 0, 0, 0)
        current_point_summary_layout.setSpacing(2)
        current_point_header = QHBoxLayout()
        current_point_kicker = QLabel("当前知识点", current_point_card)
        current_point_kicker.setObjectName("currentKnowledgePointKicker")
        current_point_header.addWidget(current_point_kicker)
        current_point_header.addStretch()
        self.current_point_review = QLabel("", current_point_card)
        self.current_point_review.setObjectName("currentKnowledgePointReview")
        self.current_point_review.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        current_point_header.addWidget(self.current_point_review)
        current_point_summary_layout.addLayout(current_point_header)
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
        current_point_summary_layout.addLayout(current_point_body)
        current_point_layout.addWidget(current_point_summary, 2, Qt.AlignmentFlag.AlignVCenter)
        self.current_point_card = current_point_card
        layout.addWidget(current_point_card)

        # Evidence is available from the explicit "依据" dialog.  The old
        # full-width teacher hint duplicated no actionable information and
        # pushed the actual conversation below the fold.
        self._evidence_panel = None

        # The upper shell is intentionally reversible and does not own any
        # session or draft state.  The immersive toggle itself lives with the
        # lower answer controls, so it remains available while this shell is
        # hidden.
        self._immersive_top_widgets = [
            heading_panel,
            subtitle,
            management_panel,
            current_point_card,
        ]

        conversation = QFrame()
        conversation.setObjectName("classroomConversation")
        conversation_layout = QVBoxLayout(conversation)
        conversation_layout.setContentsMargins(12, 12, 12, 12)
        conversation_layout.setSpacing(10)
        conversation_header = QHBoxLayout()
        conversation_title = QLabel("课堂对话")
        conversation_title.setObjectName("classroomConversationTitle")
        conversation_header.addWidget(conversation_title)
        conversation_header.addStretch(1)
        conversation_status = QLabel("●  教师在线")
        conversation_status.setObjectName("classroomConversationStatus")
        conversation_header.addWidget(conversation_status)
        self._conversation_status = conversation_status
        conversation_layout.addLayout(conversation_header)
        self.transcript = ClassroomTranscriptView(self._classroom_theme_key)
        conversation_layout.addWidget(self.transcript, 1)
        composer = QFrame(conversation)
        composer.setObjectName("classroomComposer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(10, 10, 10, 10)
        composer_layout.setSpacing(8)
        self.answer = QPlainTextEdit()
        self.answer.setMinimumHeight(48)
        self.answer.setMaximumHeight(72)
        self.answer.setPlaceholderText("写下你的解释、答案或具体不理解的地方……")
        composer_layout.addWidget(self.answer)
        interaction_row = QHBoxLayout()
        interaction_row.setSpacing(10)
        interaction_label = QLabel("本条消息用途")
        interaction_label.setToolTip("学习回答会参与课堂判断；自由聊聊只回应问题，不计入掌握判定或学习计划。")
        interaction_row.addWidget(interaction_label)
        learning_evidence = QRadioButton("学习回答")
        learning_evidence.setObjectName("learningEvidenceRadio")
        learning_evidence.setAccessibleName("学习回答模式")
        learning_evidence.setToolTip("参与当前知识点的学习回答与掌握判断。")
        learning_evidence.setChecked(True)
        learning_evidence.toggled.connect(
            lambda checked: checked and self._set_interaction_mode("learning_evidence")
        )
        interaction_row.addWidget(learning_evidence)
        free_chat = QRadioButton("自由聊聊")
        free_chat.setObjectName("freeChatRadio")
        free_chat.setAccessibleName("自由聊聊模式")
        free_chat.setToolTip("不会计入掌握判定，也不会推进学习计划。")
        free_chat.toggled.connect(
            lambda checked: checked and self._set_interaction_mode("free_chat")
        )
        interaction_row.addWidget(free_chat)
        interaction_row.addStretch(1)
        composer_layout.addLayout(interaction_row)
        self.learning_evidence_radio = learning_evidence
        self.free_chat_radio = free_chat
        self.interaction_notice = QLabel("")
        self.interaction_notice.setObjectName("interactionNotice")
        self.interaction_notice.setWordWrap(True)
        self.interaction_notice.setVisible(False)
        composer_layout.addWidget(self.interaction_notice)
        self.pending_attachments = QLabel("未选择附件。")
        self.pending_attachments.setWordWrap(True)
        self.pending_attachments.setVisible(False)
        composer_layout.addWidget(self.pending_attachments)
        answer_actions = QVBoxLayout()
        answer_actions.setSpacing(6)
        answer_inputs = QHBoxLayout()
        answer_inputs.setSpacing(6)
        attach = QPushButton("添加截图/文档")
        attach.setObjectName("classroomAttachmentButton")
        attach.setAccessibleName("添加截图或文档附件")
        attach.clicked.connect(self.choose_attachments)
        self.attach_button = attach
        answer_inputs.addWidget(attach)
        transcribe = QPushButton("🎙 语音")
        transcribe.setAccessibleName("语音输入")
        transcribe.setToolTip("点击后申请麦克风权限并开始语音输入；再次点击结束。")
        transcribe.clicked.connect(self.toggle_voice_recording)
        self.voice_button = transcribe
        answer_inputs.addWidget(transcribe)
        immersive_toggle = QPushButton("进入沉浸模式")
        immersive_toggle.setObjectName("immersiveToggleButton")
        immersive_toggle.setAccessibleName("沉浸课堂切换")
        immersive_toggle.setToolTip("隐藏课堂标题、管理、状态和知识点摘要；再次点击可恢复。")
        immersive_toggle.clicked.connect(self.toggle_immersive_mode)
        compact_button(immersive_toggle)
        self._immersive_toggle = immersive_toggle
        self.immersive_toggle = immersive_toggle
        answer_inputs.addWidget(immersive_toggle)
        answer_inputs.addStretch()
        answer_actions.addLayout(answer_inputs)
        completion_actions = QHBoxLayout()
        completion_actions.setSpacing(6)
        completion_actions.addStretch()
        self_assessed = QPushButton("我已掌握，完成此知识点")
        self_assessed.setAccessibleName("完成当前知识点")
        self_assessed.setToolTip(
            "直接完成当前知识点并进入下一知识点；当前进度会立即保存。"
        )
        self_assessed.clicked.connect(self.skip_to_next_point)
        self.complete_button = self_assessed
        completion_actions.addWidget(self_assessed)
        send = QPushButton("提交回答")
        send.setObjectName("primaryButton")
        send.setAccessibleName("提交课堂回答")
        send.clicked.connect(self.send_answer)
        self.send_button = send
        completion_actions.addWidget(send)
        answer_actions.addLayout(completion_actions)
        for button in (attach, transcribe, self_assessed, send):
            compact_button(button)
        composer_layout.addLayout(answer_actions)
        conversation_layout.addWidget(composer)

        # Conversation owns the full classroom width.  The teacher stage is
        # created lazily as a larger detachable window from the two persistent
        # stage buttons, so the Live2D surface never squeezes the transcript.
        layout.addWidget(conversation, 1)
        self._view_stack.addWidget(classroom_panel)
        self._view_stack.setCurrentIndex(0)

    @property
    def immersive_mode(self) -> bool:
        """Whether the reversible focused classroom layout is active."""

        return self._immersive_mode

    @property
    def classroom_theme_key(self) -> str:
        return self._classroom_theme_key

    def apply_theme(self, theme_key: str, *, persist: bool = True) -> None:
        """Apply one local visual skin without touching lesson or draft state."""

        normalized = normalize_classroom_theme(theme_key)
        self._classroom_theme_key = normalized
        if persist:
            settings = QSettings("MentorFlow", "MentorFlow")
            settings.setValue("appearance/classroom_theme", normalized)
            settings.sync()
        if self._classroom_panel is not None:
            self._classroom_panel.set_theme(normalized)
            self._classroom_panel.setStyleSheet(_classroom_panel_stylesheet(normalized))
        if hasattr(self, "_classroom_page_icon"):
            self._classroom_page_icon.setText(str(classroom_theme(normalized).get("symbol", "✿")))
        if hasattr(self, "transcript"):
            self.transcript.set_theme(normalized)
        if self._teacher_stage_panel is not None:
            self._teacher_stage_panel.setStyleSheet(_teacher_stage_stylesheet(normalized))
        if self._teacher_stage_window is not None:
            self._teacher_stage_window.setStyleSheet(_teacher_stage_window_stylesheet(normalized))
            for mark in self._teacher_stage_window.findChildren(QLabel, "teacherStageFrameMark"):
                mark.setText(str(classroom_theme(normalized).get("stage_header_mark", "✦")))
        web = self._teacher_stage_web
        if web is not None:
            payload = json.dumps(
                {"type": "mentorflow.teacher-theme", "theme": normalized},
                ensure_ascii=True,
                separators=(",", ":"),
            )
            web.page().runJavaScript(
                f"window.postMessage({payload}, window.location.origin);"
            )

    def _advance_typing_indicator(self) -> None:
        if self._conversation_status is None:
            return
        self._typing_step = (self._typing_step + 1) % 3
        self._conversation_status.setText("●  教师正在思考" + "·" * (self._typing_step + 1))

    def set_immersive_mode(self, enabled: bool) -> None:
        """Hide or restore only upper classroom chrome.

        Transcript, draft text, attachments, voice input, mode radios, and
        mastery/submit actions are deliberately outside ``_immersive_top_widgets``
        so switching modes cannot lose a learner's in-progress work.
        """

        self._immersive_mode = bool(enabled)
        for widget in self._immersive_top_widgets:
            widget.setVisible(not self._immersive_mode)
        button = self._immersive_toggle
        if button is not None:
            button.setText("退出沉浸模式" if self._immersive_mode else "进入沉浸模式")
            button.setToolTip(
                "恢复课堂管理、状态和知识点摘要。"
                if self._immersive_mode
                else "隐藏课堂管理、状态和知识点摘要；再次点击可恢复。"
            )
            button.setAccessibleName(
                "退出沉浸课堂" if self._immersive_mode else "进入沉浸课堂"
            )
        # Let Qt recompute the conversation height immediately after the
        # upper cards change visibility, including when the page is offscreen.
        self.layout().activate()

    def toggle_immersive_mode(self) -> None:
        """Toggle the focused classroom layout without changing the session."""

        self.set_immersive_mode(not self._immersive_mode)

    # Explicit alias for callers that describe the action as a classroom
    # toggle rather than a layout mode; both paths share one stateful method.
    def toggle_immersive_classroom(self) -> None:
        self.toggle_immersive_mode()

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
        panel.setStyleSheet(_teacher_stage_stylesheet(self._classroom_theme_key))
        panel.setMinimumWidth(330)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 16, 16, 16)
        panel_layout.setSpacing(10)
        self._teacher_stage_panel_layout = panel_layout
        header = QHBoxLayout()
        frame_mark = QLabel(str(classroom_theme(self._classroom_theme_key).get("stage_header_mark", "✦")))
        frame_mark.setObjectName("teacherStageFrameMark")
        frame_mark.setAccessibleName("教师舞台主题标记")
        header.addWidget(frame_mark)
        title = QLabel("动态教师舞台")
        title.setObjectName("teacherStageTitle")
        header.addWidget(title)
        header.addStretch()
        stage_status = QLabel("●  待机")
        stage_status.setObjectName("teacherStageStatus")
        header.addWidget(stage_status)
        self._teacher_stage_status = stage_status
        collapse = QPushButton("关闭")
        collapse.setAccessibleName("关闭教师舞台")
        collapse.setToolTip("关闭教师舞台；课堂对话不会中断。")
        collapse.clicked.connect(self._collapse_teacher_stage)
        collapse.setMinimumHeight(42)
        header.addWidget(collapse)
        panel_layout.addLayout(header)
        hint = QLabel("教师会随讲解、思考、提问、等待与鼓励自然切换表情和动作。")
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
        window.setStyleSheet(_teacher_stage_window_stylesheet(self._classroom_theme_key))
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
        # QWebEngine reports a newly-created page as hidden while its parent
        # top-level window is still hidden.  The Live2D runtime correctly
        # pauses hidden pages, so mounting the web view first can stop the
        # ticker before its first rendered frame and leave a ready-but-blank
        # stage.  Expose the detached window before creating/loading the view.
        self._teacher_stage_window.show()
        self._teacher_stage_window.raise_()
        self._ensure_teacher_stage_web()
        if self._teacher_stage_button is not None:
            self._teacher_stage_button.setText("关闭教师舞台")
            self._teacher_stage_button.setAccessibleName("关闭教师舞台")
            self._teacher_stage_button.setToolTip("关闭可移动的教师舞台，回到完整对话区。")

    def open_teacher_action_debug(self) -> None:
        """Show an opt-in inspector backed by the live stage registry.

        The native shell does not invent a second motion system.  Rows are
        read from the already-mounted React Live2D inspector, which itself is
        built from the model's runtime registry/capabilities.  Play and Stop
        therefore dispatch to the same controller instance that drives the
        visible stage and never remount the model or classroom session.
        """

        if not self.origin:
            QMessageBox.information(self, "教师动作调试", "当前服务没有可用的教师舞台页面。")
            return
        if self._teacher_stage_window is None or not self._teacher_stage_window.isVisible():
            self.open_teacher_stage()
        if self._teacher_action_debug_dialog is not None and self._teacher_action_debug_dialog.isVisible():
            self._teacher_action_debug_dialog.raise_()
            self._teacher_action_debug_dialog.activateWindow()
            self._refresh_teacher_action_debug()
            return

        dialog = QDialog(self)
        dialog.setObjectName("teacherActionDebugDialog")
        dialog.setWindowTitle("教师动作人工验收")
        dialog.setMinimumSize(1040, 680)
        layout = QVBoxLayout(dialog)
        title = QLabel("教师动作人工验收")
        title.setObjectName("teacherActionReviewTitle")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(title)
        hint = QLabel(
            "逐个播放并按肉眼观感评价。评价只保存在本机开发设置中，"
            "不会修改模型资产、学习状态、教师人设或业务数据库。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        status = QLabel("正在读取当前教师模型已注册的动作……")
        status.setObjectName("teacherActionDebugStatus")
        status.setWordWrap(True)
        layout.addWidget(status)
        notice = QLabel("播放后请直接观察教师模型；调用成功不等于肉眼有效。")
        notice.setObjectName("teacherActionDebugNotice")
        notice.setWordWrap(True)
        notice.setStyleSheet("color: #776d73;")
        layout.addWidget(notice)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("显示："))
        review_filter = QComboBox(dialog)
        review_filter.setAccessibleName("动作验收筛选")
        for filter_label, filter_value in _TEACHER_ACTION_REVIEW_FILTERS:
            review_filter.addItem(filter_label, filter_value)
        review_filter.currentIndexChanged.connect(self._rerender_teacher_action_debug_rows)
        filter_row.addWidget(review_filter)
        filter_row.addStretch(1)
        layout.addLayout(filter_row)
        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(scroll)
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        rows.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        actions = QHBoxLayout()
        refresh = QPushButton("刷新能力")
        refresh.setAccessibleName("刷新教师动作能力")
        refresh.clicked.connect(self._refresh_teacher_action_debug)
        actions.addWidget(refresh)
        stop = QPushButton("■ 停止当前动作")
        stop.setAccessibleName("停止教师动作")
        stop.clicked.connect(self._stop_teacher_action_debug)
        actions.addWidget(stop)
        restore = QPushButton("↺ 恢复待机")
        restore.setAccessibleName("恢复教师自然待机")
        restore.clicked.connect(self._restore_teacher_idle_debug)
        actions.addWidget(restore)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.close)
        actions.addWidget(close)
        actions.addStretch(1)
        layout.addLayout(actions)
        self._teacher_action_debug_dialog = dialog
        self._teacher_action_debug_rows = rows
        self._teacher_action_debug_status = status
        self._teacher_action_debug_notice = notice
        self._teacher_action_debug_filter = review_filter
        self._teacher_action_debug_actions = []
        self._teacher_action_debug_played = set()
        self._teacher_action_debug_attempts = 0
        dialog.finished.connect(self._on_teacher_action_debug_closed)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if self._teacher_stage_panel is not None:
            self._teacher_stage_panel.showNormal()
            self._teacher_stage_panel.raise_()
            dialog.raise_()
        self._refresh_teacher_action_debug()

    def _on_teacher_action_debug_closed(self, _result: int = 0) -> None:
        self._teacher_action_debug_dialog = None
        self._teacher_action_debug_rows = None
        self._teacher_action_debug_status = None
        self._teacher_action_debug_notice = None
        self._teacher_action_debug_filter = None
        self._teacher_action_debug_actions = []
        self._teacher_action_debug_played = set()

    @staticmethod
    def _load_teacher_action_reviews() -> dict[str, str]:
        raw = QSettings("MentorFlow", "MentorFlow").value(
            _TEACHER_ACTION_REVIEW_SETTINGS_KEY, "{}"
        )
        try:
            decoded = json.loads(str(raw or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = {}
        if not isinstance(decoded, dict):
            return {}
        return {
            str(action_id): str(review)
            for action_id, review in decoded.items()
            if str(review) in _TEACHER_ACTION_REVIEW_VALUES
            and str(review) != "pending"
        }

    def _save_teacher_action_reviews(self) -> None:
        settings = QSettings("MentorFlow", "MentorFlow")
        settings.setValue(
            _TEACHER_ACTION_REVIEW_SETTINGS_KEY,
            json.dumps(self._teacher_action_debug_reviews, ensure_ascii=False, sort_keys=True),
        )
        settings.sync()

    def _review_teacher_action(self, action_id: str, review: str) -> None:
        if review not in _TEACHER_ACTION_REVIEW_VALUES or review == "pending":
            return
        if review in _TEACHER_ACTION_FAILED_REVIEWS:
            # Failed human review is a durable local blocklist, not a label.
            # Remove the action from the tester immediately and keep it gone
            # across restarts; do not silently rename or rebuild it.
            self._teacher_action_debug_reviews[action_id] = review
            self._teacher_action_debug_actions = [
                action for action in self._teacher_action_debug_actions
                if str(action.get("id") or "") != action_id
            ]
            self._stop_teacher_action_debug()
            if self._teacher_action_debug_notice is not None:
                self._teacher_action_debug_notice.setText(
                    "该动作未通过验收，已从本机动作测试列表移除。"
                )
        elif self._teacher_action_debug_reviews.get(action_id) == review:
            self._teacher_action_debug_reviews.pop(action_id, None)
        else:
            self._teacher_action_debug_reviews[action_id] = review
        self._save_teacher_action_reviews()
        self._rerender_teacher_action_debug_rows()

    def _stop_teacher_action_debug(self) -> None:
        web = self._teacher_stage_web
        if web is None:
            return
        web.page().runJavaScript(
            "(() => { const buttons = [...document.querySelectorAll('button')]; "
            "buttons.find(button => button.textContent.trim() === 'Stop')?.click(); "
            "window.postMessage({type:'mentorflow.teacher-state', state:'idle'}, window.location.origin); })();"
        )
        if self._teacher_action_debug_notice is not None:
            self._teacher_action_debug_notice.setText(
                "已停止当前动作，并回到自然待机；评价结果不会被自动更改。"
            )

    def _restore_teacher_idle_debug(self) -> None:
        web = self._teacher_stage_web
        if web is None:
            return
        web.page().runJavaScript(
            "(() => { const buttons = [...document.querySelectorAll('button')]; "
            "buttons.find(button => button.textContent.trim() === 'Stop')?.click(); "
            "buttons.find(button => button.textContent.trim() === 'Restore authoritative state')?.click(); "
            "window.postMessage({type:'mentorflow.teacher-state', state:'idle'}, window.location.origin); })();"
        )
        if self._teacher_action_debug_notice is not None:
            self._teacher_action_debug_notice.setText(
                "已清理临时 Motion / Expression，并恢复自然轻微晃动的待机状态。"
            )

    def _refresh_teacher_action_debug(self) -> None:
        web = self._teacher_stage_web
        if web is None or self._teacher_action_debug_dialog is None:
            return
        self._teacher_action_debug_attempts += 1
        if self._teacher_action_debug_notice is not None:
            self._teacher_action_debug_notice.setText("正在读取当前教师模型已注册的动作……")
        # The React stage keeps the inspector opt-in.  Enable only this
        # debug surface; the underlying teacher canvas remains the same.
        web.page().runJavaScript(
            "(() => { const buttons = [...document.querySelectorAll('button')]; "
            "const face = buttons.find(item => item.textContent.trim() === '脸部'); "
            "if (face && face.getAttribute('aria-pressed') !== 'true') face.click(); "
            "const button = buttons.find(item => item.title === '调试信息'); "
            "if (button && !button.classList.contains('active')) button.click(); return Boolean(button); })();",
            lambda _enabled: self._collect_teacher_action_debug_rows(),
        )

    def _collect_teacher_action_debug_rows(self) -> None:
        web = self._teacher_stage_web
        if web is None or self._teacher_action_debug_dialog is None:
            return
        script = """
        (() => {
          const inspector = document.querySelector('[data-testid="live2d-motion-inspector"]');
          if (!inspector) return JSON.stringify({ready:false, live2d:false, actions:[]});
          const rows = [...inspector.querySelectorAll('[role="listitem"]')].map(row => {
            const button = row.querySelector('button[data-action-id]');
            if (!button || button.dataset.actionPreview !== 'true') return null;
            const title = button.getAttribute('title') || '';
            return {id: button.getAttribute('data-action-id') || '', label: button.textContent || '',
              source: button.dataset.actionSource || title.split('/')[0] || 'unknown',
              implementation: button.dataset.actionImplementation || title.split('/')[1] || 'unknown',
              parameters: button.dataset.actionParameters || '',
              reason: button.dataset.actionReason || '',
              supported: button.dataset.actionSupported !== 'false' && !button.disabled};
          }).filter(Boolean);
          return JSON.stringify({ready:true, live2d:true, actions:rows});
        })();
        """
        web.page().runJavaScript(script, self._render_teacher_action_debug_rows)

    def _render_teacher_action_debug_rows(self, payload: object) -> None:
        rows_layout = self._teacher_action_debug_rows
        status = self._teacher_action_debug_status
        if rows_layout is None:
            return
        while rows_layout.count() > 1:
            item = rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        data = payload if isinstance(payload, dict) else {}
        if isinstance(payload, str):
            try:
                decoded = json.loads(payload)
                data = decoded if isinstance(decoded, dict) else {}
            except json.JSONDecodeError:
                data = {}
        actions = data.get("actions") if isinstance(data, dict) else []
        if not data.get("ready"):
            if status is not None:
                status.setText("当前教师模型尚未完成动作注册；请稍候或确认教师舞台已加载。")
            if self._teacher_action_debug_attempts < 80:
                QTimer.singleShot(250, self._refresh_teacher_action_debug)
            return
        self._teacher_action_debug_actions = [
            dict(action)
            for action in actions
            if isinstance(action, dict) and str(action.get("id") or "").strip()
            and self._teacher_action_debug_reviews.get(str(action.get("id") or ""))
            not in _TEACHER_ACTION_FAILED_REVIEWS
        ]
        self._teacher_action_debug_actions.sort(
            key=lambda action: (
                teacher_action_human_profile(action.get("id"))[0],
                str(action.get("id") or ""),
            )
        )
        if self._teacher_action_debug_notice is not None:
            self._teacher_action_debug_notice.setText(
                "教师舞台已切到脸部近景。播放后请按肉眼效果评价；调用成功不等于动作通过。"
            )
        if not self._teacher_action_debug_actions:
            empty = QLabel("当前模型没有可执行的已注册动作。动作不会被伪造或改写为整模型变换。")
            empty.setWordWrap(True)
            rows_layout.insertWidget(0, empty)
            return
        self._rerender_teacher_action_debug_rows()

    def _rerender_teacher_action_debug_rows(self, _index: int = 0) -> None:
        rows_layout = self._teacher_action_debug_rows
        status = self._teacher_action_debug_status
        if rows_layout is None:
            return
        while rows_layout.count() > 1:
            item = rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        actions = self._teacher_action_debug_actions
        review_counts = {
            key: sum(
                1
                for action in actions
                if self._teacher_action_debug_reviews.get(str(action.get("id") or ""), "pending") == key
            )
            for key in _TEACHER_ACTION_REVIEW_LABELS
        }
        approved = review_counts["excellent"] + review_counts["keep"]
        if status is not None:
            status.setText(
                f"已发现动作 {len(actions)}　·　已注册 {len(actions)}　·　"
                f"待人工验收 {review_counts['pending']}　·　已通过 {approved}\n"
                f"很好 {review_counts['excellent']}　保留 {review_counts['keep']}　"
                f"太弱 {review_counts['weak']}　没效果 {review_counts['ineffective']}　"
                f"不自然 {review_counts['unnatural']}"
            )
        selected_filter = "all"
        if self._teacher_action_debug_filter is not None:
            selected_filter = str(self._teacher_action_debug_filter.currentData() or "all")
        filtered_actions = [
            action
            for action in actions
            if selected_filter == "all"
            or self._teacher_action_debug_reviews.get(str(action.get("id") or ""), "pending") == selected_filter
        ]
        if not filtered_actions:
            empty = QLabel("当前筛选下没有动作。")
            empty.setObjectName("teacherActionDebugEmpty")
            rows_layout.insertWidget(0, empty)
            return
        ordinal_by_id = {
            str(action.get("id") or ""): index
            for index, action in enumerate(actions, start=1)
        }
        for action in filtered_actions:
            action_id = str(action.get("id") or "").strip()
            if not action_id:
                continue
            _sort_order, human_name, description = teacher_action_human_profile(action_id)
            ordinal = ordinal_by_id[action_id]
            review = self._teacher_action_debug_reviews.get(action_id, "pending")
            row = QFrame()
            row.setObjectName("teacherActionDebugRow")
            row.setStyleSheet(
                "QFrame#teacherActionDebugRow { border: 1px solid #e5cbd6; border-radius: 10px; "
                "background: #fffafb; }"
            )
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(7)
            heading = QHBoxLayout()
            number = QLabel(f"{ordinal:02d}")
            number.setObjectName("teacherActionDebugNumber")
            number.setFixedWidth(34)
            number.setStyleSheet("font-weight: 700; color: #a84370;")
            heading.addWidget(number)
            label = QLabel(human_name)
            label.setObjectName("teacherActionDebugName")
            label.setStyleSheet("font-size: 16px; font-weight: 700;")
            heading.addWidget(label, 1)
            kind = QLabel(teacher_action_type_label(action))
            kind.setObjectName("teacherActionDebugType")
            heading.addWidget(kind)
            review_label = QLabel(_TEACHER_ACTION_REVIEW_LABELS.get(review, "待验收"))
            review_label.setObjectName("teacherActionDebugReviewState")
            review_label.setStyleSheet("font-weight: 700;")
            heading.addWidget(review_label)
            row_layout.addLayout(heading)

            summary = QLabel(f"预期表现：{description}")
            summary.setObjectName("teacherActionDebugDescription")
            summary.setWordWrap(True)
            row_layout.addWidget(summary)

            implementation = str(action.get("implementation") or "unknown")
            parameters = str(action.get("parameters") or "").strip()
            detail = (
                f"原始 ID：{action_id}　·　内部来源："
                f"{implementation} · {str(action.get('source') or 'unknown')}"
            )
            if parameters:
                detail += f"　·　参数：{parameters}"
            reason = str(action.get("reason") or "").strip()
            if reason:
                detail += f"　·　{reason}"
            source = QLabel(detail)
            source.setObjectName("teacherActionDebugSource")
            source.setStyleSheet("color: #776d73; font-size: 11px;")
            source.setWordWrap(True)
            source.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            row_layout.addWidget(source)

            support = bool(action.get("supported"))
            if not support:
                visible_state = "REGISTERED 已注册　·　PLAYABLE 当前不可调用　·　VISIBLE 待确认　·　APPROVED 待确认"
            elif action_id in self._teacher_action_debug_played:
                visible_state = "REGISTERED 已注册　·　PLAYABLE 已发起调用　·　VISIBLE 请肉眼确认"
            else:
                visible_state = "REGISTERED 已注册　·　PLAYABLE 待播放确认　·　VISIBLE 待人工验收"
            if review in {"excellent", "keep"}:
                visible_state += "　·　APPROVED 用户通过"
            elif review == "ineffective":
                visible_state += "　·　VISIBLE 用户判定无明显效果"
            elif review != "pending":
                visible_state += "　·　APPROVED 未通过"
            support_label = QLabel(visible_state)
            support_label.setObjectName("teacherActionDebugSupport")
            support_label.setWordWrap(True)
            row_layout.addWidget(support_label)

            controls = QHBoxLayout()
            play = QPushButton("▶ 播放")
            play.setAccessibleName(f"播放教师动作：{action_id}")
            play.setEnabled(support)
            action_json = json.dumps(action_id, ensure_ascii=True)
            play.clicked.connect(
                lambda _checked=False, action_id_json=action_json: self._play_teacher_action_debug(action_id_json)
            )
            controls.addWidget(play)
            stop = QPushButton("■ 停止")
            stop.setAccessibleName(f"停止教师动作：{action_id}")
            stop.setEnabled(support)
            stop.clicked.connect(self._stop_teacher_action_debug)
            controls.addWidget(stop)
            restore = QPushButton("↺ 恢复待机")
            restore.setAccessibleName(f"恢复待机：{action_id}")
            restore.clicked.connect(self._restore_teacher_idle_debug)
            controls.addWidget(restore)
            controls.addSpacing(14)
            controls.addWidget(QLabel("我的评价："))
            for review_value in ("excellent", "keep", "weak", "ineffective", "unnatural"):
                review_button = QPushButton(_TEACHER_ACTION_REVIEW_LABELS[review_value])
                review_button.setAccessibleName(
                    f"评价教师动作：{action_id}：{_TEACHER_ACTION_REVIEW_LABELS[review_value]}"
                )
                review_button.setCheckable(True)
                review_button.setChecked(review == review_value)
                review_button.clicked.connect(
                    lambda _checked=False, selected_action_id=action_id, selected_review=review_value:
                    self._review_teacher_action(selected_action_id, selected_review)
                )
                controls.addWidget(review_button)
            controls.addStretch(1)
            row_layout.addLayout(controls)
            rows_layout.insertWidget(max(0, rows_layout.count() - 1), row)

    def _play_teacher_action_debug(self, action_id_json: str) -> None:
        web = self._teacher_stage_web
        if web is None:
            return
        try:
            action_id = str(json.loads(action_id_json))
        except (TypeError, ValueError, json.JSONDecodeError):
            action_id = ""
        if action_id:
            self._teacher_action_debug_played.add(action_id)
        if self._teacher_stage_panel is not None:
            self._teacher_stage_panel.showNormal()
            self._teacher_stage_panel.raise_()
        if self._teacher_action_debug_notice is not None:
            self._teacher_action_debug_notice.setText(
                "已发起动作调用；这只证明 API 已调用，不代表肉眼有效或已经通过。请直接观察教师模型并评价。"
            )
        web.page().runJavaScript(
            "(() => { const id = %s; const button = [...document.querySelectorAll("
            "'[data-testid=\\\"live2d-motion-inspector\\\"] button[data-action-id]')].find(item => item.dataset.actionId === id); "
            "if (button && !button.disabled) button.click(); })();" % action_id_json
        )
        QTimer.singleShot(900, self._collect_teacher_action_debug_rows)

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
            placeholder = self._teacher_stage_placeholder
            insertion_index = self._teacher_stage_panel_layout.indexOf(placeholder)
            self._teacher_stage_panel_layout.removeWidget(placeholder)
            placeholder.deleteLater()
            self._teacher_stage_placeholder = None
            self._teacher_stage_panel_layout.insertWidget(max(0, insertion_index), web, 1)
        else:
            self._teacher_stage_panel_layout.insertWidget(2, web, 1)
        self._teacher_stage_web = web
        self._teacher_stage_profile = profile
        self._load_teacher_stage_url()

    def _load_teacher_stage_url(self) -> None:
        if self._teacher_stage_web is None or not self.origin:
            return
        workspace_id = str(self._teacher_stage_workspace_id or self.workspace.currentData() or "")
        query = f"&workspaceId={QUrl.toPercentEncoding(workspace_id).data().decode('ascii')}" if workspace_id else ""
        presentation = QUrl.toPercentEncoding(self._teacher_presentation_state).data().decode("ascii")
        theme = QUrl.toPercentEncoding(self._classroom_theme_key).data().decode("ascii")
        self._teacher_stage_web.load(QUrl(f"{self.origin}/?tool=teacher-stage{query}&state={presentation}&theme={theme}"))

    def _set_teacher_presentation_state(self, value: str) -> None:
        """Apply the classroom-selected state to the existing model runtime."""
        state = self._normalize_teacher_state(value)
        self._apply_teacher_visual_state(state)

    @staticmethod
    def _normalize_teacher_state(value: str) -> str:
        state = str(value or "idle").strip().lower()
        return state if state in _TEACHER_PRESENTATION_STATES else "idle"

    def _apply_teacher_visual_state(self, state: str, *, force: bool = False) -> None:
        """Send a semantic state to the already-open stage without backend work."""

        state = self._normalize_teacher_state(state)
        if self._teacher_stage_status is not None:
            self._teacher_stage_status.setText(
                f"●  {_TEACHER_PRESENTATION_LABELS.get(state, '待机')}"
            )
        if not force and state == self._teacher_presentation_state:
            return
        self._teacher_presentation_state = state
        web = self._teacher_stage_web
        if web is None:
            return
        payload = json.dumps(
            {"type": "mentorflow.teacher-state", "state": state},
            ensure_ascii=True,
            separators=(",", ":"),
        )
        web.page().runJavaScript(
            f"window.postMessage({payload}, window.location.origin);"
        )

    def _collapse_teacher_stage(self) -> None:
        if self._teacher_stage_window is not None:
            self._teacher_stage_window.hide()
        elif self._teacher_stage_panel is not None:
            self._teacher_stage_panel.setVisible(False)
        if self._teacher_stage_button is not None:
            self._teacher_stage_button.setText("打开教师舞台")
            self._teacher_stage_button.setAccessibleName("打开教师舞台")
            self._teacher_stage_button.setToolTip("打开可移动的教师舞台；对话区会保持完整宽度。")

    def _on_teacher_stage_hidden(self) -> None:
        if self._teacher_stage_button is not None:
            self._teacher_stage_button.setText("打开教师舞台")
            self._teacher_stage_button.setAccessibleName("打开教师舞台")
            self._teacher_stage_button.setToolTip("打开可移动的教师舞台；对话区会保持完整宽度。")

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
        self._cancel_pending_action()
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
        # The stage remains closed until explicitly opened.  This preserves a
        # wide, calm conversation canvas while retaining the detachable stage
        # The teacher stage has one stable entry point in the upper-right
        # classroom header.  Duplicating the same toggle beside the composer
        # made the two controls look like different actions.
        if self._teacher_stage_button is not None:
            self._teacher_stage_button.setText("打开教师舞台")
            self._teacher_stage_button.setAccessibleName("打开教师舞台")

    def exit_classroom(self) -> None:
        """Leave the current classroom view without deleting any learning data."""
        self._cancel_pending_action()
        self._cancel_voice_recording()
        self._commit_activity()
        self.session_id = ""
        self.current_session = {}
        self._render_current_point({})
        self.pending_attachment_ids.clear()
        self.answer.clear()
        self.pending_attachments.setText("未选择附件。")
        self.pending_attachments.setVisible(False)
        self.transcript.setHtml("<p>已退出本次课堂。请选择任务后重新进入，学习记录不会被删除。</p>")
        self._evidence_html = "<p>当前回合没有可展示的引用。</p>"
        if self._teacher_stage_window is not None:
            self._collapse_teacher_stage()
        self.state.setText("已退出本次课堂。请选择学习任务后重新进入；已有学习记录仍会保留。")
        if self._view_stack is not None:
            self._view_stack.setCurrentIndex(0)
        self.refresh_workspaces()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        # A detached sidecar must not float over another page.
        self._cancel_voice_recording()
        if self._teacher_stage_window is not None:
            self._collapse_teacher_stage()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._closed = True
        self._typing_timer.stop()
        self._cancel_pending_action()
        self._cancel_voice_recording()
        application = QApplication.instance()
        if application is not None:
            application.removeEventFilter(self)
        self._action_executor.shutdown(wait=False, cancel_futures=True)
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
        if self._voice_input_active():
            self.state.setText("语音正在录音或转写，请完成后再提交课堂操作。")
            return
        if not self.answer.toPlainText().strip():
            self.answer.setPlainText("我还没有理解当前概念，请针对我可能混淆的对象和关系重新解释。")
        self.send_answer()

    def _set_interaction_mode(self, mode: str) -> None:
        """Switch only the purpose of the next message, preserving the draft."""

        selected = "free_chat" if str(mode).strip() == "free_chat" else "learning_evidence"
        self.interaction_mode = selected
        selected_radio = (
            self.free_chat_radio if selected == "free_chat" else self.learning_evidence_radio
        )
        # Keep programmatic mode changes (for example restoring a session) in
        # lockstep with the visible filled-dot radio state.  Signal handlers
        # are already guarded by the checked argument, so this is idempotent.
        if selected_radio is not None and not selected_radio.isChecked():
            selected_radio.setChecked(True)
        if self.interaction_notice is not None:
            # The filled radio already communicates the mode.  Keep the
            # composer conversational instead of inserting a warning banner
            # every time the learner chooses ordinary free chat.
            self.interaction_notice.clear()
            self.interaction_notice.setVisible(False)
        self.answer.setPlaceholderText(
            "想聊什么都可以，直接告诉老师……"
            if selected == "free_chat"
            else "写下你的解释、答案或具体不理解的地方……"
        )
        if self.send_button is not None and self._action_future is None:
            self.send_button.setText("发送自由消息" if selected == "free_chat" else "提交回答")

    def _voice_input_active(self) -> bool:
        """Return whether recording or an ASR result is still being finalized."""

        return bool(
            self._voice_recording
            or self._voice_future is not None
            or self._voice_final_pending
        )

    def _refresh_voice_button(self) -> None:
        """Keep voice controls synchronized with action/ASR ownership."""

        if not hasattr(self, "voice_button"):
            return
        # A classroom action owns the answer controls until its result is
        # rendered.  A completed ASR callback must not race that ownership.
        if self._action_future is not None:
            self.voice_button.setEnabled(False)
            return
        self.voice_button.setEnabled(True)

    def _restore_action_controls(self) -> None:
        """Restore controls after a classroom action succeeds or fails."""

        self._typing_timer.stop()
        self._typing_step = 0
        if self._conversation_status is not None:
            self._conversation_status.setText("●  教师在线")
        self.answer.setReadOnly(False)
        if self.send_button is not None:
            self.send_button.setEnabled(True)
            self.send_button.setText("发送自由消息" if self.interaction_mode == "free_chat" else "提交回答")
        if self.complete_button is not None:
            self.complete_button.setEnabled(True)
            self.complete_button.setText("我已掌握，完成此知识点")
        if self.attach_button is not None:
            self.attach_button.setEnabled(True)
        for radio in (self.learning_evidence_radio, self.free_chat_radio):
            if radio is not None:
                radio.setEnabled(True)
        self._refresh_voice_button()

    def _cancel_pending_action(self) -> None:
        """Invalidate a result that must not be applied to a new/closed session."""

        self._action_generation += 1
        future = self._action_future
        self._action_future = None
        self._action_kind = ""
        self._action_context = {}
        if future is not None and not future.done():
            future.cancel()
        if hasattr(self, "answer"):
            self._restore_action_controls()

    def _set_action_pending(self, kind: str) -> None:
        self._set_teacher_presentation_state("thinking")
        self._typing_step = 0
        if self._conversation_status is not None:
            self._conversation_status.setText("●  教师正在思考·")
        self._typing_timer.start()
        self.answer.setReadOnly(True)
        if self.send_button is not None:
            self.send_button.setEnabled(False)
            self.send_button.setText(
                "正在发送自由消息…"
                if kind == "send" and self.interaction_mode == "free_chat"
                else "正在提交…"
                if kind == "send"
                else "提交回答"
            )
        if self.complete_button is not None:
            self.complete_button.setEnabled(False)
            self.complete_button.setText(
                "正在进入下一知识点…" if kind == "complete" else "我已掌握，完成此知识点"
            )
        if self.attach_button is not None:
            self.attach_button.setEnabled(False)
        if hasattr(self, "voice_button"):
            self.voice_button.setEnabled(False)
        for radio in (self.learning_evidence_radio, self.free_chat_radio):
            if radio is not None:
                radio.setEnabled(False)
        self.state.setText(
            (
                "正在发送自由消息；不会计入掌握判定或推进学习计划……"
                if self.interaction_mode == "free_chat"
                else "正在提交回答，教师完成本回合后会更新课堂……"
            )
            if kind == "send"
            else "正在完成当前知识点并进入下一知识点……"
        )

    def _perform_teaching_action(
        self,
        *,
        session_id: str,
        message: str,
        provider_session_id: str,
        attachment_ids: list[str],
        interaction_mode: str = "learning_evidence",
        free_chat_prompt: str = "",
        force_complete: bool,
        actual_elapsed_minutes: float,
        activity_event_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        """Run network work off the Qt GUI thread and return render inputs."""

        request_payload: dict[str, Any] = {
            "message": message,
            "provider_session_id": provider_session_id,
            "interaction_mode": interaction_mode,
            "attachment_ids": list(attachment_ids),
        }
        if interaction_mode == "free_chat" and free_chat_prompt:
            request_payload["free_chat_prompt"] = free_chat_prompt
        if interaction_mode == "learning_evidence":
            request_payload.update(
                force_complete=force_complete,
                actual_elapsed_minutes=actual_elapsed_minutes,
                activity_event_id=activity_event_id,
            )
        session = self.api.advance_teaching_session(session_id, **request_payload)
        material_index: dict[str, dict[str, Any]] = {}
        if workspace_id:
            try:
                material_rows = self.api.materials(workspace_id)
            except Exception:
                material_rows = []
            if not isinstance(material_rows, (list, tuple)):
                material_rows = []
            material_index = {
                str(item.get("id") or ""): item
                for item in material_rows
                if isinstance(item, dict)
            }
        return {"session": session, "material_index": material_index}

    def _start_teaching_action(
        self,
        *,
        kind: str,
        session_id: str,
        message: str,
        provider_session_id: str,
        attachment_ids: list[str],
        interaction_mode: str = "learning_evidence",
        force_complete: bool,
        actual_elapsed_minutes: float,
        activity_event_id: str,
        workspace_id: str,
    ) -> bool:
        interaction_mode = (
            "learning_evidence"
            if kind == "complete"
            else "free_chat"
            if str(interaction_mode).strip() == "free_chat"
            else "learning_evidence"
        )
        free_chat_prompt = (
            str(QSettings("MentorFlow", "MentorFlow").value(_FREE_CHAT_PROMPT_SETTINGS_KEY, "") or "")[:6000]
            if interaction_mode == "free_chat"
            else ""
        )
        if self._action_future is not None:
            self.state.setText("上一项课堂操作仍在进行，请稍候。")
            return False
        if self._voice_input_active():
            self.state.setText("语音正在录音或转写，请完成后再提交课堂操作。")
            return False
        self._action_generation += 1
        generation = self._action_generation
        self._action_kind = kind
        self._action_context = {
            "generation": generation,
            "message": message,
            "attachment_ids": list(attachment_ids),
            "interaction_mode": interaction_mode,
        }
        self._set_action_pending(kind)
        try:
            self._action_future = self._action_executor.submit(
                self._perform_teaching_action,
                session_id=session_id,
                message=message,
                provider_session_id=provider_session_id,
                attachment_ids=list(attachment_ids),
                interaction_mode=interaction_mode,
                free_chat_prompt=free_chat_prompt,
                force_complete=force_complete,
                actual_elapsed_minutes=actual_elapsed_minutes,
                activity_event_id=activity_event_id,
                workspace_id=workspace_id,
            )
        except RuntimeError as exc:
            self._action_future = None
            self._action_kind = ""
            self._action_context = {}
            self._restore_action_controls()
            self.state.setText(f"课堂操作无法启动：{exc}")
            return False
        QTimer.singleShot(40, lambda token=generation: self._poll_teaching_action(token))
        return True

    def _poll_teaching_action(self, generation: int) -> None:
        if self._closed or generation != self._action_generation:
            return
        future = self._action_future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(40, lambda token=generation: self._poll_teaching_action(token))
            return
        self._action_future = None
        kind = self._action_kind
        action_context = dict(self._action_context)
        self._action_kind = ""
        self._action_context = {}
        try:
            result = future.result()
        except ApiError as exc:
            self._render_session(self.current_session, material_index={}, update_current=False)
            self.state.setText(f"{'教师回合' if kind == 'send' else '进入下一知识点'}失败：{exc.code}：{exc.message}")
            self._restore_action_controls()
            return
        except Exception as exc:  # keep the native page responsive on transport/runtime failures
            self._render_session(self.current_session, material_index={}, update_current=False)
            action = "教师回合" if kind == "send" else "进入下一知识点"
            self.state.setText(f"{action}失败：{str(exc).strip() or '出现未知错误，请重试。'}")
            self._restore_action_controls()
            return
        if generation != self._action_generation:
            return
        session = result.get("session") if isinstance(result, dict) else None
        if not isinstance(session, dict):
            self._render_session(self.current_session, material_index={}, update_current=False)
            self.state.setText("教师回合失败：服务未返回有效课堂状态。")
            self._restore_action_controls()
            return
        if str(action_context.get("interaction_mode") or "learning_evidence") != "free_chat":
            self._commit_activity()
        if kind == "send":
            submitted_message = str(action_context.get("message") or "").strip()
            # Normally the editor is read-only while the request is in
            # flight.  Keep any programmatic ASR/draft update that arrived
            # during that interval instead of clearing newly entered text.
            if self.answer.toPlainText().strip() == submitted_message:
                self.answer.clear()
                self.pending_attachment_ids.clear()
                self.pending_attachments.setText("未选择附件。")
                self.pending_attachments.setVisible(False)
        material_index = result.get("material_index") if isinstance(result, dict) else {}
        self._render_session(
            session,
            material_index=material_index if isinstance(material_index, dict) else {},
        )
        self._restore_action_controls()

    def _render_optimistic_learner_message(
        self,
        message: str,
        *,
        interaction_mode: str = "learning_evidence",
    ) -> None:
        """Show the learner's submitted text without changing server state."""

        optimistic = deepcopy(self.current_session)
        projection = optimistic.get("current_point")
        if not isinstance(projection, dict):
            projection = {}
            optimistic["current_point"] = projection
        current_messages = projection.get("messages")
        if not isinstance(current_messages, list):
            current_messages = []
        projection["messages"] = [
            *current_messages,
            {
                "role": "user",
                "action": (
                    "free_chat"
                    if interaction_mode == "free_chat"
                    else "feedback"
                ),
                "content": message,
                "attachments": [],
            },
        ]
        projection["message_projection"] = projection["messages"]
        optimistic["current_point_projection"] = projection
        optimistic["current_point_messages"] = projection["messages"]
        self._render_session(optimistic, material_index={}, update_current=False)

    def send_answer(self) -> None:
        message = self.answer.toPlainText().strip()
        if not self.session_id:
            self.state.setText("请先进入课堂。")
            return
        if not message and not self.pending_attachment_ids:
            self.state.setText("请输入回答、具体疑问，或添加附件。")
            return
        if self._action_future is not None:
            self.state.setText("上一项课堂操作仍在进行，请稍候。")
            return
        if self._voice_input_active():
            self.state.setText("语音正在录音或转写，请完成后再提交课堂操作。")
            return
        interaction_mode = self.interaction_mode
        if interaction_mode == "free_chat":
            elapsed, event_id = 0.0, ""
        else:
            elapsed, event_id = self._activity_payload()
        optimistic_message = message or (
            "请阅读我添加的附件，并自然回应当前话题。"
            if interaction_mode == "free_chat"
            else "请阅读我添加的附件，并结合当前知识点继续指导。"
        )
        self._render_optimistic_learner_message(
            optimistic_message,
            interaction_mode=interaction_mode,
        )
        self._start_teaching_action(
            kind="send",
            session_id=self.session_id,
            message=message,
            provider_session_id=self.provider_session(),
            attachment_ids=list(self.pending_attachment_ids),
            interaction_mode=interaction_mode,
            force_complete=False,
            actual_elapsed_minutes=elapsed,
            activity_event_id=event_id,
            workspace_id=str(self.current_session.get("workspace_id") or self.workspace.currentData() or ""),
        )

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
            self.pending_attachments.setVisible(True)

    def toggle_voice_recording(self) -> None:
        """Start/stop microphone capture; partial ASR runs while recording."""

        if self._action_future is not None:
            self.state.setText("课堂操作进行中，请等待完成后再使用语音。")
            return
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
        if self._action_future is not None:
            self.voice_button.setEnabled(False)
            self.voice_button.setText("🎙 语音")
            return
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
        self._refresh_voice_button()
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
            self._refresh_voice_button()
            self.voice_button.setText("🎙 语音")

    def skip_to_next_point(self) -> None:
        if not self.session_id:
            self.state.setText("请先进入课堂。")
            return
        elapsed, event_id = self._activity_payload()
        self._start_teaching_action(
            kind="complete",
            session_id=self.session_id,
            message="学习者自我确认已掌握，完成当前知识点并进入下一知识点。",
            provider_session_id=self.provider_session(),
            attachment_ids=[],
            interaction_mode="learning_evidence",
            force_complete=True,
            actual_elapsed_minutes=elapsed,
            activity_event_id=event_id,
            workspace_id=str(self.current_session.get("workspace_id") or self.workspace.currentData() or ""),
        )

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

    def _render_session(
        self,
        session: dict[str, Any],
        *,
        material_index: dict[str, dict[str, Any]] | None = None,
        update_current: bool = True,
    ) -> None:
        if update_current:
            self.current_session = session
        self._render_current_point(session)
        self.transcript.set_session(session)
        state = session.get("state") or {}
        turn = state.get("teacher_turn_v1") or {}
        turn_state = turn.get("state") or {}
        self._set_teacher_presentation_state(teacher_presentation_state(session))
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
        if material_index is None:
            material_index = {}
            try:
                workspace_id = str(session.get("workspace_id") or self.workspace.currentData() or "")
                if workspace_id:
                    material_index = {
                        str(item.get("id") or ""): item
                        for item in self.api.materials(workspace_id)
                        if isinstance(item, dict)
                    }
            except (ApiError, AttributeError, TypeError):
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
