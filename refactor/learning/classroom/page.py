"""Native classroom entry point using only injected shared model/plan services.

Required context: account_id, data_root, model_client, plan_service. Optional
evaluator_client enables the separate structured evaluation role. Current
session/API configuration stays in the shared clients; it is never persisted
or used to create a second client here. Optional classroom_asset_root: media.py.
Call dispose() before permanently removing a page; hide cancels pending work.
set_context() supports an explicit atomic rebind to a new account's services.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import sqlite3
from uuid import uuid4

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget,
    QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
    QListWidgetItem)

from .display import ClassroomAtmosphere, ClassroomTranscriptView
from .jobs import Jobs
from .media import load_portrait
from .records import ClassroomRecords
from .teaching import ACTIONS, generate, transcript_messages
from .theme import classroom_theme, classroom_theme_options, normalize_classroom_theme
from .teacher_stage import TeacherStageWindow
from .domain import (build_checkpoint, contract_for_task, make_evidence, make_verification_item,
                     record_attempt_fact)
from .evaluator import EvaluationError, EvaluationService
from .recap import build_session_recap
from .verification import VerificationScheduler
from ..plan_models import knowledge_task_ids


def context_value(context, key, default=None):
    return context.get(key, default) if isinstance(context, Mapping) else getattr(context, key, default)


def _teacher_state_for_turn(turn):
    """Map what the teacher decided to do to the detached teacher's state."""
    decision = turn.get("decision") or {}
    move = str(decision.get("move") or "")
    state = {
        "explain": "explaining",
        "answer_directly": "talking",
        "diagnose": "questioning",
        "simplify": "confused",
        "connect_prerequisite": "explaining",
        "concrete_example": "explaining",
        "worked_example": "explaining",
        "code_example": "explaining",
        "counterexample": "explaining",
        "transfer_challenge": "questioning",
        "hint": "questioning",
        "guided_practice": "questioning",
        "retrieval_prompt": "questioning",
        "summarize": "explaining",
        "transition": "ending",
    }.get(move)
    if state is not None:
        return state
    # Older history has no decision payload; retain its previous behavior.
    return {"explain": "explaining", "confused": "confused",
            "practice": "questioning", "question": "talking",
            "answer": "talking", "continue": "talking"}.get(
                str(turn.get("action") or ""), "talking")


def valid_tasks(plan):
    tasks = [dict(task) for task in plan.tasks]
    identifiers = [str(task.get("id", "")) for task in tasks]
    if any(not value for value in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("计划任务缺少唯一标识")
    if any(task.get("status") not in {"pending", "in_progress", "completed"} for task in tasks):
        raise ValueError("计划任务状态无效")
    return tasks


def load_selection(service, records, cancel, key=None, task_id=None):
    if cancel.is_set():
        return None
    if key is not None:
        service.select_plan(key)
    if cancel.is_set():
        return None
    plans = tuple(service.list_plans())
    if len({p.key for p in plans}) != len(plans):
        raise ValueError("duplicate plan key")
    current = service.current_plan()
    if cancel.is_set():
        return None
    if current is not None and (current.key not in {p.key for p in plans} or key is not None and current.key != key):
        raise ValueError("current plan mismatch")
    if key is not None and current is None:
        raise ValueError("selected plan unavailable")
    tasks = valid_tasks(current) if current is not None else []
    task = next((t for t in tasks if str(t["id"]) == task_id), None)
    if task is None:
        task = next((t for t in tasks if t["status"] != "completed"), tasks[0] if tasks else None)
    history = records.load(current.key, str(task["id"])) if task else None
    return plans, current, tasks, task, history


class ClassroomPage(ClassroomAtmosphere):
    preferences_changed = Signal(dict)

    def __init__(self, context, parent=None):
        super().__init__("sakura", parent)
        self.setObjectName("nativeClassroom")
        self._jobs = Jobs()
        self.destroyed.connect(self._jobs.cancel)
        self._token = None
        self._callback = None
        self._busy = self._ready = self._disposed = self._suspended = False
        self.context = None
        self._account_id = None
        self._account_mismatch = False
        self._records = None
        self._preferences = {}
        self._task_colors = {}
        self._canonical_preferences = False
        self._stage_open = False
        self._stage_transitioning = False
        # Keep the task directory out of the way until the learner asks for
        # it.  The lesson surface is the primary workspace, so its initial
        # width must not be reduced by a secondary navigation rail.
        self._task_sidebar_requested = False
        self.plan = self.task = None
        self._contract = None
        self._checkpoint = None
        self._note_revision = 0
        self._preserve_refresh_input = None
        self.plans, self.tasks, self.turns = (), [], []
        self._build()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(20)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()
        self.set_context(context)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        heading = QHBoxLayout()
        title = QLabel("原生课堂")
        title.setObjectName("classroomHeading")
        heading.addWidget(title)
        heading.addStretch()
        self.theme_choice = QComboBox()
        self.theme_choice.setAccessibleName("课堂主题")
        for key, label, description in classroom_theme_options():
            self.theme_choice.addItem(label, key)
            self.theme_choice.setItemData(self.theme_choice.count() - 1, description, Qt.ItemDataRole.ToolTipRole)
        self.theme_choice.activated.connect(self._choose_theme)
        heading.addWidget(self.theme_choice)
        self.task_sidebar_toggle = QPushButton("收起任务目录")
        self.task_sidebar_toggle.setAccessibleName("任务目录")
        self.task_sidebar_toggle.clicked.connect(self._toggle_task_sidebar)
        heading.addWidget(self.task_sidebar_toggle)
        self.portrait_button = QPushButton("显示 Live2D 教师")
        self.portrait_button.setAccessibleName("显示 Live2D 教师")
        self.portrait_button.clicked.connect(self._toggle_teacher_stage)
        heading.addWidget(self.portrait_button)
        self.refresh_button = QPushButton("刷新计划")
        self.refresh_button.clicked.connect(self.refresh)
        heading.addWidget(self.refresh_button)
        layout.addLayout(heading)
        self.state = QLabel("正在载入课堂…")
        self.state.setTextFormat(Qt.TextFormat.PlainText)
        self.state.setWordWrap(True)
        self.state.setAccessibleName("课堂状态")
        layout.addWidget(self.state)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        sidebar = QWidget()
        sidebar.setObjectName("classroomSidebar")
        sidebar.setMinimumWidth(0)
        sidebar.setMaximumWidth(320)
        sidebar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 12, 0)
        side.setSpacing(8)
        self.plan_choice = QComboBox()
        self.plan_choice.setAccessibleName("当前学习计划")
        self.plan_choice.activated.connect(self._select_plan)
        side.addWidget(self.plan_choice)
        self.progress = QProgressBar()
        side.addWidget(self.progress)
        self.task_list = QListWidget()
        self.task_list.setAccessibleName("计划任务")
        self.task_list.setObjectName("classroomTaskList")
        self.task_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.task_list.setWordWrap(False)
        self.task_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.task_list.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.task_list.currentRowChanged.connect(self._select_task)
        side.addWidget(self.task_list, 1)
        self.portrait = QLabel("文本课堂")
        self.portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side.addWidget(self.portrait)
        self.media_state = QLabel("可选形象按需加载；文本课堂可独立使用。")
        self.media_state.setWordWrap(True)
        side.addWidget(self.media_state)
        splitter.addWidget(sidebar)
        main = QWidget()
        main.setObjectName("classroomMain")
        main.setMinimumWidth(360)
        main.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        center = QVBoxLayout(main)
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(8)
        self.task_heading = QLabel("选择一个计划开始学习")
        self.task_heading.setObjectName("taskHeading")
        self.task_heading.setTextFormat(Qt.TextFormat.PlainText)
        self.task_heading.setWordWrap(True)
        center.addWidget(self.task_heading)
        self.objective = QLabel()
        self.objective.setTextFormat(Qt.TextFormat.PlainText)
        self.objective.setWordWrap(True)
        self.objective.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        center.addWidget(self.objective)
        self.transcript = ClassroomTranscriptView(parent=self)
        self.transcript.setAccessibleName("课堂对话与学习记录")
        center.addWidget(self.transcript, 1)
        self.guidance_hint = QLabel("点击“开始上课”，跟着老师逐步学习；已经会了也可以直接标记学会。")
        self.guidance_hint.setWordWrap(True)
        self.guidance_hint.setObjectName("classroomGuidance")
        center.addWidget(self.guidance_hint)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("跟着老师作答，或直接说出你的疑问…")
        self.input.setAccessibleName("问题或作答")
        self.input.setMaximumHeight(90)
        self.input.textChanged.connect(self._update_guide)
        center.addWidget(self.input)
        self.guide_button = QPushButton("开始上课")
        self.guide_button.setProperty("classroomActionRole", "primary")
        self.guide_button.clicked.connect(self._continue_lesson)
        self.complete_button = QPushButton("这个知识点我已学会")
        self.complete_button.setToolTip("记录你的学习确认，然后继续下一知识点。")
        self.complete_button.clicked.connect(lambda: self.record_progress("completed"))
        primary_row = QHBoxLayout()
        primary_row.setObjectName("classroomPrimaryActions")
        primary_row.addWidget(self.guide_button, 2)
        primary_row.addWidget(self.complete_button, 1)
        center.addLayout(primary_row)
        self.confused_button = QPushButton("没听懂，换个讲法")
        self.practice_button = QPushButton("再练一道")
        self.practice_button.setToolTip("按当前任务和刚才的练习生成一道新的题目。")
        self.notes_toggle = QPushButton("学习笔记")
        self.notes_toggle.setCheckable(True)
        self.notes_toggle.toggled.connect(self._toggle_notes)
        self.cancel_button = QPushButton("停止生成")
        secondary_row = QHBoxLayout()
        secondary_row.setObjectName("classroomSecondaryActions")
        for button, action in ((self.confused_button, "confused"), (self.practice_button, "practice")):
            button.clicked.connect(lambda checked=False, kind=action: self.teach(kind))
        for button in (self.confused_button, self.practice_button, self.notes_toggle):
            button.setProperty("classroomActionRole", "secondary")
            secondary_row.addWidget(button)
        secondary_row.addStretch()
        self.cancel_button.setProperty("classroomActionRole", "cancel")
        secondary_row.addWidget(self.cancel_button)
        self.cancel_button.clicked.connect(self.cancel_request)
        center.addLayout(secondary_row)
        self.notes_panel = QWidget()
        notes_layout = QVBoxLayout(self.notes_panel)
        notes_layout.setContentsMargins(0, 0, 0, 0)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("记下理解与疑问；继续上课或标记学会时也会保存。")
        self.note.setAccessibleName("学习笔记")
        self.note.setMaximumHeight(70)
        notes_layout.addWidget(self.note)
        self.save_note_button = QPushButton("保存笔记")
        self.save_note_button.clicked.connect(self.save_note)
        notes_layout.addWidget(self.save_note_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.notes_panel.hide()
        center.addWidget(self.notes_panel)
        # Mastery verification stays in the lesson surface, but is compact
        # and exposes the account-scoped queue for the selected plan.  It
        # never derives completion from Tutor prose.  A learner may arrive at
        # the queue from any task, so the selected item resolves its own
        # persisted LearningContract before an attempt is evaluated.
        self.verification_panel = QWidget()
        verification_layout = QVBoxLayout(self.verification_panel)
        verification_layout.setContentsMargins(0, 4, 0, 0)
        verification_layout.setSpacing(4)
        self.verification_summary = QLabel("掌握检验：暂无待确认项目")
        self.verification_summary.setWordWrap(True)
        self.verification_summary.setAccessibleName("掌握检验摘要")
        verification_layout.addWidget(self.verification_summary)
        self.verification_list = QListWidget()
        self.verification_list.setAccessibleName("掌握检验队列")
        self.verification_list.setMaximumHeight(92)
        self.verification_list.currentRowChanged.connect(lambda _row: self._controls())
        verification_layout.addWidget(self.verification_list)
        self.verification_attempt = QPlainTextEdit()
        self.verification_attempt.setPlaceholderText("选中待检验项目后，在这里提交一次独立作答…")
        self.verification_attempt.setAccessibleName("掌握检验作答")
        self.verification_attempt.setMaximumHeight(54)
        verification_layout.addWidget(self.verification_attempt)
        verification_actions = QHBoxLayout()
        self.verification_start_button = QPushButton("开始检验")
        self.verification_remediate_button = QPushButton("安排补救")
        self.verification_resolve_button = QPushButton("提交检验")
        for button in (self.verification_start_button, self.verification_remediate_button,
                       self.verification_resolve_button):
            button.setProperty("classroomActionRole", "secondary")
            verification_actions.addWidget(button)
        self.verification_start_button.clicked.connect(lambda: self._verification_action("start"))
        self.verification_remediate_button.clicked.connect(lambda: self._verification_action("remediate"))
        self.verification_resolve_button.clicked.connect(lambda: self._verification_action("resolve"))
        verification_layout.addLayout(verification_actions)
        self.session_recap = QLabel("本次课堂摘要会在有学习事实后显示。")
        self.session_recap.setWordWrap(True)
        self.session_recap.setAccessibleName("课堂摘要")
        verification_layout.addWidget(self.session_recap)
        center.addWidget(self.verification_panel)
        splitter.addWidget(main)
        # Keep one reusable companion window per classroom page.  Its panel
        # remains outside the lesson splitter, so opening the teacher never
        # steals width from the transcript or task controls.
        self.teacher_window = TeacherStageWindow(parent=self)
        self.teacher_stage = self.teacher_window.stage
        self.teacher_stage.runtime_changed.connect(self._teacher_stage_runtime)
        self.teacher_window.closed.connect(self._close_teacher_stage)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setSizes([260, 850])
        self._splitter = splitter
        layout.addWidget(splitter, 1)
        self._relayout_columns()
        self._apply_theme("sakura")

    def _relayout_columns(self):
        """Keep the task rail bounded so the conversation remains usable."""
        if not hasattr(self, "_splitter") or self._disposed:
            return
        width = max(0, self.width() - 36)
        sidebar = self._splitter.widget(0)
        main = self._splitter.widget(1)
        if width <= 0 or sidebar is None or main is None:
            return
        show_sidebar = self._task_sidebar_requested and width >= 780
        if not show_sidebar:
            left_width = 0
        elif width < 1100:
            left_width = 220
        else:
            left_width = min(300, max(240, int(width * 0.22)))
        sidebar.setVisible(show_sidebar)
        center_width = max(360, width - left_width - 4)
        self._splitter.setSizes([left_width, center_width])
        self.task_sidebar_toggle.setText("收起任务目录" if show_sidebar else "显示任务目录")
        self.task_sidebar_toggle.setAccessibleName("收起任务目录" if show_sidebar else "显示任务目录")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout_columns()

    def _apply_theme(self, key):
        key = normalize_classroom_theme(key)
        self.set_theme(key)
        self.transcript.set_theme(key)
        self.theme_choice.setCurrentIndex(self.theme_choice.findData(key))
        theme = classroom_theme(key)
        self.teacher_stage.set_theme(key)
        self.setStyleSheet(f"""
            QWidget#nativeClassroom {{ color: {theme['text']}; font-family: 'Microsoft YaHei'; font-size: 14px; }}
            QWidget#nativeClassroom QLabel {{ color: {theme['text']}; background: transparent; }}
            QLabel#classroomHeading {{ font-size: 24px; font-weight: 700; }}
            QLabel#taskHeading {{ font-size: 18px; font-weight: 700; }}
            QWidget#nativeClassroom QPushButton, QWidget#nativeClassroom QComboBox {{
                min-height: 34px; padding: 4px 8px; border: 1px solid {theme['border']}; border-radius: {theme['control_radius']}px;
                color: {theme['text']}; background: {theme['surface']}; }}
            QWidget#nativeClassroom QPushButton:disabled {{ color: {theme['muted']}; background: {theme['surface_alt']}; }}
            QWidget#nativeClassroom QPushButton:hover {{ border-color: {theme['accent']}; }}
            QWidget#nativeClassroom QPushButton[classroomActionRole="primary"] {{
                min-height: 36px; background: {theme['accent_soft']}; border-color: {theme['accent']}; font-weight: 700;
            }}
            QWidget#nativeClassroom QPushButton[classroomActionRole="secondary"],
            QWidget#nativeClassroom QPushButton[classroomActionRole="cancel"] {{
                min-height: 30px; padding: 3px 7px;
            }}
            QWidget#nativeClassroom QPushButton[classroomActionRole="cancel"] {{
                color: {theme['muted']}; background: {theme['surface_alt']};
            }}
            QWidget#nativeClassroom QPlainTextEdit, QWidget#nativeClassroom QListWidget {{
                color: {theme['text']}; background: {theme['surface']}; border: 1px solid {theme['border']};
                border-radius: {theme['control_radius']}px; padding: 5px; selection-background-color: {theme['accent_soft']};
                selection-color: {theme['text']}; }}
            QWidget#nativeClassroom QListWidget::item {{ padding: 7px 3px; }}
            QWidget#nativeClassroom QListWidget::item:selected {{ color: {theme['text']}; background: {theme['accent_soft']}; }}
            QWidget#nativeClassroom QProgressBar {{ color: {theme['text']}; border: 1px solid {theme['border']};
                text-align: center; background: {theme['surface']}; }}
            QWidget#nativeClassroom QProgressBar::chunk {{ background: {theme['accent_soft']}; }}
        """)

    def apply_preferences(self, preferences):
        """Apply the current account preferences on the GUI thread."""
        if self._disposed or not isinstance(preferences, Mapping):
            return False
        values = deepcopy(dict(preferences))
        theme = normalize_classroom_theme(values.get("classroom_theme", "sakura"))
        colors = values.get("task_colors", {})
        self._preferences = values
        self._preferences["classroom_theme"] = theme
        self._task_colors = dict(colors) if isinstance(colors, Mapping) else {}
        self._apply_theme(theme)
        self._apply_task_colors()
        return True

    def _apply_task_colors(self):
        colors = self._task_colors
        for index, task in enumerate(self.tasks):
            if index >= self.task_list.count():
                break
            item = self.task_list.item(index)
            title = str(task.get("title") or task.get("id") or "")
            markers = [str(task.get(key) or "") for key in ("task_type", "learning_phase", "status")]
            markers.append(title)
            color = next((value for marker, value in colors.items()
                          if str(marker) and (str(marker) in markers or str(marker) in title)), None)
            qcolor = QColor(str(color)) if color is not None else QColor()
            item.setForeground(QBrush(qcolor) if qcolor.isValid() else QBrush())

    def _load_preferences(self):
        store = context_value(self.context, "preferences_store")
        provider = context_value(self.context, "preferences_provider")
        self._canonical_preferences = callable(getattr(store, "load", None)) or callable(provider)
        values, stored = {}, {}
        if callable(getattr(store, "load", None)):
            try:
                loaded = store.load()
            except Exception:
                loaded = None
            if isinstance(loaded, Mapping):
                stored.update(loaded)
        if callable(provider):
            try:
                provided = provider()
            except Exception:
                provided = None
            if isinstance(provided, Mapping):
                values.update(provided)
        if values:
            if "classroom_theme" in stored:
                values["classroom_theme"] = stored["classroom_theme"]
        else:
            values.update(stored)
        if values:
            self.apply_preferences(values)
        else:
            self._apply_theme("sakura")

    def _preference_snapshot(self, fallback):
        """Read shared preferences for this turn without touching Qt."""
        provider = context_value(self.context, "preferences_provider")
        if callable(provider):
            try:
                values = provider()
            except Exception:
                values = None
            if isinstance(values, Mapping):
                return deepcopy(dict(values))
        store = context_value(self.context, "preferences_store")
        if callable(getattr(store, "load", None)):
            try:
                values = store.load()
            except Exception:
                values = None
            if isinstance(values, Mapping):
                return deepcopy(dict(values))
        return deepcopy(fallback)

    def _guard(self):
        if self._disposed:
            return False
        if context_value(self.context, "account_id") != self._account_id:
            if not self._account_mismatch:
                self._account_mismatch = True
                self._invalidate()
                self._clear()
                self._records = None
                self._suspended = True
                self.state.setText("账号已变化，请由主窗口重新载入课堂。")
            return False
        return not self._suspended

    def _invalidate(self):
        self._jobs.cancel()
        # Jobs deliberately drop results from cancelled workers.  Close any
        # pending classroom turn first so cancellation cannot leave a
        # permanently unfinished row in the account history.
        if self._records is not None and self.plan is not None and self.task is not None:
            try:
                self._records.fail_pending(self.plan.key, str(self.task["id"]))
            except (OSError, ValueError, sqlite3.Error):
                pass
        self._callback = self._token = None
        self._busy = False

    def _clear(self):
        self._ready = False
        self.plan = self.task = None
        self._contract = None
        self._checkpoint = None
        self._note_revision = 0
        self.plans, self.tasks, self.turns = (), [], []
        self.plan_choice.clear()
        self.task_list.blockSignals(True)
        self.task_list.clear()
        self.task_list.blockSignals(False)
        self.input.clear()
        self.note.clear()
        self.transcript.set_session({"messages": []})
        self.portrait.clear()
        self._close_teacher_stage()
        self.teacher_stage.set_reply("")
        self._teacher_stage_set_state("idle")
        self.task_heading.setText("选择一个计划开始学习")
        self.objective.clear()
        self.progress.setValue(0)
        self._controls()

    def set_context(self, context):
        if self._disposed:
            return
        self._invalidate()
        self._clear()
        self.context = context
        self._account_id = context_value(context, "account_id")
        self._account_mismatch = False
        self._suspended = False
        self._records = None
        self._preferences = {}
        self._task_colors = {}
        self._canonical_preferences = False
        self.teacher_stage.set_asset_root(
            context_value(context, "classroom_teacher_asset_root")
            or context_value(context, "classroom_asset_root")
        )
        self._apply_theme("sakura")
        self._load_preferences()
        self.media_state.setText("可选形象按需加载；文本课堂可独立使用。")
        try:
            self._records = ClassroomRecords(context_value(context, "data_root"), self._account_id)
        except (TypeError, ValueError, OSError):
            self.state.setText("课堂账号或数据目录无效，请重新进入课堂。")
            self._controls()
            return
        self.refresh()

    def _controls(self):
        usable = self._ready and self.task is not None and not self._disposed and not self._suspended
        idle = usable and not self._busy
        model = context_value(self.context, "model_client") if self.context is not None else None
        can_generate = idle and callable(getattr(model, "complete", None))
        for button in (self.guide_button, self.confused_button, self.practice_button):
            button.setEnabled(can_generate)
        group = set(knowledge_task_ids(self.plan, str(self.task["id"]))) if idle else set()
        self.complete_button.setEnabled(idle and any(str(t["id"]) in group and t["status"] != "completed" for t in self.tasks))
        self.save_note_button.setEnabled(idle)
        self.input.setEnabled(usable)
        self.note.setEnabled(idle)
        self.cancel_button.setEnabled(self._busy)
        self.cancel_button.setVisible(self._busy)
        verification_item = self._selected_verification() if idle else None
        verification_usable = idle and verification_item is not None
        self.verification_start_button.setEnabled(verification_usable and verification_item.status not in {"passed", "resolved", "verifying"})
        self.verification_remediate_button.setEnabled(verification_usable and verification_item.status in {"remediation_required", "uncertain", "failed"})
        # A learner must explicitly enter a verification attempt before its
        # evaluator result can move the queue to passed/uncertain/remediation.
        self.verification_resolve_button.setEnabled(verification_usable and verification_item.status == "verifying")
        self.verification_attempt.setEnabled(verification_usable)
        self.theme_choice.setEnabled(not self._busy and not self._disposed)
        self.portrait_button.setEnabled(not self._disposed and not self._suspended)
        self.refresh_button.setEnabled(not self._disposed and not self._suspended)
        self.task_sidebar_toggle.setEnabled(not self._disposed and not self._suspended)
        self._update_guide()

    def _toggle_notes(self, expanded):
        self.notes_panel.setVisible(expanded)
        self.notes_toggle.setText("收起笔记" if expanded else "学习笔记")

    def _update_guide(self):
        if not hasattr(self, "guide_button"):
            return
        started = any(turn["state"] == "completed" for turn in self.turns)
        completed = bool(self.task and self.task.get("status") == "completed")
        self.guide_button.setText("发送给老师" if self.input.toPlainText().strip()
                                  else "复习这个知识点" if completed and not started
                                  else "继续上课" if started else "开始上课")
        if not self.task:
            hint = "选择一个知识点后，就可以开始上课。"
        elif self.task["status"] == "completed":
            hint = "你已标记完成；点击“复习这个知识点”先回忆要点，再让老师补充薄弱处。"
        elif started:
            hint = "跟着老师的引导作答，有疑问也可以直接问；学会后点击右侧按钮继续。"
        else:
            hint = "点击“开始上课”，老师会带你逐步学习；已经会了也可以直接标记学会。"
        self.guidance_hint.setText(hint)

    def _continue_lesson(self):
        has_input = bool(self.input.toPlainText().strip())
        started = any(turn["state"] == "completed" for turn in self.turns)
        if self.task and self.task.get("status") == "completed" and not has_input and not started:
            self.teach("continue", question="请带我复习这个知识点，先让我回忆关键点，再指出容易混淆的地方。")
            return
        action = "continue" if has_input or started else "explain"
        self.teach(action)

    def _start(self, operation, callback, message):
        if not self._guard():
            return False
        try:
            token = self._jobs.start(operation)
        except RuntimeError:
            self.state.setText("之前的请求仍未结束，请稍后重试。")
            return False
        self._token, self._callback, self._busy = token, callback, True
        self.state.setText(message)
        self._controls()
        return True

    def _poll(self):
        if not self._guard():
            return
        for token, value, error in self._jobs.drain():
            if token != self._token:
                continue
            callback = self._callback
            self._callback = self._token = None
            self._busy = False
            self._controls()
            if error:
                self._teacher_stage_set_state("error")
                self.state.setText("课堂操作失败；请刷新核对后重试。已有学习记录保留。")
            elif value is not None:
                callback(value)

    def refresh(self, checked=False):
        if not self._guard() or self._records is None:
            return
        task_id = str(self.task["id"]) if self.task else None
        # Account sync can notify the page while the learner is midway through
        # an answer.  Keep that draft and its focus while applying a real
        # remote change; unchanged polls no longer call us at all.
        preserve = {
            "task_id": task_id,
            "text": self.input.toPlainText(),
            "cursor": self.input.textCursor().position(),
            "focused": self.input.hasFocus(),
            "task_scroll": self.task_list.verticalScrollBar().value(),
            "transcript_scroll": self.transcript.verticalScrollBar().value(),
        }
        self._load(task_id=task_id, preserve_refresh_input=preserve)

    def session_recovered(self):
        """Clear a transient request message without discarding lesson input."""
        if not self._guard() or self._busy:
            return
        if self._ready:
            self._ready_message()
        elif self.state.text().startswith("课堂操作失败"):
            self.state.setText("账号连接已恢复；点击“刷新计划”重试课堂读取。")
        self._controls()

    def _load(self, key=None, task_id=None, preserve_refresh_input=None):
        service, records = context_value(self.context, "plan_service"), self._records
        self._invalidate()
        self._preserve_refresh_input = preserve_refresh_input
        self._ready = False
        if service is None:
            self.state.setText("计划服务尚未连接，请由主窗口载入当前计划。")
            self._controls()
            return
        self._start(lambda cancel: load_selection(service, records, cancel, key, task_id),
                    self._loaded, "正在读取计划和学习记录…")

    def _loaded(self, value):
        plans, plan, tasks, task, history = value
        self.plans, self.plan, self.tasks, self.task = plans, plan, tasks, task
        self._contract = self._ensure_task_contract(plan, task)
        self._ready = True
        self.plan_choice.blockSignals(True)
        self.plan_choice.clear()
        if plan is None:
            self.plan_choice.addItem("请选择计划", None)
        for p in plans:
            self.plan_choice.addItem(str(p.title), p.key)
        if plan:
            self.plan_choice.setCurrentIndex(self.plan_choice.findData(plan.key))
        self.plan_choice.blockSignals(False)
        self.task_list.blockSignals(True)
        self.task_list.clear()
        labels = {"pending": "待学习", "in_progress": "进行中", "completed": "已完成"}
        for item in tasks:
            title = str(item.get("title") or item["id"])
            self.task_list.addItem(f"{labels[item['status']]} · {title}")
            list_item = self.task_list.item(self.task_list.count() - 1)
            list_item.setToolTip(f"{labels[item['status']]} · {title}")
        self._apply_task_colors()
        if task:
            self.task_list.setCurrentRow(next(i for i, t in enumerate(tasks) if t["id"] == task["id"]))
        self.task_list.blockSignals(False)
        completed = sum(t["status"] == "completed" for t in tasks)
        self.progress.setRange(0, max(1, len(tasks)))
        self.progress.setValue(completed)
        self.progress.setFormat(f"{completed} / {len(tasks)} 项已完成")
        preserved = self._preserve_refresh_input
        self._preserve_refresh_input = None
        same_task = bool(preserved and task and str(preserved.get("task_id")) == str(task.get("id")))
        if same_task:
            self.input.setPlainText(str(preserved.get("text") or ""))
            cursor = self.input.textCursor()
            cursor.setPosition(min(int(preserved.get("cursor", 0)), len(self.input.toPlainText())))
            self.input.setTextCursor(cursor)
            if preserved.get("focused"):
                QTimer.singleShot(0, self.input.setFocus)
            QTimer.singleShot(0, lambda: self.task_list.verticalScrollBar().setValue(int(preserved.get("task_scroll", 0))))
            QTimer.singleShot(0, lambda: self.transcript.verticalScrollBar().setValue(int(preserved.get("transcript_scroll", 0))))
        else:
            self.input.clear()
        if not task:
            self.turns = []
            self.note.clear()
            self.transcript.set_session({"messages": []})
            self.task_heading.setText("选择一个计划开始学习")
            self.objective.clear()
            self.state.setText("当前计划还没有可学习的任务。" if plan else "请选择计划。" if plans else "还没有学习计划，请先在计划页创建或恢复计划。")
        else:
            self.task_heading.setText(str(task.get("title") or task["id"]))
            text = "\n".join(f"{label}：{task[key]}" for key, label in (("objective", "目标"), ("acceptance_condition", "完成要求")) if task.get(key))
            self.objective.setText(text)
            self._show_history(history)
            self._ready_message()
        self._controls()

    def _ensure_task_contract(self, plan, task):
        """Persist one stable contract for the currently selected execution task."""
        if self._records is None or plan is None or task is None:
            return None
        try:
            revision = (getattr(plan, "details", {}) or {}).get("plan_revision", 1)
            revision = int(revision) if int(revision) > 0 else 1
            contract = contract_for_task(str(plan.plan_id), task, plan_revision=revision)
            existing = self._records.load_contract(plan.key, contract.contract_id)
            if existing is not None:
                return existing
            self._records.save_contract(plan.key, contract)
            return contract
        except (TypeError, ValueError, OSError, sqlite3.Error):
            # Legacy task projections may not contain enough fields for a
            # formal contract.  Keep the existing classroom usable and let the
            # next server sync provide a richer contract.
            return None

    def _show_history(self, history):
        self.turns = history["turns"]
        self._checkpoint = history.get("checkpoint") if isinstance(history, dict) else None
        self._note_revision = int(history.get("note_revision", 0) or 0) if isinstance(history, dict) else 0
        self.note.setPlainText(history["note"])
        latest = next((turn for turn in reversed(self.turns)
                       if turn.get("state") == "completed"), None)
        if latest is None:
            self.teacher_stage.set_reply("")
            self._teacher_stage_set_state("idle")
        else:
            self.teacher_stage.set_reply(latest.get("answer", ""))
            self._teacher_stage_set_state(_teacher_state_for_turn(latest))
        if not self._canonical_preferences:
            self._apply_theme(history.get("theme", "sakura"))
        self.transcript.set_session({"messages": transcript_messages(self.turns)})
        self._refresh_verification_ui()
        self._update_guide()

    def _save_note_with_cas(self, records, plan_key, task_id, text):
        revision = records.save_note(
            plan_key, task_id, text,
            expected_revision=self._note_revision,
            idempotency_key="note:" + uuid4().hex,
        )
        self._note_revision = int(revision)
        return revision

    def _refresh_verification_ui(self):
        """Refresh the queue and recap from account-scoped durable facts."""
        if not hasattr(self, "verification_list"):
            return
        self.verification_list.blockSignals(True)
        self.verification_list.clear()
        items = []
        evidence = []
        errors = []
        if self._records is not None and self.plan is not None:
            try:
                # Queue rows are plan-scoped; recap facts are also projected
                # at plan scope so a second device can show the same pending
                # work even when the learner opens another task first.
                items = self._records.list_verification(self.plan.key)
                evidence = self._records.list_evidence(self.plan.key)
                errors = self._records.list_errors(self.plan.key)
            except (OSError, ValueError, sqlite3.Error):
                items, evidence, errors = [], [], []
        labels = {
            "queued": "待检验", "scheduled": "已安排", "verifying": "检验中",
            "uncertain": "待复核", "remediation_required": "需要补救",
            "passed": "已通过", "resolved": "已解决", "failed": "未通过",
            "in_progress": "进行中", "pending": "待处理",
        }
        for item in items:
            row = QListWidgetItem(
                f"{labels.get(item.status, item.status)} · {item.objective_id} · {item.reason[:80]}"
            )
            row.setData(Qt.ItemDataRole.UserRole, item.verification_id)
            self.verification_list.addItem(row)
        self.verification_list.blockSignals(False)
        pending = sum(item.status not in {"passed", "resolved"} for item in items)
        self.verification_summary.setText(
            f"掌握检验：{len(items)} 项，{pending} 项待处理。完成首次学习后可在这里独立作答并复核。"
            if items else "掌握检验：暂无待确认项目。学习者自述学会不会直接视为已掌握。"
        )
        recap = build_session_recap(checkpoint=self._checkpoint, evidence=evidence, errors=errors)
        if evidence or errors or self._checkpoint is not None:
            self.session_recap.setText(
                f"课堂摘要：已记录 {recap['evidence_count']} 条证据，"
                f"{recap['supported_evidence_count']} 条得到支持；"
                f"待关注能力 {len(recap['attention_competencies'])} 项。下一步：{recap['next_step']}"
            )
        else:
            self.session_recap.setText("本次课堂摘要会在有学习事实后显示。")
        self._controls()

    def _selected_verification(self):
        if not self._records or not self.plan:
            return None
        row = self.verification_list.currentItem()
        verification_id = row.data(Qt.ItemDataRole.UserRole) if row else None
        if not verification_id:
            return None
        try:
            return self._records.get_verification(self.plan.key, str(verification_id))
        except (OSError, ValueError, sqlite3.Error):
            return None

    def _verification_action(self, action):
        if not self._guard() or self._busy or not self._ready or not self.plan:
            return
        item = self._selected_verification()
        if item is None:
            self.state.setText("请先选择一个掌握检验项目。")
            return
        if action == "resolve" and not self.verification_attempt.toPlainText().strip():
            self.state.setText("请先提交一次独立作答。")
            return
        records, plan = self._records, self.plan
        # Verification items are durable plan-level records.  Resolve the
        # contract by its stable id instead of evaluating every item against
        # whichever task happens to be open in the center pane.
        contract = records.load_contract(plan.key, item.contract_id)
        if contract is None and self._contract is not None and self._contract.contract_id == item.contract_id:
            contract = self._contract
        if contract is None and action == "resolve":
            self.state.setText("这条检验缺少对应的学习契约，请先同步当前计划后重试。")
            return
        attempt = self.verification_attempt.toPlainText().strip()
        evaluator_client = context_value(self.context, "evaluator_client") or context_value(self.context, "model_client")

        def operation(cancel):
            if action == "start":
                return records.start_verification(plan.key, item.verification_id)
            if action == "remediate":
                updated = VerificationScheduler.schedule_after_remediation(item)
                records.upsert_verification(plan.key, updated)
                return updated
            if not callable(getattr(evaluator_client, "complete", None)) or contract is None:
                raise EvaluationError("EVALUATOR_UNAVAILABLE", "当前没有可用的 Evaluator。")
            commit = EvaluationService().evaluate_attempt(
                contract, attempt, evaluator_client=evaluator_client,
                records=records, plan_key=plan.key, session_id=f"verification:{item.verification_id}",
                context={"error_context": "verification"}, cancel=cancel,
            )
            return records.resolve_verification(plan.key, item.verification_id, commit.result)

        def saved(updated):
            self._refresh_verification_ui()
            if action == "resolve":
                self.verification_attempt.clear()
            self.state.setText("掌握检验已更新。" if action != "remediate" else "已安排补救，完成补救后可重新检验。")

        self._start(operation, saved, "正在处理掌握检验…")

    def _ready_message(self):
        client = context_value(self.context, "model_client")
        message = "跟着老师逐步学习，也可以直接标记已学会。" if callable(getattr(client, "complete", None)) else "连接模型后可由老师带你学习；现在仍可查看记录、保存笔记或标记已学会。"
        if self.plan and self.plan.issues:
            message += "\n" + "；".join(str(issue) for issue in self.plan.issues)
        self.state.setText(message)

    def _select_plan(self, index):
        if self._guard() and self.plan_choice.itemData(index) is not None:
            self._load(key=self.plan_choice.itemData(index))

    def _select_task(self, index):
        if self._guard() and self._ready and 0 <= index < len(self.tasks):
            self._load(task_id=str(self.tasks[index]["id"]))

    def teach(self, action):
        if not self._guard() or self._busy or not self._ready or self.task is None or action not in ACTIONS:
            return
        client = context_value(self.context, "model_client")
        if not callable(getattr(client, "complete", None)):
            self._ready_message()
            return
        question = self.input.toPlainText().strip()
        learner_attempt = question
        if not question and action in {"question", "answer"}:
            self.state.setText("请先输入问题或作答。")
            return
        if len(question) > 12000:
            self.state.setText("本次输入过长，请控制在 12000 字以内。")
            return
        question = question or {"explain": "请带我开始学习当前知识点。", "confused": "我还没理解，请换一种方式解释。",
            "practice": "请给我一道当前任务的练习。",
            "continue": "请按刚才的引导继续下一小步；如果需要我先作答，请告诉我要做什么。",
        }[action]
        records, plan, task, turns = self._records, self.plan, deepcopy(self.task), deepcopy(self.turns)
        note = self.note.toPlainText()
        preference_fallback = deepcopy(self._preferences)
        if len(note) > 20000:
            self.state.setText("笔记过长，请先保存精简后的笔记。")
            return

        def accepted(result):
            def commit(cancel):
                if cancel.is_set():
                    return None
                records.finish_turn(result["id"], result["answer"], result["state"])
                if result.get("evaluation") is not None and learner_attempt and self._contract is not None:
                    # The optional Evaluator is a separate role.  Its result
                    # was parsed on the worker before this commit; only this
                    # host-side commit writes evidence/error/checkpoint facts.
                    try:
                        EvaluationService().commit(
                            self._contract, result["evaluation"], attempt=learner_attempt,
                            records=records, plan_key=plan.key, session_id=result["id"],
                        )
                    except (EvaluationError, ValueError):
                        # A late contract mismatch must never make a good
                        # teaching turn look lost; retain the attempt as an
                        # ungraded fact and leave completion unchanged.
                        records.record_evidence(
                            plan.key,
                            record_attempt_fact(self._contract, learner_attempt, result["id"]),
                        )
                        records.save_checkpoint(
                            plan.key,
                            build_checkpoint(
                                self._contract,
                                records.list_evidence(plan.key, self._contract.contract_id),
                                records.list_errors(plan.key, self._contract.contract_id),
                            ),
                        )
                # A learner's explicit answer is an auditable fact, not an
                # evaluator verdict.  The model response above remains
                # teaching text and cannot promote the task state.
                if (result["state"] == "completed" and learner_attempt and self._contract is not None
                        and result.get("evaluation") is None and not result.get("evaluation_error")):
                    records.record_evidence(
                        plan.key,
                        record_attempt_fact(self._contract, learner_attempt, result["id"]),
                    )
                    records.save_checkpoint(
                        plan.key,
                        build_checkpoint(
                            self._contract,
                            records.list_evidence(plan.key, self._contract.contract_id),
                            records.list_errors(plan.key, self._contract.contract_id),
                        ),
                    )
                return records.load(plan.key, str(task["id"]))

            def saved(history):
                self._teacher_stage_set_state("idle")
                self._show_history(history)
                if result["state"] == "completed":
                    if self.input.toPlainText().strip() == question:
                        self.input.clear()
                    self.state.setText("老师的回复已保存。可以继续作答、提问，或标记已学会。")
                else:
                    self.state.setText("模型生成失败；问题已保留，可重新发送。")
            self._start(commit, saved, "正在保存本轮学习记录…")

        def produce(cancel):
            if cancel.is_set():
                return None
            self._save_note_with_cas(records, plan.key, str(task["id"]), note)
            service = context_value(self.context, "plan_service")
            loader = getattr(service, "learning_materials", None)
            materials = loader(plan.key, cancel=cancel) if callable(loader) else {
                "materials": [], "notices": ["本轮未提供资料正文。"]}
            if cancel.is_set():
                return None
            preferences = self._preference_snapshot(preference_fallback)
            produced = generate(client, records, plan, task, turns, action, question, cancel,
                                materials=materials, preferences=preferences,
                                checkpoint=self._checkpoint)
            # The Evaluator is a separate role.  The host may inject a
            # dedicated evaluator client sharing the same provider/secret;
            # without that explicit role binding we preserve the teaching
            # turn and do not silently spend a second model request.
            evaluator_client = context_value(self.context, "evaluator_client")
            if evaluator_client is None and getattr(client, "supports_evaluator", False):
                evaluator_client = client
            if (produced and produced.get("state") == "completed" and learner_attempt
                    and self._contract is not None and callable(getattr(evaluator_client, "complete", None))):
                try:
                    evaluated = EvaluationService().evaluate_attempt(
                        self._contract, learner_attempt, evaluator_client=evaluator_client,
                        session_id=produced["id"], context={"error_context": "independent_application"},
                        cancel=cancel,
                    )
                    produced["evaluation"] = evaluated.result
                except EvaluationError:
                    # Tutor output remains usable, but evaluator failure must
                    # not be converted into an evidence or completion fact.
                    produced["evaluation"] = None
                    produced["evaluation_error"] = True
            return produced
        self._teacher_stage_set_state("thinking")
        self._start(produce, accepted, "老师正在思考…")

    def cancel_request(self):
        if not self._guard():
            return
        self._invalidate()
        self._teacher_stage_set_state("idle")
        self.state.setText("已取消本页请求；可刷新核对记录和任务状态。")
        self._controls()

    def save_note(self):
        if not self._guard() or self._busy or not self._ready or not self.task:
            return
        records, key, task_id, note = self._records, self.plan.key, str(self.task["id"]), self.note.toPlainText()
        if len(note) > 20000:
            self.state.setText("笔记过长，请控制在 20000 字以内。")
            return
        def save(cancel):
            if cancel.is_set():
                return None
            self._save_note_with_cas(records, key, task_id, note)
            return True
        self._start(save, lambda _: self.state.setText("学习笔记已保存。"), "正在保存学习笔记…")

    def record_progress(self, status):
        if not self._guard() or self._busy or not self._ready or not self.task or status not in {"in_progress", "completed"}:
            return
        if status == "in_progress" and self.task["status"] != "pending":
            return
        service, records, plan, task = context_value(self.context, "plan_service"), self._records, self.plan, deepcopy(self.task)
        target_ids = knowledge_task_ids(plan, str(task["id"])) if status == "completed" else (str(task["id"]),)
        if not any(str(t["id"]) in target_ids and t["status"] != status for t in self.tasks):
            return
        note = self.note.toPlainText()
        if len(note) > 20000:
            self.state.setText("笔记过长，请控制在 20000 字以内。")
            return
        payload = {"status": status, "result": {"source": "native_classroom", "learning_note": note,
                    "completion_kind": "self_attested" if status == "completed" else "started"}}
        next_id = str(task["id"])
        if status == "completed":
            position = next(i for i, t in enumerate(self.tasks) if t["id"] == task["id"])
            ordered = self.tasks[position + 1:] + self.tasks[:position]
            next_id = next((str(t["id"]) for t in ordered
                            if str(t["id"]) not in target_ids and t["status"] != "completed"), next_id)

        def save(cancel):
            if cancel.is_set():
                return None
            self._save_note_with_cas(records, plan.key, str(task["id"]), note)
            if cancel.is_set():
                return None
            # Shared service owns persistence and validation. No local status shadow.
            mark_learned = getattr(service, "mark_knowledge_learned", None)
            if status == "completed" and callable(mark_learned):
                confirmed_ids = mark_learned(plan.key, str(task["id"]), learning_note=note)
                if set(confirmed_ids) != set(target_ids):
                    raise ValueError("knowledge completion scope mismatch")
            elif len(target_ids) == 1:
                service.record_task_progress(plan.plan_id, str(task["id"]), payload)
            else:
                raise ValueError("knowledge completion service unavailable")
            if cancel.is_set():
                return None
            selection = load_selection(service, records, cancel, task_id=next_id)
            if selection is not None:
                confirmed = selection[1]
                confirmed_tasks = {str(t["id"]): t for t in selection[2] if str(t["id"]) in target_ids}
                if (confirmed is None or confirmed.key != plan.key or set(confirmed_tasks) != set(target_ids)
                        or any(t["status"] != status for t in confirmed_tasks.values())):
                    raise ValueError("progress not confirmed")
                if not cancel.is_set():
                    saved_payload = {**payload, "result": confirmed_tasks[str(task["id"])].get("result", payload["result"])}
                    records.record_receipt(plan.key, str(task["id"]), saved_payload)
                    # The button is an explicit learner attestation.  Keep it
                    # as evidence for a later independent check; it is never
                    # a passing evaluator result and therefore cannot satisfy
                    # a LearningContract by itself.
                    for member_id in target_ids:
                        member = confirmed_tasks.get(str(member_id), task)
                        objective_id = str(member.get("id") or member_id)
                        # A knowledge-point group can contain several phase
                        # tasks.  Persist one stable contract per member so
                        # the plan-level verification queue can later open a
                        # different stage without borrowing the selected
                        # task's objective ids.
                        member_contract = None
                        if self._records is not None:
                            try:
                                member_contract = contract_for_task(
                                    str(plan.plan_id), member,
                                    plan_revision=int((getattr(plan, "details", {}) or {}).get("plan_revision", 1) or 1),
                                )
                                existing_contract = records.load_contract(plan.key, member_contract.contract_id)
                                if existing_contract is None:
                                    records.save_contract(plan.key, member_contract)
                                else:
                                    member_contract = existing_contract
                            except (TypeError, ValueError, OSError, sqlite3.Error):
                                member_contract = None
                        contract_id = member_contract.contract_id if member_contract else (
                            self._contract.contract_id if self._contract else f"task:{objective_id}")
                        evidence = make_evidence(
                            plan_id=str(plan.plan_id),
                            session_id=f"classroom:{plan.key}:{objective_id}",
                            contract_id=contract_id,
                            objective_ids=(objective_id,),
                            competency_ids=(str(member.get("knowledge_point_id") or objective_id),),
                            type="self_report",
                            source="self_report",
                            summary="学习者通过课堂按钮明确自述已学会。",
                            independence="self_report",
                            evaluator_result="insufficient",
                            confidence="low",
                        )
                        records.record_evidence(plan.key, evidence)
                        records.upsert_verification(plan.key, make_verification_item(
                            plan_id=str(plan.plan_id), contract_id=contract_id,
                            objective_id=objective_id, priority="normal",
                            reason="用户自述学会，等待独立场景检验。",
                            source_error_ids=(),
                        ))
                    if self._contract is not None:
                        records.save_checkpoint(
                            plan.key,
                            build_checkpoint(
                                self._contract,
                                records.list_evidence(plan.key, self._contract.contract_id),
                                records.list_errors(plan.key, self._contract.contract_id),
                            ),
                        )
            return selection

        def saved(selection):
            self._loaded(selection)
            if status == "completed":
                self.state.setText("已记录你学会了这个知识点，继续下一个。" if next_id not in target_ids
                                   else "当前计划中的任务已完成，可以继续复习或选择其他计划。")

        self._ready = False
        self._start(save, saved, "正在保存学习进度…")

    def _choose_theme(self, index):
        if not self._guard() or self._busy or not self._records:
            return
        key, records = normalize_classroom_theme(self.theme_choice.itemData(index)), self._records
        preference_fallback = deepcopy(self._preferences)
        self._apply_theme(key)

        def save(cancel):
            if cancel.is_set():
                return None
            store = context_value(self.context, "preferences_store")
            if callable(getattr(store, "load", None)) and callable(getattr(store, "save", None)):
                values = store.load()
                values["classroom_theme"] = key
                return store.save(values)
            records.save_theme(key)
            return {**preference_fallback, "classroom_theme": key}

        def saved(values):
            if isinstance(values, Mapping):
                self.apply_preferences(values)
                self.preferences_changed.emit(deepcopy(dict(values)))
            self.state.setText("课堂主题已保存。")

        self._start(save, saved, "正在保存主题…")

    def _load_portrait(self):
        if not self._guard() or self._busy:
            return
        root = context_value(self.context, "classroom_asset_root")
        if root is None:
            self.media_state.setText("尚未配置 MF 老师形象资源；文本课堂可继续使用。")
            return
        def load(cancel):
            try:
                return {"image": load_portrait(root, cancel)}
            except (OSError, ValueError, TypeError):
                return {"image": None}
        def loaded(result):
            if result["image"] is None:
                self.media_state.setText("形象资源缺失或不可用；文本课堂可继续使用。")
            else:
                self.portrait.setPixmap(QPixmap.fromImage(result["image"]))
                self.media_state.setText("MF 艾莉丝 · 静态教学形象")
            self._ready_message()
        self._start(load, loaded, "正在读取本地老师形象…")

    def _teacher_stage_set_state(self, state):
        if hasattr(self, "teacher_stage"):
            self.teacher_stage.set_state(str(state))

    def _teacher_stage_runtime(self, status):
        if self._disposed or not self._stage_open:
            return
        if status == "ready":
            self.media_state.setText("椿 · Live2D 教师已就绪；文字课堂可继续使用。")
        elif status == "error":
            self.media_state.setText("Live2D 资源暂不可用；文字课堂可继续使用。")

    def _toggle_task_sidebar(self):
        if not self._guard() or self._busy:
            return
        visible = self._splitter.widget(0).isVisible()
        if visible:
            self._task_sidebar_requested = False
        else:
            self._task_sidebar_requested = True
        self._relayout_columns()

    def _toggle_teacher_stage(self):
        if not self._guard():
            return
        if self._stage_open:
            self._close_teacher_stage()
            return
        self.media_state.setText("正在加载本地 Live2D 教师；文字课堂可继续使用。")
        self._stage_transitioning = True
        opened = False
        try:
            self.teacher_stage.set_theme(normalize_classroom_theme(self.theme_choice.currentData() or "sakura"))
            latest = next((turn for turn in reversed(self.turns)
                           if turn.get("state") == "completed"), None)
            self.teacher_stage.set_state("thinking" if self._busy else
                                         _teacher_state_for_turn(latest) if latest else "idle")
            # open_stage() is idempotent: an existing companion and WebEngine
            # are reused while its position follows the host main window.
            opened = bool(self.teacher_window.open_stage(self))
        finally:
            self._stage_transitioning = False
        if not opened:
            # Keep the text classroom usable when the optional local bundle is
            # absent or cannot be initialized.  The companion window may have
            # been shown before its WebEngine was created, so close it here and
            # restore one truthful action state.
            self._close_teacher_stage()
            self.media_state.setText("Live2D 资源暂不可用；文字课堂可继续使用。")
            return
        self._stage_open = True
        if hasattr(self, "_splitter"):
            self._relayout_columns()
        self.portrait_button.setText("收起 Live2D 教师")
        self.portrait_button.setAccessibleName("收起 Live2D 教师")

    def _close_teacher_stage(self):
        if not hasattr(self, "teacher_stage"):
            return
        self._stage_open = False
        self.teacher_window.close_stage()
        if hasattr(self, "portrait_button"):
            self.portrait_button.setText("显示 Live2D 教师")
            self.portrait_button.setAccessibleName("显示 Live2D 教师")
        if hasattr(self, "media_state"):
            self.media_state.setText("可选形象按需加载；文本课堂可独立使用。")
        if hasattr(self, "_splitter"):
            self._relayout_columns()

    def hideEvent(self, event):
        if self._stage_transitioning:
            super().hideEvent(event)
            return
        self._invalidate()
        self._close_teacher_stage()
        self._suspended = True
        self._ambient_timer.stop()
        self._controls()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self._suspended and not self._disposed:
            self._suspended = False
            self._ambient_timer.start()
            self.refresh()

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self._invalidate()
        self._poll_timer.stop()
        self._ambient_timer.stop()
        self._clear()
        self.teacher_window.dispose()

    def closeEvent(self, event):
        self.dispose()
        super().closeEvent(event)


def create_page(context, parent=None) -> QWidget:
    return ClassroomPage(context, parent)
