from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from grant_mastery204 import execute_keep_question, main, preview_keep_question
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.store import CourseStore, StoreError
from tests.refactor.new204 import test_grant_mastery204_tool as fixture204


def _ready(tmp_path):
    ids = fixture204._seed(tmp_path)
    with CourseStore(fixture204.ACCOUNT, tmp_path) as store:
        wrong = ids["m8"][-1]
        store.append_judgement(fixture204.PLAN, Judgement(
            "retake-m8-wrong", 200, datetime(2026, 10, 28, tzinfo=timezone.utc),
            "retest", "m8", "retake-m8", (("p8", True),), question_id=wrong))
        store.append_attempt_closed(fixture204.PLAN,
            AttemptClosed("retake-m8-closed", 201, "m8", "retake-m8", "retest"),
            details=dict(assessment_id="m8-attempt", question_ids=ids["m8"]))
        assert store.load_mastery(fixture204.PLAN).check_ready()
    return ids


def test_212_keep_preview_is_readonly_and_reports_one_answer_for_clean_recall(tmp_path, capsys):
    ids = _ready(tmp_path)
    db = fixture204._course_db(tmp_path)
    before = fixture204._db_digest(db)
    keep = ids["m6_old"][0]
    preview = preview_keep_question(tmp_path, fixture204.ACCOUNT, fixture204.PLAN, keep)
    assert fixture204._db_digest(db) == before
    assert preview["mode"] == "keep-question"
    assert preview["keep_question"]["module_correct"] is True
    assert preview["keep_question"]["ever_wrong"] is False
    assert preview["keep_question"]["correct_answers_required"] == 1
    assert all(row["question_id"] != keep for row in preview["questions_to_grant"])
    assert preview["will_complete_plan"] is False and preview["will_save_report"] is False
    wrong_keep = ids["m8"][-1]
    wrong_preview = preview_keep_question(tmp_path, fixture204.ACCOUNT, fixture204.PLAN, wrong_keep)
    assert wrong_preview["keep_question"]["module_correct"] is True
    assert wrong_preview["keep_question"]["ever_wrong"] is True
    assert wrong_preview["keep_question"]["correct_answers_required"] == 2

    assert main(["--data-root", str(tmp_path), "--account-id", fixture204.ACCOUNT,
        "--plan-id", fixture204.PLAN, "--keep-question-id", keep]) == 0
    assert '"dry_run": true' in capsys.readouterr().out
    assert fixture204._db_digest(db) == before


def test_212_confirm_grants_only_others_preserves_wrong_facts_and_syncs(tmp_path):
    ids = _ready(tmp_path)
    keep = ids["m6_old"][0]
    with CourseStore(fixture204.ACCOUNT, tmp_path) as store:
        before_events = store.records("judgements", fixture204.PLAN)
        before_answers = store.records("question_answers", fixture204.PLAN)
        before_credits = store.records("credits", fixture204.PLAN)
        wrong_ids = {row["event"]["question_id"] for row in before_events
            if row["event"].get("question_id") and
            row["event"].get("context") in ("module_test", "retest") and
            not all(correct for _, correct in row["event"].get("results", []))}

    outcome = execute_keep_question(tmp_path, fixture204.ACCOUNT, fixture204.PLAN, keep)
    assert Path(outcome["backup"]).is_file()
    assert keep not in outcome["newly_granted"]
    assert outcome["report_saved"] is False and outcome["plan_state"] == "active"
    with CourseStore(fixture204.ACCOUNT, tmp_path) as store:
        events = store.records("judgements", fixture204.PLAN)
        by_id = {row["id"]: row for row in events}
        assert all(by_id[row["id"]] == row for row in before_events)
        assert store.records("question_answers", fixture204.PLAN) == before_answers
        assert store.records("credits", fixture204.PLAN) == before_credits
        book = store.load_mastery(fixture204.PLAN)
        assert not book.question(keep).passed
        assert all(book.question(qid).passed for qid in set(ids["m6_old"] + ids["m6_new"] + ids["m8"]) - {keep})
        assert all(book.question(qid).ever_wrong for qid in wrong_ids)
        grants = [row for row in events if row.get("details", {}).get("grant_id") == outcome["grant_id"]]
        assert grants and all(row["details"]["override_kind"] == "final_check_passed" for row in grants)
        assert all(row["details"]["keep_question_id"] == keep for row in grants)
        pending = [row for row in store.pending_records(10000)
                   if row.entity == "judgements" and row.payload.get("details", {}).get("grant_id") == outcome["grant_id"]]
        assert len(pending) == len(grants)
        with CourseStore("replica212", tmp_path / "replica") as replica:
            replica.apply_remote([(row, i + 1) for i, row in enumerate(store.pending_records(10000))])
            assert replica.load_mastery(fixture204.PLAN).question(keep).passed is False
            assert all(replica.load_mastery(fixture204.PLAN).question(qid).passed
                       for qid in set(ids["m6_old"] + ids["m6_new"] + ids["m8"]) - {keep})

    # One correct learner answer passes a never-wrong, module-correct keep item.
    with CourseStore(fixture204.ACCOUNT, tmp_path) as store:
        store.append_judgement(fixture204.PLAN, Judgement("real-keep-answer", 300,
            datetime(2026, 10, 28, 1, tzinfo=timezone.utc), "check", "m6",
            results=(("p6", True),), question_id=keep))
        assert store.load_mastery(fixture204.PLAN).question(keep).passed
    repeat = execute_keep_question(tmp_path, fixture204.ACCOUNT, fixture204.PLAN, keep)
    assert repeat["already_applied"] is True and repeat["backup"] is None


def test_212_keep_refuses_initially_passed_item_and_rejects_changed_grant_scope(tmp_path):
    ids = _ready(tmp_path)
    with CourseStore(fixture204.ACCOUNT, tmp_path) as store:
        keep = ids["m6_old"][0]
        store.append_judgement(fixture204.PLAN, Judgement("pass-before-grant", 300,
            datetime(2026, 10, 28, 1, tzinfo=timezone.utc), "check", "m6",
            results=(("p6", True),), question_id=keep))
    try:
        preview_keep_question(tmp_path, fixture204.ACCOUNT, fixture204.PLAN, keep)
    except ValueError as exc:
        assert "已经通过" in str(exc)
    else:
        raise AssertionError("an already-passed keep question must be refused")

    # A newly added final item after a grant cannot be silently covered by the
    # old immutable grant identifier.
    keep = ids["m6_old"][1]
    outcome = execute_keep_question(tmp_path, fixture204.ACCOUNT, fixture204.PLAN, keep)
    with CourseStore(fixture204.ACCOUNT, tmp_path) as store:
        extra = fixture204._question("late-question", "m6", "p6")
        store.add_bank_questions(fixture204.PLAN, "m6", [extra])
    before = fixture204._db_digest(fixture204._course_db(tmp_path))
    try:
        preview_keep_question(tmp_path, fixture204.ACCOUNT, fixture204.PLAN, keep)
    except ValueError as exc:
        assert "新的未通过题" in str(exc)
    else:
        raise AssertionError("scope changes require a new explicit grant")
    with CourseStore(fixture204.ACCOUNT, tmp_path) as store:
        try:
            store.grant_final_check_questions_except(fixture204.PLAN, keep,
                grant_id=outcome["grant_id"])
        except StoreError as exc:
            assert exc.code == "GRANT_SCOPE_CHANGED"
        else:
            raise AssertionError("store must not extend the immutable grant to a new question")
        assert not store.load_mastery(fixture204.PLAN).question(extra["id"]).passed
    assert fixture204._db_digest(fixture204._course_db(tmp_path)) == before


def test_212_store_repeat_is_noop_after_keep_passes_and_completed_plan(tmp_path):
    ids = _ready(tmp_path)
    keep = ids["m6_old"][0]
    result = execute_keep_question(tmp_path, fixture204.ACCOUNT, fixture204.PLAN, keep)
    with CourseStore(fixture204.ACCOUNT, tmp_path) as store:
        store.append_judgement(fixture204.PLAN, Judgement("answer-keep-repeat", 300,
            datetime(2026, 10, 28, 1, tzinfo=timezone.utc), "check", "m6",
            results=(("p6", True),), question_id=keep))
        repeated = store.grant_final_check_questions_except(fixture204.PLAN, keep,
            grant_id=result["grant_id"])
        assert repeated["already_applied"] is True
        store.complete_plan(fixture204.PLAN, today=date(2026, 10, 28))
        assert store.get_plan(fixture204.PLAN)["state"] == "completed"
        store.save_report(fixture204.PLAN, {"summary": "Completed in synthetic test."})
        assert store.load_report(fixture204.PLAN) is not None
        repeated_completed = store.grant_final_check_questions_except(fixture204.PLAN, keep,
            grant_id=result["grant_id"])
        assert repeated_completed["already_applied"] is True
    before = fixture204._db_digest(fixture204._course_db(tmp_path))
    repeat = execute_keep_question(tmp_path, fixture204.ACCOUNT, fixture204.PLAN, keep)
    assert repeat["already_applied"] is True and repeat["backup"] is None
    assert repeat["plan_state"] == "completed" and repeat["report_saved"] is False
    assert repeat["keep_question"]["current_passed"] is True
    assert repeat["keep_question"]["correct_answers_required"] == 0
    assert fixture204._db_digest(fixture204._course_db(tmp_path)) == before


def test_212_repeat_grant_preserves_passed_item_and_late_wrong_fact(tmp_path):
    ids = _ready(tmp_path)
    keep = ids["m6_old"][0]
    outcome = execute_keep_question(tmp_path, fixture204.ACCOUNT, fixture204.PLAN, keep)
    granted_question = outcome["newly_granted"][0]
    with CourseStore(fixture204.ACCOUNT, tmp_path) as store:
        question = next(row for row in store.records("question_bank", fixture204.PLAN)
                        if row["id"] == granted_question)
        store.append_judgement(fixture204.PLAN, Judgement("later-wrong-on-grant", 300,
            datetime(2026, 10, 28, 1, tzinfo=timezone.utc), "check", question["module_id"],
            results=tuple((point_id, False) for point_id in question["point_ids"]),
            question_id=granted_question))
        # BUG-196: an old client's late answer remains a fact, but cannot put
        # an already-passed question back into the final-check pool.
        book = store.load_mastery(fixture204.PLAN)
        assert book.question(granted_question).passed
        assert not book.question(keep).passed
        assert book.next_check_questions() == [keep]
        events = store.records("judgements", fixture204.PLAN)
        late = next(row for row in events if row["event"]["event_id"] == "later-wrong-on-grant")
        assert late["event"]["question_id"] == granted_question
        assert late["event"]["results"] == [[point_id, False] for point_id in question["point_ids"]]

    before = fixture204._db_digest(fixture204._course_db(tmp_path))
    preview = preview_keep_question(tmp_path, fixture204.ACCOUNT, fixture204.PLAN, keep)
    assert preview["already_applied"] is True
    assert preview["questions_to_grant"] == []
    assert preview["keep_question"]["current_passed"] is False
    assert preview["currently_unpassed_count"] == 1

    with CourseStore(fixture204.ACCOUNT, tmp_path) as store:
        repeated = store.grant_final_check_questions_except(fixture204.PLAN, keep,
            grant_id=outcome["grant_id"])
        assert repeated["already_applied"] is True
        assert repeated["newly_granted"] == []
        assert store.records("judgements", fixture204.PLAN) == events
    assert fixture204._db_digest(fixture204._course_db(tmp_path)) == before
