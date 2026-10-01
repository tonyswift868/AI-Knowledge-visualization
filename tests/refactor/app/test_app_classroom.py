"""Classroom orchestration end to end with scripted agents (ARCH-07)."""
import json
import re
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.classroom import RESET, Classroom
from refactor.learning.connections.errors import ModelError
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore
from refactor.learning.course.store import StoreError
from refactor.learning.course.judge_agent import Verdict
from refactor.learning.preferences.storage import StorageError

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
        self.choice_bank_points = set()
        self.teacher_format_errors = 0
        self.lock = threading.Lock()

    def complete(self, purpose, messages, *, system=None, on_text=None, cancel=None, plan_id="", max_tokens=4096, **_):
        with self.lock:
            self.calls.append(purpose)
        if purpose == "lesson_prep" or (purpose == "teacher" and system.startswith("现场分步")):
            from tests.refactor.guided_lessons.fixtures import wire
            task = json.loads(messages[-1]['content'].split('\n', 1)[1])
            def one(point, live=False):
                result = wire(point['chunk_ids'][0], live=live, short=True)
                result['questions'][0]['stem'] = '它和排队有什么不同？'
                return result
            value = {'lessons': [{'point_id': p['id'], 'lesson': one(p)} for p in task['points']]} if 'points' in task else one(task, live=purpose == 'teacher')
            return SimpleNamespace(text=json.dumps(value, ensure_ascii=False), search_hits=(), citations=())
        if purpose == 'lesson_review':
            return SimpleNamespace(text='{"verdict":"approve","issues":[],"revised":null}', search_hits=(), citations=())
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
            task = last_json(messages, "【本次判定】")
            points = json.JSONDecoder().raw_decode(messages[-1]["content"].split("【知识点清单】\n", 1)[1])[0]
            value = {"kind": kind, "correct": correct, "wrong_points": wrong,
                     "reason": "说明了关键区别" if correct else "说法不够具体"}
            if task["question"]["type"] in ("short", "fix_error"):
                value.update(uncertain=False, checks=[dict(point_id=points[min(i, len(points)-1)]["id"], requirement=req, applies=True,
                    met=points[min(i, len(points)-1)]["id"] not in wrong,
                    evidence=task["reply"] if points[min(i, len(points)-1)]["id"] not in wrong else "")
                    for i, req in enumerate(task['required_points'])] if kind == "answer" else [])
            text = json.dumps(value, ensure_ascii=False)
        elif purpose == "questions":
            content = messages[-1]["content"]
            points = json.JSONDecoder().raw_decode(content.split("【知识点清单】\n", 1)[1])[0]
            excerpts = json.JSONDecoder().raw_decode(content.split("【资料原文（数据）】\n", 1)[1])[0]
            task = json.loads(content.split("【本次出题要求】\n", 1)[1])
            purpose_name = task["purpose"]
            rows = []
            work = points[:1] if purpose_name == "quick_check" else points
            for i, p in enumerate(work):
                chunk = next(c for c in excerpts)
                available_angles = [a for a in ('recall', 'apply', 'contrast', 'fix_error', 'transfer') if a not in task.get('used_angles', {}).get(p['id'], ())]
                for variant in range(task.get('counts', {}).get(p['id'], 2) if purpose_name == 'bank' else 1):
                  if self.choice or (purpose_name == 'bank' and p['id'] in self.choice_bank_points):
                    rows.append({"id": f"q{i}-{variant}", "type": "choice", "stem": f"关于{p['title']}哪项正确（第{len(self.calls)}轮{i}-{variant}）？",
                                 "options": ["甲说法", "乙说法", "丙说法", "丁说法"], "answer": "乙说法",
                                 "reference": "乙说法正确", "point_ids": [p["id"]], "chunk_ids": [chunk]})
                  else:
                    rows.append({"id": f"q{i}-{variant}", "type": "short", "stem": f"请解释{p['title']}的作用（第{len(self.calls)}轮第{i}-{variant}题）",
                                 "options": [], "answer": "标准答案", "reference": "要点：作用",
                                 "point_ids": [p["id"]], "chunk_ids": [chunk], 'required_points': ['作用']})
                  if purpose_name == 'bank': rows[-1]['angle'] = available_angles[variant]
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
                if thread.name in ('classroom-profile', 'classroom-question-bank', 'classroom-lesson-prep'):
                    thread.join(5)
            store.close()


def ctx(streamed=None):
    return SimpleNamespace(cancel=threading.Event(), stream=(streamed.append if streamed is not None else lambda d: None))


def act(classroom, name, *args, streamed=None):
    method = getattr(classroom, name)
    view = classroom.run(PLAN, lambda course, state, c: method(course, state, c, *args), ctx(streamed))
    deadline = time.monotonic() + 3
    while view['step'] == 'preparing_questions' and time.monotonic() < deadline:
        time.sleep(.01)
        view = classroom.run(PLAN, lambda *a: None, ctx(streamed))
    if name in ('next', 'reply'):
        from tests.refactor.guided_lessons.fixtures import advance_to_question
        view = advance_to_question(classroom, PLAN, view, ctx(streamed))
    return view


def credits(store):
    return {key: credit.state for key, credit in store.load_ledger(PLAN)._credits.items()}  # revoked ones are gone


def contexts(store):
    rows = sorted(store.records("judgements", PLAN), key=lambda j: j["event"]["seq"])
    return [(j["event"]["context"], [tuple(r) for r in j["event"]["results"]]) for j in rows]


@pytest.mark.parametrize("context", ["casual", "quick_confirm", "module_test", "retest", "check"])
def test_answer_event_is_idempotent_after_interrupted_classroom_save(room, context):
    classroom, _, store = room
    course = classroom._course(PLAN)
    state = classroom._rebuild(course)
    if context == 'module_test': state['test'] = {'attempt': 'test-attempt'}
    pending = {"kind": context, "point_ids": ["p0"], "question": {"stem": "问？", "chunk_ids": []},
               "answer_id": "answer-" + context}
    verdict = Verdict("answer", True, (), "正确。")
    classroom._record(course, state, context, pending, verdict, "回答", angle='recall' if context == 'check' else None)
    classroom._record(course, state, context, pending, verdict, "回答", angle='recall' if context == 'check' else None)
    assert len([row for row in store.records("judgements", PLAN) if row["id"] == pending["answer_id"]]) == 1
    assert classroom._prior_verdict(course, state, pending).reason == '正确。'


def test_casual_retry_after_teacher_failure_reuses_saved_verdict(room, monkeypatch):
    classroom, agents, store = room
    act(classroom, 'enter')
    act(classroom, 'next')
    agents.verdicts = [('answer', False, ['p0'])]
    original = classroom._teacher
    monkeypatch.setattr(classroom, '_teacher', lambda *_a, **_k: (_ for _ in ()).throw(ModelError('MODEL_TIMEOUT', '老师超时')))
    act(classroom, 'reply', '先进先出')
    assert len(store.records('judgements', PLAN)) == 1
    monkeypatch.setattr(classroom, '_teacher', original)
    act(classroom, 'reply', '重试')
    assert len(store.records('judgements', PLAN)) == 1
    assert agents.verdicts == []


def test_practice_retry_after_record_write_reuses_result(room, monkeypatch):
    classroom, agents, store = room
    course = classroom._course(PLAN)
    resume = classroom._rebuild(course)
    state = dict(resume, step='practicing', practice={'index': 0, 'count': 1, 'resume': resume})
    pending = {'kind': 'practice', 'answer_id': 'practice-once', 'point_ids': ['p0'],
               'question': {'id': 'q0', 'module_id': 'm0', 'point_ids': ['p0'], 'type': 'short', 'stem': '解释队列',
                            'options': [], 'answer': '标准答案'}}
    state['pending'] = pending
    agents.verdicts = [('answer', False, ['p0'])]
    monkeypatch.setattr(classroom, '_say', lambda *_a, **_k: (_ for _ in ()).throw(ModelError('MODEL_TIMEOUT', '中断')))
    with pytest.raises(ModelError):
        classroom._practice_reply(course, state, ctx(), '错误', pending)
    monkeypatch.undo()
    classroom._practice_reply(course, state, ctx(), '重试', pending)
    assert len(store.records('practice_results', PLAN)) == 1
    assert agents.verdicts == []


def test_classroom_save_failure_is_reported(room, monkeypatch):
    classroom, _, _ = room
    monkeypatch.setattr(classroom, "_file", lambda *_: SimpleNamespace(read=lambda default: default,
        write=lambda *_: (_ for _ in ()).throw(StorageError("WRITE_FAILED"))))
    with pytest.raises(BridgeError, match="保存失败"):
        act(classroom, "enter")


def test_complete_plan_failure_does_not_advance_classroom_and_can_retry(room, monkeypatch):
    classroom, _, store = room
    course = classroom._course(PLAN)
    state = classroom._rebuild(course)
    state.update(step="checking", pending={"kind": "check"}, check={"queue": []})
    original = store.complete_plan
    monkeypatch.setattr(store, "complete_plan", lambda *_args, **_kwargs: (_ for _ in ()).throw(StoreError("DISK_FULL", "写入失败")))
    with pytest.raises(StoreError):
        classroom._complete(course, state, ctx())
    assert state["step"] == "checking" and state["pending"] == {"kind": "check"}
    monkeypatch.setattr(store, "complete_plan", original)


def test_reopen_uses_database_completion_state(room):
    classroom, _, store = room
    course = classroom._course(PLAN)
    stale = classroom._rebuild(course)
    stale["step"] = "completed"
    classroom._file(PLAN).write(stale)
    assert classroom._load(classroom._course(PLAN))["step"] != "completed"


def test_a_module_from_first_point_to_retest_and_on_to_a_skipped_module(room):
    classroom, agents, store = room
    # Bank preparation is asynchronous: select the later module's fixture
    # type before enter(), not after that bank may already have been cached.
    agents.choice_bank_points = {'p2'}
    view = act(classroom, "enter")
    assert view["step"] == "teach" and view["can_skip"] and view["module"]["title"] == "队列"

    streamed = []
    view = act(classroom, "next", streamed=streamed)
    assert view["step"] == "answer" and view["question"]["stem"] == "它和排队有什么不同？"
    teach = [m for m in view["messages"] if m["kind"] == "step"][0]["content"]
    assert "(mf-source:k1)" in teach and "【" not in teach             # citation linked, question marker removed
    assert streamed == [] and credits(store) == {}  # prepared steps are immediate snapshots, not model streams

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
    queue = classroom._load(classroom._course(PLAN))['test']['queue']
    agents.verdicts = [("answer", q['point_ids'][0] != 'p1',
                        ['p1'] if q['point_ids'][0] == 'p1' else []) for q in queue]
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
    for question in store.records('question_bank', PLAN):
        if question['module_id'] == 'm1':
            store.mark_bank_correct(PLAN, question['id'])
    view = act(classroom, "start_test", "skip_test")
    assert set(view["question"]["options"]) == {"甲说法", "乙说法", "丙说法", "丁说法"}
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
        view = act(classroom, "reply", "按原文解释") if view['question'] else act(classroom, 'start_check')
        rounds += 1
    assert view["step"] == "completed" and view["check"]["passed"] == 3 and view["plan"]["state"] == "completed"
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
    view = classroom.run(PLAN, lambda co, st, cx: classroom._teacher(co, st, cx, '回答插话，不出题。', ['p0']), ctx(streamed))
    assert view["step"] == "teach" and RESET in streamed


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
    assert flow.delete_started(PLAN) == {"deleted": True}
    assert store.list_plans(include_archived=True) == [] and not classroom._file(PLAN).path.exists()


def test_a_test_closed_before_a_crash_is_finished_not_closed_twice(room):
    """BUG-23: the store closed the attempt but the room state was never saved (app killed mid-reply)."""
    classroom, agents, store = room
    act(classroom, "enter")
    act(classroom, "next")
    agents.verdicts = [("answer", True, []), ("answer", True, [])]
    act(classroom, "reply", "先进先出")
    view = act(classroom, "reply", "入队在尾，出队在头")
    assert view["step"] == "test_offer"
    act(classroom, "start_test", "module_test")
    agents.verdicts = [("answer", True, [])]
    act(classroom, "reply", "先进先出")
    before_last = classroom._file(PLAN).read({})                             # the state a crash would leave behind
    agents.verdicts = [("answer", True, [])]
    view = act(classroom, "reply", "入队在尾，出队在头")
    assert view["step"] == "test_done" and view["module"]["state"] == "passed"
    finished = (credits(store), store.load_ledger(PLAN).to_dict())
    classroom._file(PLAN).write(before_last)
    agents.verdicts = [("answer", True, [])]
    view = act(classroom, "reply", "入队在尾，出队在头")                    # the learner answers again after restarting
    assert view["step"] == "test_done" and view["module"]["state"] == "passed"
    closed = [r for r in store.records("attempt_closed", PLAN)]
    assert len(closed) == 1 and (credits(store), store.load_ledger(PLAN).to_dict()) == finished   # same bookkeeping
    assert act(classroom, "next_module")["module"]["title"] == "栈"


def test_free_check_on_a_completed_plan_survives_reopening(room):
    """BUG-30: the room is 'practicing' while the plan is completed; reloading must keep the practice question."""
    classroom, agents, store = room
    act(classroom, "enter")
    course = classroom._course(PLAN)
    course.plan = dict(course.plan, state="completed")
    state = classroom._load(course)
    resume = dict(state, step="completed", pending=None)
    practicing = dict(resume, step="practicing", phase="practice",
                      pending={"kind": "practice", "point_ids": ["p0"], "question": {"stem": "问"}},
                      practice={"resume": resume, "targets": ["p0"], "queue": [], "index": 0, "count": 1})
    classroom._file(PLAN).write(practicing)
    reloaded = classroom._load(course)
    assert reloaded["step"] == "practicing" and reloaded["pending"]["kind"] == "practice"
    classroom._file(PLAN).write(resume)
    assert classroom._load(course)["step"] == "completed"                    # the plain completed room is kept too
