from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from desktop.plans_page import PlansPage


class _PolicyOnlyApi:
    def runtime_policy(self) -> dict:
        return {
            "scheduling": {
                "minimum_daily_minutes": 1,
                "maximum_daily_minutes": 720,
                "default_daily_minutes": 40,
                "maximum_review_rounds": 8,
            }
        }


def test_plan_task_table_is_reachable_in_laptop_height_window() -> None:
    app = QApplication.instance() or QApplication([])
    page = PlansPage(_PolicyOnlyApi())  # type: ignore[arg-type]
    page.resize(1100, 720)
    page.table.setRowCount(10)
    page._set_table_state(True)
    page.show()
    app.processEvents()

    assert page.table.height() >= 240
    assert page.detail_scroll.verticalScrollBar().maximum() > 0

    page.close()


def test_empty_plan_hides_table_and_technical_headers() -> None:
    app = QApplication.instance() or QApplication([])
    page = PlansPage(_PolicyOnlyApi())  # type: ignore[arg-type]
    page._set_table_state(False, "还没有计划任务。")
    page.show()
    app.processEvents()

    assert page.table.isHidden()
    assert page.table.horizontalHeader().isHidden()
    assert page.table.verticalHeader().isHidden()
    assert page.table_empty_state.isVisible()

    page.close()


def test_stale_plan_shows_recomputed_pending_daily_capacity() -> None:
    app = QApplication.instance() or QApplication([])
    page = PlansPage(_PolicyOnlyApi())  # type: ignore[arg-type]
    page.daily_minutes.setValue(25)

    page._render_plan(
        {
            "status": "stale",
            "stale": True,
            "execution_tasks": [],
            "old_plan_estimate": {"total_estimated_task_minutes": 285},
            "pending_plan_estimate": {
                "total_estimated_task_minutes": 6375,
                "recommended_minutes_per_day": 290,
            },
        }
    )

    assert page.daily_minutes.value() == 290
    assert page.adjust_button.isVisible() is False  # parent page is not shown yet
    assert page.adjust_button.isHidden() is False

    page.close()
