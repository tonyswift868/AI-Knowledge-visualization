"""Keep one Qt application and isolated nonsecret settings for Desktop tests."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ['MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY'] = '1'


@pytest.fixture(scope="session", autouse=True)
def desktop_qt_session(tmp_path_factory):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    previous_format = QSettings.defaultFormat()
    settings_root = tmp_path_factory.mktemp("desktop-qt-settings")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(settings_root))
    # WebEngine cannot safely be torn down/reinitialized between test modules.
    application = QApplication.instance() or QApplication([])
    yield application
    QSettings.setDefaultFormat(previous_format)


@pytest.fixture(autouse=True)
def restore_desktop_application_style(desktop_qt_session):
    """Startup tests share QApplication; their product stylesheet must not leak."""
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance()
    stylesheet = application.styleSheet()
    try:
        yield
    finally:
        application.setStyleSheet(stylesheet)
