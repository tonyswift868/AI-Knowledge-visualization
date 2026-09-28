"""Offline multi-day Classroom simulation for MF-03 batch eight.

The real Classroom owns the state transitions and writes to CourseStore. The
gateway below only supplies deterministic agent-shaped responses; it never
calls a model or network. A clock subclass controls both the classroom event
time and the local timezone used by Classroom._today.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.course.ledger import PlanWindow, study_day
from refactor.learning.course.store import CourseStore


ROOT = Path(r"D:/MentorFlow/.local/test-tmp/timeline")
ACCOUNT = "mfa8_" + "d" * 64
PLAN = "b" * 32
EAST = timezone(timedelta(hours=8))
DAY1 = date(2026, 10, 1)
DAY2 = date(2026, 10, 2)
DAY3 = date(2026, 10, 3)
SETTINGS = {
    "teacher_style": "warm",
    "detail": "brief",
    "examples": "few",
    "analogy_domains": ["life"],
    "refresh_time": "04:00",
}


class ClockDateTime(datetime):
    """datetime whose no-argument astimezone uses the injected account zone."""

    zone = EAST

    def astimezone(self, tz=None):
        return type(self).fromtimestamp(self.timestamp(), tz or type(self).zone)


class Clock:
    def __init__(self):
        self.zone = EAST
        ClockDateTime.zone = self.zone
        self.instant = ClockDateTime(2026, 10, 1, 10, tzinfo=self.zone)

    def set(self, value):
        if type(value) is date:
            value = datetime.combine(value, time(10), self.zone)
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        aware = value.astimezone(timezone.utc)
        self.instant = ClockDateTime.fromtimestamp(aware.timestamp(), self.zone)

    def now(self):
        return self.instant

    def study_day(self):
        return study_day(self.instant, time(4), self.zone)


def _last_json(messages, label):
    content = messages[-1]["content"]
    return json.loads(content.split(label + "\n", 1)[1])


class FakeAgent:
    """Deterministic teacher, judge, question and profile responses."""

    def __init__(self):
        self.calls = []
        self.verdicts = []
        self.lock = threading.Lock()

    def complete(self, purpose, messages, *, on_text=None, **_kwargs):
        with self.lock:
            self.calls.append(purpose)
        if purpose == "teacher":
            task = _last_json(messages, "【本步任务】")
            if "用随口问" in task["director"]:
                text = ("我们先看这个知识点 [c1]。\n"
                        "【随口问】它的关键作用是什么？\n【/随口问】")
            else:
                text = "好的，按刚才的结果继续。"
            if on_text:
                on_text(text)
            return SimpleNamespace(text=text, search_hits=(), citations=())
        if purpose == "judge":
            with self.lock:
                kind, correct, wrong = self.verdicts.pop(0)
            return SimpleNamespace(text=json.dumps({
                "kind": kind, "correct": correct, "wrong_points": wrong,
                "reason": "回答覆盖了关键要点" if correct else "混淆了关键要点",
            }, ensure_ascii=False), search_hits=(), citations=())
        if purpose == "questions":
            content = messages[-1]["content"]
            points = json.loads(content.split("【知识点清单】\n", 1)[1].split(
                "\n【资料原文（数据）】", 1)[0])
            purpose_name = json.loads(content.split("【本次出题要求】\n", 1)[1])["purpose"]
            rows = []
            for index, point in enumerate(points):
                rows.append({
                    "id": f"q-{purpose_name}-{index}",
                    "type": "choice" if purpose_name == "module_test" and point["id"] == "p2" else "short",
                    "stem": f"请说明{point['title']}的关键作用（第 {index + 1} 题）",
                    "options": ["甲答案", "乙答案", "丙答案", "丁答案"]
                    if purpose_name == "module_test" and point["id"] == "p2" else [],
                    "answer": "乙答案" if purpose_name == "module_test" and point["id"] == "p2" else "标准答案",
                    "reference": "关键要点是正确区分并说明作用。",
                    "point_ids": [point["id"]], "chunk_ids": ["k1" if point["id"] != "p2" else "k2"],
                })
            return SimpleNamespace(text=json.dumps({"questions": rows}, ensure_ascii=False),
                                   search_hits=(), citations=())
        if purpose == "profile":
            task = _last_json(messages, "【本次档案更新】")
            return SimpleNamespace(text=json.dumps({
                "progress": task["progress"], "weak_points": [
                    {"point_id": point_id, "note": "具体困难尚待确认"}
                    for point_id in task["weak_point_ids"]
                ], "preferences": task["preferences"], "last_stop": "本次课堂停在当前模块。",
            }, ensure_ascii=False), search_hits=(), citations=())
        raise AssertionError(f"unexpected fake-agent purpose: {purpose}")


@dataclass
class Trace:
    title: str
    rows: list[dict] = field(default_factory=list)
    conclusion: str = ""
    notes: list[str] = field(default_factory=list)


def _write_report(traces):
    lines = [
        "# MF-03 第八批：课堂跨天时间加速模拟", "",
        "基线：`755b29b`。实际运行的是 `Classroom`、`CourseStore`、掌握度引擎和 `today_status`；"
        "教师、判定、出题、档案请求由本文件的确定性假 agent 响应，不调模型、不联网。", "",
        "课堂每次 snapshot 的 `today` 都是页面使用的 `CourseStore.today_status` 结果。"
        "`progress_day` 是课堂未退出时固定的开课学习日；`study_day` 是当前账号学习日。", "",
        "| 剧本 | 结论 |", "| --- | --- |",
    ]
    lines.extend(f"| {trace.title} | {trace.conclusion} |" for trace in traces)
    lines.append("")
    for trace in traces:
        lines.extend([
            f"## {trace.title}", "",
            "| 时刻 | 当前学习日 | 进度日 | 操作 | 目标 | 已确认 | 暂记 | 欠账 | 超前 | 逾期 | 协商 | 深夜/休息 | 时长完成/计划完成 |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
        ])
        for row in trace.rows:
            lines.append("| " + " | ".join([
                row["at"], row["study_day"], row["progress_day"], row["action"],
                str(row["today_target"]), str(row["today_confirmed"]), str(row["today_tentative"]),
                str(row["debt_minutes"]), f"{row['ahead_minutes']}/{row['ahead_days']}",
                str(row["overdue_days"]), "是" if row["needs_negotiation"] else "否",
                ("是" if row["is_late_night"] else "否") + "/" + ("是" if row["needs_rest"] else "否"),
                ("是" if row["time_completed"] else "否") + "/" + ("是" if row["completed"] else "否"),
            ]) + " |")
        lines.extend(["", f"结论：{trace.conclusion}", ""])
        lines.extend(f"- {note}" for note in trace.notes)
        lines.append("")
    lines.extend([
        "## 架构师问题记录", "",
        "| 现象 | 原因 | 建议修改 | 验证 |",
        "| --- | --- | --- | --- |",
        "| 本轮课堂流程可由假 agent 连续驱动，但实际页面/导演状态机没有被本工位修改 | `classroom.py` 属于架构师范围 | 由架构师接入真实课堂 UI 后复测相同状态序列 | 本报告的真实 Classroom 离线运行；未调模型、未开 WebView |",
        "| 掌握度事件的 `seq` 由课堂内部时间序列生成，学习日由状态固定 | 课堂用 `_day(state)` 写账本，today_status 用 `classroom_day` 读取 | 保持事件时间和学习日两个维度的明确契约 | 跨刷新前后 `progress_day` 与 credit.day 均保持 DAY1 |",
        "| 最终 CHK-01 尚未接入课堂，全部模块结束只显示‘检验即将接入’ | 当前 `Classroom.next_module` 在所有模块完成后进入 finished，但不启动 checking | 由架构师实现最终检验编排，完成后再核对 `completed` 与逾期 | 本轮只验证模块检测、重测、直接考，不伪称计划完成 |",
        "", "## 未验证范围", "",
        "真实模型回答质量、WebView/Live2D 渲染、真实课堂按钮和最终 CHK-01 未在本轮验证。",
    ])
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "classroom-report.md").write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(scope="module")
def evidence():
    traces = []
    yield traces
    _write_report(traces)


@pytest.fixture
def classroom_room(evidence):
    ROOT.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    agent = FakeAgent()
    with tempfile.TemporaryDirectory(dir=ROOT) as root:
        store = CourseStore(ACCOUNT, Path(root))
        store.create_plan(PLAN, goal="学会队列和栈", final_goal="能正确应用两种结构", level="beginner",
                          window=PlanWindow(DAY1, DAY3, 120))
        store.add_sources(PLAN, [dict(id="s", kind="url", url="https://example.invalid/offline",
                                      chunks=[dict(chunk_id="k1", text="队列按先进先出处理元素。"),
                                              dict(chunk_id="k2", text="栈按后进先出处理元素。")])])
        store.save_outline(PLAN, [dict(id="stage", title="基础")], [
            dict(id="m0", stage_id="stage", title="队列"),
            dict(id="m1", stage_id="stage", title="栈"),
        ])
        store.save_generated_module(PLAN, "m0", [
            dict(id="p0", title="队列定义", summary="先进先出", minutes=20, chunk_ids=["k1"]),
            dict(id="p1", title="队列操作", summary="入队出队", minutes=20, chunk_ids=["k1"]),
        ], check_minutes=10, today=DAY1)
        store.save_generated_module(PLAN, "m1", [
            dict(id="p2", title="栈定义", summary="后进先出", minutes=30, chunk_ids=["k2"]),
        ], check_minutes=10, today=DAY1)
        store.start_plan(PLAN, today=DAY1)
        settings = SimpleNamespace(load=lambda: dict(SETTINGS))
        services = SimpleNamespace(store=store, gateway=agent, account_id=ACCOUNT,
                                   data_root=Path(root), settings=settings)
        classroom = Classroom(services, clock.study_day, now=clock.now)
        trace = Trace("单课堂：暂记→检测失败→重测→直接考→跨刷新→重新进入")
        evidence.append(trace)
        try:
            yield classroom, clock, agent, store, trace
        finally:
            for thread in list(threading.enumerate()):
                if thread.name == "classroom-profile":
                    thread.join(5)
            store.close()


def _ctx(streamed=None):
    return SimpleNamespace(cancel=threading.Event(), stream=(streamed.append if streamed is not None else lambda _d: None))


def _act(classroom, name, *args, streamed=None):
    method = getattr(classroom, name)
    return classroom.run(PLAN, lambda course, state, ctx: method(course, state, ctx, *args), _ctx(streamed))


def _record(clock, trace, action, view):
    today = view["today"]
    assert today is not None, f"Classroom snapshot did not expose today status after {action}"
    row = dict(today, at=clock.now().isoformat(), action=action)
    trace.rows.append(row)
    return today


def _assert_today(clock, trace, view, action, *, target, confirmed, tentative, debt, ahead,
                  ahead_days, overdue=0, progress_day=None, study_day_value=None,
                  late=False, rest=False, time_completed=False, completed=False):
    today = _record(clock, trace, action, view)
    assert (today["today_target"], today["today_confirmed"], today["today_tentative"]) == (target, confirmed, tentative)
    assert (today["debt_minutes"], today["ahead_minutes"], today["ahead_days"]) == (debt, ahead, ahead_days)
    assert today["overdue_days"] == overdue
    assert today["study_day"] == (study_day_value or clock.study_day()).isoformat()
    assert today["progress_day"] == (progress_day or clock.study_day()).isoformat()
    assert today["is_late_night"] is late and today["needs_rest"] is rest
    assert today["time_completed"] is time_completed and today["completed"] is completed
    return today


def test_classroom_cross_day_timeline_uses_real_today_status(classroom_room):
    classroom, clock, agent, store, trace = classroom_room

    view = _act(classroom, "enter")
    _assert_today(clock, trace, view, "DAY1 进入课堂", target=30, confirmed=0, tentative=0,
                  debt=0, ahead=0, ahead_days=0)

    # Teaching p0 and p1 creates tentative minutes on the pinned entry day.
    view = _act(classroom, "next")
    _assert_today(clock, trace, view, "DAY1 讲完队列定义，记暂记", target=30, confirmed=0, tentative=20, debt=0, ahead=0, ahead_days=0)
    agent.verdicts.append(("answer", True, []))
    view = _act(classroom, "reply", "先进先出，按顺序处理")
    _assert_today(clock, trace, view, "DAY1 随口问答对，继续讲队列操作", target=30, confirmed=0, tentative=30,
                  debt=0, ahead=10, ahead_days=0)

    # Two wrong casual answers are recorded, then the host moves on without a loop.
    agent.verdicts.append(("answer", False, ["p1"]))
    view = _act(classroom, "reply", "不会")
    _assert_today(clock, trace, view, "DAY1 随口问第一次答错，仍暂记", target=30, confirmed=0, tentative=30,
                  debt=0, ahead=10, ahead_days=0)
    agent.verdicts.append(("answer", False, ["p1"]))
    view = _act(classroom, "reply", "还是不会")
    assert view["step"] == "test_offer"
    _assert_today(clock, trace, view, "DAY1 随口问第二次答错，进入检测入口", target=30, confirmed=0, tentative=30,
                  debt=0, ahead=10, ahead_days=0)

    # 20 + 10 test minutes become formal; p1 is revoked and becomes red.
    _act(classroom, "start_test", "module_test")
    agent.verdicts.extend([("answer", True, []), ("answer", False, ["p1"])])
    _act(classroom, "reply", "先进先出")
    view = _act(classroom, "reply", "不会")
    assert view["module"]["state"] == "failed" and view["red"] == ["队列操作"]
    _assert_today(clock, trace, view, "DAY1 模块检测失败，p0与检测时长转正式、p1撤回", target=30,
                  confirmed=30, tentative=0, debt=0, ahead=0, ahead_days=0)
    assert store.load_ledger(PLAN).status(DAY1).remaining_confirmed_minutes == 60

    # Cross the refresh while still in the same classroom session. The red point
    # is retested at DAY2, but its credit remains recorded on DAY1.
    clock.set("2026-10-02T03:59:00+08:00")
    view = _act(classroom, "start_test", "retest")
    _assert_today(clock, trace, view, "DAY1 开课日凌晨重测开始", target=30, confirmed=30, tentative=0,
                  debt=0, ahead=0, ahead_days=0, progress_day=DAY1, late=True, rest=True)
    clock.set("2026-10-02T04:01:00+08:00")
    agent.verdicts.append(("answer", True, []))
    view = _act(classroom, "reply", "入队在尾，出队在头")
    assert view["module"]["state"] == "passed" and view["red"] == []
    _assert_today(clock, trace, view, "DAY2 刷新后仍在课堂，重测补记仍归 DAY1", target=30, confirmed=30,
                  tentative=0, debt=0, ahead=20, ahead_days=0, progress_day=DAY1,
                  study_day_value=DAY2, rest=True)
    assert store.records("credits", PLAN)[-1]["credit"]["day"] == DAY1.isoformat()
    assert store.load_ledger(PLAN).status(DAY1).ahead_minutes == 20

    # Switch module without leaving: skip test for m1 credits all 40 minutes on
    # the same pinned day and therefore fills the remaining future target days.
    view = _act(classroom, "next_module")
    assert view["module"]["id"] == "m1" and view["step"] == "teach"
    _act(classroom, "start_test", "skip_test")
    before_judge = len(agent.calls)
    view = _act(classroom, "reply", "乙答案")
    assert "judge" not in agent.calls[before_judge:]
    assert view["module"]["state"] == "skipped"
    _assert_today(clock, trace, view, "DAY2 课堂未退出，直接考跳过 m1，模块时长记开课日并超前", target=30,
                  confirmed=30, tentative=0, debt=0, ahead=60, ahead_days=2, progress_day=DAY1,
                  study_day_value=DAY2, rest=True, time_completed=True)

    # End the session and enter again: no pinned day is carried over.
    _act(classroom, "next_module")
    assert store.load_mastery(PLAN).check_ready()
    view = _act(classroom, "leave")
    _assert_today(clock, trace, view, "DAY2 下课，清除开课日锁定", target=30, confirmed=30, tentative=0,
                  debt=0, ahead=30, ahead_days=1, progress_day=DAY2, study_day_value=DAY2,
                  time_completed=True)
    view = _act(classroom, "enter")
    _assert_today(clock, trace, view, "DAY2 重新进入课堂，使用新学习日", target=30, confirmed=30, tentative=0,
                  debt=0, ahead=30, ahead_days=1, progress_day=DAY2, study_day_value=DAY2,
                  time_completed=True)
    assert view["today"]["completed"] is False  # final CHK-01 is not part of Classroom yet
    _act(classroom, "leave")

    trace.conclusion = "与时长和模块掌握需求一致：暂记、检测确认/撤回、重测补记、直接考跳过、跨刷新锁定和重新进入均通过。"
    trace.notes.extend([
        "DAY1 目标 30；讲完两点暂记 40；检测通过点与检测分钟正式 30，失败点撤回。",
        "DAY2 04:01 已跨刷新但未退出，progress_day 仍为 DAY1；重测与直接考产生的记录 day 均为 DAY1，today_status 返回 needs_rest=true。",
        "离开后再进入 progress_day 变为 DAY2，超前由 60 分降为剩余未来目标 30 分；最终检验未接入，所以 completed 保持 false。",
    ])


def test_classroom_module_test_pass_confirms_tentative_credits(classroom_room):
    """The pass branch confirms every taught point and the test duration."""
    classroom, clock, agent, store, trace = classroom_room
    trace.title = "模块检测通过：暂记全部确认"

    _act(classroom, "enter")
    _act(classroom, "next")
    agent.verdicts.append(("answer", True, []))
    _act(classroom, "reply", "先进先出")
    agent.verdicts.append(("answer", True, []))
    view = _act(classroom, "reply", "入队出队")
    assert view["step"] == "test_offer"
    assert {row["credit"]["state"] for row in store.records("credits", PLAN)} == {"tentative"}

    _act(classroom, "start_test", "module_test")
    agent.verdicts.extend([("answer", True, []), ("answer", True, [])])
    _act(classroom, "reply", "先进先出")
    view = _act(classroom, "reply", "入队在尾，出队在头")
    assert view["step"] == "test_done" and view["module"]["state"] == "passed" and view["red"] == []
    credits = {credit.credit_id: credit.state for credit in store.load_ledger(PLAN)._credits.values()}
    assert credits["learn:p0"] == "confirmed"
    assert credits["learn:p1"] == "confirmed"
    assert next(value for key, value in credits.items() if key.startswith("test:")) == "confirmed"
    _assert_today(clock, trace, view, "模块检测通过，暂记点和检测时长均确认", target=30, confirmed=30,
                  tentative=0, debt=0, ahead=20, ahead_days=0)
    trace.conclusion = "模块检测通过路径确认暂记点和检测分钟，状态为 passed。"
    trace.notes.append("两点先记暂记；模块检测两题全对后，learn:p0、learn:p1 与 test 时长全部转为 confirmed。")
    _act(classroom, "leave")
