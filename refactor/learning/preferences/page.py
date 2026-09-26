from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
                              QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QPushButton,
                              QFrame, QScrollArea, QTableWidget, QTableWidgetItem,
                              QColorDialog, QHeaderView)

from .store import PreferenceStore, DEFAULTS
from .storage import StorageError
from .themes import classroom_theme, classroom_theme_options


def _value(context, key, default=None):
    return context.get(key, default) if isinstance(context, Mapping) else getattr(context, key, default)


class PreferencesPage(QWidget):
    preferences_changed = Signal(dict)
    _OWNED_KEYS = ('classroom_theme', 'analogy_keywords', 'task_colors',
                   'font_scale', 'compact_mode', 'show_calendar')

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self._disposed = False
        shared_store = _value(context, 'preferences_store')
        self.store = (shared_store if callable(getattr(shared_store, 'load', None))
                      and callable(getattr(shared_store, 'save', None))
                      else PreferenceStore(_value(context, 'account_id'), _value(context, 'data_root')))
        outer = QVBoxLayout(self)
        scroll = QScrollArea(self); scroll.setWidgetResizable(True)
        panel = QWidget(scroll); box = QVBoxLayout(panel)
        title = QLabel('设置与偏好'); title.setObjectName('pageTitle'); box.addWidget(title)
        note = QLabel('为当前账号保存课堂主题、类比关键词、任务颜色和界面显示偏好。自由聊天提示词位于“更多工具 → 教师闲聊”。')
        note.setWordWrap(True); box.addWidget(note)
        self.theme = QComboBox(); self.theme.setAccessibleName('课堂主题')
        for key, label, description in classroom_theme_options(): self.theme.addItem(label, key)
        self.font_scale = QDoubleSpinBox(); self.font_scale.setAccessibleName('界面字号比例')
        self.font_scale.setRange(0.85, 1.5); self.font_scale.setSingleStep(0.05); self.font_scale.setDecimals(2)
        self.font_scale.setSuffix(' ×')
        self.compact_mode = QCheckBox('使用紧凑布局'); self.compact_mode.setAccessibleName('紧凑布局')
        self.show_calendar = QCheckBox('在总览显示近期日历'); self.show_calendar.setAccessibleName('总览显示近期日历')
        form = QFormLayout(); form.addRow('课堂主题', self.theme); form.addRow('界面字号比例', self.font_scale)
        form.addRow('布局密度', self.compact_mode); form.addRow('总览日历', self.show_calendar); box.addLayout(form)
        self.preview = QFrame(); preview_box = QVBoxLayout(self.preview)
        self.preview_title = QLabel(); self.preview_note = QLabel(); self.preview_note.setWordWrap(True)
        self.preview_sample = QLabel('老师：今天从一个你熟悉的例子开始。\n你：我准备好了。'); self.preview_sample.setWordWrap(True)
        for widget in (self.preview_title, self.preview_note, self.preview_sample): preview_box.addWidget(widget)
        box.addWidget(self.preview)
        box.addWidget(QLabel('类比关键词'))
        self.analogy_keywords = QLineEdit(); self.analogy_keywords.setAccessibleName('类比关键词'); self.analogy_keywords.setMaxLength(1000)
        self.analogy_keywords.setPlaceholderText('例如：烹饪、足球、音乐'); box.addWidget(self.analogy_keywords)
        box.addWidget(QLabel('任务颜色'))
        self.task_colors = QTableWidget(0, 2); self.task_colors.setHorizontalHeaderLabels(['任务标记', '颜色'])
        self.task_colors.setAccessibleName('任务颜色'); self.task_colors.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.task_colors.setMaximumHeight(200); box.addWidget(self.task_colors)
        color_actions = QHBoxLayout()
        self.add_color_button = QPushButton('添加任务颜色'); self.remove_color_button = QPushButton('移除所选颜色')
        color_actions.addWidget(self.add_color_button); color_actions.addWidget(self.remove_color_button); color_actions.addStretch()
        box.addLayout(color_actions)
        actions = QHBoxLayout()
        self.save_button = QPushButton('保存偏好'); self.reset_button = QPushButton('恢复默认偏好')
        actions.addWidget(self.save_button); actions.addWidget(self.reset_button); actions.addStretch(); box.addLayout(actions)
        self.status = QLabel(''); self.status.setWordWrap(True); box.addWidget(self.status); box.addStretch()
        scroll.setWidget(panel); outer.addWidget(scroll)
        self.theme.currentIndexChanged.connect(self._show_preview)
        self.save_button.clicked.connect(self._save)
        self.reset_button.clicked.connect(self._reset)
        self.add_color_button.clicked.connect(lambda: self._add_color())
        self.remove_color_button.clicked.connect(lambda: self.task_colors.removeRow(self.task_colors.currentRow()))
        self._reload()

    def _reload(self):
        if self._disposed:
            return None
        try:
            values = self.store.load()
        except StorageError as error:
            self._fill(DEFAULTS); self.status.setText(str(error))
            return None
        self._fill(values)
        self.status.setText('已读取当前账号偏好；本机缓存会随账号同步。')
        return values

    def _show_preview(self, *_):
        theme = classroom_theme(self.theme.currentData())
        self.preview_title.setText(f"{theme['symbol']}  {theme['label']}")
        self.preview_note.setText(theme['description'])
        self.preview.setStyleSheet(
            f"QFrame {{ background: {theme['surface']}; color: {theme['text']}; border: 1px solid {theme['border']}; border-radius: {theme['panel_radius']}px; }}"
            f"QLabel {{ background: transparent; border: none; color: {theme['text']}; font-family: {theme['font_family']}; font-size: {theme['font_size']}px; }}")
        self.preview_sample.setStyleSheet(f"background: {theme['teacher_bubble']}; color: {theme['text']}; padding: 12px; border-radius: {theme['bubble_radius']}px;")

    def _add_color(self, name='', color='#c95f8d'):
        if self._disposed or self.task_colors.rowCount() >= 64: return
        row = self.task_colors.rowCount(); self.task_colors.insertRow(row)
        self.task_colors.setItem(row, 0, QTableWidgetItem(name))
        button = QPushButton(); button.setAccessibleName('选择任务颜色')
        self._paint_color(button, color)
        button.clicked.connect(lambda: self._pick_color(button))
        self.task_colors.setCellWidget(row, 1, button)

    @staticmethod
    def _paint_color(button, color):
        button.setProperty('taskColor', color)
        button.setText(color)
        button.setStyleSheet(f'border-left: 18px solid {color}; padding: 5px;')

    def _pick_color(self, button):
        if self._disposed: return
        color = QColorDialog.getColor(QColor(button.property('taskColor')), self, '选择任务颜色')
        if color.isValid(): self._paint_color(button, color.name())

    def _fill(self, values):
        self.theme.setCurrentIndex(self.theme.findData(values['classroom_theme']))
        self.font_scale.setValue(float(values['font_scale']))
        self.compact_mode.setChecked(values['compact_mode'])
        self.show_calendar.setChecked(values['show_calendar'])
        self.analogy_keywords.setText(values['analogy_keywords'])
        self.task_colors.setRowCount(0)
        for name, color in values['task_colors'].items(): self._add_color(name, color)
        self._show_preview()

    def apply_preferences(self, values):
        """Apply canonical fields from the shared shell, ignoring display keys."""
        if self._disposed or not isinstance(values, Mapping):
            return None
        try:
            current = self.store.load()
            updates = {key: values[key] for key in self._OWNED_KEYS if key in values}
            if not updates:
                self._fill(current)
                self.status.setText('已读取当前账号偏好；本机缓存会随账号同步。')
                return current
            candidate = dict(current)
            candidate.update(updates)
            saved = current if candidate == current else self.store.save(candidate)
        except StorageError as error:
            self.status.setText(str(error))
            return None
        self._fill(saved)
        self.status.setText('当前账号偏好已应用；改动等待账号同步。')
        return saved

    def _save(self):
        if self._disposed: return
        try:
            colors = {}
            for row in range(self.task_colors.rowCount()):
                name = self.task_colors.item(row, 0).text().strip()
                if not name: continue
                if name in colors: raise StorageError('任务标记名称不能重复。')
                colors[name] = self.task_colors.cellWidget(row, 1).property('taskColor')
            current = self.store.load()
            values = self.store.save(dict(current, classroom_theme=self.theme.currentData(),
                                          analogy_keywords=self.analogy_keywords.text(), task_colors=colors,
                                          font_scale=self.font_scale.value(), compact_mode=self.compact_mode.isChecked(),
                                          show_calendar=self.show_calendar.isChecked()))
            self.preferences_changed.emit(values)
            self.status.setText('偏好已保存到本机并加入账号同步队列。')
        except StorageError as error:
            self.status.setText(str(error))

    def _reset(self):
        if not self._disposed:
            self._fill(DEFAULTS); self._save()

    def showEvent(self, event):
        super().showEvent(event)
        self._reload()

    def dispose(self):
        if not self._disposed:
            self._disposed = True
            self.setEnabled(False)

    def closeEvent(self, event):
        self.dispose(); super().closeEvent(event)


def create_page(context, parent=None):
    return PreferencesPage(context, parent)
