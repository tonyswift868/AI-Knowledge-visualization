"""Tiny disposable offscreen Qt host for click253's browser regression."""
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

app = QApplication([])
view = QWebEngineView()
view.resize(800, 600)
view.show()
view.setUrl(QUrl('about:blank'))
timer = QTimer()
timer.timeout.connect(app.quit)
timer.start(60000)
app.exec()
