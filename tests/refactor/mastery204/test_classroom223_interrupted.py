"""A committed cloze judgement survives a stale saved classroom position."""
import copy

from refactor.learning.app.classroom import Classroom
from tests.refactor.mastery70.test_question70_assessment_flow import (
    PLAN, DAY, NOW, act, env, position,
)
from tests.refactor.mastery204.test_classroom223_retired_queue import (
    immutable, retire_as_cloze,
)


def _settle(e, view):
    question = view['question']
    question_id = question['id']
    if question['kind'] == 'recall':
        act(e, 'recall_reveal', question_id, '复核自写原文回答')
        pending = e.store.classroom_position(PLAN)['state']['pending']['question']
        return act(e, 'recall_submit', question_id,
                   list(range(len(pending.get('key_points', [])))), True)
    return act(e, 'answer', [0])


def test_223_committed_cloze_answer_survives_stale_position_after_restart(env):
    e = env
    position(e, 'm0')
    view = act(e, 'start_test', 'module_test')
    cloze_id = view['question']['id']
    stale_position = copy.deepcopy(e.store.classroom_position(PLAN)['state'])

    _settle(e, view)
    retire_as_cloze(e, cloze_id)
    judged = e.store.records('judgements', PLAN)
    assert any(row['event'].get('question_id') == cloze_id for row in judged)
    immutable_before_resume = immutable(e)

    # Simulate a crash after the judgement transaction commits but before the
    # subsequent classroom-position save reaches disk.
    e.store.save_classroom_position(PLAN, stale_position)
    e.room = Classroom(e.services, lambda: DAY, now=lambda: NOW)
    e.room._prepare_next_module = lambda *args, **kwargs: None
    view = act(e, 'enter')

    state = e.store.classroom_position(PLAN)['state']
    assert view['question'] and view['question']['id'] != cloze_id
    assert cloze_id in state['test']['question_ids']
    assert immutable(e) == immutable_before_resume
