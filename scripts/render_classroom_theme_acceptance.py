"""Render every native classroom skin against one deterministic lesson.

This is an acceptance helper, not a product fixture.  It never connects to the
backend, never reads the learner database, and redirects QSettings to the
chosen output directory so a review cannot change the installed application.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


# Running ``python scripts/render_classroom_theme_acceptance.py`` places only
# the scripts directory on sys.path; add the repository root so the offline
# renderer works from a clean checkout without a caller-managed PYTHONPATH.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _session() -> dict:
    teacher_turn = {
        "action": "check_understanding",
        "presentation_state": "questioning",
        "concept": {"name": "哈希表中的冲突处理"},
        "blocks": [
            {
                "type": "explanation",
                "title": "先建立直觉",
                "content": "哈希函数负责把键映射到位置；不同的键落到同一位置时，就产生冲突。",
            },
            {
                "type": "example",
                "title": "一个小例子",
                "content": "把学号放进固定数量的抽屉：抽屉相同并不代表学号相同，所以还要比较原始键。",
            },
        ],
        "check": {"prompt": "如果两个键的哈希值相同，为什么仍然不能认为它们相等？"},
        "expected_learner_action": "用自己的话说明冲突与键相等的区别。",
        "state": {"phase": "understand", "status": "awaiting_answer"},
    }
    messages = [
        {
            "role": "teacher",
            "content": "",
            "teacher_turn": teacher_turn,
            "attachments": [],
        },
        {
            "role": "user",
            "content": "哈希值只是位置线索，真正判断相等还要比较原始键，对吗？",
            "attachments": [],
        },
        {
            "role": "teacher",
            "action": "free_chat_response",
            "content": "对，就是这个意思。顺便说，刚才你提到的游戏例子也很适合解释冲突。",
            "attachments": [],
        },
    ]
    return {
        "session_id": "acceptance-session",
        "workspace_id": "acceptance-workspace",
        "actual_elapsed_minutes": 8.5,
        "state": {
            "current_teaching_step": "understand",
            "estimated_task_minutes": 20,
            "teacher_turn_v1": teacher_turn,
            "teacher_presentation_state": "questioning",
        },
        "current_point": {
            "name": "哈希表中的冲突处理",
            "ordinal": 4,
            "total": 12,
            "messages": messages,
            "evidence": [],
        },
        "messages": messages,
    }


class _OfflineApi:
    session_token = ""

    def active_workspaces(self, *, include_archived: bool = False) -> dict:
        del include_archived
        return {"items": []}

    def materials(self, workspace_id: str) -> list[dict]:
        del workspace_id
        return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=900)
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    import desktop.classroom_page as classroom_module
    from desktop.classroom_theme import CLASSROOM_THEMES, classroom_theme_render_signature

    settings = QSettings(
        str(output_root / "acceptance-settings.ini"),
        QSettings.Format.IniFormat,
    )
    classroom_module.QSettings = lambda *_args, **_kwargs: settings
    application = QApplication.instance() or QApplication([])
    # The Qt offscreen platform does not enumerate Windows fonts on every
    # host.  Load the system CJK font explicitly so screenshots prove the
    # actual Chinese classroom copy instead of rendering empty tofu boxes.
    font_path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "msyh.ttc"
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.is_file() else -1
    font_families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    font_family = font_families[0] if font_families else "Microsoft YaHei UI"
    application.setFont(QFont(font_family, 10))
    page = classroom_module.ClassroomPage(_OfflineApi(), lambda: "", "")
    page.resize(max(900, args.width), max(700, args.height))
    page._view_stack.setCurrentIndex(1)
    page._render_session(_session(), material_index={})
    page.answer.setPlainText("这里保留用户正在编辑的草稿，不会因换肤而消失。")
    page.show()
    application.processEvents()

    files: list[dict[str, object]] = []
    try:
        for key, theme in CLASSROOM_THEMES.items():
            page.apply_theme(key)
            application.processEvents()
            QTest.qWait(450)
            destination = output_root / f"classroom-{key}.png"
            if not page.grab().save(str(destination), "PNG"):
                raise RuntimeError(f"cannot save {destination}")
            files.append(
                {
                    "theme": key,
                    "label": theme["label"],
                    "path": str(destination),
                    "bytes": destination.stat().st_size,
                    "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                    "render_signature": list(classroom_theme_render_signature(key)),
                    "draft_preserved": page.answer.toPlainText()
                    == "这里保留用户正在编辑的草稿，不会因换肤而消失。",
                    "session_preserved": page.current_session.get("session_id")
                    == "acceptance-session",
                }
            )
        page.apply_theme("sakura")
        page.toggle_immersive_mode()
        application.processEvents()
        QTest.qWait(250)
        immersive_destination = output_root / "classroom-sakura-immersive.png"
        if not page.grab().save(str(immersive_destination), "PNG"):
            raise RuntimeError(f"cannot save {immersive_destination}")
        files.append(
            {
                "theme": "sakura",
                "variant": "immersive",
                "label": "樱花教室 · 沉浸模式",
                "path": str(immersive_destination),
                "bytes": immersive_destination.stat().st_size,
                "sha256": hashlib.sha256(immersive_destination.read_bytes()).hexdigest(),
                "render_signature": list(classroom_theme_render_signature("sakura")),
                "top_shell_hidden": all(
                    not widget.isVisible() for widget in page._immersive_top_widgets
                ),
                "composer_visible": page.answer.isVisible(),
                "immersive_toggle_visible": page.immersive_toggle.isVisible(),
                "draft_preserved": page.answer.toPlainText()
                == "这里保留用户正在编辑的草稿，不会因换肤而消失。",
                "session_preserved": page.current_session.get("session_id")
                == "acceptance-session",
            }
        )
    finally:
        page.close()
        application.processEvents()

    summary = {
        "schema": "mentorflow.classroom-theme-acceptance.v1",
        "offline": True,
        "font_path": str(font_path),
        "font_family": font_family,
        "font_loaded": font_id >= 0,
        "settings_path": str(output_root / "acceptance-settings.ini"),
        "render_signature_unique": len({tuple(item["render_signature"]) for item in files if "render_signature" in item}),
        "render_signature_theme_count": len(CLASSROOM_THEMES),
        "files": files,
    }
    (output_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
