"""Native counterpart of web/loading: 300 ms / 3 s / 15 s, no network.

Timers measure one operation, not its changing status copy. Colours and the
1.8-second breath / 3.4-second five-petal drift mirror loading.css/theme.css.
"""
from __future__ import annotations

import math
import time
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

PALETTES = {
    'sakura': dict(bg='#fbf4f7', surface='#ffffff', text='#35202f', muted='#75596a',
                   accent='#d65a86', petal='#f29bb8', soft='#f5e6ed'),
    'aurora': dict(bg='#0c0e1c', surface='#181b34', text='#eef0ff', muted='#b3b7de',
                   accent='#9b8cff', petal='#4fe0cd', soft='#222541'),
}


def system_reduce_motion():
    """Respect Windows Settings > Accessibility > Animation effects."""
    import os
    if os.name == 'nt':
        import ctypes
        enabled = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return not bool(enabled.value)
    return False


class _Art(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setFixedSize(72, 64)

    def paintEvent(self, event):
        loading = self.owner
        if loading.reduced or loading.tier == 'quiet':
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        age = loading.age
        pulse = .45 + .55 * (1 - math.cos(age * math.tau / 1.8)) / 2
        p.setOpacity(pulse)
        p.setBrush(QColor(loading.colors['accent']))
        p.drawEllipse(QPointF(36, 30), 5, 5)
        if loading.tier == 'long':
            for i in range(5):
                progress = ((age + i * .6) % 3.4) / 3.4
                p.save()
                p.translate(36, 30)
                p.rotate(i * 72)
                p.translate(0, -8 - 16 * math.sin(progress * math.pi))
                p.setOpacity(.65 * math.sin(progress * math.pi))
                p.setBrush(QColor(loading.colors['petal']))
                petal = QPainterPath()
                petal.moveTo(0, 0)
                petal.cubicTo(-5, -3, -5, -9, 0, -11)
                petal.cubicTo(5, -9, 5, -3, 0, 0)
                p.drawPath(petal)
                p.restore()
        else:
            p.setOpacity(.22)
            for y, width in ((49, 42), (57, 30)):
                p.drawRoundedRect(QRectF(15, y, width, 4), 2, 2)


class NativeLoading(QWidget):
    retry_requested = Signal()
    cancel_requested = Signal()
    tier_changed = Signal(str)

    def __init__(self, parent=None, *, clock=time.monotonic, theme='sakura', reduced=None):
        super().__init__(parent)
        self.setObjectName('nativeLoading')
        self.clock = clock
        self.active = False
        self.started = 0.
        self.age = 0.
        self.tier = 'quiet'
        self.slow = False
        self.reduced = system_reduce_motion() if reduced is None else bool(reduced)
        self._can_retry = self._can_cancel = False
        self.setMinimumHeight(112)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(12)
        self.art = _Art(self)
        row.addWidget(self.art)
        copy = QVBoxLayout()
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.slow_label = QLabel('比平时慢，可能是网络问题。')
        self.slow_label.setWordWrap(True)
        copy.addWidget(self.label)
        copy.addWidget(self.slow_label)
        actions = QHBoxLayout()
        self.retry_button = QPushButton('重试')
        self.cancel_button = QPushButton('取消')
        self.retry_button.clicked.connect(self.retry_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)
        for button in (self.retry_button, self.cancel_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        actions.addStretch(1)
        copy.addLayout(actions)
        row.addLayout(copy, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.advance)
        self.configure(theme, self.reduced)
        self.hide()

    def configure(self, theme='sakura', reduced=None):
        self.theme = theme if theme in PALETTES else 'sakura'
        self.colors = PALETTES[self.theme]
        if reduced is not None:
            self.reduced = bool(reduced) or system_reduce_motion()
        c = self.colors
        self.label.setStyleSheet(f"color:{c['text']};font-size:14px;font-weight:600;background:transparent")
        self.slow_label.setStyleSheet(f"color:{c['muted']};font-size:12px;background:transparent")
        for button in (self.retry_button, self.cancel_button):
            button.setStyleSheet(f"QPushButton{{color:{c['accent']};background:{c['surface']};"
                                f"border:1px solid {c['soft']};border-radius:8px;padding:6px 12px}}")
        self.timer.setInterval(100 if self.reduced else 33)
        self.art.setVisible(not self.reduced)
        self.update()

    def begin(self, label, *, retry=False, cancel=False, restart=True):
        if restart or not self.active:
            self.started = self.clock()
        self.active = True
        self.label.setText(label)
        self.setAccessibleName(label)
        self._can_retry, self._can_cancel = retry, cancel
        self.show()
        self.advance()
        self.timer.start()

    def finish(self):
        self.active = False
        self.timer.stop()
        self.hide()

    def advance(self):
        if not self.active:
            return
        self.age = max(0., self.clock() - self.started)
        tier = 'quiet' if self.age < .3 else 'medium' if self.age < 3 else 'long'
        changed = tier != self.tier
        self.tier, self.slow = tier, self.age >= 15
        self.label.setVisible(tier != 'quiet')
        self.art.setVisible(tier != 'quiet' and not self.reduced)
        self.slow_label.setVisible(self.slow)
        self.retry_button.setVisible(self.slow and self._can_retry)
        self.cancel_button.setVisible(self.slow and self._can_cancel)
        if changed:
            self.tier_changed.emit(tier)
        self.art.update()
        self.update()

    def paintEvent(self, event):
        if self.tier == 'quiet':
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor(self.colors['surface']))
        gradient.setColorAt(1, QColor(self.colors['soft'] if self.tier == 'long' else self.colors['surface']))
        p.setBrush(gradient)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 16, 16)


class WebFirstFrame(QObject):
    """Native cover until React has painted content; loadFinished alone is early.

    Installed from the desktop shell, so learning/classroom code is unchanged.
    JavaScript only observes the local DOM; it sends no credentials or actions.
    """
    def __init__(self, home, *, theme='sakura', reduced=False,
                 selector='#root', label='正在准备学习空间…', cancel=None):
        super().__init__(home)
        self.home = home
        self.selector = selector
        self.operation_label = label
        self.cancel_available = cancel is not None
        self.cover = QWidget(home)
        self.cover.setObjectName('nativeFirstFrameCover')
        self.cover.setStyleSheet(f"#nativeFirstFrameCover{{background:{PALETTES[theme]['bg']}}}")
        layout = QVBoxLayout(self.cover)
        layout.addStretch(1)
        title = QLabel('MentorFlow')
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"font-size:28px;font-weight:800;color:{PALETTES[theme]['text']}")
        layout.addWidget(title)
        self.loading = NativeLoading(self.cover, theme=theme, reduced=reduced)
        layout.addWidget(self.loading)
        layout.addStretch(1)
        self.loading.begin(label, retry=True, cancel=cancel is not None)
        if cancel is not None:
            self.loading.cancel_requested.connect(cancel)
        self.loading.retry_requested.connect(self.retry)
        self.cover.show()
        self.cover.raise_()
        home.installEventFilter(self)
        self.cover.setGeometry(home.rect())
        self.poll = QTimer(self)
        self.poll.setInterval(100)
        self.poll.timeout.connect(self.probe)
        self.poll.start()
        self._inflight = False
        self._done = False
        self._epoch = 0
        home.page.loadFinished.connect(self.loaded)
        home.destroyed.connect(self.destroyed_home)

    def destroyed_home(self):
        self._done = True

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            self.cover.setGeometry(self.home.rect())
            self.cover.raise_()
        elif event.type() == QEvent.Type.Close:
            self._done = True
            self.poll.stop()
            self.loading.finish()
        return False

    def loaded(self, success):
        if not success:
            self.loading.begin('学习空间未能打开，请重试。', retry=True, restart=False)
        self.probe()

    def retry(self):
        self._epoch += 1
        self._inflight = False
        self.loading.begin(self.operation_label, retry=True, cancel=self.cancel_available)
        self.home.view.reload()

    def probe(self):
        if getattr(self.home, '_disposed', False):
            self._done = True
            self.poll.stop()
            self.loading.finish()
        if self._done or self._inflight:
            return
        self._inflight = True
        epoch = self._epoch
        import json
        self.home.page.runJavaScript("(()=>{const r=document.querySelector(" + json.dumps(self.selector) + ") || document.body;" + """
            if(!r || !r.innerText.trim()) return false;
            if(!window.__mfNativePaint){window.__mfNativePaint=1;
              requestAnimationFrame(()=>requestAnimationFrame(()=>window.__mfNativePaint=2));}
            return window.__mfNativePaint===2;})()""", lambda ready: self.observed(ready, epoch))

    def observed(self, ready, epoch):
        if epoch != self._epoch or self._done:
            return
        self._inflight = False
        if ready:
            self._done = True
            self.poll.stop()
            self.loading.finish()
            self.cover.hide()
            self.home.removeEventFilter(self)


def account_appearance(data_root, account_id):
    from refactor.learning.preferences.storage import AccountJSON
    try:
        settings = AccountJSON(account_id, data_root, 'app-settings.json').read({})
        theme = settings.get('theme', 'sakura')
        return (theme if theme in PALETTES else 'sakura', bool(settings.get('reduce_motion', False)))
    except (ValueError, OSError, TypeError, AttributeError):
        return 'sakura', False
