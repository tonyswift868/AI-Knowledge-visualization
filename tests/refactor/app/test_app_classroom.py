"""New classroom persistence, errors and plan management; source bank fixtures."""
import threading
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.classroom import RESET, Classroom
from refactor.learning.connections.errors import ModelError
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore, StoreError
from refactor.learning.course.judge_agent import Verdict
from refactor.learning.preferences.storage import StorageError
from tests.refactor.integration38.support import Agents, seed_bank, context as ctx, operate, to_question, join_workers
ACCOUNT='mfa1_'+'c'*64
PLAN='a'*32
DAY=date(2026,10,1)
SETTINGS=dict(teacher_style='warm',detail='brief',examples='few',analogy_domains=['life'],refresh_time='04:00')

@pytest.fixture
def room(tmp_path):
    store=CourseStore(ACCOUNT,tmp_path)
    store.create_plan(PLAN,goal='学会队列',final_goal='能用队列写程序',level='beginner',window=PlanWindow(DAY,DAY+timedelta(days=1),120))
    store.add_sources(PLAN,[dict(id='s',url='https://docs.python.org/3/tutorial/',adopted=True,chunks=[
        dict(chunk_id='k1',text='队列是先进先出的结构。'),dict(chunk_id='k2',text='栈是后进先出的结构。')])])
    store.save_outline(PLAN,[dict(id='st',title='基础')],[dict(id='m0',stage_id='st',title='队列'),dict(id='m1',stage_id='st',title='栈')])
    store.save_generated_module(PLAN,'m0',[dict(id='p0',title='队列定义',summary='先进先出',minutes=10,chunk_ids=['k1']),
        dict(id='p1',title='队列操作',summary='入队出队',minutes=15,chunk_ids=['k1'])],check_minutes=5,today=DAY)
    store.save_generated_module(PLAN,'m1',[dict(id='p2',title='栈定义',summary='后进先出',minutes=10,chunk_ids=['k2'])],check_minutes=5,today=DAY)
    seed_bank(store,PLAN)
    store.start_plan(PLAN,today=DAY)
    agents=Agents()
    services=SimpleNamespace(store=store,gateway=agents,account_id=ACCOUNT,data_root=tmp_path,settings=SimpleNamespace(load=lambda:dict(SETTINGS)))
    classroom=Classroom(services,lambda:DAY,now=lambda:datetime(2026,10,1,10,tzinfo=timezone.utc))
    try:yield classroom,agents,store
    finally:
        classroom.cancel_materials();join_workers();store.close()

def act(classroom,name,*args,streamed=None):
    view=operate(classroom,PLAN,name,*args,streamed=streamed)
    if view.get('failure'):raise BridgeError('CLASS_FAILED',view['failure']['message'])
    if name in ('next','reply'):view=to_question(classroom,PLAN,view,streamed=streamed)
    return view

def credits(store):return {r['id']:r['credit']['state'] for r in store.records('credits',PLAN)}
def contexts(store):return [(r['event']['context'],[tuple(v) for v in r['event']['results']]) for r in store.records('judgements',PLAN)]

@pytest.mark.parametrize("context", ["casual", "quick_confirm", "module_test", "retest", "check"])
def test_answer_event_is_idempotent_after_interrupted_classroom_save(room, context):
    classroom, _, store = room
    course = classroom._course(PLAN)
    state = classroom._rebuild(course)
    if context in ('module_test','retest'): state['test'] = {'attempt': 'test-attempt'}
    pending = {"kind": context, "point_ids": ["p0"], "question": store.records('question_bank',PLAN)[0],
               "answer_id": "answer-" + context}
    verdict = Verdict("answer", True, (), "正确。")
    classroom._record(course, state, context, pending, verdict, "回答")
    classroom._record(course, state, context, pending, verdict, "回答")
    assert len([row for row in store.records("judgements", PLAN) if row["id"] == pending["answer_id"]]) == 1
    assert classroom._prior_verdict(course, state, pending).reason == '正确。'


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


def test_a_malformed_teacher_answer_is_retried_and_the_page_is_told_to_reset(room):
    classroom, agents, store = room
    act(classroom, "enter")
    agents.teacher_format_errors = 1
    streamed = []
    view = classroom.run(PLAN, lambda co, st, cx: classroom._teacher(co, st, cx, '回答插话，不出题。', ['p0']), ctx(streamed))
    assert view["step"] == "teach" and RESET in streamed


def test_position_survives_a_restart(room):
    classroom, agents, store = room
    act(classroom, "enter")
    act(classroom, "next")
    classroom._file(PLAN).path.unlink()
    fresh = Classroom(classroom.services, lambda: DAY)
    view = fresh.run(PLAN, lambda *a: None, ctx())
    assert view["points"][0]["current"] and view["step"] == "teach"       # unanswered point is taught again

@pytest.mark.parametrize('practice',[False,True])
def test_casual_and_practice_resume_saved_verdict_without_regrading(room,monkeypatch,practice):
    classroom,agents,store=room
    act(classroom,'enter');act(classroom,'next')
    if practice:
        act(classroom,'reply','队列是先进先出的结构。')
        operate(classroom,PLAN,'start_practice','m0','p0',1)
    original=classroom._save
    monkeypatch.setattr(classroom,'_save',lambda *args:(_ for _ in ()).throw(BridgeError('CLASS_SAVE_FAILED','保存失败')))
    with pytest.raises(BridgeError):
        classroom.perform(PLAN,'reply',ctx(),('队列是先进先出的结构。',),request_id='saved-grade')
    before=len(agents.calls)
    entity='practice_results' if practice else 'judgements'
    count=len(store.records(entity,PLAN))
    monkeypatch.setattr(classroom,'_save',original)
    classroom.now=lambda:datetime(2026,10,1,11,tzinfo=timezone.utc)
    v=classroom.perform(PLAN,'reply',ctx(),('队列是先进先出的结构。',),request_id='saved-grade')
    assert not v['failure'] and len(agents.calls)==before and len(store.records(entity,PLAN))==count

def test_steps_are_enforced(room):
    classroom,agents,store=room
    operate(classroom,PLAN,'next');operate(classroom,PLAN,'choose',0)
    v=operate(classroom,PLAN,'start_test','module_test')
    assert v['failure'] and v['step']=='teaching' and not store.records('judgements',PLAN)
    operate(classroom,PLAN,'next')
    v=operate(classroom,PLAN,'choose',0)
    assert v['failure'] and v['question'] and v['step']=='answer'

def test_a_test_closed_before_a_crash_is_finished_not_closed_twice(room):
    classroom,agents,store=room
    act(classroom,'enter');act(classroom,'next')
    act(classroom,'reply','队列是先进先出的结构。');act(classroom,'reply','队列是先进先出的结构。')
    act(classroom,'start_test','module_test');act(classroom,'reply','队列是先进先出的结构。')
    before=classroom._file(PLAN).read({})
    v=act(classroom,'reply','队列是先进先出的结构。')
    assert v['step']=='test_done' and v['module']['state']=='passed'
    finished=store.load_ledger(PLAN).to_dict();calls=len(agents.calls)
    classroom._file(PLAN).write(before)
    v=act(classroom,'reply','队列是先进先出的结构。')
    assert v['step']=='test_done' and len(store.records('attempt_closed',PLAN))==1
    assert store.load_ledger(PLAN).to_dict()==finished and len(agents.calls)==calls


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
