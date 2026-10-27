from datetime import datetime, timezone

from refactor.learning.course import question_bank
from refactor.learning.course.mastery import (
    AttemptClosed,
    Judgement,
    MasteryBook,
    PointRef,
    event_to_dict,
)


PLAN = "p"
MODULE = "m"
ATTEMPT = "first"
CUTOFF = "2026-10-27T12:00:00+00:00"


def _question(qid, created_at):
    return {
        "id": qid,
        "module_id": MODULE,
        "point_ids": ["point"],
        "kind": "objective",
        "role": "bank",
        "origin": "found",
        "created_at": created_at,
    }


class _Records:
    def __init__(self, modules, questions, judgements, closes):
        self.rows = {
            "modules": modules,
            "question_bank": questions,
            "judgements": judgements,
            "attempt_closed": closes,
            "question_answers": [],
        }

    def records(self, entity, _plan_id):
        return self.rows[entity]


def test_209_post_close_supplement_is_frozen_manifest_outside_and_final_eligible():
    tested_at = datetime(2026, 10, 27, 11, 59, tzinfo=timezone.utc)
    judgement = Judgement(
        "answer", 1, tested_at, "module_test", MODULE, ATTEMPT,
        (("point", True),), question_id="tested",
    )
    closed = AttemptClosed("closed", 2, MODULE, ATTEMPT, "module_test")
    rows = [
        {"event": event_to_dict(judgement)},
        {"event": event_to_dict(closed), "details": {"question_ids": ["tested"]}},
    ]
    bank = [
        _question("tested", "2026-10-27T11:00:00+00:00"),
        _question("late", "2026-10-27T12:01:00+00:00"),
    ]
    store = _Records(
        [{"id": MODULE, "question_extraction": {"supplemental_after": CUTOFF}}],
        bank,
        [{"event": event_to_dict(judgement)}],
        [{"event": event_to_dict(closed)}],
    )

    supplemental = question_bank.supplemental_question_ids(store, PLAN, MODULE)
    book = MasteryBook.from_course_records(
        [{"id": "point", "module_id": MODULE, "position": 0}],
        [{"id": MODULE, "position": 0}],
        bank,
        {"point"},
        rows,
    )

    assert supplemental == {"late"}
    assert book.module(MODULE).state == "passed"
    effective_ids = {
        q["id"] for q in question_bank.assessment_questions(bank, MODULE)
    }
    assert book._final_question_ids == effective_ids == {"tested", "late"}
    assert "late" in book.next_check_questions()


def test_209_preexisting_unasked_question_is_final_pool_but_not_post_close_supplement():
    tested_at = datetime(2026, 10, 27, 11, 59, tzinfo=timezone.utc)
    judgement = Judgement(
        "answer", 1, tested_at, "module_test", MODULE, ATTEMPT,
        (("point", True),), question_id="tested",
    )
    closed = AttemptClosed("closed", 2, MODULE, ATTEMPT, "module_test")
    rows = [
        {"event": event_to_dict(judgement)},
        {"event": event_to_dict(closed), "details": {"question_ids": ["tested"]}},
    ]
    bank = [
        _question("tested", "2026-10-27T11:00:00+00:00"),
        _question("reserve", "2026-10-27T11:30:00+00:00"),
        _question("late", "2026-10-27T12:01:00+00:00"),
    ]
    store = _Records(
        [{"id": MODULE, "question_extraction": {"supplemental_after": CUTOFF}}],
        bank,
        [{"event": event_to_dict(judgement)}],
        [{"event": event_to_dict(closed)}],
    )
    effective_ids = {
        q["id"] for q in question_bank.assessment_questions(bank, MODULE)
    }
    frozen_ids = {"tested"}
    book = MasteryBook.from_course_records(
        [{"id": "point", "module_id": MODULE, "position": 0}],
        [{"id": MODULE, "position": 0}],
        bank,
        {"point"},
        rows,
    )

    # Frozen IDs define the old denominator. Their complement is not itself a
    # supplement: it also includes a pre-existing reserve question.
    outside_frozen = effective_ids - frozen_ids
    assert outside_frozen == {"reserve", "late"}
    assert question_bank.supplemental_question_ids(store, PLAN, MODULE) == {"late"}
    # Both reserve and late remain final-check eligible; only late was added
    # after the original module assessment.
    assert book.module(MODULE).state == "passed"
    assert book._final_question_ids == effective_ids == {"tested", "reserve", "late"}
    assert {"reserve", "late"} <= set(book.next_check_questions())


def test_209_later_full_assessment_does_not_reset_first_supplement_boundary():
    first_at = datetime(2026, 10, 27, 11, 59, tzinfo=timezone.utc)
    first_judgement = Judgement(
        "first-answer", 1, first_at, "module_test", MODULE, ATTEMPT,
        (("point", True),), question_id="tested",
    )
    first_close = AttemptClosed("first-close", 2, MODULE, ATTEMPT, "module_test")
    late_judgement = Judgement(
        "later-answer", 3, datetime(2026, 10, 27, 12, 2, tzinfo=timezone.utc),
        "module_test", MODULE, "later-full-test", (("point", True),), question_id="late",
    )
    later_close = AttemptClosed("later-close", 4, MODULE, "later-full-test", "module_test")
    events = [first_judgement, first_close, late_judgement, later_close]
    rows = [
        {"event": event_to_dict(first_judgement)},
        {"event": event_to_dict(first_close), "details": {"question_ids": ["tested"]}},
        {"event": event_to_dict(late_judgement)},
        {"event": event_to_dict(later_close), "details": {"question_ids": ["tested", "late"]}},
    ]
    bank = [
        _question("tested", "2026-10-27T11:00:00+00:00"),
        _question("late", "2026-10-27T12:01:00+00:00"),
    ]
    store = _Records(
        [{"id": MODULE, "question_extraction": {"supplemental_after": CUTOFF}}],
        bank,
        [{"event": event_to_dict(event)} for event in (first_judgement, late_judgement)],
        [{"event": event_to_dict(event)} for event in (first_close, later_close)],
    )

    # The classifier is anchored to the first closed detection; later full
    # tests do not rewrite what was added after the original pass boundary.
    assert question_bank.supplemental_question_ids(store, PLAN, MODULE) == {"late"}
    book = MasteryBook.from_course_records(
        [{"id": "point", "module_id": MODULE, "position": 0}],
        [{"id": MODULE, "position": 0}],
        bank,
        {"point"},
        rows,
    )
    assert book.module(MODULE).state == "passed"
    assert book._final_question_ids == {"tested", "late"}
