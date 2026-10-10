"""Lightweight native LoginGate shell; no account, network, database or WebEngine imports."""
from __future__ import annotations

from enum import Enum
from PySide6.QtCore import Qt, Signal, QTimer, QVariantAnimation, QRectF, QLineF
from pathlib import Path

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QColor, QCursor, QLinearGradient, QPainter, QPainterPath, QRadialGradient, QPen
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (QApplication, QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QCheckBox,
                               QLineEdit, QComboBox, QGraphicsDropShadowEffect, QToolButton, QSizePolicy)

LOGIN_GATE_OBJECT_NAME = "mentorflowLoginGateWindow"
_ICONS = (Path(__file__).resolve().parent / "resources" / "login").as_posix()

# Same mark as the new window's sidebar (web/src/ui.tsx BrandMark).
BRAND_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#f29bb8"/><stop offset="1" stop-color="#b8457a"/></linearGradient></defs>'
    '<rect width="40" height="40" rx="12" fill="url(#g)"/><g transform="translate(20 21)">'
    + "".join(f'<path transform="rotate({a}) scale(.42)" d="M0 0C-10-7-13-22-6-31L0-26.5L6-31C13-22 10-7 0 0Z" fill="#fff" opacity=".95"/>'
              for a in (0, 72, 144, 216, 288))
    + '<circle r="2.6" fill="#b8457a"/></g></svg>')


# Static background: deep plum night fading into sakura pink, soft glows and a few
# petals. Painted once per resize/expose; no animation (see BUG-04).
_PETALS = ((0.12, 0.30, 11, 20), (0.86, 0.22, 9, -35), (0.78, 0.52, 7, 60), (0.08, 0.66, 8, -15),
           (0.92, 0.80, 10, 25), (0.20, 0.90, 6, 70), (0.55, 0.10, 6, -50), (0.35, 0.18, 5, 10))


def paint_night_sakura(painter, width, height):
    sky = QLinearGradient(0, 0, width * 0.35, height)
    sky.setColorAt(0.0, QColor("#2a1428"))
    sky.setColorAt(0.45, QColor("#5a2250"))
    sky.setColorAt(0.8, QColor("#a83a74"))
    sky.setColorAt(1.0, QColor("#e3769d"))
    painter.fillRect(0, 0, width, height, sky)
    for cx, cy, radius, color in ((0.15, 0.05, 0.75, (242, 155, 184, 70)), (0.95, 0.35, 0.6, (155, 110, 255, 45)),
                                  (0.35, 1.0, 0.8, (255, 170, 200, 80))):
        glow = QRadialGradient(width * cx, height * cy, width * radius)
        glow.setColorAt(0.0, QColor(*color))
        glow.setColorAt(1.0, QColor(color[0], color[1], color[2], 0))
        painter.fillRect(0, 0, width, height, glow)
    petal = QPainterPath()
    petal.moveTo(0, 0)
    petal.cubicTo(-10, -7, -13, -22, -6, -31)
    petal.lineTo(0, -26.5)
    petal.lineTo(6, -31)
    petal.cubicTo(13, -22, 10, -7, 0, 0)
    painter.setPen(Qt.PenStyle.NoPen)
    for x, y, size, angle in _PETALS:
        painter.save()
        painter.translate(width * x, height * y)
        painter.rotate(angle)
        painter.scale(size / 31, size / 31)
        painter.setBrush(QColor(255, 214, 229, 120))
        painter.drawPath(petal)
        painter.restore()


def login_qss() -> str:
    return f"""
QWidget#mentorflowLoginGateWindow {{
  font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"; font-size: 14px; color: #35202f; }}
QLabel {{ background: transparent; }}
QLabel#loginTitle {{ color: #ffffff; font-size: 28px; font-weight: 800; letter-spacing: 0.3px; }}
QLabel#loginSubtitle {{ color: rgba(255, 228, 238, 0.82); font-size: 13px; }}
QLabel#loginStateTitle {{ color: #35202f; font-size: 19px; font-weight: 800; }}
QLabel#loginStateDetail {{ color: #75596a; font-size: 13px; }}
QLabel#loginSessionNotice {{ color: #ffe3ee; font-size: 13px; }}
QFrame#loginCard {{ background: #ffffff; border: 1px solid rgba(185, 96, 136, 0.18); border-radius: 22px; }}
QLineEdit {{ min-height: 44px; padding: 0 14px; border: 1px solid rgba(185, 96, 136, 0.32); border-radius: 13px;
  background: #fffafc; color: #35202f; font-size: 14px; selection-background-color: #f29bb8; }}
QLineEdit:hover {{ border-color: rgba(214, 90, 134, 0.55); }}
QLineEdit:focus {{ border: 1.5px solid #d65a86; background: #ffffff; }}
QLineEdit:disabled {{ color: #a78b9a; background: #faf3f6; }}
QComboBox {{ min-height: 40px; padding: 0 36px 0 14px; border: 1px solid rgba(185, 96, 136, 0.32); border-radius: 12px;
  background: #fffafc; color: #35202f; font-size: 14px; }}
QComboBox:hover {{ border-color: rgba(214, 90, 134, 0.55); }}
QComboBox:focus {{ border: 1.5px solid #d65a86; }}
QComboBox#loginAccountBox {{ min-height: 44px; border-radius: 13px; padding: 0 36px 0 4px; }}
QComboBox#loginAccountBox QLineEdit {{ border: 0; background: transparent; min-height: 40px; padding: 0 10px; }}
QComboBox#loginAccountBox QLineEdit:focus {{ border: 0; background: transparent; }}
QComboBox::drop-down {{ border: 0; width: 32px; }}
QComboBox::down-arrow {{ image: url({_ICONS}/chevron.svg); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: #ffffff; border: 1px solid rgba(185, 96, 136, 0.3); border-radius: 10px; padding: 4px;
  selection-background-color: rgba(214, 90, 134, 0.12); selection-color: #b8457a; outline: 0; }}
QCheckBox {{ color: #75596a; font-size: 13px; spacing: 7px; }}
QCheckBox:hover {{ color: #d65a86; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid #ba809b; background: #ffffff; }}
QCheckBox::indicator:unchecked {{ image: none; border: 1px solid #ba809b; border-radius: 5px; background: #ffffff; }}
QCheckBox::indicator:hover {{ border-color: #d65a86; }}
QCheckBox::indicator:checked {{ border: 0; image: url({_ICONS}/check.svg);
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f07aa2, stop:1 #b8457a); }}
QCheckBox:disabled {{ color: #c7b3be; }}
QPushButton#loginPrimaryButton {{ min-height: 50px; color: white; border: 0; border-radius: 14px; font-size: 15px; font-weight: 800;
  background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f07aa2, stop:0.45 #d65a86, stop:1 #a8467e); }}
QPushButton#loginPrimaryButton:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #e0648f, stop:1 #973b6f); }}
QPushButton#loginPrimaryButton:pressed {{ background: #973b6f; padding-top: 2px; }}
QPushButton#loginPrimaryButton:disabled {{ background: #e3c6d3; color: #fff7fa; }}
QPushButton#loginAccountAction {{ min-height: 26px; padding: 2px 4px; border: 0; background: transparent; color: #b8457a; font-size: 13px; font-weight: 600; }}
QPushButton#loginAccountAction:hover {{ color: #d65a86; text-decoration: underline; }}
QPushButton#loginAccountAction:pressed {{ color: #973b6f; }}
QPushButton#loginAccountAction:disabled {{ color: #c7b3be; }}
QPushButton#loginSmallButton {{ min-height: 40px; padding: 0 12px; border: 1px solid rgba(185, 96, 136, 0.32); border-radius: 12px;
  background: #ffffff; color: #75596a; font-size: 13px; font-weight: 600; }}
QPushButton#loginSmallButton:hover {{ color: #d65a86; border-color: #d65a86; background: rgba(214, 90, 134, 0.06); }}
QPushButton#loginSmallButton:pressed {{ background: rgba(214, 90, 134, 0.14); }}
QPushButton#loginSmallButton:disabled {{ color: #c7b3be; border-color: rgba(185, 96, 136, 0.15); }}
QPushButton#loginCancelButton {{ min-height: 34px; padding: 0 12px; border: 0; border-radius: 11px; background: transparent; color: rgba(255, 236, 244, 0.85); font-size: 13px; }}
QPushButton#loginCancelButton:hover {{ color: #ffffff; background: rgba(255, 255, 255, 0.12); }}
QToolTip {{ background: #ffffff; color: #35202f; border: 1px solid rgba(185, 96, 136, 0.3); border-radius: 8px; padding: 6px 8px; }}
"""

class LoginGateState(str, Enum):
    LOGIN_READY = "LOGIN_READY"
    AUTHENTICATING = "AUTHENTICATING"
    AUTH_FAILED = "AUTH_FAILED"


class CaptionButton(QToolButton):
    """Native-sized caption hit area; animate paint, not the whole window QSS."""
    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.action = action
        self._hovered = False
        self._level = 0.0
        self.theme, self.reduced = 'sakura', False
        self.setFixedSize(46, 32)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAccessibleName('关闭' if action == 'close' else '最小化')
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(100)
        self.animation.valueChanged.connect(self._paint_level)

    def _paint_level(self, value):
        self._level = float(value)
        self.update()

    def configure(self, theme, reduced):
        self.theme, self.reduced = theme, bool(reduced)
        self._transition()

    def _transition(self):
        target = 2.0 if self.isDown() else 1.0 if self._hovered else 0.0
        self.animation.stop()
        if self.reduced or not self.isVisible():
            self._paint_level(target)
        else:
            self.animation.setStartValue(self._level)
            self.animation.setEndValue(target)
            self.animation.start()

    def enterEvent(self, event):
        self._hovered = True
        super().enterEvent(event)
        self._transition()

    def leaveEvent(self, event):
        self._hovered = False
        super().leaveEvent(event)
        self._transition()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self._transition()

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self._transition()

    def mouseMoveEvent(self, event):
        down, hovered = self.isDown(), self._hovered
        super().mouseMoveEvent(event)
        self._hovered = self.rect().contains(event.position().toPoint())
        if down != self.isDown() or hovered != self._hovered:
            self._transition()

    def keyPressEvent(self, event):
        super().keyPressEvent(event)
        self._transition()

    def keyReleaseEvent(self, event):
        super().keyReleaseEvent(event)
        self._transition()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def hitButton(self, point):
        return self.rect().contains(point)

    @staticmethod
    def _blend(first, second, fraction):
        return QColor.fromRgbF(*(a + (b - a) * fraction for a, b in zip(first.getRgbF(), second.getRgbF())))

    def paintEvent(self, event):
        close = self.action == 'close'
        hover = QColor('#c42b1c' if close else '#343956' if self.theme == 'aurora' else '#e5e5e5')
        pressed = QColor('#a62317' if close else '#272c46' if self.theme == 'aurora' else '#cccccc')
        normal = QColor(hover); normal.setAlpha(0)
        level = min(2.0, max(0.0, self._level))
        background = self._blend(normal, hover, level) if level <= 1 else self._blend(hover, pressed, level - 1)
        foreground = self._blend(QColor('#ffffff'), QColor('#202020'), min(1.0, level)) if not close and self.theme == 'sakura' else QColor('#ffffff')
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(background)
        p.drawRoundedRect(QRectF(self.rect()), 5, 5)
        p.setPen(QPen(foreground, 1.5))
        x, y = self.width() / 2, self.height() / 2
        if close:
            p.drawLine(QLineF(x-4.5, y-4.5, x+4.5, y+4.5))
            p.drawLine(QLineF(x+4.5, y-4.5, x-4.5, y+4.5))
        else:
            p.drawLine(QLineF(x-5, y+3, x+5, y+3))
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(foreground, 1, Qt.PenStyle.DotLine))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(3, 3, -3, -3), 3, 3)


class LoginGateWindow(QWidget):
    capabilities_received = Signal(object)
    first_paint_completed = Signal()
    login_requested = Signal()
    retry_requested = Signal()
    loading_retry_requested = Signal()
    background_succeeded = Signal(str, object)
    background_failed = Signal(str, str)
    cancel_requested = Signal()
    closed = Signal()
    register_requested = Signal()
    forgot_password_requested = Signal()
    account_selected = Signal(str)
    account_remove_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(LOGIN_GATE_OBJECT_NAME)
        self.setWindowTitle("登录 MentorFlow")
        # Frameless with our own top bar (drag to move, minimise, close); the
        # painted background replaces the plain system caption and frame.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # Qt recreates a raster top-level when its first OpenGL child arrives.
        # A visible, permanent 1px anchor makes this window use OpenGL from
        # birth, before WebEngine is warmed or an account page is attached.
        # Hiding the anchor would defer the surface conversion until later.
        self._composition_anchor = QOpenGLWidget(self)
        self._composition_anchor.setFixedSize(1, 1)
        self._composition_anchor.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._composition_anchor.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Reserve the native form viewport from first paint, including the
        # native consent controls. Loading/form/refresh never resize the shell.
        area = self.screen().availableGeometry()
        available_width = max(240, area.width() - 48)
        available_height = max(320, area.height() - 80)
        self.resize(min(480, available_width), min(740, available_height))
        self.setMinimumSize(min(440, available_width), min(680, available_height))
        self.state = LoginGateState.LOGIN_READY
        self._centered = False
        self._first_paint_scheduled = False
        self._login_enabled = True
        self._update_login_available = True
        self.account_capabilities = {}
        self.current_theme = ('aurora' if QApplication.styleHints().colorScheme() is Qt.ColorScheme.Dark
                              else 'sakura')
        self._build()
        self.set_loading_appearance(self.current_theme)
        self.capabilities_received.connect(self.set_account_capabilities)
        self.set_state(LoginGateState.LOGIN_READY)

    def _build(self) -> None:
        self.setStyleSheet(login_qss())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 10, 28, 18)
        outer.setSpacing(8)
        chrome = QHBoxLayout()
        chrome.setContentsMargins(0, 0, 0, 0)
        chrome.setSpacing(2)
        chrome.addStretch(1)
        self.minimize_button = CaptionButton('minimize', self)
        self.minimize_button.setObjectName("loginChrome")
        self.minimize_button.setText("—")
        self.minimize_button.setToolTip("最小化")
        self.minimize_button.clicked.connect(self.showMinimized)
        self.close_button = CaptionButton('close', self)
        self.close_button.setObjectName("loginChromeClose")
        self.close_button.setText("✕")
        self.close_button.setToolTip("关闭")
        self.close_button.clicked.connect(self.close)
        chrome.addWidget(self.minimize_button)
        chrome.addWidget(self.close_button)
        outer.addLayout(chrome)
        self.brand_widgets = []
        brand = QSvgWidget()
        brand.setObjectName("loginBrandMark")
        brand.load(QByteArray(BRAND_SVG.encode("utf-8")))
        brand.setFixedSize(60, 60)
        brand_wrap = QHBoxLayout()
        brand_wrap.addStretch(1)
        brand_wrap.addWidget(brand)
        brand_wrap.addStretch(1)
        outer.addLayout(brand_wrap)
        self.brand_widgets.append(brand)
        title = QLabel("MentorFlow")
        title.setObjectName("loginTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(title)
        self.brand_widgets.append(title)
        subtitle = QLabel("你的 AI 学习导师")
        subtitle.setObjectName("loginSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(subtitle)
        self.brand_widgets.append(subtitle)
        outer.addSpacing(4)
        card = QFrame()
        self.login_card = card
        card.setObjectName("loginCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 26, 28, 26)
        card_layout.setSpacing(12)
        card.setMinimumHeight(230)
        self.content_layout = card_layout
        self.auth_content = None
        self.state_title = QLabel()
        self.state_title.setTextFormat(Qt.TextFormat.PlainText)
        self.state_title.setObjectName("loginStateTitle")
        self.state_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.state_title)
        self.state_detail = QLabel()
        self.state_detail.setObjectName("loginStateDetail")
        self.state_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_detail.setWordWrap(True)
        self.state_detail.setMinimumHeight(40)
        card_layout.addWidget(self.state_detail)
        from .native_loading import NativeLoading
        self.loading = NativeLoading(card)
        self.loading.retry_requested.connect(self.loading_retry_requested)
        self.loading.cancel_requested.connect(self.cancel_requested)
        card_layout.addWidget(self.loading)
        account_row = QHBoxLayout()
        self.username_field = QLineEdit()
        self.username_field.setObjectName('nativeUsername')
        self.username_field.setPlaceholderText('账号（点右侧箭头选择已记住的账号）')
        self.username_field.setAccessibleName('账号')
        self.username_field.setMaxLength(320)
        self.account_selector = QComboBox()
        self.account_selector.setObjectName('loginAccountBox')
        self.account_selector.setAccessibleName('账号')
        self.account_selector.setEditable(True)
        self.account_selector.setLineEdit(self.username_field)   # the same box: choose from the list or type a new name
        self.account_selector.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.account_selector.setCompleter(None)
        self.account_selector.activated.connect(lambda _: self.account_selected.emit(self.account_selector.currentData() or ''))
        self.add_account_button = QPushButton('添加')
        self.add_account_button.clicked.connect(self.cancel_requested.emit)
        self.remove_account_button = QPushButton('移除')
        self.remove_account_button.setToolTip('只移除本机账号与凭据；服务器账号及学习资料保留。')
        self.remove_account_button.clicked.connect(lambda: self.account_remove_requested.emit(self.account_selector.currentData() or ''))
        self.add_account_button.setToolTip('登录另一个账号')
        for button in (self.add_account_button, self.remove_account_button):
            button.setObjectName('loginSmallButton')
        account_row.setSpacing(8)
        account_row.addWidget(self.account_selector,1)
        account_row.addWidget(self.remove_account_button)
        self.add_account_button.hide()   # typing another name in the box is how a new account signs in
        card_layout.addLayout(account_row)
        self.password_field = QLineEdit()
        self.password_field.setObjectName('nativePassword')
        self.password_field.setPlaceholderText('密码')
        self.password_field.setAccessibleName('密码')
        self.password_field.setMaxLength(1024)
        self.password_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_field.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.show_password = QCheckBox('显示密码')
        self.show_password.toggled.connect(lambda visible: self.password_field.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password))
        self.password_field.returnPressed.connect(self._activate)
        card_layout.addWidget(self.password_field)
        # Options on one row inside the card (show password, remember password, auto login).
        options = QHBoxLayout()
        options.setSpacing(10)
        self.options_row = options
        options.addWidget(self.show_password)
        card_layout.addLayout(options)
        self.primary_button = QPushButton("登录 MentorFlow")
        self.primary_button.setObjectName("loginPrimaryButton")
        self.primary_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.primary_button.setAccessibleName("登录 MentorFlow")
        self.primary_button.setDefault(True)
        self.primary_button.clicked.connect(self._activate)
        card_layout.addWidget(self.primary_button)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(46)
        shadow.setOffset(0, 16)
        shadow.setColor(QColor(40, 10, 32, 110))
        card.setGraphicsEffect(shadow)
        self.card_shadow = shadow
        outer.addWidget(card, 1)
        self.remember_box = QCheckBox("保存会话")
        self.remember_box.setObjectName("rememberLoginCheckBox")
        self.auto_box = QCheckBox("自动登录")
        self.auto_box.setObjectName("autoLoginCheckBox")
        self.auto_box.setEnabled(False)
        self.remember_box.toggled.connect(self._remember_toggled)
        self.remember_box.hide()
        self.remember_password_box = QCheckBox('记住密码')
        self.remember_password_box.setObjectName('rememberPasswordCheckBox')
        self.remember_password_box.setToolTip('密码仅保存在此设备的 Windows 凭据管理器；30天未登录后失效，成功登录续期。')
        self.auto_box.setToolTip('打开软件时自动登录这个账号')
        options.addWidget(self.remember_password_box)
        options.addWidget(self.auto_box)
        for box in (self.show_password,self.remember_password_box,self.auto_box):
            box.setSizePolicy(QSizePolicy.Policy.Minimum,QSizePolicy.Policy.Fixed)
        options.addStretch(1)
        def synchronize_consent():
            self.remember_box.setChecked(self.remember_password_box.isChecked() or self.auto_box.isChecked())
            self.auto_box.setEnabled(True)
        self.remember_password_box.toggled.connect(synchronize_consent)
        self.auto_box.toggled.connect(synchronize_consent)
        self.auto_box.setEnabled(True)
        self.cancel_button = QPushButton("取消并选择其他账号")
        self.cancel_button.setObjectName("loginCancelButton")
        self.cancel_button.clicked.connect(self.cancel_requested)
        outer.addWidget(self.cancel_button)
        self.notice_label = QLabel()
        self.notice_label.setTextFormat(Qt.TextFormat.PlainText)
        self.notice_label.setWordWrap(True)
        self.notice_label.setObjectName("loginSessionNotice")
        outer.addWidget(self.notice_label)
        account_actions = QHBoxLayout()
        self.register_button = QPushButton('注册账号')
        self.forgot_password_button = QPushButton('忘记密码')
        for button in (self.register_button, self.forgot_password_button):
            button.setAccessibleName(button.text())
            button.setObjectName('loginAccountAction')
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setEnabled(False)
            account_actions.addWidget(button)
            if button is self.register_button:
                account_actions.addStretch(1)
        self.register_button.clicked.connect(lambda: self._account_action('register'))
        self.forgot_password_button.clicked.connect(lambda: self._account_action('forgot_password'))
        card_layout.insertLayout(card_layout.indexOf(self.primary_button), account_actions)
        self.cancel_button.hide()
        # Everything clickable shows a hand cursor.
        for kind in (QPushButton, QCheckBox, QComboBox, QToolButton):
            for widget in self.findChildren(kind):
                widget.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))




    def set_account_capabilities(self, capabilities):
        # Only explicit server capabilities enable account actions. Other values do not
        # advertise registration or reset support for the current realm.
        self.account_capabilities = {key: isinstance(capabilities, dict) and capabilities.get(key) is True
                                     for key in ('register', 'forgot_password')}
        self.register_button.setEnabled(self.account_capabilities['register'])
        self.forgot_password_button.setEnabled(self.account_capabilities['forgot_password'])
        self._sync_account_actions()
        self.register_button.setToolTip('' if self.account_capabilities['register'] else '当前账号服务尚未开放注册')
        self.forgot_password_button.setToolTip('' if self.account_capabilities['forgot_password'] else '当前账号服务暂未开放密码找回')

    def _sync_account_actions(self) -> None:
        visible = self.auth_content is None and self.state is not LoginGateState.AUTHENTICATING
        self.register_button.setVisible(visible)
        self.forgot_password_button.setVisible(visible)

    def _account_action(self, action):
        if not self._update_login_available or self.account_capabilities.get(action) is not True:
            return
        (self.register_requested if action == 'register' else self.forgot_password_requested).emit()

    def _remember_toggled(self, enabled: bool) -> None:
        self.auto_box.setEnabled(enabled)
        if not enabled:
            self.auto_box.setChecked(False)

    def set_auth_content(self, content: QWidget | None) -> None:
        if self.auth_content is not None:
            self.content_layout.removeWidget(self.auth_content)
            self.auth_content.hide()
        self.auth_content = content
        # Qt does not support ancestor graphics effects on QQuickWidget, which
        # WebEngine uses for its surface. Keep the native card's shadow only
        # while it contains native controls (not a cached/offscreen web image).
        self.card_shadow.setEnabled(content is None)
        # Keep the brand title; registration needs the extra room for its form.
        for index, widget in enumerate(self.brand_widgets):
            widget.setVisible(content is None or index == 1)
        self.state_title.setVisible(content is None)
        self.state_detail.setVisible(content is None)
        self.primary_button.setVisible(content is None)
        self._set_native_form_visible(content is None)
        self._sync_account_actions()
        self.cancel_button.setVisible(
            content is None
            and self.state in {LoginGateState.AUTHENTICATING, LoginGateState.LOGIN_READY, LoginGateState.AUTH_FAILED}
        )
        self.notice_label.setVisible(content is None)
        if content is not None:
            self.loading.finish()
            self.content_layout.addWidget(content, 1)
            content.setEnabled(True)
            content.show()

    def _set_native_form_visible(self, visible: bool) -> None:
        # The lifecycle form replaces the credential form in the same card.
        # Disabled native controls must not consume the WebEngine form's space.
        for widget in (self.account_selector, self.remove_account_button,
                       self.password_field, self.show_password,
                       self.remember_password_box, self.auto_box):
            widget.setVisible(visible)


    def closeEvent(self, event) -> None:
        self.loading.finish()
        self.clear_password()
        self.closed.emit()
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._centered:
            screen = self.screen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.center() - self.rect().center())
            self._centered = True
            self.showNormal()
            # One normal activation request on initial user launch, not a
            # topmost flag or repeated foreground-stealing loop.
            self.raise_()
            self.activateWindow()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.current_theme == 'aurora':
            sky = QLinearGradient(0, 0, self.width(), self.height())
            sky.setColorAt(0, QColor('#0c0e1c'))
            sky.setColorAt(1, QColor('#222541'))
            painter.fillRect(self.rect(), sky)
        else:
            paint_night_sakura(painter, self.width(), self.height())
        painter.end()
        super().paintEvent(event)
        if not self._first_paint_scheduled:
            self._first_paint_scheduled = True
            # This is a Qt paint/event-loop milestone, not proof of compositor
            # presentation or that a real credential form is interactive.
            QTimer.singleShot(0, self.first_paint_completed.emit)

    def mousePressEvent(self, event) -> None:
        # Empty space in the top band moves the window with the system's own
        # move loop, so Windows snapping and multi-monitor moves keep working.
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 120 and self.windowHandle():
            self.windowHandle().startSystemMove()
            return
        super().mousePressEvent(event)

    def _activate(self) -> None:
        if not self._login_enabled or not self._update_login_available:
            return
        if self.state is LoginGateState.AUTH_FAILED:
            self.retry_requested.emit()
        else:
            self.login_requested.emit()

    def clear_password(self):
        self.password_field.clear()
        self.show_password.setChecked(False)

    def take_credentials(self):
        username, password = self.username_field.text().strip(), self.password_field.text()
        self.clear_password()
        return username, password

    def set_state(
        self,
        state: LoginGateState,
        *,
        detail: str = "",
    ) -> None:
        previous = self.state
        self.state = state
        if state is LoginGateState.LOGIN_READY:
            title = "登录你的账号"
            message = detail or "输入账号和密码登录 MentorFlow。"
            button = "登录 MentorFlow"
            enabled = True
        elif state is LoginGateState.AUTHENTICATING:
            title = ('正在启动 MentorFlow' if '启动' in detail else
                     '正在恢复登录' if '恢复' in detail else
                     '正在退出账号' if '退出' in detail else
                     '正在打开账号服务' if '打开账号' in detail else '正在登录')
            message = detail or "正在连接账号服务…"
            button = "正在登录…"
            enabled = False
        else:
            title = "暂时无法继续"
            message = detail or "登录未完成，请检查网络后重试。"
            button = "重试"
            enabled = True
        self.state_title.setText(title)
        self.state_detail.setText(message)
        if state is LoginGateState.AUTHENTICATING:
            self.loading.begin(detail or '正在连接账号服务…', retry=True, cancel=True,
                               restart=previous is not LoginGateState.AUTHENTICATING)
            self.state_detail.hide()
        else:
            self.loading.finish()
            self.state_detail.setVisible(self.auth_content is None)
        self.primary_button.setText(button)
        self._login_enabled = enabled
        self.primary_button.setEnabled(enabled)
        for widget in (self.username_field, self.password_field, self.show_password):
            widget.setEnabled(enabled)
        for widget in (self.remember_password_box,self.account_selector,self.add_account_button,self.remove_account_button):
            widget.setEnabled(enabled)
        self.remember_box.setEnabled(enabled)
        self.auto_box.setEnabled(enabled)
        self.cancel_button.setVisible(state in {LoginGateState.AUTHENTICATING, LoginGateState.LOGIN_READY, LoginGateState.AUTH_FAILED})
        self._sync_account_actions()
        if enabled:
            self.primary_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_loading_appearance(self, theme='sakura', reduced=False):
        self.current_theme = theme if theme in {'sakura', 'aurora'} else 'sakura'
        self.loading.configure(self.current_theme, reduced)
        for button in (self.minimize_button, self.close_button):
            button.configure(self.current_theme, self.loading.reduced)
        style = login_qss()
        if self.current_theme == 'aurora':
            style += '''
QFrame#loginCard {background:#181b34;border:1px solid #343956;}
QLabel#loginStateTitle {color:#eef0ff;}
QLabel#loginStateDetail {color:#b3b7de;}
QLineEdit,QComboBox,QCheckBox {color:#eef0ff;background:#222541;}
QLineEdit:disabled,QComboBox:disabled {color:#7a7fab;background:#222541;border-color:#343956;}
QCheckBox::indicator {background:#222541;border-color:#343956;}
QCheckBox::indicator:unchecked {background:#222541;border-color:#7a7fab;}
QPushButton#loginPrimaryButton {background:#9b8cff;color:#0c0e1c;}
QPushButton#loginPrimaryButton:disabled {background:#343956;color:#7a7fab;}
QPushButton#loginSmallButton,QPushButton#loginAccountAction {color:#b3b7de;}
QPushButton#loginSmallButton:disabled {background:#222541;color:#7a7fab;border-color:#343956;}
'''
        self.setStyleSheet(style)
        self.update()

    def set_login_available(self, available):
        """Update policy gates credentials, never the window's system controls."""
        self._update_login_available = bool(available)
        self.login_card.setEnabled(available)
        self.cancel_button.setEnabled(available)
