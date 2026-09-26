"""Local library entry without the legacy runner, vault, or service stack.

The local_asset_page selection/recovery flow is adapted onto MaterialsPage.
Both entries address the same stable records through MaterialStore; a separate
store session lets disposing either page leave the other session usable.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from PySide6.QtWidgets import QWidget

from ..materials_page import MaterialsPage
from ..store import MaterialStore


class LibraryPage(MaterialsPage):
    def __init__(self, context: Mapping, parent: QWidget | None = None):
        store = MaterialStore(Path(context["data_root"]), context["account_id"])
        super().__init__(store, parent, title="本地资料")


def create_page(context: Mapping, parent: QWidget | None = None) -> QWidget:
    """The shell calls dispose() before dropping or changing the account page."""
    return LibraryPage(context, parent)
