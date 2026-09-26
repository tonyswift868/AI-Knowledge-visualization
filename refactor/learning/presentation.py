"""Shared display labels; never change the persisted server values."""


def human_status(value):
    # Extracted from MainWindow._human_status, with truthful unknown handling.
    raw = str(value or "")
    return {"active": "学习中", "learning": "学习中", "in_progress": "进行中",
            "planned": "待学习", "pending": "待学习", "completed": "已完成",
            "complete": "已完成", "paused": "已暂停", "archived": "已归档",
            "needs_adjustment": "需调整", "draft": "草稿", "cancelled": "已取消",
            "skipped": "已跳过"}.get(raw.casefold(), raw or "未记录")


DETAIL_LABELS = {"learning_goal": "学习目标", "goal": "目标", "deadline": "截止日期",
    "start_date": "开始日期", "minutes_per_day": "每日学习分钟", "preferred_weekdays": "学习日",
    "current_level": "当前水平", "success_criteria": "完成标准",
    "total_estimated_task_minutes": "预计总分钟", "unscheduled_task_minutes": "未安排分钟",
    "warning": "计划提示", "stale": "计划是否已过期", "activated_at": "启用时间"}


def completed_task(task):
    return str(task.get("status") or "").casefold() in {"completed", "complete"}
