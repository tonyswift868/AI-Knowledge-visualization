"""Local resource schemes registered before any Qt WebEngine is created."""
from __future__ import annotations

from PySide6.QtWebEngineCore import QWebEngineUrlScheme


TEACHER_STAGE_SCHEME = b"mentorflow"


def register_teacher_stage_scheme() -> None:
    """Register early; handlers and model resources remain lazy and local.

    Chromium snapshots scheme capabilities when its first profile starts.
    Registering in the classroom after a login page exists is too late for
    fetch(), even though normal document navigation can appear to work.
    """
    if QWebEngineUrlScheme.schemeByName(TEACHER_STAGE_SCHEME).name() == TEACHER_STAGE_SCHEME:
        return
    scheme = QWebEngineUrlScheme(TEACHER_STAGE_SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.LocalScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
        | QWebEngineUrlScheme.Flag.CorsEnabled
        | QWebEngineUrlScheme.Flag.FetchApiAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)
