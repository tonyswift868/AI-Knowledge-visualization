from __future__ import annotations

from app.generic_knowledge import calendar_capacities, schedule_by_capacity
from app.knowledge_policy import SCHEDULING_POLICY


def _item(total: int = 100) -> dict[str, object]:
    quarter = total // 4
    return {
        "id": "random-domain-concept",
        "name": "Random domain sentinel",
        "mastery_estimate": {
            "understand_minutes": quarter,
            "recall_minutes": quarter,
            "guided_practice_minutes": quarter,
            "independent_application_minutes": total - quarter * 3,
            "review_minutes": 10,
            "total_mastery_task_minutes": total + 10,
        },
        "source_evidence": [{"chunk_id": "random-domain-chunk"}],
        "acceptance_method": "Give an observable application.",
    }


def test_unavailable_dates_override_special_capacity_and_rest_days_stay_zero():
    calendar = calendar_capacities(
        {
            "start_date": "2026-07-27",
            "deadline": "2026-07-29",
            "study_weekdays": [0, 2],
            "unavailable_dates": ["2026-07-27"],
            "special_date_minutes": {
                "2026-07-27": 500,
                "2026-07-28": 500,
                "2026-07-29": 9999,
            },
            "daily_task_minutes": 60,
        }
    )

    assert [day["capacity_minutes"] for day in calendar] == [
        0,
        0,
        SCHEDULING_POLICY.maximum_special_date_minutes,
    ]
    assert [day["availability_reason"] for day in calendar] == [
        "unavailable_date",
        "fixed_rest_weekday",
        "special_availability",
    ]


def test_estimated_minutes_are_balanced_and_learning_units_remain_splittable():
    calendar, unscheduled = schedule_by_capacity(
        [_item(100)],
        {
            "start_date": "2026-07-27",
            "deadline": "2026-07-29",
            "study_weekdays": [0, 1, 2, 3, 4, 5, 6],
            "unavailable_dates": [],
            "special_date_minutes": {},
            "daily_task_minutes": 40,
        },
        review_rounds=0,
    )

    loads = [day["planned_task_minutes"] for day in calendar]
    tasks = [task for day in calendar for task in day["tasks"]]
    assert unscheduled == 0
    assert sum(loads) == 100
    assert max(loads) - min(loads) <= 1
    assert all(
        0 < task["planned_task_minutes"] <= SCHEDULING_POLICY.maximum_task_segment_minutes
        for task in tasks
    )
    assert any(task["phase_part_count"] > 1 for task in tasks)


def test_capacity_shortage_is_reported_without_rewriting_estimates():
    item = _item(100)
    calendar, unscheduled = schedule_by_capacity(
        [item],
        {
            "start_date": "2026-07-27",
            "deadline": "2026-07-27",
            "study_weekdays": [0],
            "unavailable_dates": [],
            "special_date_minutes": {},
            "daily_task_minutes": 10,
        },
        review_rounds=0,
    )

    assert calendar[0]["planned_task_minutes"] == 10
    assert unscheduled == 90
    assert item["mastery_estimate"]["total_mastery_task_minutes"] == 110
