"""Resource layout shared by server/source and canonical Frozen app imports.

PyInstaller places app.* code at a virtual _MEIPASS/app path, while shipped
resources preserve the project layout (_MEIPASS/backend/config, frontend, etc.).
Resource locations must therefore not be inferred from a consumer module's
depth. These functions never change Python's import machinery.
"""
from pathlib import Path
import sys


def project_resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def backend_resource_root() -> Path:
    return project_resource_root() / "backend"
