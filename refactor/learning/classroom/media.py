"""Lazy, optional preview of the confirmed MF-owned teaching portrait.

context.classroom_asset_root may point to the existing
frontend/public/teacher2d/original-ai-teacher-blonde-fang directory (or its
relocated E: equivalent). No discovery of private models, copying or download.
The full Live2D/voice runtimes are deliberately not imported.
"""
from pathlib import Path
import json


def load_portrait(asset_root, cancel):
    if asset_root is None or cancel.is_set():
        return None
    root = Path(asset_root).resolve()
    manifest = root / "manifest.json"
    if manifest.stat().st_size > 128000:
        return None
    profile = json.loads(manifest.read_text(encoding="utf-8"))
    if profile.get("id") != "mentorflow-blonde-fang-v9" or profile.get("license") != "Project-owned generated artwork":
        return None
    name = "teacher-blonde-fang-v9-teaching-heart-key.png"
    if profile.get("imageStates", {}).get("teaching") != "/teacher2d/original-ai-teacher-blonde-fang/" + name:
        return None
    path = root / name
    if path.resolve().parent != root or path.stat().st_size > 32 * 1024 * 1024 or cancel.is_set():
        return None
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QImageReader
    reader = QImageReader(str(path))
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > 40000000:
        return None
    reader.setScaledSize(size.scaled(QSize(240, 260), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    return image if not image.isNull() else None
