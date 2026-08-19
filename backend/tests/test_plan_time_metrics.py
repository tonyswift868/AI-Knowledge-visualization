from __future__ import annotations

from desktop.plans_page import PlansPage, plan_time_metrics, task_time_metrics


def test_plan_time_metrics_mixes_measured_and_legacy_completed_effort() -> None:
    metrics = plan_time_metrics(
        [
            {
                "day_date": "2030-01-02",
                "status": "completed",
                "estimated_task_minutes": 30,
            },
            {
                "day_date": "2030-01-02",
                "status": "completed",
                "estimated_task_minutes": 40,
                "actual_elapsed_minutes": 12.5,
            },
            {
                "day_date": "2030-01-02",
                "status": "in_progress",
                "estimated_task_minutes": 50,
            },
            {
                "day_date": "2030-01-03",
                "status": "planned",
                "estimated_task_minutes": 60,
            },
        ]
    )

    assert metrics["planned_minutes"] == 180
    assert metrics["measured_actual_minutes"] == 12.5
    assert metrics["completed_measured_minutes"] == 12.5
    assert metrics["completed_fallback_minutes"] == 30
    assert metrics["completed_effort_minutes"] == 42.5
    day = metrics["days"]["2030-01-02"]
    assert day["effort"] == 42.5
    assert day["ratio"] == 1.0
    assert "实际记录 12.5 分钟" in day["summary"]
    assert "完成任务折算时长 30 分钟" in day["summary"]


def test_measured_elapsed_wins_and_incomplete_tasks_do_not_get_fallback() -> None:
    completed = task_time_metrics(
        {
            "status": "done",
            "estimated_task_minutes": 90,
            "actual_elapsed_minutes": 7,
        }
    )
    incomplete = task_time_metrics(
        {
            "status": "planned",
            "estimated_task_minutes": 90,
        }
    )

    assert completed["completed_effort_minutes"] == 7
    assert completed["completed_fallback_minutes"] == 0
    assert incomplete["completed_effort_minutes"] == 0
    assert incomplete["completed_fallback_minutes"] == 0


def test_plan_time_metrics_stays_zero_without_completed_or_measured_records() -> None:
    metrics = plan_time_metrics(
        [
            {
                "day_date": "2030-01-04",
                "status": "planned",
                "estimated_task_minutes": 75,
            }
        ]
    )

    assert metrics["planned_minutes"] == 75
    assert metrics["measured_actual_minutes"] == 0
    assert metrics["completed_effort_minutes"] == 0
    assert metrics["days"]["2030-01-04"]["effort"] == 0
    assert "完成任务时长 0 分钟" in metrics["days"]["2030-01-04"]["summary"]


def test_stale_plan_status_never_presents_old_minutes_as_current() -> None:
    message = PlansPage._plan_status_text(
        {
            "stale": True,
            "total_estimated_task_minutes": 285,
            "old_plan_estimate": {"total_estimated_task_minutes": 285},
            "pending_plan_estimate": {
                "total_estimated_task_minutes": 6375,
                "initial_learning_minutes": 5051,
                "projected_review_minutes": 1324,
            },
            "tasks": [{}] * 40,
        }
    )

    assert "当前知识地图重新评估为 6375 分钟" in message
    assert "首次学习 5051 + 复习 1324" in message
    assert "旧版 285 分钟已过期" in message
    assert "当前计划：stale" not in message
