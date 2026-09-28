"""Batch ten: explicit time/behaviour oracles, real stores, real Classroom.

No model or network; the fake agents and local clock are reused from batches
seven/eight. The Markdown report is an audit, including out-of-scope findings,
not a claim that passing test assertions prove every product rule is satisfied.
"""
from collections import Counter
from datetime import date, time, timedelta, timezone
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from refactor.learning.app import handlers, classroom as classroom_module
from refactor.learning.app.classroom import Classroom
from refactor.learning.app.reminder import ReminderService
from refactor.learning.app.settings import AppSettings
from refactor.learning.course.ledger import PlanWindow, study_day
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.store import CourseStore, StoreError
from refactor.learning.services.daily_clock import DailyClock
from test_course_timeline_simulation import InjectedClock, HandlerRegistry
from test_classroom_timeline_simulation import FakeAgent


ROOT = Path(r"D:/MentorFlow/.local/test-tmp/timeline")
START = date(2026, 10, 5)  # Monday
END = START + timedelta(days=41)
PLAN = "a" * 32
OTHER = "b" * 32
EAST = timezone(timedelta(hours=8))
RULES = {
    "PLAN-05": "计划定死、不动态重排；补欠：用户后来学的时间算作补之前那天的进度，不重排计划。",
    "PLAN-08": "修改截止日期或时间后，只对还没学的部分重新分配每日时长，已完成和欠的记录全部保留，不能乱。",
    "TEACH-05": "只要没退出课堂，学的都记到前一天；退出课堂后再进来算新的一天。",
    "TEACH-07": "跳过的模块，其估算时长在做‘直接考’的那一天全部记为完成，用户可能因此超额、领先几天。",
    "TEACH-02": "没过的点记录下来，从多个角度出题重测，重测答对一次即可。",
    "SYS-04": "到点还没开始当天任务就提醒。只在软件开着时提醒。",
    "CHK-01": "截止日到了还没学完或没检验完，计划标为‘已逾期’，显示逾期几天，继续学到完成为止。",
}
LABELS = dict(today_target="今日目标", today_confirmed="正式", today_tentative="暂记",
              debt_minutes="欠账", ahead_minutes="超前分钟", ahead_days="超前天数",
              overdue_days="逾期天数", needs_negotiation="需协商", remaining_minutes="剩余分钟",
              history_debt_minutes="冻结历史欠账", progress_day="课堂进度日", study_day="当前学习日",
              time_completed="时长已满", completed="计划完成", needs_rest="休息提示")


def readable(value):
    if isinstance(value, dict):
        return "；".join(f"{LABELS.get(k, k)}={readable(v)}" for k, v in value.items())
    if type(value) is bool:
        return "是" if value else "否"
    if isinstance(value, (list, tuple)):
        return "、".join(readable(v) for v in value)
    if value == "passed":
        return "模块通过"
    if value == "failed":
        return "模块未通过"
    return str(value).replace("|", "/").replace("\n", " ")


@pytest.fixture(scope="module")
def audit():
    rows = []

    def check(scene, rule, expected, actual, *, finding="合理", before="", observed=None):
        rows.append(dict(scene=scene, rule=rule, expected=readable(expected), actual=readable(actual),
                         finding=finding, before=before))
        # Out-of-scope findings assert the reproduced behaviour, while the
        # report still compares it against the desired product behaviour.
        assert actual == (expected if observed is None else observed), f"{scene}: {actual!r} != {expected!r}"

    yield check
    counts = Counter(row["finding"] for row in rows)
    lines = ["# MF-03 第十批：时间行为审查", "", "基线：a6f286e。全部为本工位离线实测；未调模型、未连接服务器、未启动真实界面。",
             "复用第七批注入时钟/首页桥注册器和第八批假 agent，运行真实 CourseStore、Ledger、MasteryBook、Classroom、DailyClock、ReminderService。",
             "七周/六周逐日数值由独立累计公式核对，不用实际输出反填期望。表中分钟均为任务完成量，不是挂机时间。", "",
             "结论计数（按表中核对项计，含逐日行）：" + readable(dict(counts)), "",
             "| 场景 | 需求 | 按需求应当是什么 | 实际是什么 | 是否合理 | 修复前复现 |",
             "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {r['scene']} | {r['rule']} | {r['expected']} | {r['actual']} | {r['finding']} | {r['before']} |" for r in rows]
    lines += ["", "## 判断依据（需求原文摘录）", ""]
    lines += [f"- [{key}] {value}" for key, value in RULES.items()]
    lines += ["- [PLAN-05] ‘讲完一个点且随口问答上，先记“暂记”进度（浅色）’；‘学到当天时长用完就停，哪怕某个模块只开了个头，第二天接着学。’",
              "- [PLAN-02] ‘开始执行后只能改日期（PLAN-08）或加资料（PLAN-10）。’", "",
              "## 需要产品决定", "",
              "1. 已逾期/休息日但有欠账、或只剩最终检验（today_target=0）要不要提醒？A 保持只提醒当日有安排的任务；B 有未完成的工作就提醒。建议 B 仅用于逾期，休息日保持 A；当前保留 A。",
              "2. 进入课堂但尚未产生任何答题/时长记录，是否算已开始？A 以进入即开始并记录开课事件；B 以实际学习活动为准。建议 A；当前存储没有开课事件，本轮未引入新同步实体。",
              "3. 改提醒时间为已经过去的时刻：A 当场提醒；B 等下次正常跨过时刻。建议 B，避免仅保存设置就突然提醒；当前为 B。", "",
              "## 范围与交接", "",
              "模块已经开始后删改被 PLAN_STARTED 拒绝，符合 PLAN-02；测试了开始前删/改/重排以及开始后拒绝且事务不变，没有绕过治理。",
              "最终检验正在由架构师编排，本轮只通过真实掌握事件验证检验未完的逾期，不修改导演提示和最终检验流程。",
              "架构师需处理：讲解结束即暂记（未等随口问答上）；答对后自动继续下一点，超出当天目标仍继续；墙钟回拨导致判定事件seq倒序、重测答对仍红。表中作为现存不合理项列出，没有改许可范围外的函数。",
              "系统托盘实际弹窗、真实操作系统睡眠/时区设置、WebView 显示和付费模型未测；时钟变化均由注入值模拟。",
              "详细复现日志：clock-audit-baseline.txt（7个产品失败断言，另1个是测试错误地用按ID排序的最后一条当最新记录，已改用operation_seq）；重复进入单独复现见clock-audit-enter-before.txt。最终 pytest 结果见 clock-audit-validation.txt。", "",
              "## 问题记录（现象、原因、排查、解决、验证）", "",
              "| 现象 | 原因 | 排查 | 解决/交接 | 验证 |",
              "| --- | --- | --- | --- | --- |",
              "| 回拨/改刷新/改时区后课堂今日数据消失 | today.py 将开课日晚于当前日一律判非法 | 三种真实Classroom调用均得到today=None，日志定位到INVALID_DATE | 保留日期类型校验，允许真实开课日晚于回拨后的日历日 | 三种变更后都保留开课日、时长记录和今日数据 |",
              "| 已学过却被提醒尚未开始 | 提醒把FIFO当天填充量当作当天活动，补欠/撤回/答错会归零 | 两份真实数据库计划，甲有活动、乙没有；修复前通知包含甲乙 | CourseStore.has_study_activity读取原始提交和判定，提醒结合该证据 | 补欠、撤回、答错三场景均只提醒乙；跨刷新/重开仍正确 |",
              "| 上课中保存无关设置会丢提醒 | configure无条件清pending | 19:59开课、20点等待、保存相同时间设置、下课无通知 | 只有实际时间配置变化才清pending | 下课收到一次通知 |",
              "| 重复enter在刷新后改了开课日 | enter无条件覆写day | 03:59进入、04:01再次enter得到新日 | 只在新会话设置day，leave清会话；新对象忽略磁盘残留in_session | 重复进入仍旧日，下课后/新对象用新日 |",
              "| 随口问未答已计暂记 | _teach_point讲解后立即调用_credit_learning | 未提交reply，数据库已有20分钟 | 架构师把记账调用移到判定确认分支；仅改_credit_learning无法完成调用时序 | 假agent驱动真实Classroom记录20而需求应为0 |",
              "| 今日量满后自动讲下一点 | _casual_reply自动推进未判断今日目标 | 目标15、第一点20，答对后又计第二点20 | 架构师在推进新点前依据today_status暂停并允许用户主动继续 | 两条learn记录40分钟，需求应停在第一点 |",
              "| 系统时间回拨后重测答对仍红 | _seq使用time.time_ns，掌握引擎按seq重算，重测被排到模块检测前 | 同时注入显示时钟和_seq依赖的墙钟；核对持久化判定seq与AttemptClosed | 架构师为事件分配持久化递增顺序；_seq/_record不在本轮允许范围 | test_audit_wall_clock_rollback_reorders_mastery 显示答对仍failed且不补记 |", ""]
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "clock-audit.md").write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def env(monkeypatch):
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT, prefix="audit-") as root:
        source = InjectedClock()
        source.set(START)
        monkeypatch.setattr(handlers, "datetime", source.datetime)
        store = CourseStore("clock_audit", Path(root))
        settings = AppSettings("clock_audit", Path(root))
        settings.save(dict(refresh_time="04:00", reminder_time="20:00", reminder_enabled=True))
        agent = FakeAgent()
        services = SimpleNamespace(store=store, settings=settings, gateway=agent,
                                   account_id="clock_audit", data_root=Path(root))
        registry = HandlerRegistry()
        handlers.register(registry, SimpleNamespace(services=services, settings_changed=lambda _: None))
        room = Classroom(services, lambda: study_day(source.now(), time.fromisoformat(settings.load()["refresh_time"]), source.zone),
                         now=lambda: source.datetime.now(timezone.utc))
        value = SimpleNamespace(clock=source, store=store, settings=settings, agent=agent,
                                room=room, registry=registry, root=Path(root))
        try:
            yield value
        finally:
            for worker in list(threading.enumerate()):
                if worker.name == "classroom-profile":
                    worker.join(5)
            store.close()


def prepare(env, *, plan=PLAN, total=2130, end=END, weighted=True, active=True):
    weights = (1, 1, 1, 1, 1, 1, 0) if weighted else (1,) * 7
    special = {START + timedelta(days=8): .5} if weighted else {}
    store = env.store
    store.create_plan(plan, goal="计划甲" if plan == PLAN else "计划乙",
                      window=PlanWindow(START, end, 180, weights, special))
    store.add_sources(plan, [dict(id="s", url="https://example.invalid/offline", chunks=[
        dict(chunk_id="k1", text="队列按先进先出处理。"), dict(chunk_id="k2", text="栈按后进先出处理。")])])
    store.save_outline(plan, [dict(id="stage")], [dict(id="m0", stage_id="stage", title="队列"),
                                                    dict(id="m1", stage_id="stage", title="栈")])
    # m0=50; m1=total-50, the batch-eight fake agent understands p0,p1,p2.
    store.save_generated_module(plan, "m0", [dict(id="p0", title="队列定义", summary="先进先出", minutes=20, chunk_ids=["k1"]),
        dict(id="p1", title="队列操作", summary="入队出队", minutes=20, chunk_ids=["k1"])], check_minutes=10, today=START)
    store.save_generated_module(plan, "m1", [dict(id="p2", title="栈定义", summary="后进先出", minutes=total-60,
                                                     chunk_ids=["k2"])], check_minutes=10, today=START)
    if active:
        store.start_plan(plan, today=START)


def home(env, plan=PLAN):
    return next(row["today"] for row in env.registry.methods["plans.list"]({}, None)["plans"] if row["id"] == plan)


def check_status(audit, scene, expected, actual, rule="PLAN-05", **kwargs):
    audit(scene, rule, expected, None if actual is None else {key: actual[key] for key in expected}, **kwargs)


def act(env, name, *args):
    method = getattr(env.room, name)
    return env.room.run(PLAN, lambda co, st, cx: method(co, st, cx, *args),
                        SimpleNamespace(cancel=threading.Event(), stream=lambda _: None))


def snapshot(env):
    return env.room.run(PLAN, lambda *args: None)


def credit(env, minutes, *, plan=PLAN, tentative=False, unit="p0", day=None):
    day = day or study_day(env.clock.now(), time(4), env.clock.zone)
    number = len(env.store.records("credits", plan))
    env.store.add_credit(plan, f"audit-{number}", unit, minutes, day, "tentative" if tentative else "confirmed")


def oracle(targets, day, total, credited):
    """Cumulative scalar oracle for confirmed-only FIFO, independent of _fill."""
    before = sum(v for d, v in targets.items() if d < day)
    current = targets.get(day, 0)
    after = sum(v for d, v in targets.items() if d > day)
    future_credit = min(after, max(0, credited-before-current))
    prefix = 0
    full = 0
    for d, amount in sorted(targets.items()):
        if d <= day or amount == 0:
            continue
        prefix += amount
        if prefix <= future_credit:
            full += 1
    return dict(today_target=current, today_confirmed=min(current, max(0, credited-before)), today_tentative=0,
                debt_minutes=max(0, before-credited), ahead_minutes=future_credit, ahead_days=full,
                remaining_minutes=total-credited)


@pytest.mark.parametrize("mixed", [False, True], ids=["daily-enough", "miss-catchup-overdue"])
def test_audit_six_weeks_daily_targets_and_fifo(env, audit, mixed):
    prepare(env)
    targets = {START+timedelta(days=i): (0 if i % 7 == 6 else 30 if i == 8 else 60) for i in range(42)}
    assert env.store.load_ledger(PLAN).targets == targets
    credited = 0
    for i in range(42):
        day = START+timedelta(days=i)
        env.clock.set(day)
        amount = targets[day]
        if mixed:
            amount = 0 if i in (2, 3, 4, 7, 40) else 300 if i == 5 else amount
        if amount:
            credit(env, amount)
            credited += amount
        expected = oracle(targets, day, 2130, credited)
        expected["needs_negotiation"] = expected["debt_minutes"] >= 120
        expected["overdue_days"] = 0
        check_status(audit, f"六周{'欠补' if mixed else '逐日够量'}：第{i+1}天学{amount}分钟", expected, home(env))
        assert env.store.load_ledger(PLAN).targets == targets  # no implicit replan
    if mixed:
        assert credited == 2070
        for offset, amount, left in [(42, 20, 40), (43, 40, 0)]:
            env.clock.set(START+timedelta(days=offset))
            credit(env, amount)
            check_status(audit, f"截止后第{offset-41}天继续学{amount}分钟", dict(today_target=0, debt_minutes=left,
                         remaining_minutes=left, overdue_days=offset-41, time_completed=left == 0, completed=False),
                         home(env), rule="CHK-01")


def test_audit_seven_week_extension_keeps_history_and_reopen(env, audit):
    prepare(env, total=2520, weighted=False)
    for i in range(21):
        env.clock.set(START+timedelta(days=i))
        if i not in (2, 3):
            credit(env, 60)
    day = START+timedelta(days=21)
    env.clock.set(day)
    check_status(audit, "三周后欠两天，显示120分钟并建议协商", dict(debt_minutes=120, needs_negotiation=True), home(env))
    end = START+timedelta(days=48)
    env.store.save_intake(PLAN, window=PlanWindow(START, end, 180), today=day, reason="每天安排太紧，延长一周")
    ledger = env.store.load_ledger(PLAN)
    # 2520 - 19*60 = 1380 over 28 days: first 8 days 50, remaining 20 days 49.
    expected_targets = {day+timedelta(days=i): 50 if i < 8 else 49 for i in range(28)}
    audit("延长一周：只重排剩余1380分钟，历史欠120保留", "PLAN-08", expected_targets, ledger.targets)
    check_status(audit, "重排后首页不重复追讨已重分配的历史欠账", dict(today_target=50, debt_minutes=0,
                 history_debt_minutes=120, remaining_minutes=1380), home(env), rule="PLAN-08")
    history = ledger.to_dict()["history"]
    for i, (d, minutes) in enumerate(expected_targets.items()):
        env.clock.set(d)
        credit(env, minutes)
        check_status(audit, f"延长后第{i+1}天按新目标学{minutes}分钟", dict(today_target=minutes,
                     today_confirmed=minutes, debt_minutes=0, ahead_minutes=0, history_debt_minutes=120), home(env))
    assert env.store.load_ledger(PLAN).to_dict()["history"] == history
    with CourseStore("clock_audit", env.root) as reopened:
        assert reopened.load_ledger(PLAN).to_dict() == env.store.load_ledger(PLAN).to_dict()


def test_audit_module_edits_replan_before_start_and_guard_active(env, audit):
    prepare(env, total=120, weighted=False, end=START+timedelta(days=3), active=False)
    env.store.reorder_modules(PLAN, ["m1", "m0"], today=START)
    env.store.delete_module(PLAN, "m1", today=START)
    env.store.save_generated_module(PLAN, "m0", [dict(id="p0", title="修订队列", summary="修订内容", minutes=110,
                                                   chunk_ids=["k1"])], check_minutes=10, today=START)
    audit("开始前删模块/调整顺序/修改估算后重排", "PLAN-08", [30]*4, list(env.store.load_ledger(PLAN).targets.values()))
    env.store.start_plan(PLAN, today=START)
    credit(env, 30)
    before = env.store.load_ledger(PLAN).to_dict()
    for operation in [lambda: env.store.delete_module(PLAN, "m0", today=START),
                      lambda: env.store.reorder_modules(PLAN, ["m0"], today=START),
                      lambda: env.store.save_generated_module(PLAN, "m0", [], check_minutes=1, today=START)]:
        with pytest.raises(StoreError) as error:
            operation()
        assert error.value.code == "PLAN_STARTED"
        assert env.store.load_ledger(PLAN).to_dict() == before
    audit("已开始后中途删改模块", "PLAN-02", "拒绝 PLAN_STARTED，账本不变", "拒绝 PLAN_STARTED，账本不变")


@pytest.mark.parametrize("change", ["rollback", "refresh", "zone"])
def test_audit_classroom_day_survives_backwards_calendar(env, audit, change):
    prepare(env, total=90, weighted=False, end=START+timedelta(days=2))
    env.clock.set("2026-10-05T12:00:00+08:00")
    act(env, "enter")
    if change == "rollback":
        env.clock.set("2026-10-05T03:00:00+08:00")
    elif change == "refresh":
        env.settings.save({"refresh_time": "14:00"})
    else:
        env.clock.zone = timezone(timedelta(hours=-7))
    view = act(env, "next")
    check_status(audit, f"课堂内{change}导致学习日倒退，仍显示开课日", dict(progress_day="2026-10-05",
                 study_day="2026-10-04", today_target=30, today_tentative=20), view["today"], rule="TEACH-05",
                 finding="原不合理，已修", before="today_status 拒绝开课日大于当前日，Classroom.snapshot 返回 today=None")
    assert env.store.records("credits", PLAN)[0]["credit"]["day"] == "2026-10-05"
    act(env, "leave")
    assert act(env, "enter")["today"]["progress_day"] == "2026-10-04"


def test_audit_classroom_refresh_failure_retest_and_skip(env, audit):
    prepare(env, total=180, weighted=False, end=START+timedelta(days=2))
    env.clock.set("2026-10-05T23:50:00+08:00")
    act(env, "enter")
    act(env, "next")
    env.agent.verdicts.extend([("answer", True, []), ("answer", True, [])])
    act(env, "reply", "先进先出")
    act(env, "reply", "入队出队")
    env.clock.set("2026-10-06T04:01:00+08:00")
    check_status(audit, "跨凌晨刷新：课堂仍是前一天、首页已是新一天", dict(today_target=60, today_tentative=40,
                 progress_day="2026-10-05", study_day="2026-10-06", needs_rest=True), snapshot(env)["today"], rule="TEACH-05")
    check_status(audit, "跨刷新后首页显示前一天还欠20分钟", dict(today_target=60, today_tentative=0, debt_minutes=20), home(env))
    act(env, "start_test", "module_test")
    env.agent.verdicts.extend([("answer", True, []), ("answer", False, ["p1"])])
    act(env, "reply", "先进先出")
    view = act(env, "reply", "不会")
    assert view["module"]["state"] == "failed"
    check_status(audit, "检测失败撤回20、检测记10：旧课堂正式30，首页欠30", dict(today_confirmed=30, today_tentative=0,
                 debt_minutes=0), view["today"])
    check_status(audit, "检测失败后新学习日欠30，退出不丢账", dict(today_confirmed=0, debt_minutes=30), act(env, "leave")["today"])
    act(env, "enter")
    act(env, "start_test", "retest")
    env.agent.verdicts.append(("answer", True, []))
    view = act(env, "reply", "入队在尾出队在头")
    check_status(audit, "新一日重测补20，先还昨日欠账，仍欠10", dict(today_confirmed=0, debt_minutes=10), view["today"])
    act(env, "next_module")
    act(env, "start_test", "skip_test")
    view = act(env, "reply", "乙答案")
    check_status(audit, "直接考跳过130分钟：补欠10、填今日60、超前60", dict(today_confirmed=60, debt_minutes=0,
                 ahead_minutes=60, ahead_days=1, time_completed=True, completed=False), view["today"], rule="TEACH-07")
    latest = max(env.store.records("credits", PLAN), key=lambda row: row["operation_seq"])
    assert latest["credit"]["day"] == "2026-10-06"


def make_reminder(env, *, at="2026-10-06T19:59:00+08:00"):
    env.clock.set(at)
    notices = []
    service = ReminderService("clock_audit", env.root, lambda: env.store, settings=env.settings.load(),
        now=lambda: env.clock.datetime.now(timezone.utc), timezone=None,
        notifier=lambda title, message, click: notices.append(message) or True)
    return service, notices


@pytest.mark.parametrize("activity", ["catchup", "revoked", "wrong_answer"])
def test_audit_real_reminder_distinguishes_activity_from_fifo(env, qt_app, audit, activity):
    prepare(env, total=180, weighted=False, end=START+timedelta(days=2))
    prepare(env, plan=OTHER, total=180, weighted=False, end=START+timedelta(days=2))
    service, notices = make_reminder(env)
    try:
        if activity in ("catchup", "revoked"):
            credit(env, 10, tentative=activity == "revoked")
            if activity == "revoked":
                env.store.revoke_credits(PLAN, "p0", today=START+timedelta(days=1))
        else:
            env.store.append_judgement(PLAN, Judgement("wrong", 1, env.clock.now(), "casual", "m0", results=(("p0", False),)))
        assert home(env)["today_confirmed"] == home(env)["today_tentative"] == 0
        env.clock.set("2026-10-06T20:00:00+08:00")
        service.clock.check_now()
        audit(f"多计划到点提醒：甲已{activity}，仅提醒乙", "SYS-04", [False, True],
              ["计划甲" in notices[0], "计划乙" in notices[0]], finding="原不合理，已修",
              before="甲今日学习被FIFO填入昨天/暂记被撤/只有答题；显示0分钟，错误通知甲尚未开始")
        env.clock.set("2026-10-06T19:00:00+08:00")
        service.clock.check_now()
        env.clock.set("2026-10-06T20:01:00+08:00")
        service.clock.check_now()
        assert len(notices) == 1
    finally:
        service.dispose()


def test_audit_reminder_keeps_pending_on_unrelated_settings_save(env, qt_app, audit):
    prepare(env)
    service, notices = make_reminder(env, at="2026-10-05T19:59:00+08:00")
    try:
        service.class_started()
        env.clock.set("2026-10-05T20:00:00+08:00")
        service.clock.check_now()
        assert not notices
        service.settings_changed(dict(service.settings, teacher_style="warm"))
        service.class_ended()
        audit("上课时延后提醒，保存无关设置后下课仍检查", "SYS-04", 1, len(notices),
              finding="原不合理，已修", before="DailyClock.configure 无条件丢弃 pending，已过20点再不提醒")
    finally:
        service.dispose()


def test_audit_clock_sleep_settings_timezone_rollback_and_stop(env, qt_app, audit):
    env.clock.set("2026-10-05T19:59:00+08:00")
    notices, days, late = [], [], []
    active = [False]
    clock = DailyClock(now=lambda: env.clock.datetime.now(timezone.utc), timezone=None, reminder=time(20),
                       today_unfinished=lambda: True, in_class=lambda: active[0])
    clock.reminder_due.connect(lambda: notices.append(clock.current_study_day))
    clock.study_day_changed.connect(days.append)
    clock.late_night_notice.connect(lambda: late.append(clock.current_study_day))
    try:
        env.clock.set("2026-10-08T21:00:00+08:00")
        clock.check_now()
        audit("睡眠三天后唤醒，仅检查当前日，不补历史提醒", "SYS-04", [date(2026, 10, 8)], notices)
        env.clock.set("2026-10-08T19:00:00+08:00")
        clock.check_now()
        env.clock.set("2026-10-08T21:00:00+08:00")
        clock.check_now()
        audit("系统时间回拨再过提醒点，同日不重复", "SYS-04", 1, len(notices))
        env.clock.set("2026-10-09T12:00:00+08:00")
        clock.check_now()
        clock.configure(refresh=time(14))
        audit("白天把刷新改为14点，12点仍属昨天", "TEACH-05", date(2026, 10, 8), clock.current_study_day)
        clock.configure(refresh=time(4))
        env.clock.zone = timezone(timedelta(hours=-7))
        clock.check_now()
        audit("相同瞬间切换时区，学习日立即跟随当地", "TEACH-05", date(2026, 10, 8), clock.current_study_day)
        clock.stop()
        env.clock.set("2026-10-15T21:00:00-07:00")
        clock.start()
        audit("软件停止期间不追补提醒", "SYS-04", 1, len(notices))
        active[0] = True
        env.clock.set("2026-10-16T04:00:00-07:00")
        clock.check_now()
        env.clock.set("2026-10-16T03:00:00-07:00")
        clock.check_now()
        env.clock.set("2026-10-16T04:00:00-07:00")
        clock.check_now()
        audit("课堂跨刷新发一次休息提示，回拨不重复", "TEACH-05", [date(2026, 10, 16)], late)
    finally:
        clock.dispose()


def test_audit_out_of_scope_teacher_timing_is_reported(env, audit):
    prepare(env, total=90, weighted=False, end=START+timedelta(days=5))  # 15/day; a point costs20
    act(env, "enter")
    view = act(env, "next")
    audit("未作答已暂记：应随口问答上才暂记；实际先记20", "PLAN-05", 0,
          sum(c["credit"]["minutes"] for c in env.store.records("credits", PLAN)), finding="不合理，需架构师", observed=20)
    env.agent.verdicts.append(("answer", True, []))
    view = act(env, "reply", "先进先出")
    audit("今日目标15已超额：应停在当前点，实际自动讲第二点", "PLAN-05", ["learn:p0"],
          [r["id"] for r in env.store.records("credits", PLAN)], finding="不合理，需架构师", observed=["learn:p0", "learn:p1"])


def test_audit_repeated_enter_must_not_repin_open_session(env, audit):
    prepare(env, total=90, weighted=False, end=START+timedelta(days=2))
    env.clock.set("2026-10-06T03:59:00+08:00")
    act(env, "enter")
    env.clock.set("2026-10-06T04:01:00+08:00")
    view = act(env, "enter")
    check_status(audit, "课堂未退出，重复进入请求不更换开课日", dict(progress_day="2026-10-05", study_day="2026-10-06"),
                 view["today"], rule="TEACH-05", finding="原不合理，已修",
                 before="enter 每次覆写 day，重复请求在04点后改为新日")
    act(env, "next")
    assert env.store.records("credits", PLAN)[0]["credit"]["day"] == "2026-10-05"
    act(env, "leave")
    assert act(env, "enter")["today"]["progress_day"] == "2026-10-06"
    env.clock.set("2026-10-07T10:00:00+08:00")
    env.room = Classroom(env.room.services, lambda: date(2026, 10, 7), now=env.room.now)
    audit("重新启动课堂对象不会沿用崩溃前in_session旧日期", "TEACH-05", "2026-10-07",
          act(env, "enter")["today"]["progress_day"])


@pytest.mark.parametrize("case", ["partial", "ahead", "rest", "overdue", "checking", "before-start", "enter-only"])
def test_audit_multi_plan_reminder_boundary_policy(env, qt_app, audit, case):
    prepare(env, total=180, weighted=False, end=START+timedelta(days=2))
    prepare(env, plan=OTHER, total=180, weighted=False, end=START+timedelta(days=2))
    clock_at = "2026-10-05T19:59:00+08:00"
    rule = "SYS-04"
    finding = "合理"
    if case == "partial":
        credit(env, 10)
        expected = [False, True]
        scene = "甲当天已学10但未完成、乙未开始，只提醒乙"
    elif case == "ahead":
        credit(env, 120)
        clock_at = "2026-10-06T19:59:00+08:00"
        expected = [False, True]
        scene = "甲昨天学超覆盖今日，乙未开始，只提醒乙"
    elif case == "rest":
        env.store.save_intake(PLAN, window=PlanWindow(START, START+timedelta(days=2), 180,
                              date_weights={START: 0}), today=START)
        expected = [False, True]
        scene = "甲休息日目标0、乙有任务，只提醒乙"
    elif case == "overdue":
        clock_at = "2026-10-08T19:59:00+08:00"
        expected = [False, False]
        scene = "逾期未学完但目标0，目前两计划都不提醒；是否改为提醒需产品决定"
        finding = "待产品决定"
    elif case == "checking":
        clock_at = "2026-10-08T19:59:00+08:00"
        for module_id, points, seq in [("m0", ("p0", "p1"), 1), ("m1", ("p2",), 3)]:
            env.store.append_judgement(PLAN, Judgement(f"j{seq}", seq, env.clock.now(), "module_test", module_id,
                                        f"a{seq}", tuple((p, True) for p in points)))
            env.store.append_attempt_closed(PLAN, AttemptClosed(f"c{seq}", seq+1, module_id, f"a{seq}", "module_test"))
        env.store.start_checking(PLAN)
        credit(env, 180)
        expected = [False, False]
        scene = "时长已满、最终检验未完，逾期仍为1但不提醒（产品决定）"
        finding = "待产品决定"
    elif case == "before-start":
        clock_at = "2026-10-04T19:59:00+08:00"
        expected = [False, False]
        scene = "开始日前目标0，不提前提醒"
    else:
        act(env, "enter")
        act(env, "leave")
        expected = [True, True]
        scene = "甲只进入退出未答题，仍被视为未开始；是否算开始需产品决定"
        finding = "待产品决定"
    service, notices = make_reminder(env, at=clock_at)
    try:
        if case in ("overdue", "checking"):
            check_status(audit, scene + "：首页逾期", dict(overdue_days=1, today_target=0, completed=False), home(env), rule="CHK-01")
        elif case in ("partial", "ahead"):
            check_status(audit, scene + "：首页互不串账", dict(today_confirmed=10 if case == "partial" else 60,
                         today_target=60), home(env))
            assert home(env, OTHER)["today_confirmed"] == 0
        env.clock.set(env.clock.instant + timedelta(minutes=1))
        service.clock.check_now()
        message = notices[0] if notices else ""
        audit(scene, rule, expected, ["计划甲" in message, "计划乙" in message], finding=finding)
        assert len(notices) <= 1
    finally:
        service.dispose()


def test_audit_activity_uses_study_day_across_refresh_and_reopen(env, audit):
    prepare(env)
    env.clock.set("2026-10-06T03:59:00+08:00")
    env.store.append_judgement(PLAN, Judgement("before", 1, env.clock.now(), "casual", "m0", results=(("p0", False),)))
    with CourseStore("clock_audit", env.root) as reopened:
        audit("凌晨03:59答错也算学习过，归属昨日；重开库结果一致", "SYS-04", [True, False],
              [reopened.has_study_activity(PLAN, day=d, refresh=time(4), tz=EAST) for d in (START, START+timedelta(days=1))])
    env.clock.set("2026-10-06T04:00:00+08:00")
    env.store.append_judgement(PLAN, Judgement("after", 2, env.clock.now(), "casual", "m0", results=(("p0", False),)))
    assert env.store.has_study_activity(PLAN, day=START+timedelta(days=1), refresh=time(4), tz=EAST)


def test_audit_changed_reminder_to_past_is_not_backfilled(env, qt_app, audit):
    prepare(env)
    service, notices = make_reminder(env, at="2026-10-05T19:00:00+08:00")
    try:
        service.settings_changed(dict(service.settings, reminder_time="18:00"))
        audit("19点将提醒设为18点，不当场追补，选择待产品确认", "SYS-04", 0, len(notices), finding="待产品决定")
        env.clock.set("2026-10-06T18:00:00+08:00")
        service.clock.check_now()
        assert len(notices) == 1
    finally:
        service.dispose()


def test_audit_wall_clock_rollback_reorders_mastery(env, monkeypatch, audit):
    """Unlike a display-only clock change, move _seq's wall clock too."""
    prepare(env, total=90, weighted=False, end=START+timedelta(days=2))
    ticks = iter(range(1000))
    monkeypatch.setattr(classroom_module, "time", SimpleNamespace(
        time_ns=lambda: int(env.clock.instant.timestamp())*1_000_000_000 + next(ticks)*1000))
    act(env, "enter")
    act(env, "next")
    env.agent.verdicts.extend([("answer", True, []), ("answer", True, [])])
    act(env, "reply", "先进先出")
    act(env, "reply", "入队出队")
    act(env, "start_test", "module_test")
    env.agent.verdicts.extend([("answer", True, []), ("answer", False, ["p1"])])
    act(env, "reply", "先进先出")
    assert act(env, "reply", "不会")["module"]["state"] == "failed"
    closed = env.store.records("attempt_closed", PLAN)[0]["event"]["seq"]
    env.clock.set(env.clock.instant - timedelta(hours=1))
    act(env, "start_test", "retest")
    env.agent.verdicts.append(("answer", True, []))
    view = act(env, "reply", "入队在尾出队在头")
    retest = next(r["event"] for r in env.store.records("judgements", PLAN) if r["event"]["context"] == "retest")
    assert retest["seq"] < closed and retest["results"] == [["p1", True]]
    audit("回拨一小时后重测答对：seq排序将重测放到检测之前", "TEACH-02", "passed",
          view["module"]["state"], finding="不合理，需架构师", observed="failed")
    assert not any(r["id"] == "relearn:p1" for r in env.store.records("credits", PLAN))
