from __future__ import annotations

from pathlib import Path

from desktop.classroom_page import (
    _current_point_messages,
    current_knowledge_point_summary,
    session_transcript,
    session_transcript_html,
    teacher_action_human_profile,
    teacher_action_type_label,
    teacher_turn_html,
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


def test_visible_teacher_turn_reads_naturally_without_internal_structure_labels() -> None:
    rendered = teacher_turn_html(
        {
            "concept": {"name": "二分查找"},
            "blocks": [
                {"type": "objective", "content": "先理解每次排除一半范围。"},
                {"type": "explanation", "content": "比较中间值，再决定向左还是向右。"},
                {"type": "worked_example", "content": "例如在 1 到 8 中寻找 6。"},
            ],
            "check": {"prompt": "如果中间值更小，下一步去哪里？"},
            "expected_learner_action": "请直接说出你的判断。",
        }
    )

    assert "二分查找" in rendered
    assert "比较中间值" in rendered
    assert "下一步去哪里" in rendered
    assert "学习目标" not in rendered
    assert "核心讲解" not in rendered
    assert "完整示例" not in rendered
    assert "最小检查" not in rendered
    assert "请你做：" not in rendered


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


def test_native_classroom_compacts_horizontal_status_point_and_composer() -> None:
    source = Path(__file__).parents[2].joinpath("desktop", "classroom_page.py").read_text(encoding="utf-8")

    assert source.count('setObjectName("currentKnowledgePointCard")') == 1
    assert "current_point_layout = QHBoxLayout(current_point_card)" in source
    assert "current_point_layout.addWidget(self.state, 1, Qt.AlignmentFlag.AlignVCenter)" in source
    assert "current_point_layout.addWidget(current_point_summary, 2, Qt.AlignmentFlag.AlignVCenter)" in source
    assert "current_point_summary_layout.addLayout(current_point_header)" in source
    assert "current_point_summary_layout.addLayout(current_point_body)" in source
    assert "QVBoxLayout(current_point_card)" not in source
    assert "current_point_card.setMinimumHeight(54)" in source
    assert "self.answer.setMaximumHeight(72)" in source
    assert "self.pending_attachments.setVisible(False)" in source
    assert "self.pending_attachments.setVisible(True)" in source
    assert "layout.addWidget(conversation, 1)" in source


def test_classroom_has_one_landing_entry_and_detachable_teacher_toggle() -> None:
    source = Path(__file__).parents[2].joinpath("desktop", "classroom_page.py").read_text(encoding="utf-8")

    assert 'QPushButton("进入/继续课堂")' not in source
    assert 'stage_button = QPushButton("打开教师舞台")' in source
    assert 'action_debug = QPushButton("教师动作调试")' in source
    assert 'dialog.setWindowTitle("教师动作人工验收")' in source
    assert 'button.dataset.actionImplementation' in source
    assert 'stop = QPushButton("■ 停止当前动作")' in source
    assert 'restore = QPushButton("↺ 恢复待机")' in source
    assert 'review_button = QPushButton(_TEACHER_ACTION_REVIEW_LABELS[review_value])' in source
    assert 'heading_row.addWidget(stage_button)' in source
    assert 'layout.addWidget(conversation, 1)' in source


def test_classroom_removes_redundant_teacher_strip_and_uses_compact_danger_exit() -> None:
    source = Path(__file__).parents[2].joinpath("desktop", "classroom_page.py").read_text(encoding="utf-8")

    assert "今日 AI 教师" not in source
    assert "教师会先讲解、举例和检查，再等待你的回答。" not in source
    assert 'exit_classroom.setObjectName("classroomExitButton")' in source
    assert "QPushButton#classroomExitButton" in source
    assert "background: #c73e4c" in source
    assert "max-width: 142px" in source
    assert "management_actions.addWidget(exit_classroom)" in source


def test_teacher_action_review_copy_is_chinese_truthful_and_keeps_unknowns_uninvented() -> None:
    source = Path(__file__).parents[2].joinpath("desktop", "classroom_page.py").read_text(encoding="utf-8")
    assert teacher_action_human_profile("motion:Cry:0")[1:] == (
        "哭泣",
        "模型原生哭泣动作，具体表现请播放确认。",
    )
    assert teacher_action_human_profile("expression:no-highlights")[1] == "失神 / 眼神失去高光"
    assert teacher_action_human_profile("motion:Wave:4")[1:] == (
        "Wave（待确认）",
        "模型已注册该项目，具体表现请播放确认。",
    )
    assert teacher_action_type_label({"implementation": "registered-motion"}) == "🎬 模型原生动作"
    assert teacher_action_type_label({"implementation": "registered-expression"}) == "😊 模型表情"
    assert teacher_action_type_label({"implementation": "internal-parameters"}) == "🦴 参数驱动动作"
    assert 'TeacherStageWindow' in source
    assert 'QComboBox(preview)' not in source
    assert '动作预览 / 测试' not in source
    assert '播放全部一次' not in source
    for action in (
        "idle", "greeting", "thinking", "talking", "explaining", "questioning",
        "praising", "encouraging", "confused", "shy", "playful_annoyed", "ending",
    ):
        assert f'"{action}"' in source
    assert 'QPushButton("我已掌握，完成此知识点")' in source
    assert 'QPushButton("提交回答")' in source
    assert 'QPushButton("我没理解，请针对纠错")' not in source
    assert '完成来源：' not in source


def test_fresh_package_spec_contains_only_generic_built_in_presets() -> None:
    root = Path(__file__).parents[2]
    spec = root.joinpath("MentorFlow.spec").read_text(encoding="utf-8")
    assert r"E:\MentorFlowData" not in spec
    assert "preset_settings.json" not in spec
    assert "desktop_settings.json" not in spec
    assert "mentorflow-balanced-default.json" in spec


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
