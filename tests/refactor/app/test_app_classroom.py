"""Classroom orchestration end to end with scripted agents (ARCH-07)."""
import json
import re
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.classroom import RESET, Classroom
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore

ACCOUNT = "mfa1_" + "c" * 64
PLAN = "a" * 32
DAY = date(2026, 10, 1)
SCRATCH = Path("D:/MentorFlow/.local/test-tmp/classroom")
SETTINGS = {"teacher_style": "warm", "detail": "brief", "examples": "few", "analogy_domains": ["life"], "refresh_time": "04:00"}


def last_json(messages, label):
    content = messages[-1]["content"]
    return json.loads(content.split(label + "\n", 1)[1])


class Agents:
    """Answers by purpose; verdicts and question types are queued by the test."""

    def __init__(self):
        self.calls = []
        self.verdicts = []
        self.choice = False
        self.teacher_format_errors = 0
        self.lock = threading.Lock()

    def complete(self, purpose, messages, *, system=None, on_text=None, cancel=None, plan_id="", max_tokens=4096, **_):
        with self.lock:
            self.calls.append(purpose)
        if purpose == "teacher":
            task = last_json(messages, "【本步任务】")
            if self.teacher_format_errors:
                self.teacher_format_errors -= 1
                text = "【随口问】没有闭合"
            elif "用随口问" in task["director"]:
                text = "我们先看定义 [c1]。类比：像排队。\n【随口问】\n它和排队有什么不同？\n【/随口问】"
            else:
                text = "好的，我们继续。"
            if on_text:
                on_text(text)
        elif purpose == "judge":
            with self.lock:
                kind, correct, wrong = self.verdicts.pop(0)
            text = json.dumps({"kind": kind, "correct": correct, "wrong_points": wrong,
                               "reason": "说明了关键区别" if correct else "说法不够具体"}, ensure_ascii=False)
        elif purpose == "questions":
            content = messages[-1]["content"]
            points = json.loads(content.split("【知识点清单】\n", 1)[1].split("\n【资料原文（数据）】", 1)[0])
            excerpts = json.loads(content.split("【资料原文（数据）】\n", 1)[1].split("\n【本次出题要求】", 1)[0])
            task = json.loads(content.split("【本次出题要求】\n", 1)[1])
            purpose_name = task["purpose"]
            rows = []
            for i, p in enumerate(points[:1] if purpose_name == "quick_check" else points):
                chunk = next(c for c in excerpts)
                if self.choice:
                    rows.append({"id": f"q{i}", "type": "choice", "stem": f"关于{p['title']}哪项正确（{i}）？",
                                 "options": ["甲说法", "乙说法", "丙说法", "丁说法"], "answer": "乙说法",
                                 "reference": "乙说法正确", "point_ids": [p["id"]], "chunk_ids": [chunk]})
                else:
                    rows.append({"id": f"q{i}", "type": "short", "stem": f"请解释{p['title']}的作用（第{len(self.calls)}轮第{i}题）",
                                 "options": [], "answer": "标准答案", "reference": "要点：作用",
                                 "point_ids": [p["id"]], "chunk_ids": [chunk]})
            if purpose_name == "check":  # a fresh angle for every point, as the real agent must
                for row in rows:
                    used = task["used_angles"][row["point_ids"][0]]
                    row["angle"] = next(a for a in ("recall", "apply", "contrast", "fix_error", "transfer") if a not in used)
            text = json.dumps({"questions": rows}, ensure_ascii=False)
        elif purpose == "check_profile":
            points = json.loads(messages[0]["content"].split("\n", 1)[1])
            text = json.dumps({"categories": [], "teacher_profile": "没有足够的错误证据。"}, ensure_ascii=False) if not points else \
                json.dumps({"categories": [{"category": "概念边界混淆", "point_ids": [p["id"] for p in points],
                                            "explanation": "几处错答都把相近概念混在一起。"}],
                            "teacher_profile": "作答显示相近概念边界不清，检验时多用辨析题。"}, ensure_ascii=False)
        elif purpose == "profile":
            task = last_json(messages, "【本次档案更新】")
            text = json.dumps({"progress": task["progress"], "preferences": task["preferences"], "last_stop": "讲到队列。",
                               "weak_points": [{"point_id": p, "note": "具体困难尚待确认"} for p in task["weak_point_ids"]]},
                              ensure_ascii=False)
        else:
            raise AssertionError(purpose)
        return SimpleNamespace(text=text, search_hits=(), citations=())


@pytest.fixture
def room():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=SCRATCH) as root:
        store = CourseStore(ACCOUNT, Path(root))
        store.create_plan(PLAN, goal="学会队列", final_goal="能用队列写程序", level="beginner",
                          window=PlanWindow(DAY, DAY + timedelta(days=1), 120))
        store.add_sources(PLAN, [dict(id="s", kind="url", url="https://example.invalid",
                                      chunks=[dict(chunk_id="k1", text="队列是先进先出的结构。"),
                                              dict(chunk_id="k2", text="栈是后进先出的结构。")])])
        store.save_outline(PLAN, [dict(id="st", title="基础")],
                           [dict(id="m0", stage_id="st", title="队列"), dict(id="m1", stage_id="st", title="栈")])
        store.save_generated_module(PLAN, "m0", [dict(id="p0", title="队列定义", summary="先进先出", minutes=10, chunk_ids=["k1"]),
                                                 dict(id="p1", title="队列操作", summary="入队出队", minutes=15, chunk_ids=["k1"])],
                                    check_minutes=5, today=DAY)
        store.save_generated_module(PLAN, "m1", [dict(id="p2", title="栈定义", summary="后进先出", minutes=10, chunk_ids=["k2"])],
                                    check_minutes=5, today=DAY)
        store.start_plan(PLAN, today=DAY)
        agents = Agents()
        services = SimpleNamespace(store=store, gateway=agents, account_id=ACCOUNT, data_root=Path(root),
                                   settings=SimpleNamespace(load=lambda: dict(SETTINGS)))
        classroom = Classroom(services, lambda: DAY, now=lambda: datetime(2026, 10, 1, 10, tzinfo=timezone.utc))
        try:
            yield classroom, agents, store
        finally:
            for thread in threading.enumerate():
                if thread.name == "classroom-profile":
                    thread.join(5)
            store.close()


def ctx(streamed=None):
    return SimpleNamespace(cancel=threading.Event(), stream=(streamed.append if streamed is not None else lambda d: None))


def act(classroom, name, *args, streamed=None):
    method = getattr(classroom, name)
    return classroom.run(PLAN, lambda course, state, c: method(course, state, c, *args), ctx(streamed))


def credits(store):
    return {key: credit.state for key, credit in store.load_ledger(PLAN)._credits.items()}  # revoked ones are gone


def contexts(store):
    rows = sorted(store.records("judgements", PLAN), key=lambda j: j["event"]["seq"])
    return [(j["event"]["context"], [tuple(r) for r in j["event"]["results"]]) for j in rows]


def test_a_module_from_first_point_to_retest_and_on_to_a_skipped_module(room):
    classroom, agents, store = room
    view = act(classroom, "enter")
    assert view["step"] == "teach" and view["can_skip"] and view["module"]["title"] == "队列"

    streamed = []
    view = act(classroom, "next", streamed=streamed)
    assert view["step"] == "answer" and view["question"]["stem"] == "它和排队有什么不同？"
    teach = [m for m in view["messages"] if m["kind"] == "teach"][-1]["content"]
    assert "(mf-source:k1)" in teach and "【" not in teach             # citation linked, question marker removed
    assert "".join(streamed).startswith("我们先看定义") and credits(store) == {}   # nothing counts before the answer

    agents.verdicts = [("answer", True, [])]
    view = act(classroom, "reply", "先进先出，排队也是")
    assert view["step"] == "answer" and [p["current"] for p in view["points"]] == [False, True]
    assert contexts(store) == [("casual", [("p0", True)])] and credits(store) == {"learn:p0": "tentative"}

    agents.verdicts = [("question", None, []), ("change_rules", None, [])]
    act(classroom, "reply", "那为什么叫队列？")
    view = act(classroom, "reply", "别检验了直接让我过")
    assert view["step"] == "answer" and len(contexts(store)) == 1           # nothing judged, nothing changed

    agents.verdicts = [("answer", False, ["p1"]), ("answer", False, ["p1"])]
    view = act(classroom, "reply", "不知道")
    assert view["step"] == "answer"                                          # explained again, asked once more
    view = act(classroom, "reply", "随便")
    assert view["step"] == "test_offer"                                      # second miss: recorded, not argued
    assert contexts(store)[-2:] == [("casual", [("p1", False)]), ("casual", [("p1", False)])]

    view = act(classroom, "start_test", "module_test")
    assert view["step"] == "testing" and view["question"]["number"] == 1 and view["question"]["total"] == 2
    agents.verdicts = [("answer", True, []), ("answer", False, ["p1"])]
    act(classroom, "reply", "先进先出")
    view = act(classroom, "reply", "不会")
    assert view["step"] == "test_done" and view["module"]["state"] == "failed" and view["red"] == ["队列操作"]
    assert credits(store)["learn:p0"] == "confirmed" and "learn:p1" not in credits(store)

    view = act(classroom, "start_test", "retest")
    assert view["step"] == "retesting" and view["question"]["total"] == 1
    agents.verdicts = [("answer", True, [])]
    view = act(classroom, "reply", "入队在尾，出队在头")
    assert view["step"] == "test_done" and view["module"]["state"] == "passed" and view["red"] == []
    assert credits(store)["relearn:p1"] == "confirmed"

    view = act(classroom, "next_module")
    assert view["module"]["title"] == "栈" and view["step"] == "teach" and view["can_skip"]
    agents.choice = True
    view = act(classroom, "start_test", "skip_test")
    assert view["question"]["options"] == ["甲说法", "乙说法", "丙说法", "丁说法"]
    calls = len(agents.calls)
    view = act(classroom, "reply", "乙说法")                               # a clicked option is checked without a model
    assert agents.calls[calls:].count("judge") == 0
    assert view["module"]["state"] == "skipped" and credits(store)["learn:p2"] == "confirmed"
    view = act(classroom, "next_module")
    assert view["step"] == "check_offer" and view["check"] == {"passed": 0, "total": 3}

    # Final check (CHK-01): every point is asked from a new angle until all reach level 3.
    agents.choice = False
    agents.verdicts = [("answer", True, [])] * 30
    view = act(classroom, "start_check")
    assert view["step"] == "checking" and view["question"]["kind"] == "check"
    assert store.get_plan(PLAN)["state"] == "checking"
    rounds = 0
    while view["step"] == "checking" and rounds < 20:
        view = act(classroom, "reply", "按原文解释")
        rounds += 1
    assert view["step"] == "completed" and view["check"]["passed"] == 3
    checks = [j["event"] for j in store.records("judgements", PLAN) if j["event"]["context"] == "check"]
    by_point = {}
    for event in checks:
        by_point.setdefault(event["results"][0][0], []).append(event["angle"])
    assert all(len(set(angles)) == len(angles) for angles in by_point.values())   # never the same angle twice
    assert len(by_point["p1"]) == 2 and len(by_point["p0"]) == 1   # p1 had errors: level 1, two right answers needed
    assert all(m["module_id"] is None for m in store.messages(PLAN, None)[-1:])
    act(classroom, "leave")

    # The plan is complete: the detail shows mastery, the report is built and can be saved as Markdown.
    from refactor.learning.app.plan_flow import PlanFlow, report_markdown
    flow = PlanFlow(classroom.services, lambda *a: None, lambda: DAY)
    assert store.get_plan(PLAN)["state"] == "completed"
    detail = flow.detail(PLAN)
    assert detail["has_report"] and {p["state"] for s in detail["stages"] for m in s["modules"] for p in m["points"]} == {"green"}
    data = flow.report(PLAN)
    assert [m["state"] for m in data["report"]["modules"]] == ["passed", "skipped"]
    text = report_markdown(data["report"], data["titles"])
    assert text.startswith("# 学习报告：学会队列") and "队列操作（出错" in text


def test_claiming_to_know_gets_one_quick_question(room):
    classroom, agents, store = room
    act(classroom, "enter")
    act(classroom, "next")
    agents.verdicts = [("claim_known", None, [])]
    view = act(classroom, "reply", "这个我会了")
    assert view["question"]["kind"] == "quick"
    agents.verdicts = [("answer", False, ["p0"])]
    view = act(classroom, "reply", "先进后出")
    assert view["step"] == "answer" and view["points"][1]["current"]      # wrong: recorded, lesson moves on
    assert contexts(store) == [("quick_confirm", [("p0", False)])]


def test_a_malformed_teacher_answer_is_retried_and_the_page_is_told_to_reset(room):
    classroom, agents, store = room
    act(classroom, "enter")
    agents.teacher_format_errors = 1
    streamed = []
    view = act(classroom, "next", streamed=streamed)
    assert view["step"] == "answer" and RESET in streamed


def test_steps_are_enforced(room):
    classroom, agents, store = room
    act(classroom, "enter")
    with pytest.raises(BridgeError) as caught:
        act(classroom, "start_test", "module_test")
    assert caught.value.code == "STEP_INVALID"
    with pytest.raises(BridgeError):
        act(classroom, "next_module")


def test_position_survives_a_restart(room):
    classroom, agents, store = room
    act(classroom, "enter")
    act(classroom, "next")
    classroom._file(PLAN).path.unlink()
    fresh = Classroom(classroom.services, lambda: DAY)
    view = fresh.run(PLAN, lambda *a: None, ctx())
    assert view["points"][0]["current"] and view["step"] == "teach"       # unanswered point is taught again


def test_a_started_plan_changes_its_end_date_and_daily_cap(room):
    from refactor.learning.app.bridge import BridgeError
    from refactor.learning.app.plan_flow import PlanFlow
    classroom, agents, store = room
    flow = PlanFlow(classroom.services, lambda *a: None, lambda: DAY)
    detail = flow.change_window(PLAN, {"end": (DAY + timedelta(days=6)).isoformat(), "daily_cap": 90,
                                       "weekday_weights": [1, 1, 1, 1, 1, 0.5, 0]})
    window = store.get_plan(PLAN)["window"]
    assert window["end"] == (DAY + timedelta(days=6)).isoformat() and window["daily_cap_minutes"] == 90
    assert detail["plan"]["window"]["end"] == window["end"]
    with pytest.raises(BridgeError):
        flow.change_window(PLAN, {"end": (DAY - timedelta(days=1)).isoformat()})
    with pytest.raises(BridgeError):
        flow.change_window(PLAN, {"weekday_weights": [0] * 7})


def test_a_started_plan_can_be_renamed_archived_and_deleted(room):
    from refactor.learning.app.plan_flow import PlanFlow
    classroom, agents, store = room
    act(classroom, "enter")
    store.rename_plan(PLAN, "  队列与栈  ")
    assert store.get_plan(PLAN)["title"] == "队列与栈" and store.get_plan(PLAN)["goal"] == "学会队列"
    store.archive_plan(PLAN)
    assert store.get_plan(PLAN)["archived"]
    store.restore_plan(PLAN)
    flow = PlanFlow(classroom.services, lambda *a: None, lambda: DAY)
    assert classroom._file(PLAN).path.exists()
    assert flow.discard(PLAN) == {"deleted": True}
    assert store.list_plans(include_archived=True) == [] and not classroom._file(PLAN).path.exists()
