from __future__ import annotations

from pathlib import Path

from desktop.classroom_page import (
    _current_point_messages,
    current_knowledge_point_summary,
    session_transcript,
    session_transcript_html,
)


def test_current_knowledge_point_summary_uses_turn_task_and_route_metadata() -> None:
    summary = current_knowledge_point_summary(
        {
            "state": {
                "current_knowledge_point_id": "point-internal-id",
                "active_task_id": "review-task-internal-id",
                "route_index": 1,
                "route_length": 3,
                "teacher_turn_v1": {
                    "concept": {"name": "二分查找"},
                    "state": {"phase": "guided_practice"},
                },
                "today_plan": {
                    "tasks": [
                        {
                            "id": "review-task-internal-id",
                            "knowledge_point_id": "point-internal-id",
                            "title": "二分查找",
                            "task_type": "review",
                            "review_cycle": 2,
                        }
                    ]
                },
            }
        }
    )

    assert summary == {
        "name": "二分查找",
        "phase": "引导练习",
        "route": "路线 2/3",
        "review": "复习第 2 轮",
    }
    assert "point-internal-id" not in " ".join(str(value) for value in summary.values())


def test_transcript_markup_keeps_role_bubbles_and_attachments() -> None:
    html = session_transcript_html(
        {
            "messages": [
                {
                    "role": "teacher",
                    "teacher_turn": {
                        "concept": {"name": "二分查找"},
                        "blocks": [{"type": "explanation", "content": "先看中间值。"}],
                    },
                },
                {
                    "role": "user",
                    "content": "我先比较中间值。",
                    "attachments": [{"original_filename": "answer.png"}],
                },
            ]
        }
    )

    assert 'class="teacher-message"' in html
    assert 'class="learner-message"' in html
    assert "answer.png" in html
    assert "border-radius" in html
    assert "<table" not in html


def test_historical_session_uses_only_current_structured_turn_when_projection_is_empty() -> None:
    session = {
        "current_point": {"name": "当前概念", "messages": []},
        "state": {
            "teacher_turn_v1": {
                "concept": {"name": "当前概念"},
                "blocks": [{"type": "explanation", "content": "只显示当前讲解。"}],
            }
        },
        "messages": [
            {"role": "teacher", "content": "旧知识点一"},
            {"role": "user", "content": "旧知识点二"},
        ],
    }

    rendered = session_transcript(session)
    assert "只显示当前讲解" in rendered
    assert "旧知识点一" not in rendered
    assert "旧知识点二" not in rendered


def test_historical_session_without_structured_turn_never_replays_unscoped_history() -> None:
    messages = _current_point_messages(
        {
            "current_point": {"name": "x 的平方根", "messages": []},
            "state": {},
            "messages": [
                {"role": "teacher", "content": "旧知识点讲解"},
                {"role": "user", "content": "当前问题"},
                {"role": "teacher", "content": "当前知识点讲解"},
            ],
        }
    )

    assert messages == []


def test_native_transcript_has_compact_scrollbar_and_responsive_layout() -> None:
    source = Path(__file__).parents[2].joinpath("desktop", "classroom_page.py").read_text(encoding="utf-8")

    assert "class ClassroomTranscriptView(QScrollArea)" in source
    assert "QScrollBar::handle:vertical" in source
    assert "QGridLayout()" in source
    assert "setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)" in source


def test_classroom_has_one_landing_entry_and_header_teacher_toggle() -> None:
    source = Path(__file__).parents[2].joinpath("desktop", "classroom_page.py").read_text(encoding="utf-8")

    assert 'QPushButton("进入/继续课堂")' not in source
    assert 'stage_button = QPushButton("展开教师面板")' in source
    assert 'heading_row.addWidget(stage_button)' in source
    assert 'QPushButton("我已掌握，完成此知识点")' in source
    assert 'QPushButton("提交回答")' in source
    assert 'QPushButton("我没理解，请针对纠错")' not in source
    assert '完成来源：' not in source


def test_first_transcript_render_stays_at_opening_and_later_turns_follow_latest() -> None:
    source = Path(__file__).parents[2].joinpath("desktop", "classroom_page.py").read_text(encoding="utf-8")

    assert "self._has_rendered_session = False" in source
    assert "scroll_to_latest = self._has_rendered_session" in source
    assert "setValue(self.verticalScrollBar().minimum())" in source


def test_first_launch_tutorial_is_once_only_without_settings_replay_control() -> None:
    root = Path(__file__).parents[2]
    source = root.joinpath("desktop", "main_window.py").read_text(encoding="utf-8")
    settings_source = root.joinpath("desktop", "system_page.py").read_text(encoding="utf-8")

    assert 'self.settings.value("tutorial/completed", "")' in source
    assert 'self.settings.setValue("tutorial/completed", "1")' in source
    assert 'QPushButton("重新播放新手引导")' not in settings_source
