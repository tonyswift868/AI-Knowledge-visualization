import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from refactor.desktop.account_shell_main import _account_initial, _circular_avatar_pixmap


def _app():
    return QApplication.instance() or QApplication([])


def test_account_list_avatar_is_circular_and_keeps_center_of_wide_photo():
    _app()
    photo = QImage(80, 40, QImage.Format.Format_ARGB32)
    photo.fill(QColor("red"))
    photo.setPixelColor(40, 20, QColor("blue"))

    result = _circular_avatar_pixmap(QPixmap.fromImage(photo), 32).toImage()

    assert result.size().width() == 32
    assert result.pixelColor(0, 0).alpha() == 0
    assert result.pixelColor(16, 16).blue() > result.pixelColor(16, 16).red()
    assert result.pixelColor(16, 1).alpha() > 0


def test_empty_account_avatar_stays_empty():
    _app()
    result = _circular_avatar_pixmap(QPixmap(), 32)

    assert result.size().width() == 32
    assert result.toImage().pixelColor(16, 16).alpha() == 0


def test_login_list_fallback_uses_login_name_initial_while_nickname_remains_label():
    nickname = "昵称"
    username = "login-name"

    assert _account_initial(username) == "L"
    assert _account_initial(username) != nickname[:1]
