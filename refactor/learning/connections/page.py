from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                              QLabel, QLineEdit, QPushButton, QComboBox,
                              QDoubleSpinBox, QSpinBox, QScrollArea)

from .errors import ModelError
from ..preferences.storage import StorageError


DEFAULT_PRESET = {
    'name': 'DeepSeek Flash',
    'base_url': 'https://api.deepseek.com',
    'model': 'deepseek-flash',
}


def _value(context, key):
    return context[key] if isinstance(context, dict) else getattr(context, key)


class ConnectionsPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.client = _value(context, 'model_client')
        if self.client.account_id != _value(context, 'account_id') or self.client.data_root != Path(_value(context, 'data_root')).expanduser().resolve():
            raise ValueError('Model client does not belong to this account/data root')
        self._disposed = False
        self._future = self._executor = None
        self._cancel = threading.Event()
        outer = QVBoxLayout(self)
        scroll = QScrollArea(self); scroll.setWidgetResizable(True)
        panel = QWidget(scroll); box = QVBoxLayout(panel)
        title = QLabel('模型连接'); title.setObjectName('pageTitle'); box.addWidget(title)
        note = QLabel('保存预设后，计划、课堂和工具会共用此连接。只有点击测试或执行模型动作时才会发送请求。')
        note.setWordWrap(True); box.addWidget(note)
        self.presets = QComboBox(); self.presets.setAccessibleName('已保存的模型预设'); box.addWidget(self.presets)
        form = QFormLayout()
        self.name = QLineEdit(); self.name.setMaxLength(80)
        self.base_url = QLineEdit(); self.base_url.setPlaceholderText('HTTPS API 地址，或本机模型地址')
        self.model = QLineEdit(); self.model.setMaxLength(200)
        self.api_key = QLineEdit(); self.api_key.setEchoMode(QLineEdit.EchoMode.Password); self.api_key.setMaxLength(1024)
        self.temperature = QDoubleSpinBox(); self.temperature.setRange(0, 2); self.temperature.setSingleStep(0.1); self.temperature.setValue(0.2)
        self.timeout = QSpinBox(); self.timeout.setRange(1, 300); self.timeout.setValue(60); self.timeout.setSuffix(' 秒')
        self.temperature.setToolTip('控制模型回答的随机程度：数值越低越稳定，越高越发散。0.2 适合计划和课堂的稳定输出。')
        self.timeout.setToolTip('单次模型请求最多等待的时间；只影响等待多久，不限制回答长度或模型能力。')
        for text, field in [('预设名称', self.name), ('模型地址', self.base_url), ('模型名称', self.model), ('API Key', self.api_key), ('生成温度', self.temperature), ('请求时限', self.timeout)]:
            field.setAccessibleName(text); form.addRow(text, field)
        box.addLayout(form)
        parameter_hint = QLabel('生成温度控制回答的稳定程度；请求时限控制单次网络请求最多等待多久。计划、课堂和工具共用这两个设置。')
        parameter_hint.setWordWrap(True)
        parameter_hint.setObjectName('modelParameterHint')
        box.addWidget(parameter_hint)
        actions = QHBoxLayout()
        self.save_button = QPushButton('保存并使用预设')
        self.test_button = QPushButton('保存并测试连接')
        self.cancel_button = QPushButton('取消测试'); self.cancel_button.setEnabled(False)
        self.delete_button = QPushButton('删除所选预设')
        for button in (self.save_button, self.test_button, self.cancel_button, self.delete_button): actions.addWidget(button)
        box.addLayout(actions)
        self.status = QLabel('尚未配置模型。'); self.status.setWordWrap(True); box.addWidget(self.status)
        hint = QLabel('API Key 保存在当前 Windows 用户的凭据库中，不会写入普通配置。测试会发送一条简短请求，服务商可能计费。')
        hint.setWordWrap(True); box.addWidget(hint)
        self.model_notice = QLabel('默认使用 DeepSeek Flash 的 OpenAI 兼容 Chat Completions 接口；模型名称和地址均可修改。')
        self.model_notice.setWordWrap(True); box.addWidget(self.model_notice); box.addStretch()
        scroll.setWidget(panel); outer.addWidget(scroll)
        self._timer = QTimer(self); self._timer.setInterval(40); self._timer.timeout.connect(self._poll)
        self.presets.currentIndexChanged.connect(self._selected)
        self.save_button.clicked.connect(self._save)
        self.test_button.clicked.connect(self._test)
        self.cancel_button.clicked.connect(lambda: self._cancel.set())
        self.delete_button.clicked.connect(self._delete)
        self._refresh()

    def _refresh(self, selected=None):
        try:
            presets = self.client.list_presets()
            active = self.client.get_configuration()
            identifier = selected or (active['id'] if active else None)
            self.presets.blockSignals(True)
            self.presets.clear(); self.presets.addItem('新建预设', None)
            for preset in presets: self.presets.addItem(preset['name'], preset)
            index = next((i for i in range(1, self.presets.count()) if self.presets.itemData(i)['id'] == identifier), 0)
            self.presets.setCurrentIndex(index); self.presets.blockSignals(False)
            self._selected()
        except (ModelError, StorageError) as error:
            self.status.setText(str(error))

    def _selected(self, *_):
        preset = self.presets.currentData()
        self.api_key.clear()
        self.name.setText(preset['name'] if preset else DEFAULT_PRESET['name'])
        self.base_url.setText(preset['base_url'] if preset else DEFAULT_PRESET['base_url'])
        self.model.setText(preset['model'] if preset else DEFAULT_PRESET['model'])
        self.temperature.setValue(preset['temperature'] if preset else 0.2)
        self.timeout.setValue(int(preset['timeout']) if preset else 60)
        self.api_key.setPlaceholderText('已保存；留空保留当前密钥' if preset and preset['has_api_key'] else '输入 API Key')
        self.delete_button.setEnabled(preset is not None)
        self.model_notice.setVisible(not preset or (
            preset['base_url'].rstrip('/') in ('https://api.deepseek.com', 'https://api.deepseek.com/v1')
            and preset['model'] == DEFAULT_PRESET['model']))
        self.status.setText('编辑后点击“保存并使用预设”使其生效。' if preset else '已填入 DeepSeek Flash；填写 API Key 后保存使用，也可选择其他模型。')

    def _save(self):
        if self._disposed: return False
        selected = self.presets.currentData()
        try:
            saved = self.client.save_preset(self.name.text(), self.base_url.text(), self.model.text(),
                api_key=self.api_key.text() or None, preset_id=selected['id'] if selected else None,
                temperature=self.temperature.value(), timeout=self.timeout.value())
            self._refresh(saved['id'])
            self.status.setText('预设已保存，后续模型动作将使用此配置。')
            return True
        except (ModelError, StorageError) as error:
            self.status.setText(str(error)); return False
        finally:
            self.api_key.clear()

    def _busy(self, value):
        for widget in (self.presets, self.name, self.base_url, self.model, self.api_key, self.temperature, self.timeout,
                       self.save_button, self.test_button, self.delete_button): widget.setEnabled(not value)
        self.cancel_button.setEnabled(value)

    def _test(self):
        if self._disposed or self._future is not None or not self._save(): return
        self._cancel = threading.Event()
        if self._executor is None: self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='mf-model-test')
        self._future = self._executor.submit(self.client.complete, [{'role': 'user', 'content': 'Respond with exactly OK.'}], cancel=self._cancel)
        self._busy(True); self.status.setText('正在测试连接…'); self._timer.start()

    def _poll(self):
        if self._disposed or self._future is None or not self._future.done(): return
        future, self._future = self._future, None
        self._timer.stop(); self._busy(False)
        try:
            future.result()
            self.status.setText('连接成功，模型已返回有效文字回复。')
        except (ModelError, StorageError) as error:
            self.status.setText(str(error))
        except Exception:
            self.status.setText('测试未完成，请检查配置后重试。')

    def _delete(self):
        selected = self.presets.currentData()
        if self._disposed or not selected: return
        try:
            self.client.delete_preset(selected['id']); self._refresh()
            self.status.setText('所选预设及其 API Key 已删除。')
        except (ModelError, StorageError) as error:
            self.status.setText(str(error))

    def dispose(self):
        if self._disposed: return
        self._disposed = True
        self._cancel.set(); self._timer.stop(); self.api_key.clear()
        if self._executor is not None: self._executor.shutdown(wait=False, cancel_futures=True)
        self._future = self._executor = None
        self.setEnabled(False)

    def closeEvent(self, event):
        self.dispose(); super().closeEvent(event)


def create_page(context, parent=None):
    return ConnectionsPage(context, parent)
