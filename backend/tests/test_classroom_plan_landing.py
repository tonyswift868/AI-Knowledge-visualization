from __future__ import annotations


def test_classroom_landing_normalizes_active_plan_cards() -> None:
    from desktop.classroom_page import plan_landing_items

    cards = plan_landing_items(
        [
            {
                "workspace_id": "active-plan",
                "title": "真实计划",
                "archived": False,
                "plan_id": "plan-1",
                "knowledge_point_count": 12,
                "completed_knowledge_point_count": 3,
                "today_plan": {
                    "task_count": 2,
                    "planned_task_minutes": 35,
                    "completed_task_minutes": 10,
                    "tasks": [{"title": "二分查找"}, {"title": "边界条件"}],
                },
            },
            {"workspace_id": "archived", "plan_id": "plan-old", "archived": True},
            {"workspace_id": "setup-only", "plan_id": None, "archived": False},
        ]
    )

    assert len(cards) == 1
    card = cards[0]
    assert card["workspace_id"] == "active-plan"
    assert card["title"] == "真实计划"
    assert card["today_task_count"] == 2
    assert card["today_planned_minutes"] == 35
    assert card["today_completed_minutes"] == 10
    assert card["progress_completed"] == 3
    assert card["progress_total"] == 12
    assert [task["title"] for task in card["today_tasks"]] == ["二分查找", "边界条件"]


def test_classroom_landing_card_enters_only_selected_workspace(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QPushButton

    from desktop.classroom_page import ClassroomPage

    class FakeApi:
        started: list[str] = []

        def active_workspaces(self, *, include_archived: bool = False) -> dict:
            assert include_archived is False
            return {
                "items": [
                    {
                        "workspace_id": "plan-a",
                        "title": "计划 A",
                        "plan_id": "mastery-a",
                        "knowledge_point_count": 4,
                        "completed_knowledge_point_count": 1,
                        "today_plan": {"task_count": 1, "planned_task_minutes": 20, "tasks": [{"title": "概念 A"}]},
                    },
                    {
                        "workspace_id": "plan-b",
                        "title": "计划 B",
                        "plan_id": "mastery-b",
                        "knowledge_point_count": 5,
                        "today_plan": {"task_count": 2, "planned_task_minutes": 30, "tasks": [{"title": "概念 B"}]},
                    },
                ]
            }

        def start_teaching_session(self, workspace_id: str, _provider: str = "") -> dict:
            self.started.append(workspace_id)
            return {"session_id": f"session-{workspace_id}", "workspace_id": workspace_id, "state": {}, "messages": []}

        def materials(self, _workspace_id: str) -> list[dict]:
            return []

    application = QApplication.instance() or QApplication([])
    api = FakeApi()
    page = ClassroomPage(api, lambda: "")
    try:
        cards = page.findChildren(QPushButton, "primaryButton")
        starts = [button for button in cards if button.text() == "开始今日课程"]
        assert len(starts) == 2
        starts[1].click()
        application.processEvents()
        assert api.started == ["plan-b"]
        assert page.session_id == "session-plan-b"
        assert page.workspace.currentData() == "plan-b"
        assert page._view_stack is not None and page._view_stack.currentIndex() == 1
    finally:
        page.close()
