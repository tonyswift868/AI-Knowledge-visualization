"""The Desktop's single canonical backend package boundary.

Source loads the checked-in package by its file spec, without changing global
search paths. Frozen uses PyInstaller's normal importer and resource layout.
Neither mode aliases backend.app or repairs an already conflicting namespace.
Importing this module alone does not open a database; load_app must follow the
account storage binding.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys
import threading

_LOCK = threading.RLock()


def _package_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "app"
    return Path(__file__).resolve().parents[1] / "backend" / "app"


def _check_origin(module, name: str) -> None:
    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None)
    relative = name.split(".")[1:]
    target = _package_root().joinpath(*relative)
    if origin is None and spec is not None and spec.submodule_search_locations is not None:
        locations = tuple(Path(p).resolve() for p in spec.submodule_search_locations)
        if locations == (target.resolve(),):
            return
        raise ImportError(f"Canonical backend namespace conflict: {name}")
    expected = target / "__init__.py" if getattr(spec, "submodule_search_locations", None) is not None else target.with_suffix(".py")
    if not origin or Path(origin).resolve() != expected.resolve():
        raise ImportError(f"Canonical backend namespace conflict: {name}")
    # Frozen file paths are virtual. Check the actual PYZ key as well: a
    # backend.app alias can otherwise appear to have an innocuous file origin.
    entry = getattr(getattr(spec, "loader", None), "_pyz_entry_name", name)
    if entry != name:
        raise ImportError(f"Canonical backend archive conflict: {name}")


def prepare_backend_imports() -> None:
    with _LOCK:
        package = sys.modules.get("app")
        if package is None:
            if getattr(sys, "frozen", False):
                package = importlib.import_module("app")
            else:
                spec = importlib.util.spec_from_file_location("app", _package_root() / "__init__.py")
                if spec is None or spec.loader is None:
                    raise ImportError("Canonical backend package is unavailable")
                package = importlib.util.module_from_spec(spec)
                # Normal package registration, not a second copy or alias.
                sys.modules["app"] = package
                spec.loader.exec_module(package)
        _check_origin(package, "app")
        for name, module in tuple(sys.modules.items()):
            if name.startswith("app.") and module is not None:
                _check_origin(module, name)


def load_app():
    prepare_backend_imports()
    from app.main import app
    _check_origin(sys.modules["app.main"], "app.main")
    return app
