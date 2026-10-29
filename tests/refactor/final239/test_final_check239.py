from datetime import datetime, timezone

from refactor.learning.course.mastery import (
    AttemptClosed,
    Judgement,
    MasteryBook,
    PointRef,
)


NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)


def _question(qid):
    return {"id": qid, "module_id": "m", "point_ids": ["p"]}


def _ready_book():
    book = MasteryBook([PointRef("p", "m")], [_question("q1"), _question("q2")])
    book.apply(Judgement("module-q1", 1, NOW, "module_test", "m", "module",
                         (("p", True),), question_id="q1"))
    book.apply(Judgement("module-q2", 2, NOW, "module_test", "m", "module",
                         (("p", True),), question_id="q2"))
    book.apply(AttemptClosed("module-close", 3, "m", "module", "module_test"),
               details={"assessment_id": "module", "question_ids": ["q1", "q2"]})
    assert book.check_ready()
    return book


def test_only_unpassed_question_repeats_without_using_passed_questions_as_spacers239():
    book = _ready_book()
    # q1 passes after its first correct final-check answer. q2 remains pending
    # after a wrong answer and must be repeated immediately, without q1 acting
    # as an artificial spacing question.
    book.apply(Judgement("check-q1", 4, NOW, "check", "m",
                         results=(("p", True),), question_id="q1"))
    # A stale card from an older client can still produce a late wrong event
    # for q1. Keep that fact without revoking its already-earned pass.
    book.apply(Judgement("check-q1-stale-wrong", 5, NOW, "check", "m",
                         results=(("p", False),), question_id="q1"))
    book.apply(Judgement("check-q2-wrong", 6, NOW, "check", "m",
                         results=(("p", False),), question_id="q2"))

    assert book.question("q1").passed is True
    assert book.question("q2").passed is False
    assert book.question("q1").check_questions == 2
    assert book.question("q1").check_correct == 1
    assert book.question("q1").ever_wrong is False
    assert book.stats()["questions"]["q1"]["check_errors_before_pass"] == 0
    assert book.next_check_questions(min_gap=3) == ["q2"]


def test_last_question_requires_two_consecutive_correct_answers_after_wrong239():
    book = _ready_book()
    book.apply(Judgement("check-q1", 4, NOW, "check", "m",
                         results=(("p", True),), question_id="q1"))
    book.apply(Judgement("check-q2-wrong", 5, NOW, "check", "m",
                         results=(("p", False),), question_id="q2"))

    assert book.next_check_questions(min_gap=3) == ["q2"]
    book.apply(Judgement("check-q2-correct-1", 6, NOW, "check", "m",
                         results=(("p", True),), question_id="q2"))
    assert book.question("q2").passed is False
    assert book.next_check_questions(min_gap=3) == ["q2"]
    book.apply(Judgement("check-q2-correct-2", 7, NOW, "check", "m",
                         results=(("p", True),), question_id="q2"))

    assert book.question("q2").passed is True
    assert book.check_complete()
    # A retry changes only the projection; every original answer remains in
    # the immutable event history and is included in the statistics.
    assert book.stats()["check_questions"] == 4
    assert book.question("q2").check_questions == 3
    assert book.question("q2").check_correct == 2
    assert book.question("q2").ever_wrong is True


def test_saved_check_card_is_dropped_when_synced_mastery_has_passed_it239(tmp_path):
    from types import SimpleNamespace
    from tests.refactor.direct97.support97 import make

    env = make(tmp_path, questions=2, points=2)
    try:
        class SyncedMastery:
            def question(self, question_id):
                return SimpleNamespace(passed=question_id == "q0-0")

        course = SimpleNamespace(book=lambda: SyncedMastery())
        state = {
            "phase": "check",
            "step": "checking",
            "pending": {"question": {"id": "q0-0"}, "revealed": False},
            "check": {"queue": [{"id": "q0-0"}], "index": 0},
            "check_used": 4,
        }

        resumed = env.room._reconcile_kept_check(course, state)

        assert resumed["pending"] is None
        assert resumed["check"] is None
        assert resumed["check_used"] == 0
        assert resumed["resume_kept_check"] is True
    finally:
        env.room.cancel_materials()
        env.store.close()


def test_final_check_progress_snapshot_uses_all_111_questions_not_point_count239(tmp_path):
    # Deliberately make the number of assessed points differ from the actual
    # question pool, matching the user's 111-question / 100+ point plan.
    from tests.refactor.direct97.support97 import PLAN, make

    env = make(tmp_path, questions=0, points=120)
    try:
        from tests.refactor.integration38.support import bank_question
        from refactor.learning.course.point_materials import _selection_passages

        source = next(row for row in _selection_passages(env.store, PLAN) if row['id'] == 'k1')['source']
        rows = []
        for index in range(111):
            row = bank_question('p0', 'm0', index=index, kind='fill', chunk='k1')
            row.update(stem=f'进度样本题目 {index} 的答案是____。',
                       source=source, answer_source=source, answer=[[f'答案{index}']])
            rows.append(row)
        env.store.add_bank_questions(PLAN, 'm0', rows)
        course = env.room._course(PLAN)
        state = env.room._rebuild(course)
        state.update(phase="check", step="checking")
        view = env.room.snapshot(course, state)

        assert len(course.points) == 121
        assert len(env.room._questions(course)) == 111
        assert view["check"]["total"] == 111
        assert view["check"]["passed"] == 0
    finally:
        env.room.cancel_materials()
        env.store.close()
