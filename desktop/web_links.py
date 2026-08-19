"""Route links from embedded WebEngine pages to the system browser."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def open_external_window_request(request: Any) -> bool:
    """Open a safe WebEngine new-window request in the default browser.

    ``target=_blank`` does nothing in a QWebEngineView unless the host accepts
    the new-window request.  Only ordinary web URLs are forwarded; embedded
    pages cannot use this bridge for local files or script URLs.
    """

    try:
        url = request.requestedUrl()
    except (AttributeError, RuntimeError, TypeError):
        return False
    if not isinstance(url, QUrl):
        url = QUrl(str(url or ""))
    if not url.isValid() or url.scheme().casefold() not in {"http", "https"} or not url.host():
        return False
    return bool(QDesktopServices.openUrl(url))


def install_external_link_handler(page: Any) -> Any:
    """Install the shared target-blank bridge and return ``page``."""

    page.newWindowRequested.connect(open_external_window_request)
    return page
