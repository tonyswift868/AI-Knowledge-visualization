"""BUG-41: marking an on-the-spot question (quick check, deepen) correct must not fail."""
from refactor.learning.course.question_agent import Question
from tests.refactor.app.test_app_classroom import PLAN, room  # noqa: F401  (pytest fixture)


def test_marking_an_off_bank_question_correct_is_a_no_op(room):
    _classroom, _agents, store = room
    store.mark_bank_correct(PLAN, 'quick-check-q1')          # not in the bank: no StoreError
    q = Question('bank-1', 'short', '请解释作用？', (), '答案', '要点', ('p0',), ('k1',), 'recall')
    qid = store.add_bank_questions(PLAN, 'm0', [q])[0]
    store.mark_bank_correct(PLAN, qid)
    row = next(r for r in store.records('question_bank', PLAN) if r['id'] == qid)
    assert row['correct_seen'] is True
