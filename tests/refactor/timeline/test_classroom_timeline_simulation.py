"""Offline multi-day Classroom simulation for MF-03 batch eight.

The real Classroom owns the state transitions and writes to CourseStore. The
gateway below only supplies deterministic agent-shaped responses; it never
calls a model or network. A clock subclass controls both the classroom event
time and the local timezone used by Classroom._today.
"""
from __future__ import annotations
from tests.refactor.objective44.support import objective_bank, scripted_answer
from tests.refactor.integration38.support import Agents as FakeAgent, seed_bank, to_question, join_workers

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
        store.add_sources(PLAN, [dict(id="s", kind="url", url="https://docs.python.org/3/tutorial/", adopted=True,
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
        seed_bank(store,PLAN,count=1)
        objective_bank(store)
        classroom = Classroom(services, clock.study_day, now=clock.now)
        trace = Trace("单课堂：暂记→检测失败→重测→直接考→跨刷新→重新进入")
        evidence.append(trace)
        try:
            yield classroom, clock, agent, store, trace
        finally:
            for thread in list(threading.enumerate()):
                if thread.name in ('classroom-profile', 'classroom-question-bank', 'classroom-lesson-prep'):
                    thread.join(5)
            store.close()


def _ctx(streamed=None):
    return SimpleNamespace(cancel=threading.Event(), stream=(streamed.append if streamed is not None else lambda _d: None))




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
    _act(classroom, 'enter'); _act(classroom, 'next')
    agent.verdicts.extend([('answer', True, []), ('answer', False, ['p1'])])
    _act(classroom, 'reply', '先进先出'); view = _act(classroom, 'reply', '不会')
    assert view['today']['today_target'] == 30 and view['today']['ahead_minutes'] == 10
    _act(classroom, 'start_test', 'module_test')
    queue = classroom._load(classroom._course(PLAN))['test']['queue']
    assert len(queue)==2
    agent.verdicts.extend(('answer', q['point_ids'][0]!='p1', ['p1'] if q['point_ids'][0]=='p1' else []) for q in queue)
    for _ in queue: view=_act(classroom,'reply','检测作答')
    assert view['module']['state']=='failed'
    credits=store.load_ledger(PLAN).to_dict()['credits']
    assert sum(c['minutes'] for c in credits)==50  # Learned 40 + legacy module reserve 10; no relearning/revocation.
    assert all(c['state']=='confirmed' for c in credits)
    assert not any(r['op']=='revoke' for r in store.records('ledger_events',PLAN))
    clock.set('2026-10-02T03:59:00+08:00')
    view=_act(classroom,'start_test','retest')
    assert view['question']['total']==1 and view['today']['is_late_night']
    clock.set('2026-10-02T04:01:00+08:00')
    agent.verdicts.append(('answer',True,[]));view=_act(classroom,'reply','入队在尾出队在头')
    assert view['module']['state']=='passed'
    assert view['today']['progress_day']==DAY1.isoformat() and view['today']['study_day']==DAY2.isoformat()
    assert sum(c['minutes'] for c in store.load_ledger(PLAN).to_dict()['credits'])==60
    assert {c['credit']['day'] for c in store.records('credits',PLAN)}=={DAY1.isoformat()}
    assert not any(r['id'].startswith('relearn:') for r in store.records('credits',PLAN))
    _act(classroom,'next_module');view=_act(classroom,'start_test','skip_test')
    assert view['question']['total']==1
    if not view['question']['options']:agent.verdicts.append(('answer',True,[]))
    view=_act(classroom,'reply','乙答案')
    assert view['module']['state']=='skipped'
    assert sum(c['minutes'] for c in store.load_ledger(PLAN).to_dict()['credits'])==100
    assert {c['credit']['day'] for c in store.records('credits',PLAN)}=={DAY1.isoformat()}
    _act(classroom,'next_module');assert store.load_mastery(PLAN).check_ready()
    _act(classroom,'leave');view=_act(classroom,'enter')
    assert view['today']['progress_day']==DAY2.isoformat() and not view['today']['completed']
    _act(classroom,'leave')
    trace.conclusion='按题重测不撤回已学内容；跨刷新记账仍归开课日，重进课堂切换学习日。'


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

def _act(classroom,name,*args,streamed=None):
    if name=='reply':name,args=scripted_answer(classroom,PLAN,classroom.services.gateway,args[0])
    view=classroom.perform(PLAN,name,_ctx(streamed),args)
    if view.get('failure'):raise AssertionError(view['failure'])
    if name in ('next','reply','answer'):view=to_question(classroom,PLAN,view,streamed=streamed)
    return view
