"""User-selected optional component storage, separate from program updates."""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


def storage_root(data_root, component):
    if component not in {"tts", "asr"}:
        raise ValueError("unknown component")
    data_root = Path(data_root)
    try:
        value = json.loads((data_root / "config" / f"{component}-storage.json").read_text(encoding="utf-8"))
        path = Path(value["path"])
        if path.is_absolute():
            return path
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return data_root / "models"


def save_storage(data_root, component, path):
    if component not in {"tts", "asr"} or not Path(path).is_absolute():
        raise ValueError("invalid component storage")
    target = Path(data_root) / "config" / f"{component}-storage.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps({"path": str(Path(path).resolve())}), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def choose_installation(parent, title, *, size_bytes, installed_bytes, default):
    """Native folder choice and explicit, size-bearing consent. No I/O download."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    default = Path(default)
    default.mkdir(parents=True, exist_ok=True)
    selected = QFileDialog.getExistingDirectory(parent, title + "：选择组件存放目录", str(default))
    if not selected:
        return None
    message = (f"下载约 {size_bytes / 1024 ** 3:.2f} GiB，安装后约 {installed_bytes / 1024 ** 3:.2f} GiB。\n"
               f"存放位置：{selected}\n下载后会校验文件，完成即可使用。是否开始下载？")
    answer = QMessageBox.question(parent, title, message,
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                  QMessageBox.StandardButton.No)
    return Path(selected) if answer == QMessageBox.StandardButton.Yes else None
