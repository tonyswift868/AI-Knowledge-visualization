"""Single-source build/version metadata for the native desktop."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable


def _roots() -> Iterable[Path]:
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        yield Path(frozen)
    here = Path(__file__).resolve()
    yield here.parents[1]
    yield here.parents[1] / "package"


def _paths() -> Iterable[Path]:
    for root in _roots():
        yield root / "mentorflow-version.json"
        yield root / "package" / "mentorflow-version.json"


def load_version_manifest() -> dict[str, Any]:
    """Read the root/package manifest in source and frozen builds."""

    for path in dict.fromkeys(path.resolve() for path in _paths()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("version"), str):
            return payload
    return {
        "product": "MentorFlow",
        "version": "unknown",
        "channel": "unknown",
        "update": {},
    }


VERSION_MANIFEST = load_version_manifest()


def version_display(manifest: dict[str, Any] | None = None) -> str:
    payload = manifest or VERSION_MANIFEST
    product = str(payload.get("product") or "MentorFlow")
    version = str(payload.get("version") or "unknown")
    channel = str(payload.get("channel") or "unknown")
    return f"{product} {version} · 渠道：{channel}"


__all__ = ["VERSION_MANIFEST", "load_version_manifest", "version_display"]
