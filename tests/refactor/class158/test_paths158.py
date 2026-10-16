"""Regression checks for bounded classroom source and prediction paths."""
import copy
import time

from refactor.learning.app.classroom import Classroom
from refactor.learning.course import teacher_agent
from refactor.learning.course.request_layout import AppendOnlyPage
from refactor.learning.course.teacher_agent import Teaching
from tests.refactor.concept114.test_concept114_classroom import concept_environment
from tests.refactor.conversation37.test_grounded_conversation37 import PLAN, act


def _enter_concept(e):
    view = act(e, "next")
    assert view["step"] == "teaching"
    course = e.room._course(PLAN)
    return course, e.room._load(course)


def test_teaching_version_hashes_only_the_focused_source_text(tmp_path, monkeypatch):
    e = concept_environment(tmp_path)
    monkeypatch.setattr(Classroom, "_materials", staticmethod(lambda: e.materials))
    try:
        course, state = _enter_concept(e)

        class CourseProxy:
            def __init__(self, wrapped):
                self._wrapped = wrapped
                self.excerpt_calls = []

            def __getattr__(self, name):
                return getattr(self._wrapped, name)

            def excerpts(self, point_ids):
                self.excerpt_calls.append(list(point_ids))
                return self._wrapped.excerpts(point_ids)

        proxy = CourseProxy(course)
        initial = e.room._teaching_version(proxy, state)
        assert initial
        assert all(len(point_ids) <= 1 for point_ids in proxy.excerpt_calls)

        with e.store.transaction():
            row = next(row for row in e.store.records("source_chunks", PLAN) if row["id"] == "c0")
            row["text"] += " 当前面积比较补充条件。"
            e.store._put("source_chunks", PLAN, row)
        focused_course = e.room._course(PLAN)
        focused = e.room._teaching_version(focused_course, state)
        assert focused != initial

        with e.store.transaction():
            row = next(row for row in e.store.records("source_chunks", PLAN) if row["id"] == "c1")
            row["text"] += " 本段添加与当前小要点无关的资料。"
            e.store._put("source_chunks", PLAN, row)
        unrelated_course = e.room._course(PLAN)
        assert e.room._teaching_version(unrelated_course, state) == focused
    finally:
        e.room.cancel_materials()
        e.store.close()


def test_prefetch_keeps_explicit_empty_page_for_concept_requests(tmp_path, monkeypatch):
    e = concept_environment(tmp_path)
    monkeypatch.setattr(Classroom, "_materials", staticmethod(lambda: e.materials))
    seen_pages = []
    jobs = []
    original_prefetch = e.room.prefetch

    def request(ctx):
        seen_pages.append(ctx.get("request_page", "missing"))
        return ("system", [])

    def generate(plan_id, tctx, req, cancel, emit, *, prefetched=False):
        jobs.append((tctx.get("request_page", "missing"), prefetched))
        return Teaching("==依据当前讲义继续。==", ("c0",)), req, "==依据当前讲义继续。=="

    try:
        e.room.prefetch = lambda *args, **kwargs: dict(total=0, ready=0)
        course, state = _enter_concept(e)
        e.room.cancel_prefetch(PLAN)
        seen_pages.clear()
        e.room._teacher_request_page._accepted = None
        e.room.prefetch = original_prefetch
        monkeypatch.setattr("refactor.learning.app.classroom.teacher_agent.teacher_request", request)
        monkeypatch.setattr(e.room, "_generate_teacher", generate)
        version = e.room._teaching_version(course, state)

        result = e.room.prefetch(PLAN, version)
        assert result["total"] > 0
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            group = e.room._prefetches.get(PLAN)
            if group and all(job["done"] for job in group["jobs"].values()):
                break
            time.sleep(0.01)

        assert seen_pages and all(page is None for page in seen_pages)
        assert jobs and all(page is None and prefetched for page, prefetched in jobs)
    finally:
        e.room.cancel_prefetch(PLAN)
        e.room.cancel_materials()
        e.store.close()


def test_direct_review_focus_and_citation_alias_follow_resumed_subpoint(tmp_path, monkeypatch):
    e = concept_environment(tmp_path)
    monkeypatch.setattr(Classroom, "_materials", staticmethod(lambda: e.materials))
    try:
        course = e.room._course(PLAN)
        state = e.room._load(course)
        state.update(
            step="direct_review",
            index=0,
            pending=None,
            concept=None,
            direct_review=dict(
                points=["p0"],
                index=0,
                answered=False,
                question="",
                resume=dict(concept=dict(point_id="p0", subpoint_id="p0-s2")),
            ),
        )

        assert e.room._focus(course, state) == ["p0"]
        assert e.room._teaching_excerpts(course, state, ["p0"]) == {"c1": "单位面积上的压力叫压强。"}

        monkeypatch.setattr(
            e.room,
            "_teacher",
            lambda *args, **kwargs: Teaching("看这一句定义。[c1]", ("c1",)),
        )
        e.room._ask_direct(course, state, None)

        assert "mf-source:c1" in state["direct_review"]["question"]
        assert "mf-source:c0" not in state["direct_review"]["question"]
    finally:
        e.room.cancel_materials()
        e.store.close()


def test_concept_teacher_turn_bypasses_and_does_not_replace_append_cache(tmp_path, monkeypatch):
    e = concept_environment(tmp_path)
    monkeypatch.setattr(Classroom, "_materials", staticmethod(lambda: e.materials))
    page = AppendOnlyPage()
    sentinel = ("legacy-system", [{"role": "user", "content": "legacy source prefix"}], [], "accepted legacy reply")
    page._accepted = sentinel
    observed = []
    original_request = teacher_agent.teacher_request

    def capture_request(context):
        observed.append(context.get("request_page", "missing"))
        return original_request(context)

    monkeypatch.setattr(teacher_agent, "teacher_request", capture_request)
    try:
        course, state = _enter_concept(e)
        e.room.cancel_prefetch(PLAN)
        observed.clear()
        e.room._teacher_request_page = page

        e.room._teacher(course, state, None, "围绕当前小要点继续。", ["p0"])

        assert observed and all(value is None for value in observed)
        assert page._accepted is sentinel
    finally:
        e.room.cancel_prefetch(PLAN)
        e.room.cancel_materials()
        e.store.close()


def test_related_passage_ranking_deduplicates_shared_chunks(tmp_path):
    common = "needle concept alpha beta."
    unique = "needle."

    class FakeCourse:
        points = [dict(id=f"p{i}") for i in range(13)]

        def passages(self, point_ids):
            rows = {"aa_shared": {"text": common}}
            if point_ids[0] == "p12":
                rows["zz_unique"] = {"text": unique}
            return copy.deepcopy(rows)

    related = Classroom._related_passages(FakeCourse(), {}, "needle concept alpha beta", max_chars=100, max_passages=2)

    assert len(related) == 2
    assert "aa_shared" in related
    assert "zz_unique" in related
    assert len(set(related)) == len(related)


def test_related_passages158_preserves_legacy_long_paragraph_prefix():
    text='needle '+('无标点长段落'*1000)
    class FakeCourse:
        points=[dict(id='p0')]
        def passages(self,point_ids):return {'long':{'text':text}}
    assert Classroom._related_passages(FakeCourse(),{},'needle') == {'long':text[:4000]}
