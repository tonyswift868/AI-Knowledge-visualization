"""Native page entry: create_page(context, parent=None) -> QWidget."""
from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget,
    QScrollArea, QTextBrowser, QVBoxLayout, QWidget,
)

from .jobs import PageJobs
from .service import ToolsService
from ..preferences.store import PreferenceStore
from ..preferences.storage import StorageError


def label(text):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


def button(text, name, callback):
    widget = QPushButton(text)
    widget.setObjectName(name)
    widget.clicked.connect(callback)
    return widget


def browser(name):
    widget = QTextBrowser()
    widget.setObjectName(name)
    widget.setOpenExternalLinks(False)
    return widget


def _context_value(context, key, default=None):
    return context.get(key, default) if isinstance(context, Mapping) else getattr(context, key, default)


def _context_set(context, key, value):
    if isinstance(context, dict):
        context[key] = value
    else:
        setattr(context, key, value)


class ActivityPane(QWidget):
    def __init__(self, page, kind):
        super().__init__(page)
        self.page, self.kind = page, kind
        self.row = None
        self.rows = []
        heading = "复习" if kind == "review" else "学习远征"
        layout = QVBoxLayout(self)
        layout.addWidget(label("根据你的资料建立复习，提交后查看评阅和参考答案。" if kind == "review"
                               else "围绕学习目标建立关卡；作答通过后前进，随时可继续已保存的活动。"))
        split = QSplitter(Qt.Orientation.Horizontal)
        left, right = QWidget(), QWidget()
        left_box, right_box = QVBoxLayout(left), QVBoxLayout(right)
        left_box.setContentsMargins(0, 0, 10, 0)
        right_box.setContentsMargins(10, 0, 0, 0)
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)

        group = QGroupBox(f"新建{heading}")
        form = QFormLayout(group)
        self.title_input = QLineEdit()
        self.title_input.setObjectName(kind + "_title")
        self.title_input.setMaxLength(100)
        self.title_input.setPlaceholderText("给这次活动起个名字")
        form.addRow("活动名称", self.title_input)
        self.source = QPlainTextEdit()
        self.source.setObjectName(kind + "_source")
        self.source.setPlaceholderText("粘贴要复习的真实学习资料" if kind == "review" else "描述学习目标、已有基础和想解决的问题")
        self.source.setMinimumHeight(120)
        form.addRow("学习资料" if kind == "review" else "学习目标", self.source)
        self.count = QSpinBox()
        self.count.setObjectName(kind + "_count")
        self.count.setRange(1, 10)
        self.count.setValue(3)
        form.addRow("题目数" if kind == "review" else "关卡数", self.count)
        self.binding = QComboBox()
        self.binding.setObjectName(kind + "_binding")
        self.binding.addItem("独立活动", None)
        self.binding.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.binding.setMinimumContentsLength(12)
        form.addRow("关联任务（可选）", self.binding)
        self.create_button = button("生成并建立活动", kind + "_create", self.create)
        form.addRow(self.create_button)
        left_box.addWidget(group)
        left_box.addWidget(label("生成题目和评阅答案会调用当前模型。"))
        left_box.addWidget(label("活动和作答保存在当前账号并加入同步队列。关联任务的进度会保存到所选计划。"))
        left_box.addStretch()

        self.history = QComboBox()
        self.history.setObjectName(kind + "_history")
        self.history.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.history.addItem("尚无已保存活动", None)
        self.history.currentIndexChanged.connect(self.choose)
        right_box.addWidget(label("继续已保存的活动"))
        right_box.addWidget(self.history)
        self.progress = label("新建活动后，题目会显示在这里。")
        self.progress.setObjectName(kind + "_progress")
        right_box.addWidget(self.progress)
        self.question = browser(kind + "_question")
        self.question.setMinimumHeight(90)
        right_box.addWidget(self.question, 2)
        self.answer = QPlainTextEdit()
        self.answer.setObjectName(kind + "_answer")
        self.answer.setPlaceholderText("用自己的话作答")
        self.answer.setMinimumHeight(75)
        right_box.addWidget(self.answer, 1)
        actions = QHBoxLayout()
        self.hint_button = button("提示", kind + "_hint", self.hint)
        self.submit_button = button("提交答案", kind + "_submit", self.submit)
        actions.addWidget(self.hint_button)
        actions.addWidget(self.submit_button)
        right_box.addLayout(actions)
        self.feedback = browser(kind + "_feedback")
        self.feedback.setMinimumHeight(100)
        right_box.addWidget(self.feedback, 2)
        self.sync_status = label("")
        self.sync_status.setObjectName(kind + "_sync_status")
        right_box.addWidget(self.sync_status)
        self.retry_button = button("重试保存计划进度", kind + "_retry_progress", self.retry)
        right_box.addWidget(self.retry_button)
        self.render()

    def set_catalog(self, plans):
        selected = self.binding.currentData()
        self.binding.clear()
        self.binding.addItem("独立活动", None)
        for plan in plans:
            for task in plan.tasks:
                value = {"plan_key": plan.key, "plan_id": plan.plan_id, "task_id": str(task["id"])}
                self.binding.addItem(f"{plan.title} / {task['title']}", value)
                if value == selected:
                    self.binding.setCurrentIndex(self.binding.count() - 1)

    def set_rows(self, rows, selected=None):
        selected = selected or (self.row["id"] if self.row else None)
        self.rows = rows
        self.history.blockSignals(True)
        self.history.clear()
        if not rows:
            self.history.addItem("尚无已保存活动", None)
        for row in rows:
            state = "已完成" if row["status"] == "completed" else f"{row['cursor']}/{len(row['items'])}"
            self.history.addItem(f"{row['title']} · {state}", row["id"])
        index = self.history.findData(selected)
        self.history.setCurrentIndex(max(0, index))
        self.history.blockSignals(False)
        self.choose()

    def choose(self):
        record_id = self.history.currentData()
        self.row = next((r for r in self.rows if r["id"] == record_id), None)
        self.answer.clear()
        self.render()

    def accept(self, row, clear_answer=True):
        draft = self.answer.toPlainText()
        self.set_rows([row, *(r for r in self.rows if r["id"] != row["id"])], row["id"])
        if not clear_answer:
            self.answer.setPlainText(draft)
        sync = row.get("plan_sync")
        self.page.status.setText("本地已保存，计划进度待重试。" if sync and sync["state"] == "pending" else "已保存。")

    def create(self):
        kind, title, source, count = self.kind, self.title_input.text(), self.source.toPlainText(), self.count.value()
        binding, service = self.binding.currentData(), self.page.service
        self.page.run(lambda cancel: service.create_activity(kind, title, source, count, binding, cancel), self.accept)

    def submit(self):
        if not self.row:
            return
        kind, record_id, cursor, answer = self.kind, self.row["id"], self.row["cursor"], self.answer.toPlainText()
        service = self.page.service
        self.page.run(lambda cancel: service.submit(kind, record_id, cursor, answer, cancel), self.accept)

    def hint(self):
        if self.row:
            kind, record_id, service = self.kind, self.row["id"], self.page.service
            self.page.run(lambda cancel: service.hint(kind, record_id, cancel), lambda row: self.accept(row, False))

    def retry(self):
        if self.row:
            kind, record_id, service = self.kind, self.row["id"], self.page.service
            self.page.run(lambda cancel: service.retry_progress(kind, record_id, cancel), lambda row: self.accept(row, False))

    def render(self):
        row = self.row
        active = row is not None and row["status"] != "completed"
        self.submit_button.setEnabled(active)
        self.hint_button.setEnabled(active)
        self.answer.setEnabled(active)
        self.retry_button.hide()
        self.sync_status.clear()
        self.question.clear()
        self.feedback.clear()
        if not row:
            self.progress.setText("尚无活动。填写资料或目标即可开始。")
            return
        binding = row.get("binding")
        self.progress.setText(f"{row['title']} · 已完成 {row['cursor']} / {len(row['items'])}"
                              + (" · 已关联计划任务" if binding else " · 独立活动"))
        if active:
            item = row["items"][row["cursor"]]
            text = f"第 {row['cursor'] + 1} 题\n\n{item['prompt']}"
            if row["cursor"] in row["hinted_indices"]:
                text += "\n\n提示：" + item["hint"]
                self.hint_button.setEnabled(False)
            self.question.setPlainText(text)
        else:
            self.question.setPlainText("活动已完成，作答结果已保存。" + ("\n完成表示已答完；请根据评阅反馈继续巩固薄弱处。" if self.kind == "review" else ""))
        if row["attempts"]:
            attempts = []
            for attempt in row["attempts"]:
                item = row["items"][attempt["index"]]
                outcome = "回答正确" if attempt["correct"] else "需要巩固"
                hint = "（使用过提示）" if attempt["hint_used"] else ""
                text = f"第 {attempt['index'] + 1} 题 · {outcome}{hint}\n你的答案：{attempt['answer']}\n模型评阅：{attempt['feedback']}"
                if self.kind == "review" or attempt["correct"]:
                    text += f"\n参考答案：{item['expected_answer']}\n解析：{item['explanation']}"
                attempts.append(text)
            self.feedback.setPlainText("\n\n".join(reversed(attempts)))
        sync = row.get("plan_sync")
        if sync:
            self.sync_status.setText("计划进度已保存。" if sync["state"] == "saved" else sync.get("error", "本地已保存，计划进度待保存。"))
            self.retry_button.setVisible(sync["state"] == "pending")


class ChatPane(QWidget):
    preferences_changed = Signal(dict)

    def __init__(self, page):
        super().__init__(page)
        self.page, self.sessions, self.session = page, [], None
        self._disposed = False
        self._prompt_dirty = False
        context = page.service.context
        shared_store = _context_value(context, 'preferences_store')
        has_shared_store = (callable(getattr(shared_store, 'load', None))
                            and callable(getattr(shared_store, 'save', None)))
        self.preference_store = (shared_store if has_shared_store else PreferenceStore(
            _context_value(context, 'account_id'), _context_value(context, 'data_root')))
        # ToolsService reads this same context on every chat request. Inject the
        # existing store, or the account-scoped fallback, rather than keeping a
        # second prompt source in the widget.
        if not has_shared_store:
            _context_set(context, 'preferences_store', self.preference_store)
            _context_set(context, 'preferences_provider', self.preference_store.load)
        elif not callable(_context_value(context, 'preferences_provider')):
            _context_set(context, 'preferences_provider', self.preference_store.load)
        layout = QVBoxLayout(self)
        layout.addWidget(label("和教师自由交流，保留连续对话；不会修改学习计划。"))
        top = QHBoxLayout()
        self.history = QComboBox()
        self.history.setObjectName("chat_history")
        self.history.currentIndexChanged.connect(self.choose)
        top.addWidget(self.history, 1)
        self.new_button = button("新建对话", "chat_new", self.create)
        top.addWidget(self.new_button)
        layout.addLayout(top)
        self.persona = QLineEdit("你是一位耐心、清晰的学习教师，请根据用户的问题自然交流。")
        self.persona.setMaxLength(2000)
        self.persona.setObjectName("chat_persona")
        form = QFormLayout()
        form.addRow("新对话的教师风格", self.persona)
        layout.addLayout(form)

        self.preference_group = QGroupBox('聊天偏好')
        self.preference_group.setCheckable(True)
        self.preference_group.setChecked(False)
        preference_body = QWidget()
        self.preference_body = preference_body
        preference_layout = QVBoxLayout(preference_body)
        preference_layout.addWidget(label('提示词会添加到下一次闲聊请求；留空表示不添加自定义提示词。'))
        self.free_chat_prompt = QPlainTextEdit()
        self.free_chat_prompt.setObjectName('chat_free_chat_prompt')
        self.free_chat_prompt.setAccessibleName('自由聊天提示词')
        self.free_chat_prompt.setPlaceholderText('例如：回答时先给一个简短例子（最多 6000 字）')
        # Keep the optional editor compact; QPlainTextEdit remains vertically
        # scrollable for longer prompts instead of forcing the chat pane taller.
        self.free_chat_prompt.setFixedHeight(72)
        self.free_chat_prompt.textChanged.connect(self._prompt_edited)
        preference_layout.addWidget(self.free_chat_prompt)
        preference_actions = QHBoxLayout()
        self.save_prompt_button = button('保存聊天提示词', 'chat_save_prompt', self._save_chat_preferences)
        self.reload_prompt_button = button('重新读取', 'chat_reload_prompt', self._reload_chat_preferences)
        self.chat_prompt_status = label('')
        self.chat_prompt_status.setObjectName('chat_prompt_status')
        preference_actions.addWidget(self.save_prompt_button)
        preference_actions.addWidget(self.reload_prompt_button)
        preference_actions.addWidget(self.chat_prompt_status, 1)
        preference_layout.addLayout(preference_actions)
        preference_scroll = QScrollArea(self.preference_group)
        self.preference_scroll = preference_scroll
        preference_scroll.setObjectName('chat_preference_scroll')
        preference_scroll.setWidgetResizable(True)
        preference_scroll.setFrameShape(QFrame.Shape.NoFrame)
        preference_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        preference_scroll.setFixedHeight(104)
        preference_scroll.setWidget(preference_body)
        group_layout = QVBoxLayout(self.preference_group)
        group_layout.addWidget(preference_scroll)
        layout.addWidget(self.preference_group)
        self.preference_group.toggled.connect(self._toggle_preferences)
        self._toggle_preferences(False)
        self._load_chat_preferences()

        self.transcript = browser("chat_transcript")
        layout.addWidget(self.transcript, 4)
        self.input = QPlainTextEdit()
        self.input.setObjectName("chat_input")
        self.input.setPlaceholderText("输入想聊的话题；失败时内容会保留")
        layout.addWidget(self.input, 1)
        self.send_button = button("发送", "chat_send", self.send)
        layout.addWidget(self.send_button)
        self.set_sessions([])

    def _toggle_preferences(self, expanded):
        # Keep the group header visible while its editor is collapsed.
        self.preference_scroll.setVisible(expanded)

    def _prompt_edited(self):
        if not self._disposed:
            self._prompt_dirty = True

    def _set_prompt(self, value):
        self.free_chat_prompt.blockSignals(True)
        self.free_chat_prompt.setPlainText(value)
        self.free_chat_prompt.blockSignals(False)
        self._prompt_dirty = False

    def _reload_chat_preferences(self):
        self._load_chat_preferences(force=True)

    def _load_chat_preferences(self, *, force=False):
        if self._disposed:
            return
        if self._prompt_dirty and not force:
            self.chat_prompt_status.setText('当前有未保存编辑，外部偏好未覆盖；可保存或重新读取。')
            return
        try:
            context = self.page.service.context
            provider = _context_value(context, 'preferences_provider')
            values = provider() if callable(provider) else self.preference_store.load()
            if values is None:
                values = self.preference_store.load()
            if not isinstance(values, Mapping):
                raise StorageError('偏好设置格式无效。')
            prompt = values.get('free_chat_prompt', '')
            if not isinstance(prompt, str):
                raise StorageError('自由聊天提示词格式无效。')
            self._set_prompt(prompt)
            self.chat_prompt_status.setText('已读取当前账号聊天偏好；本机缓存会随账号同步。')
        except (StorageError, TypeError, ValueError) as error:
            self.chat_prompt_status.setText(str(error))

    def apply_preferences(self, values):
        """Refresh the editor without writing; the shared store remains canonical."""
        if self._disposed or not isinstance(values, Mapping):
            return None
        if 'free_chat_prompt' in values and isinstance(values['free_chat_prompt'], str):
            prompt = values['free_chat_prompt']
            if self._prompt_dirty and prompt != self.free_chat_prompt.toPlainText():
                self.chat_prompt_status.setText('当前有未保存编辑，外部偏好未覆盖；可保存或重新读取。')
            else:
                self._set_prompt(prompt)
                self.chat_prompt_status.setText('已读取当前账号聊天偏好；本机缓存会随账号同步。')
        return values

    def _save_chat_preferences(self):
        if self._disposed:
            return None
        try:
            current = self.preference_store.load()
            values = dict(current, free_chat_prompt=self.free_chat_prompt.toPlainText())
            saved = self.preference_store.save(values)
        except StorageError as error:
            self.chat_prompt_status.setText(str(error))
            return None
        self._prompt_dirty = False
        self.chat_prompt_status.setText('聊天提示词已保存并加入账号同步队列。')
        self.page.status.setText('聊天提示词已保存；下一次闲聊会使用最新内容。')
        self.preferences_changed.emit(saved)
        return saved

    def set_sessions(self, sessions, selected=None):
        selected = selected or (self.session.session_id if self.session else None)
        self.sessions = sessions
        self.history.blockSignals(True)
        self.history.clear()
        if not sessions:
            self.history.addItem("尚无对话，请先新建", None)
        for session in sessions:
            title = session.messages[0].content[:32] if session.messages else "新对话"
            self.history.addItem(f"{title} · {session.created_at.astimezone():%m-%d %H:%M}", session.session_id)
        self.history.setCurrentIndex(max(0, self.history.findData(selected)))
        self.history.blockSignals(False)
        self.choose()

    def choose(self):
        self.session = next((s for s in self.sessions if s.session_id == self.history.currentData()), None)
        self.input.clear()
        self.send_button.setEnabled(self.session is not None)
        self.input.setEnabled(self.session is not None)
        self.transcript.setPlainText("\n\n".join(
            ("我" if m.role == "user" else "教师") + "：\n" + m.content
            for m in self.session.messages) if self.session else "新建对话后即可开始。")
        self.transcript.verticalScrollBar().setValue(self.transcript.verticalScrollBar().maximum())

    def accept(self, session):
        self.set_sessions([session, *(s for s in self.sessions if s.session_id != session.session_id)], session.session_id)
        self.page.status.setText("对话已保存。")

    def create(self):
        persona, service = self.persona.text(), self.page.service
        self.page.run(lambda cancel: service.new_chat(persona, cancel), self.accept)

    def send(self):
        if self.session:
            session_id, message, service = self.session.session_id, self.input.toPlainText(), self.page.service
            self.page.run(lambda cancel: service.send(session_id, message, cancel), self.accept)

    def dispose(self):
        self._disposed = True


class ToolsPage(QWidget):
    preferences_changed = Signal(dict)

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.setObjectName("learning_tools_page")
        self.service = ToolsService(context)
        self.jobs = PageJobs(self)
        self.loaded = False
        layout = QVBoxLayout(self)
        heading = label("更多工具")
        heading.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(heading)
        layout.addWidget(label("学习远征与自由聊天放在这里；复习从“我的计划”中选定计划后进入。"))
        self.status = label("正在读取当前账号的记录…")
        self.status.setObjectName("tools_status")
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        self.reload_button = button("重新读取记录", "tools_reload", self.reload)
        self.plans_button = button("刷新可关联计划", "tools_plans", self.reload_plans)
        self.cancel_button = button("取消当前操作", "tools_cancel", self.cancel)
        self.cancel_button.setEnabled(False)
        controls.addWidget(self.reload_button)
        controls.addWidget(self.plans_button)
        controls.addStretch()
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)
        self.tabs = QTabWidget()
        # Keep the account-local review adapter alive for existing records and
        # the plan-page review dialog, but reserve its visible product entry
        # for the selected plan.  The zero-height host keeps old callers that
        # access ``page.review`` working without putting a third activity into
        # this tools page.
        self.review_host = QWidget(self)
        self.review_host.setObjectName("reviewCompatibilityHost")
        self.review_host.setMinimumHeight(0)
        self.review_host.setMaximumHeight(0)
        layout.addWidget(self.review_host)
        self.review = ActivityPane(self.review_host, "review")
        # ActivityPane uses its logical page for service/job access.  Keep
        # that adapter on the real ToolsPage while the widget itself lives in
        # the zero-height compatibility host.
        self.review.page = self
        self.chat = ChatPane(self)
        self.chat.preferences_changed.connect(self.preferences_changed.emit)
        self.expedition = ActivityPane(self, "expedition")
        self.review_tab_index = self.tabs.addTab(QWidget(), "复习")
        # Keep the existing pane/service for account-local records and older
        # callers, but make the product entry point unambiguous: review is a
        # plan action, while this page contains only the two independent tools.
        self.tabs.setTabVisible(self.review_tab_index, False)
        self.tabs.addTab(self.chat, "教师闲聊")
        self.tabs.addTab(self.expedition, "学习远征")
        self.tabs.setCurrentWidget(self.chat)
        layout.addWidget(self.tabs, 1)
        self.jobs.busy_changed.connect(self.busy_changed)
        self.jobs.failed.connect(self.status.setText)
        QTimer.singleShot(0, self.reload)

    def apply_preferences(self, values):
        return self.chat.apply_preferences(values)

    def run(self, operation, callback):
        if self.jobs.run(operation, callback):
            self.status.setText("正在处理，可取消当前操作…")

    def busy_changed(self, busy):
        self.tabs.setEnabled(not busy)
        self.reload_button.setEnabled(not busy)
        self.plans_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def reload(self):
        self.run(self.service.library, self.accept_library)

    def accept_library(self, library):
        self.review.set_rows(library["review"])
        self.chat.set_sessions(library["chat"])
        self.expedition.set_rows(library["expedition"])
        self.status.setText("已读取当前账号记录。")
        if not self.loaded:
            self.loaded = True
            self.reload_plans()

    def reload_plans(self):
        self.run(self.service.catalog, self.accept_catalog)

    def accept_catalog(self, catalog):
        plans, current_key = catalog
        self.review.set_catalog(plans)
        self.expedition.set_catalog(plans)
        current = next((p.title for p in plans if p.key == current_key), None)
        self.status.setText(f"当前计划：{current}。可自行选择关联任务。" if current else "可建立独立活动，也可选择可用的计划任务。")

    def cancel(self):
        self.jobs.cancel()
        self.status.setText("已请求取消，等待当前服务调用结束；已保存的记录保留。")

    def dispose(self):
        self.chat.dispose()
        self.jobs.dispose()

    def closeEvent(self, event):
        self.dispose()
        # A tools page owns timers, native editors and compatibility panes.  A
        # plain close() only hides the top-level widget, leaving that QObject
        # tree to Python's garbage collector.  On Windows/offscreen Qt this
        # can destroy parent/child wrappers after QApplication teardown and
        # corrupt the heap when a page is reopened or an account is switched.
        # Schedule the QObject tree for deletion while Qt still owns the event
        # loop; callers can still close the page idempotently.
        self.deleteLater()
        super().closeEvent(event)


def create_page(context, parent=None) -> QWidget:
    return ToolsPage(context, parent)
