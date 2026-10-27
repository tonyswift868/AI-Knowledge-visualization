"""Synthetic-copy checks for the one-time BUG-131 operator tool."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from grant_mastery204 import execute, main, preview

from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.store import CourseStore


ACCOUNT = "offline204"
PLAN = "plan204"
DAY = date(2026, 10, 27)


def _question(identifier: str, module_id: str, point_id: str) -> dict:
    source = dict(title="isolated source", url="https://example.invalid/source", locator="paragraph 1")
    return dict(id=identifier, module_id=module_id, point_ids=[point_id], kind="choice",
        stem=f"Question {identifier}?", answer=[0], options=["supported", "unsupported"],
        explanation="", origin="found", source=source, answer_source=source,
        importance="key", role="bank", uses=[], created_at="2026-10-27T00:00:00+00:00",
        distractor_check=dict(state="passed", checks=[dict(option="unsupported", index=1,
            verdict="not_supported", quote="", removed=False)], reason="", at="2026-10-27T00:00:00+00:00"))


def _seed(data_root: Path, *, m6_count=48, m6_correct=46, m8_count=5, m8_correct=4,
          new_m6_count=30, add_answer_facts=True) -> dict:
    store = CourseStore(ACCOUNT, data_root)
    store.create_plan(PLAN, goal="isolated plan", final_goal="complete a test plan", level="basic",
                      window=PlanWindow(DAY, DAY + timedelta(days=10), 120), total_minutes=60)
    store.add_sources(PLAN, [dict(id="source", url="https://example.invalid/source", title="source",
        adopted=True, chunks=[dict(chunk_id="chunk", text="A source sentence.")])])
    store.save_outline(PLAN, [dict(id="stage", title="stage")], [
        dict(id="m6", stage_id="stage", title="Module 6"),
        dict(id="m8", stage_id="stage", title="Module 8"),
    ])
    for module_id, point_id in (("m6", "p6"), ("m8", "p8")):
        store.save_generated_module(PLAN, module_id,
            [dict(id=point_id, title=point_id, summary="mastery goal", minutes=10, chunk_ids=["chunk"])],
            check_minutes=5, today=DAY, final_check_minutes=5, check_question_minutes=2)
    old_m6 = [_question(f"m6-old-{i}", "m6", "p6") for i in range(m6_count)]
    old_m8 = [_question(f"m8-q-{i}", "m8", "p8") for i in range(m8_count)]
    new_m6 = [_question(f"m6-new-{i}", "m6", "p6") for i in range(new_m6_count)]
    store.add_bank_questions(PLAN, "m6", old_m6 + new_m6)
    store.add_bank_questions(PLAN, "m8", old_m8)
    store.start_plan(PLAN, today=DAY)
    for module_id, point_id, attempt_id, rows, correct_count in (
            ("m6", "p6", "m6-attempt", old_m6, m6_correct),
            ("m8", "p8", "m8-attempt", old_m8, m8_correct)):
        qids = [row["id"] for row in rows]
        for index, question in enumerate(rows):
            correct = index < correct_count
            event = Judgement(f"{attempt_id}-answer-{index}", index + 1,
                datetime(2026, 10, 27, 1, index, tzinfo=timezone.utc), "module_test", module_id,
                attempt_id=attempt_id, results=((point_id, correct),), question_id=question["id"])
            store.append_judgement(PLAN, event)
            if add_answer_facts:
                store.append_question_answer(PLAN, f"fact-{attempt_id}-{index}", module_id=module_id,
                    context="module_test", attempt_id=attempt_id, question_id=question["id"],
                    point_ids=[point_id], angle=None, correct=correct,
                    answer="recorded learner response", question=question,
                    at=datetime(2026, 10, 27, 1, index, tzinfo=timezone.utc))
        closed = AttemptClosed(f"{attempt_id}-closed", len(rows) + 2, module_id, attempt_id, "module_test")
        store.append_attempt_closed(PLAN, closed, details=dict(assessment_id=attempt_id, question_ids=qids))
    store.add_credit(PLAN, "existing-credit", "learn:p6", 4, DAY, "tentative")
    store.close()
    return dict(m6_old=[q["id"] for q in old_m6], m6_new=[q["id"] for q in new_m6],
                m8=[q["id"] for q in old_m8])


def _db_digest(path: Path) -> tuple[tuple[str, str], ...]:
    result = []
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.exists():
            result.append((candidate.name, hashlib.sha256(candidate.read_bytes()).hexdigest()))
    return tuple(result)


def _course_db(data_root: Path) -> Path:
    return data_root / "learning" / "accounts" / ACCOUNT / "course.sqlite3"


def test_204_preview_is_readonly_and_projects_frozen_m6_and_failed_m8(tmp_path, capsys):
    ids = _seed(tmp_path)
    db_path = _course_db(tmp_path)
    before = _db_digest(db_path)
    result = preview(tmp_path, ACCOUNT, PLAN, "m6", ids["m6_new"], False)
    after = _db_digest(db_path)

    assert before == after
    assert result["mode"] == "questions"
    assert result["module"]["current_state"] == "passed"  # 46/48, frozen original test.
    assert result["module"]["projected_state"] == "passed"
    assert len(result["questions"]) == len(ids["m6_new"])
    assert all(row["projected_passed"] for row in result["questions"])
    assert all(not row["current_passed"] for row in result["questions"])
    assert preview(tmp_path, ACCOUNT, PLAN, "m8", [ids["m8"][-1]], False)["module"]["current_state"] == "failed"
    assert main(["--data-root", str(tmp_path), "--account-id", ACCOUNT, "--plan-id", PLAN,
                 "--module-id", "m6", "--question-id", ids["m6_new"][0]]) == 0
    output = capsys.readouterr().out
    assert '"dry_run": true' in output and "预演未写数据库" in output
    assert _db_digest(db_path) == before


def test_204_confirm_backs_up_preserves_history_is_idempotent_and_syncs(tmp_path):
    from refactor.learning.course.store import CourseStore

    ids = _seed(tmp_path)
    db_path = _course_db(tmp_path)
    with CourseStore(ACCOUNT, tmp_path) as before_store:
        before_judgements = before_store.records("judgements", PLAN)
        before_answers = before_store.records("question_answers", PLAN)
        before_credits = before_store.records("credits", PLAN)
        before_outbox = len(before_store.pending_records(10000))

    outcome = execute(tmp_path, ACCOUNT, PLAN, "m6", ids["m6_new"], False)
    backup = Path(outcome["backup"])
    assert backup.parent == db_path.parent and backup.is_file()
    from grant_mastery204 import _readonly
    with _readonly(backup) as backup_db:
        assert backup_db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup_db.execute("SELECT count(*) FROM question_answers").fetchone()[0] == len(before_answers)
    assert outcome["newly_granted"] == ids["m6_new"]

    with CourseStore(ACCOUNT, tmp_path) as store:
        after_judgements = {row["id"]: row for row in store.records("judgements", PLAN)}
        assert all(after_judgements[row["id"]] == row for row in before_judgements)
        assert store.records("question_answers", PLAN) == before_answers
        assert store.records("credits", PLAN) == before_credits
        book = store.load_mastery(PLAN)
        assert book.module("m6").state == "passed"
        assert all(book.question(qid).passed for qid in ids["m6_new"])
        grants = [row for row in store.records("judgements", PLAN)
                  if row.get("details", {}).get("user_override") is True]
        assert len(grants) == len(ids["m6_new"])
        assert len(store.pending_records(10000)) > before_outbox
        pending_grants = [row for row in store.pending_records(10000)
                          if row.entity == "judgements" and row.payload.get("details", {}).get("user_override")]
        assert len(pending_grants) == len(ids["m6_new"])
        with CourseStore(ACCOUNT, tmp_path / "replica") as replica:
            replica.apply_remote([(row, index + 1) for index, row in enumerate(store.pending_records(10000))])
            assert replica.load_mastery(PLAN).module("m6").state == "passed"
            assert all(replica.load_mastery(PLAN).question(qid).passed for qid in ids["m6_new"])
            assert replica.records("question_answers", PLAN) == before_answers

    repeat = execute(tmp_path, ACCOUNT, PLAN, "m6", ids["m6_new"], False)
    assert repeat["already_applied"] is True and repeat["backup"] is None
    first_backup_bytes = backup.read_bytes()
    # Module 8 remains untouched unless the operator explicitly selects its
    # real wrong item. That explicit path exists for a later user decision.
    m8_wrong = ids["m8"][-1]
    explicit_m8 = execute(tmp_path, ACCOUNT, PLAN, "m8", [m8_wrong], False)
    second_backup = Path(explicit_m8["backup"])
    assert second_backup != backup and backup.read_bytes() == first_backup_bytes
    with CourseStore(ACCOUNT, tmp_path) as store:
        assert len([row for row in store.records("judgements", PLAN)
                    if row.get("details", {}).get("user_override") is True]) == len(ids["m6_new"]) + 1
        assert store.load_mastery(PLAN).module("m8").state == "passed"
        wrong_after_grant = store.load_mastery(PLAN).question(m8_wrong)
        assert wrong_after_grant.module_correct is True and wrong_after_grant.ever_wrong is True
        assert len(list(db_path.parent.glob("course.sqlite3.before-manual-204-*.bak"))) >= 2


def test_204_final_mode_refuses_unpassed_module_then_uses_completion_and_report(tmp_path):
    ids = _seed(tmp_path)
    before = preview(tmp_path, ACCOUNT, PLAN, None, [], True)
    assert before["check_ready"] is False
    assert not before["will_complete_plan_and_save_report"]
    with CourseStore(ACCOUNT, tmp_path) as before_store:
        before_events = before_store.records("judgements", PLAN)
        before_answers = before_store.records("question_answers", PLAN)
        before_credits = before_store.records("credits", PLAN)
    try:
        execute(tmp_path, ACCOUNT, PLAN, None, [], True)
    except Exception as exc:
        assert getattr(exc, "code", None) == "CHECK_NOT_READY"
    else:
        raise AssertionError("final grant must not silently forgive the genuinely failed module")
    with CourseStore(ACCOUNT, tmp_path) as after_store:
        assert after_store.records("judgements", PLAN) == before_events
        assert after_store.records("question_answers", PLAN) == before_answers
        assert after_store.records("credits", PLAN) == before_credits
        assert after_store.load_mastery(PLAN).module("m8").state == "failed"

    # In the synthetic copy only, record the user's explicitly imagined later
    # correction for M8 through normal events, then exercise the future final mode.
    with CourseStore(ACCOUNT, tmp_path) as store:
        qid = ids["m8"][-1]
        event = Judgement("m8-final-retry", 100, datetime(2026, 10, 27, 3, tzinfo=timezone.utc),
            "retest", "m8", attempt_id="m8-retest", results=(("p8", True),), question_id=qid)
        store.append_judgement(PLAN, event)
        store.append_attempt_closed(PLAN,
            AttemptClosed("m8-retest-closed", 101, "m8", "m8-retest", "retest"),
            details=dict(assessment_id="m8-attempt", question_ids=ids["m8"]))
        assert store.load_mastery(PLAN).check_ready()

    ready = preview(tmp_path, ACCOUNT, PLAN, None, [], True)
    assert ready["check_ready"] and ready["will_complete_plan_and_save_report"]
    completed = execute(tmp_path, ACCOUNT, PLAN, None, [], True)
    assert completed["plan_state"] == "completed" and completed["report_saved"]
    with CourseStore(ACCOUNT, tmp_path) as store:
        assert store.get_plan(PLAN)["state"] == "completed"
        assert store.load_mastery(PLAN).check_complete()
        report = store.load_report(PLAN)
        assert report["plan"]["plan_id"] == PLAN
        assert store.records("question_answers", PLAN)  # only the prior real-looking synthetic facts
    repeated = execute(tmp_path, ACCOUNT, PLAN, None, [], True)
    assert repeated["already_applied"] is True and repeated["backup"] is None

