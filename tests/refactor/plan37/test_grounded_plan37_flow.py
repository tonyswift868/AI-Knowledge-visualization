"""Batch 37: real course persistence, fake search/model and MF-04 preparation."""
from datetime import date, timedelta
import json
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app.bridge import BridgeError, CallContext
from refactor.learning.app.plan_flow import PlanFlow, map_ranges, short_plan_name
from refactor.learning.course import planner_agents as agents, plan_intent
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.research import FetchedContent
from refactor.learning.course.source_filter import load_lists
from refactor.learning.course.store import CourseStore

ACCOUNT = "mfa1_" + "5" * 64
TODAY = date(2026, 10, 3)
URL = "https://openstax.org/books/college-physics-2e/pages/11-3-pressure"
QUOTE = "Pressure is the force per unit perpendicular area over which the force is applied."


def form(**patch):
    return dict(goal="初二物理：压强与浮力", final_goal="理解压强的定义与生活应用", level="beginner", level_note="",
        notes="", start=TODAY.isoformat(), end=(TODAY + timedelta(days=13)).isoformat(), daily_cap=60,
        weekday_weights=[1]*7, special_days=[], only_mine=False, **patch)


def raw_map(chunk="c1", *, invalid=False, count=1):
    points = [{"title": f"压强的定义{i}", "summary": "理解单位面积上的压力", "citations": [{"chunk_id": chunk, "quote": QUOTE}]}
              for i in range(count)]
    if invalid:
        points += [{"title": "资料没有的液体公式", "summary": "不应保留", "citations": [{"chunk_id": chunk, "quote": "这一整段不在取得的原文中，不能拿来教学。"}]}]
    return {"stages": [{"title": "压强", "modules": [{"key": "m1", "title": "压强与面积", "summary": "理解压强",
        "refs": ["s1.1"], "prerequisites": [], "points": points}]}], "message": "先从压强的定义学起。"}


class Model:
    def __init__(self):
        self.calls = []
        self.invalid = False
        self.count = 1
        self.missing = False
    def get_configuration(self):
        return {"model": "offline"}
    def complete(self, purpose, messages, **kwargs):
        self.calls.append(purpose)
        from tests.refactor.wire121.model121 import directory_reply
        reply=directory_reply(purpose,messages,kwargs.get('system'))
        if reply is not None:return reply
        text = messages[-1]["content"]
        if purpose == 'point_difficulty':
            result = [dict(id=p['id'],difficulty='中') for p in json.loads(messages[0]['content'])['points']]
        elif purpose == "sufficiency":
            from refactor.learning.course.planner_agents import PREVIEW_SYSTEM
            result = ({"summary": "理解压强与浮力", "modules": [{"title": "压强", "summary": "理解生活应用"}]}
                if kwargs.get('system') == PREVIEW_SYSTEM else
                {"enough": True, "goal": "初二物理：压强与浮力", "focus": ["压强"], "questions": []})
        elif purpose == "research":
            result = {"sources": [{"title": "Pressure", "url": URL, "why": "正规教材", "type":"教材", "modules":["压强"]}]}
        elif purpose == "source_scoring":
            import re
            result = {"scores": [{"id": r, "relevance": 5, "credibility": 5, "note": "OpenStax 正规教材，有定义原文。"}
                for r in re.findall(r"\[(r\d+)\]", text)]}
        elif purpose == "coverage_check":
            result = {"covered": not self.missing, "missing": [{"content": "浮力", "why": "未取得浮力正文", "points": 2}] if self.missing else [],
                      "message": "缺浮力原文，需要补资料。" if self.missing else ""}
        elif purpose == 'outline' and kwargs.get('system') == agents.MATERIAL_STRUCTURE_SYSTEM:
            packet=json.loads(text); section=packet['directory'][0]
            result=dict(summary='按资料理解压强',modules=[dict(key='m1',title='压强与面积',summary='理解压强',refs=[section['id']],chapters=[section['title']])],missing=['浮力'] if self.missing else [])
        elif purpose == 'generate':
            import re
            alias=re.search(r'\[(c\d+)\] [^\n]*（paragraph）\n',text)[1]
            result=dict(points=[dict(title=p['title'],summary=p['summary'],chunks=[
                'c9999' if p['title']=='资料没有的液体公式' else alias],minutes=15)
                for p in raw_map(invalid=self.invalid,count=self.count)['stages'][0]['modules'][0]['points']])
            self.invalid=False
        elif purpose == "outline":
            chunks = json.JSONDecoder().raw_decode(text.split("【已取得的原文段落】\n")[1])[0]
            result = raw_map(chunks[0]["chunk_id"], invalid=self.invalid, count=self.count)
        elif purpose == "plan_intent":
            payload = json.loads(text.split("\n", 1)[1])
            assert "http" not in payload["text"]
            refs = payload["ctx"]["sections"]
            result = {"intents": [{"kind": "limit_scope", "include": [refs[0]["id"]], "exclude": []}], "reply": ""}
        else:
            raise AssertionError(f"obsolete model call: {purpose}")
        return SimpleNamespace(text=json.dumps(result, ensure_ascii=False), search_hits=(), citations=())


@pytest.fixture
def fixture(tmp_path):
    store = CourseStore(ACCOUNT, tmp_path)
    model = Model()
    events, prepared = [], []
    body = ('<html><head><title>Pressure OpenStax</title></head><body><h1>Pressure</h1><h2>Pressure definition</h2>'
            + f'<p>{QUOTE} </p>'*25 + '</body></html>').encode()
    def fetch(url, **kwargs):
        assert url == URL
        actual = body if model.count == 1 else ('<html><h1>Pressure</h1><p>'+QUOTE*100+'</p></html>').encode()
        return FetchedContent(url, url, "text/html", actual, "2026-10-03T00:00:00Z")
    def prepare(store, pid, mid, ctx, progress):
        from refactor.learning.course.point_materials import prepare_module
        from tests.refactor.material_questions70.test_material_questions70_contract import split70
        # This plan-only fixture has no original exercises. Zero questions is
        # a completed extraction, not an instruction to manufacture questions.
        result=prepare_module(store,pid,mid,dict(question_splitter=split70,cancel=ctx['cancel'],gateway=model),lambda *a:None)
        assert store.records("points", pid)
        prepared.append(mid)
        progress(1, 1, "原文与备题完成")
        return result
    services = SimpleNamespace(store=store, gateway=model, data_root=tmp_path, account_id=ACCOUNT,
                               catalog=SimpleNamespace(load_lists=load_lists), _closed=False)
    flow = PlanFlow(services, lambda kind, data: events.append((kind, data)), lambda: TODAY, fetcher=fetch, prepare_module=prepare)
    ctx = CallContext("test", threading.Event(), lambda _: None)
    yield flow, model, ctx, events, prepared
    services._closed = True
    store.close()


def adopt_and_map(fixture):
    flow, model, ctx, *_ = fixture
    draft = flow.current()
    pid = draft["plan_id"]
    response = flow.run(pid, lambda d, c: flow.submit_form(d, c, form()), ctx)
    assert response["draft"]["busy"] is None
    assert response['draft']['phase'] == 'preview' and 'research' not in model.calls
    flow.run(pid, lambda d,c: flow.chat(d,c,'确认并找资料'),ctx)
    assert "research" in model.calls
    flow.run(pid,lambda d,c:flow.chat(d,c,URL),ctx)
    flow.run(pid, lambda d, c: flow.make_outline(d, c), ctx)
    flow.run(pid,lambda d,c:flow.chat(d,c,'确认结构'),ctx)
    return flow.load(pid)


def test_plan37_whole_offline_flow_and_reopen(fixture):
    flow, model, ctx, events, prepared = fixture
    draft = adopt_and_map(fixture)
    pid = draft["plan_id"]
    flow.run(pid, lambda d, c: flow.estimate(d), ctx)
    result = flow.run(pid, lambda d, c: flow.generate(d, c), ctx)
    assert result["draft"]["phase"] == "ready" and result["draft"]["busy"] is None
    assert len(prepared) == 1 and model.calls.count("generate") == 1
    assert any("第 1 模块的资料与题目 1/1" in str(e) for e in events)
    detail = flow.detail(pid)
    point = detail["stages"][0]["modules"][0]["points"][0]
    assert QUOTE in point["citations"][0]["quote"]
    assert detail["plan"]["title"] == "初二物理：压强与浮力"
    assert flow.load(pid)["outline"]["grounded"]
    flow.start_request(pid)
    assert flow.services.store.get_plan(pid)["state"] == "active"

def test_plan70_zero_question_first_module_is_ready_and_keeps_points(fixture):
    flow,model,ctx,*_=fixture
    draft=adopt_and_map(fixture);pid=draft['plan_id']
    flow.estimate(draft)
    flow.generate(draft,ctx)
    assert flow.load(pid)['phase']=='ready' and flow.services.store.records('points',pid)
    module=flow.services.store.records('modules',pid)[0]
    assert module['check_minutes']==module['final_check_minutes']==module['question_count']==0


def test_plan37_prunes_invalid_number_without_retrying_valid_points(fixture):
    fixture[1].invalid = True
    draft = adopt_and_map(fixture)
    assert len(agents.outline_modules(draft["outline"])[0]["points"]) == 1
    assert fixture[1].calls.count("generate") == 1
    assert all(p["title"] != "资料没有的液体公式" for p in agents.outline_modules(draft["outline"])[0]["points"])


@pytest.mark.parametrize("bad", ["missing", "unadopted", "short", "invented"])
def test_plan37_reference_must_be_in_selected_obtained_original(bad):
    sources = [{"id": "s", "title": "教材", "url": URL}]
    chunks = [{"id": "c1", "source_id": "s", "position": 0, "text": QUOTE, "section": ["Pressure"]}]
    sections = agents.build_sections(sources, {"s": chunks})
    data = raw_map()
    citation = data["stages"][0]["modules"][0]["points"][0]["citations"][0]
    if bad == "missing": citation["chunk_id"] = "nonexistent"
    elif bad == "unadopted": chunks[0]["source_id"] = "other"
    elif bad == "short": citation["quote"] = "Pressure"
    else: citation["quote"] = "This invented paragraph was not fetched."
    result = agents.parse_map(json.dumps(data), sections, sources, chunks)
    assert not result["stages"] and result["dropped"] == ["压强的定义0"]




def test_plan37_user_cap_warns_without_blocking_generation(fixture):
    flow, model, ctx, *_ = fixture
    model.count = 30
    draft = adopt_and_map(fixture)
    # Point count is now host-owned, so force a genuine cap overrun via the cap.
    draft["form"].update(end=TODAY.isoformat(), daily_cap=10)
    flow.estimate(draft)
    r = draft["outline"]["ranges"]
    assert r['over_cap'] and r['suggested_days']>=2  # Even learning alone exceeds the user's cap.
    flow.extend_end(draft, r["suggested_end"])
    assert not draft["estimate"]["blocked"]
    flow.generate(draft, ctx)
    assert draft["phase"] == "ready"


def test_plan37_estimate_weights_and_no_model_minutes():
    w = PlanWindow(TODAY, TODAY+timedelta(days=1), 30, (1,)*7, {TODAY: .5})
    r = map_ranges(w, 4)
    assert r["learning"] == 32 and r["total"] == [32,32]
    known=map_ranges(w,4,module_counts=[4],question_counts=[3])
    assert known['total']==[46,46]  # 32 learned + 3x2 module + round(3x2.6) final.
    assert not r['over_cap']
    assert known['over_cap'] and known['suggested_end']


@pytest.mark.parametrize("goal", ["初二物理：压强与浮力", "我想学习初二物理：压强与浮力，能够掌握不同环境的应用和计算方法", "想学非常非常长的一个知识主题然后把所有技巧全部掌握"])
def test_plan37_short_name_uses_subject(goal):
    assert 1 <= len(short_plan_name(goal)) <= 20
    if "物理" in goal:
        assert "压强与浮力" in short_plan_name(goal)


def test_plan37_abandon_changes_both_goal_and_display_name(fixture):
    flow, _, ctx, *_ = fixture
    draft = adopt_and_map(fixture)
    draft["form"]["goal"] += "（含 p=ρgh）"
    draft["goal"] = "在 2026-10-03 至 2026-10-16 期间每天30分钟，" + draft["form"]["goal"]
    flow._abandon_scope(draft, ctx, ["p=ρgh"])
    plan = flow.services.store.get_plan(draft["plan_id"])
    assert all("p=ρgh" not in v for v in (draft["goal"], draft["name"], plan["goal"], plan["title"]))
    assert len(plan["title"]) <= 20
    assert plan["title"] == "初二物理：压强与浮力"


def test_plan37_busy_cleared_before_view_and_lock_released_on_error(fixture):
    flow, _, ctx, *_ = fixture
    draft = flow.current()
    pid = draft["plan_id"]
    result = flow.run(pid, lambda d, c: flow.status(pid, "busy"), ctx)
    assert result["draft"]["busy"] is None
    with pytest.raises(BridgeError):
        flow.run(pid, lambda d, c: (_ for _ in ()).throw(BridgeError("FAIL", "失败")), ctx)
    assert flow._lock(pid).acquire(False)
    flow._lock(pid).release()
    assert pid not in flow._status


def test_plan37_url_import_precedes_scope_with_fresh_directory(fixture):
    flow, _, ctx, *_ = fixture
    draft = adopt_and_map(fixture)
    order = []
    def imported(d, c, urls):
        order.append("import")
        assert urls == [URL]
        d["scope"] = {}
    flow._import_links = imported
    original = flow._carry_out
    def carry(d, c, intent, context):
        if intent.kind != "import_sources": order.append(intent.kind)
        return original(d, c, intent, context)
    flow._carry_out = carry
    flow.chat(draft, ctx, f"{URL} 只学第一节的压强定义，其他的先不学")
    assert order == ["import", "limit_scope"] and draft["scope"]["include"] == ["s1.1"]


def test_plan37_multiple_intents_sorted_import_first():
    context = {"phase": "sources", "today": TODAY.isoformat(), "form": form(), "recent": [], "cards": [],
               "sections": [{"id": "s1.1", "source": "教材", "title": "定义"}], "modules": [], "facts": {}}
    answer = {"intents": [{"kind": "limit_scope", "include": ["s1.1"], "exclude": []}, {"kind": "import_sources", "urls": [URL]}], "reply": ""}
    assert [i.kind for i in plan_intent.parse_intents(json.dumps(answer), context)] == ["import_sources", "limit_scope"]


def test_plan37_missing_prepare_module_does_not_claim_ready(fixture):
    flow, _, ctx, *_ = fixture
    draft = adopt_and_map(fixture)
    flow.estimate(draft)
    flow._prepare_module = None
    import sys
    from unittest.mock import patch
    with patch.dict(sys.modules, {"refactor.learning.course.point_materials": None}), pytest.raises(BridgeError) as err:
        flow.generate(draft, ctx)
    assert err.value.code == "PREPARATION_UNAVAILABLE" and draft["phase"] == "outline"


def test_plan37_other_modules_prepare_in_background(fixture):
    flow, _, ctx, *_ = fixture
    entered, finish = threading.Event(), threading.Event()
    thread_ids = []
    def prepare(store, pid, mid, ctx, progress):
        thread_ids.append(threading.get_ident())
        entered.set()
        finish.wait(2)
    flow._module_ready = lambda pid, mid: bool(thread_ids)  # scheduling-only; becomes ready after the call
    draft = flow.current()
    draft["phase"] = "ready"
    draft["generation"] = {"second": "pending"}
    flow.save(draft)
    pid = draft["plan_id"]
    flow._prepare_remaining(pid, [{"id": "second"}], prepare)
    assert entered.wait(1) and thread_ids == [thread_ids[0]]
    assert thread_ids[0] != threading.get_ident()
    finish.set()
    import time
    end = time.monotonic()+2
    while pid in flow._preparing and time.monotonic() < end:
        time.sleep(.01)
    assert flow.load(pid)["generation"]["second"] == "done"


def test_plan37_background_completion_does_not_recreate_started_draft(fixture):
    flow = fixture[0]
    draft = flow.current()
    flow._file(draft["plan_id"]).path.unlink()
    flow._record_preparation(draft["plan_id"], "module", "done")
    assert not flow._file(draft["plan_id"]).path.exists()
