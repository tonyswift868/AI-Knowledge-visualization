"""Single-source build/version metadata for the native desktop."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable


def _roots() -> Iterable[Path]:
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        yield Path(sys.executable).absolute().parent
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


def account_capabilities(manifest: dict[str, Any] | None = None) -> dict[str, bool]:
    """Read public switches from verified package metadata; absent means disabled.

    This is presentation capability, not server authorization. The packaged
    startup binds it only after the existing installation security gate passes.
    """
    payload = VERSION_MANIFEST if manifest is None else manifest
    values = payload.get('account_capabilities') if isinstance(payload, dict) else None
    return {key: isinstance(values, dict) and values.get(key) is True
            for key in ('register', 'forgot_password')}


def version_display(manifest: dict[str, Any] | None = None) -> str:
    payload = manifest or VERSION_MANIFEST
    product = str(payload.get("product") or "MentorFlow")
    version = str(payload.get("version") or "unknown")
    channel = str(payload.get("channel") or "unknown")
    return f"{product} {version} · 渠道：{channel}"


def installed_release_details(manifest: dict[str, Any] | None = None) -> str | None:
    """Offline, plain-text installed release notes; never copy configuration.

    Present only when this release ships bounded notes. BASE has no notes;
    TARGET 1.4.1 introduces the installed-notes and copy-version affordance.
    """
    payload = VERSION_MANIFEST if manifest is None else manifest
    notes = payload.get("installed_release_notes")
    if not isinstance(notes, str) or not notes.strip() or len(notes) > 2000:
        return None
    return version_display(payload) + "\n\n" + notes.strip()


__all__ = ["VERSION_MANIFEST", "load_version_manifest", "version_display", "installed_release_details", "account_capabilities"]
