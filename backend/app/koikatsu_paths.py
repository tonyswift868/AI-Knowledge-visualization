"""Single source of truth for MentorFlow's local Koikatsu 3D resources."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
KOIKATSU_ROOT = Path(
    os.getenv(
        "MENTORFLOW_KOIKATSU_ROOT",
        str(PROJECT_ROOT / "legacy" / "koikatsu-3d"),
    )
).expanduser()
KOIKATSU_RESOURCE_ROOT = KOIKATSU_ROOT / "resources"
KKS_RUNTIME_ROOT = Path(
    os.getenv(
        "MENTORFLOW_KKS_ASSET_ROOT",
        str(KOIKATSU_RESOURCE_ROOT / "runtime"),
    )
).expanduser()
KKS_WEB_RESOURCE_ROOT = KOIKATSU_RESOURCE_ROOT / "web"
KKS_TEACHER_REGISTRY_ROOT = KOIKATSU_RESOURCE_ROOT / "teacher-registry"
TEACHER_AVATAR_REGISTRY_ROOT = KOIKATSU_RESOURCE_ROOT / "avatar-registry"
KKS_CARD_PRESET_ROOT = KOIKATSU_RESOURCE_ROOT / "card-presets" / "1.0.0"
