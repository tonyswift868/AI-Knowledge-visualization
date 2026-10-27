"""209: additions, supplemental questions, mastery, and final-check entry compose."""
from datetime import date, datetime, timedelta, timezone
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.course import materials, point_materials, question_bank
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.store import CourseStore
from tests.refactor.integration38.support import Agents, bank_question


PLAN = "a" * 32
ACCOUNT = "joint209-offline"
TODAY = date(2026, 10, 27)
OLD_EXERCISE = "1. 计算 1+1=____。\n答案：2"
NEW_EXERCISE = "补充一条例题。\n\n1. 计算 3+3=____。\n答案：6"


class AdditionGateway(Agents):
    """Only the plan-additions model boundary is substituted."""
    def complete(self, purpose, messages, **_kwargs):
        if purpose == "plan_additions":
            self.calls.append(purpose)
            return SimpleNamespace(text=json.dumps({
                "supplements": [{"point_id": "concept", "chunk_ids": ["added-c"],
                                 "reason": "补充资料覆盖已有小要点"}],
                "new_modules": [], "overlaps": [], "ignored": [],
            }, ensure_ascii=False), search_hits=(), citations=())
        return super().complete(purpose, messages, **_kwargs)


def _mark_module_passed(store, module_id, question_id, *, seq):
    attempt = f"first-{module_id}"
    store.append_judgement(PLAN, Judgement(
        f"judgement-{module_id}", seq, datetime(2026, 10, 27, tzinfo=timezone.utc),
        "module_test", module_id, attempt, (("concept", True),), question_id=question_id))
    store.append_attempt_closed(PLAN, AttemptClosed(
        f"closed-{module_id}", seq + 1, module_id, attempt, "module_test"),
        details={"question_ids": [question_id], "assessment_id": attempt})


def _fixture(store_root, monkeypatch, *, reorder, pass_other=True):
    store = CourseStore(ACCOUNT, store_root)
    store.create_plan(PLAN, goal="理解资料", final_goal="掌握模块内容", level="beginner",
                      window=PlanWindow(TODAY, TODAY + timedelta(days=10), 120))
    store.add_sources(PLAN, [dict(id="old-source", url="https://example.invalid/old",
        title="旧资料", adopted=True, chunks=[dict(chunk_id="old-c", text=OLD_EXERCISE,
        section=["旧练习"]), dict(chunk_id="plain-c", text="一个清楚但没有习题的概念说明。",
        section=["概念说明"])])])
    store.save_outline(PLAN, [dict(id="stage", title="阶段")], [
        dict(id="m0", stage_id="stage", title="已有概念", prerequisites=[]),
        dict(id="m1", stage_id="stage", title="另一个模块", prerequisites=[]),
    ])
    store.save_generated_module(PLAN, "m0", [dict(
        id="concept", title="大知识点", summary="掌握原练习与补充资料",
        goal="掌握原练习与补充资料", structure="concept_v1", minutes=15,
        chunk_ids=["old-c"], subpoints=[dict(id="concept-s1", title="计算练习",
        minutes=15, chunk_ids=["old-c"], notes=[])])], check_minutes=2,
        final_check_minutes=2, check_question_minutes=2, today=TODAY)
    store.save_generated_module(PLAN, "m1", [dict(
        id="other", title="另一个点", summary="掌握旧资料", minutes=10,
        chunk_ids=["old-c"])], check_minutes=2, final_check_minutes=2,
        check_question_minutes=2, today=TODAY)
    # This original objective item supplies a frozen module-test manifest.
    original = bank_question("concept", "m0", index=0, kind="choice", chunk="old-c")
    original.update(id="original-question", subpoint_id="concept-s1", stem="原始模块检测题？",
                    options=["原题正确项", "原题干扰项"], answer=[0])
    store.add_bank_questions(PLAN, "m0", [original])
    store.save_module_preparation(PLAN, "m0", dict(state="done", question_count=1))
    other = None
    if pass_other:
        other = bank_question("other", "m1", index=0, kind="choice", chunk="old-c")
        other.update(id="other-question", stem="另一个模块检测题？",
                     options=["正确项", "干扰项"], answer=[0])
        store.add_bank_questions(PLAN, "m1", [other])
        store.save_module_preparation(PLAN, "m1", dict(state="done", question_count=1))
    if reorder:
        # Reordering is a structure edit and the store correctly rejects it
        # after start; establish the reordered route before study begins.
        store.reorder_modules(PLAN, ["m1", "m0"], today=TODAY)
    store.start_plan(PLAN, today=TODAY)
    _mark_module_passed(store, "m0", original["id"], seq=1)
    # m1's point is distinct, so its own first detection closes the route too.
    store.add_credit(PLAN, "learning-concept", "concept", 15, TODAY, "confirmed")
    if pass_other:
        attempt = "first-m1"
        store.append_judgement(PLAN, Judgement("judgement-m1", 3,
            datetime(2026, 10, 27, tzinfo=timezone.utc), "module_test", "m1", attempt,
            (("other", True),), question_id=other["id"]))
        store.append_attempt_closed(PLAN, AttemptClosed("closed-m1", 4, "m1", attempt,
            "module_test"), details={"question_ids": [other["id"]], "assessment_id": attempt})
        store.add_credit(PLAN, "learning-other", "other", 10, TODAY, "confirmed")

    def import_addition(staged, plan_id, _path, **_kwargs):
        staged.add_sources(plan_id, [dict(id="added-source", url="https://example.invalid/add",
            title="补充资料", adopted=True, chunks=[dict(chunk_id="added-c",
            text=NEW_EXERCISE, section=["计算练习"])])])
        return {"id": "added-source"}

    monkeypatch.setattr(materials, "import_file", import_addition)
    gateway = AdditionGateway()
    services = SimpleNamespace(store=store, gateway=gateway, account_id=ACCOUNT,
                               data_root=store_root,
                               settings=SimpleNamespace(load=lambda: dict(
                                   teacher_style="warm", detail="brief", examples="few",
                                   analogy_domains=["life"], refresh_time="04:00")))
    flow = PlanFlow(services, lambda *_args: None, lambda: TODAY)
    return store, flow, gateway, services, original, other


def _apply_real_addition(flow):
    flow._picked["source-token"] = [Path("supplement.txt")]
    analyzed = flow.additions_analyze(PLAN, "source-token",
        SimpleNamespace(cancel=threading.Event()))
    assert analyzed["preview"]["supplements"]
    exact = flow.additions_generate(analyzed["addition_id"], None, None,
        SimpleNamespace(cancel=threading.Event()))
    assert exact["exact"]
    return flow.additions_apply(analyzed["addition_id"], exact["confirmation_id"])


def _finder_without_cloze(packet, _passages, **_kwargs):
    # The production extraction/compiler runs normally; only external passage
    # locating is deterministic. Literal exercises in the source are retained.
    return dict(subpoints=[dict(id=row["id"], cloze=[]) for row in packet["subpoints"]])


def _answer_original_final_check(store, module_id, question_id, *, seq):
    store.append_judgement(PLAN, Judgement(
        f"final-old-{module_id}", seq, datetime(2026, 10, 27, tzinfo=timezone.utc),
        "check", module_id, f"final-old-{module_id}", (("concept" if module_id == "m0" else "other", True),),
        question_id=question_id))


def test_joint209_restructure_unstarted_module_then_preserve_passed_module_addition_final(
        tmp_path, monkeypatch):
    from refactor.learning.course.restructure import apply_structure

    store, flow, gateway, services, original, _other = _fixture(
        tmp_path, monkeypatch, reorder=False, pass_other=False)
    room = Classroom(services, lambda: TODAY,
        now=lambda: datetime(2026, 10, 27, 12, tzinfo=timezone.utc))
    room._prepare_next_module = lambda *_args, **_kwargs: None
    try:
        # A tested module cannot have its points replaced: the real restructure
        # guard protects every member named by a module-test close. Keep M0 as-is
        # and restructure only the still-untested M1 point.
        assert store.load_mastery(PLAN).module("m0").state == "passed"
        assert store.load_mastery(PLAN).module("m1").state == "untested"
        # The unstarted M1 is eligible for the 114 regroup contract. Give its
        # original point one mapped child; apply_structure must carry that ID,
        # not invent an unmapped child in the proposal.
        unstarted = store._get("points", PLAN, "other")
        unstarted.update(structure="concept_v1", goal="理解无题概念", summary="理解无题概念",
            chunk_ids=["plain-c"], subpoints=[dict(
                id="other-s1", title="无题概念", minutes=10, chunk_ids=["plain-c"],
                notes=[dict(text="这是无题概念的简短讲义。", chunk_id="plain-c")])])
        with store.transaction():
            store._put("points", PLAN, unstarted)
        plan_structure = dict(plan_id=PLAN, today=TODAY.isoformat(), modules=[dict(
            module_id="m1", replace_point_ids=["other"], navigation=[], points=[dict(
                id="m1-restructured", title="重排后的无题知识点", summary="理解无题概念",
                goal="理解无题概念", structure="concept_v1", minutes=10,
                chunk_ids=["plain-c"], subpoints=[dict(id="other-s1", title="无题概念")])])],
            regroup_subpoints=True)
        preview = apply_structure(store, PLAN, plan_structure, dry_run=True)
        assert preview["can_apply"], preview.get("issues")
        apply_structure(store, PLAN, plan_structure, dry_run=False)
        assert store.load_mastery(PLAN).module("m0").state == "passed"
        assert {p["id"] for p in store.records("points", PLAN)} == {"concept", "m1-restructured"}
        assert original["id"] in {q["id"] for q in store.records("question_bank", PLAN)}

        # M1's real preparation completes with no question because its source
        # is explanatory prose only; no replacement/fake question is introduced.
        prepared_m1 = point_materials.prepare_module(store, PLAN, "m1", {
            "finder": _finder_without_cloze,
        }, lambda *_args: None)
        assert prepared_m1["questions"] == 0
        assert point_materials.preparation_done(store, PLAN, "m1")
        store.save_module_preparation(PLAN, "m1", dict(state="done", question_count=0))

        old_m0_judgements = [r for r in store.records("judgements", PLAN)
                             if r["event"].get("module_id") == "m0"]
        old_m0_closes = [r for r in store.records("attempt_closed", PLAN)
                         if r["event"].get("module_id") == "m0"]
        old_m0_credits = [r for r in store.records("credits", PLAN)
                          if r["credit"].get("unit_id") == "concept"]
        old_ledger = store.load_ledger(PLAN).to_dict()

        # Route 206's real PlanFlow addition to the already-passed M0 concept
        # point; normal preparation resumes after its source fingerprint changes.
        result = _apply_real_addition(flow)
        assert result["supplement_count"] == 1
        prepared_after_addition = point_materials.prepare_module(store, PLAN, "m0", {
            "finder": _finder_without_cloze,
        }, lambda *_args: None)
        assert prepared_after_addition["supplemental_questions"] == 1
        assert old_m0_judgements == [r for r in store.records("judgements", PLAN)
                                     if r["event"].get("module_id") == "m0"]
        assert old_m0_closes == [r for r in store.records("attempt_closed", PLAN)
                                 if r["event"].get("module_id") == "m0"]
        assert old_m0_credits == [r for r in store.records("credits", PLAN)
                                  if r["credit"].get("unit_id") == "concept"]
        assert old_ledger == store.load_ledger(PLAN).to_dict()
        assert store.load_mastery(PLAN).module("m0").state == "passed"

        current_questions = question_bank.assessment_questions(store.records("question_bank", PLAN), "m0")
        added_questions = [q for q in current_questions
                           if "added-c" in (q.get("source") or {}).get("chunk_ids", [])]
        assert len(added_questions) == 1, "补充资料中的原题应进入当前 assessment bank"
        supplement_ids = question_bank.supplemental_question_ids(store, PLAN, "m0")
        assert supplement_ids == {added_questions[0]["id"]}

        # M1 is a genuine no-question module. The classroom's explicit direct
        # path asks the learner once, then lets them choose to skip; this writes
        # the real skip fact and makes the final-check offer reachable.
        ctx = SimpleNamespace(cancel=threading.Event(), stream=lambda *_args: None)
        room._save(room._course(PLAN), room._rebuild(room._course(PLAN)))
        test_offer = room.perform(PLAN, "start_test", ctx, ("skip_test",))
        assert test_offer["step"] == "direct_review"
        reviewed = room.perform(PLAN, "reply", ctx, ("我理解了这个概念。",))
        assert reviewed["step"] == "direct_choice"
        room.perform(PLAN, "direct_choice", ctx, (True,))
        final_offer = room.perform(PLAN, "next_module", ctx)
        assert final_offer["step"] == "check_offer"
        assert room._course(PLAN).book().module("m0").state == "passed"
        assert room._course(PLAN).book().module("m1").state == "skipped"

        # Only the supplemental M0 question remains unanswered in the actual
        # final pool; the normal Classroom entry must present it.
        _answer_original_final_check(store, "m0", original["id"], seq=50)
        expected = added_questions[0]
        assert expected["id"] in room._course(PLAN).book().next_check_questions(min_gap=3)
        view = room.perform(PLAN, "start_check", ctx)
        assert view["step"] == "checking"
        assert view["question"]["id"] == expected["id"]
        assert room._course(PLAN).book().module("m0").state == "passed"
        assert question_bank.supplemental_question_ids(store, PLAN, "m0") == supplement_ids
        assert gateway.calls.count("plan_additions") == 1
    finally:
        room.cancel_materials(PLAN)
        store.close()


@pytest.mark.parametrize("reorder", [False, True], ids=["original-order", "after-reorder"])
def test_joint209_addition_after_pass_keeps_pass_and_routes_supplement_to_final(
        tmp_path, monkeypatch, reorder):
    store, flow, gateway, services, original, other = _fixture(tmp_path, monkeypatch,
                                                                 reorder=reorder)
    room = Classroom(services, lambda: TODAY,
                     now=lambda: datetime(2026, 10, 27, 12, tzinfo=timezone.utc))
    try:
        before_judgements = store.records("judgements", PLAN)
        before_closes = store.records("attempt_closed", PLAN)
        before_credits = store.records("credits", PLAN)
        before_ledger = store.load_ledger(PLAN).to_dict()
        before_module_passes = {mid: store.load_mastery(PLAN).module(mid).state
                                 for mid in ("m0", "m1")}
        assert before_module_passes == {"m0": "passed", "m1": "passed"}

        result = _apply_real_addition(flow)
        assert result["supplement_count"] == 1
        assert gateway.calls == ["plan_additions"]
        prepared = point_materials.prepare_module(store, PLAN, "m0", {
            "finder": _finder_without_cloze,
        }, lambda *_args: None)
        assert prepared["supplemental_questions"] == 1
        module = next(row for row in store.records("modules", PLAN) if row["id"] == "m0")
        assert module["question_extraction"]["state"] == "done"

        new_questions = [q for q in question_bank.assessment_questions(
            store.records("question_bank", PLAN), "m0") if q["id"] != original["id"]]
        assert len(new_questions) == 1
        supplemental_ids = question_bank.supplemental_question_ids(store, PLAN, "m0")
        assert supplemental_ids == {new_questions[0]["id"]}
        assert original["id"] not in supplemental_ids
        assert store.load_mastery(PLAN).module("m0").state == "passed"
        assert store.load_mastery(PLAN).module("m1").state == "passed"

        # Preserve every pre-existing progress fact byte-for-byte; adding and
        # preparing material must not reopen or re-score a passed module.
        assert store.records("judgements", PLAN) == before_judgements
        assert store.records("attempt_closed", PLAN) == before_closes
        assert store.records("credits", PLAN) == before_credits
        assert store.load_ledger(PLAN).to_dict() == before_ledger

        # Clear the old questions from the final pool so the live Classroom
        # entry must serve the newly-added supplemental item.
        _answer_original_final_check(store, "m0", original["id"], seq=20)
        _answer_original_final_check(store, "m1", other["id"], seq=21)
        course = room._course(PLAN)
        assert new_questions[0]["id"] in course.book().next_check_questions(min_gap=3)
        state = room._rebuild(course)
        assert state["step"] == "check_offer"
        view = room.perform(PLAN, "start_check", SimpleNamespace(
            cancel=threading.Event(), stream=lambda *_args: None))
        assert view["step"] == "checking"
        assert view["question"] is not None
        assert view["question"]["id"] == new_questions[0]["id"]
        assert room._course(PLAN).book().module("m0").state == "passed"
        assert question_bank.supplemental_question_ids(store, PLAN, "m0") == supplemental_ids

        # Re-entering the final-check action while the persisted item is still
        # unanswered resumes that item; it does not route back to a passed
        # module or spin up another preparation cycle.
        resumed = room.perform(PLAN, "start_check", SimpleNamespace(
            cancel=threading.Event(), stream=lambda *_args: None))
        assert resumed["step"] == "checking"
        assert resumed["question"]["id"] == new_questions[0]["id"]
        assert gateway.calls == ["plan_additions"]
    finally:
        room.cancel_materials(PLAN)
        store.close()
