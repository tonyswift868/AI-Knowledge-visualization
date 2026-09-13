"""Resolve local-only teacher visual assets outside the application payload."""

from __future__ import annotations

import os
from pathlib import Path
from .runtime_resources import project_resource_root


PRIVATE_TEACHER_ROUTE = "/teacher2d/tsubaki-live2d"
PRIVATE_TEACHER_MANIFEST_URL = f"{PRIVATE_TEACHER_ROUTE}/manifest.json"


def private_teacher_asset_root() -> Path:
    """Return the dedicated private model directory for the current learner."""

    configured = str(os.getenv("MENTORFLOW_PRIVATE_TEACHER_ASSET_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    user_root = str(os.getenv("MENTORFLOW_USER_DATA_ROOT") or "").strip()
    if user_root:
        return (
            Path(user_root).expanduser().resolve()
            / "teacher-assets"
            / "tsubaki-live2d"
        )
    project_root = project_resource_root()
    return project_root / "data" / "teacher-assets" / "tsubaki-live2d"


def private_teacher_asset_available() -> bool:
    """Return whether the local model has a manifest and can be mounted."""

    root = private_teacher_asset_root()
    return root.is_dir() and (root / "manifest.json").is_file()
