"""Saved pre-223 scenes migrate without replaying or losing recorded answers."""
import copy
import json

from refactor.learning.app.classroom import Classroom
from refactor.learning.course.store import _wire_id
from tests.refactor.mastery70.test_question70_assessment_flow import (
    env, act, answer, position, PLAN, DAY, NOW,
)


def retire_as_cloze(e, *ids):
    for q in e.store.records('question_bank', PLAN):
        if q['id'] in ids:
            # Seed a pre-migration row, not a newly admitted generated question.
            row = dict(q, origin='cloze')
            e.store._db.execute('UPDATE _records SET payload=? WHERE entity=? AND entity_id=?',
                (json.dumps(row), 'question_bank', _wire_id('question_bank', PLAN, q['id'])))
            e.store._materialize('question_bank', PLAN, row, 1)


def reopen(e):
    e.room = Classroom(e.services, lambda: DAY, now=lambda: NOW)
    e.room._prepare_next_module = lambda *args, **kwargs: None
    return act(e, 'enter')


def immutable(e):
    return {name: copy.deepcopy(e.store.records(name, PLAN)) for name in
            ('question_answers', 'judgements', 'attempt_closed', 'credits', 'ledger_events')}


def test_223_unanswered_cloze_removed_from_frozen_attempt_without_reset(env):
    e = env
    position(e, 'm0')
    view = act(e, 'start_test', 'module_test')
    view = answer(e, view)
    attempt = e.store.classroom_position(PLAN)['state']['test']['attempt']
    retire_as_cloze(e, 'q0-1', 'q0-3')
    before = immutable(e)
    view = reopen(e)
    assert view['question']['id'] == 'q0-2'
    saved = e.store.classroom_position(PLAN)['state']
    assert saved['test']['attempt'] == attempt
    assert saved['test']['question_ids'] == ['q0-0', 'q0-2', 'q0-4']
    assert immutable(e) == before
    while view['question']:
        view = answer(e, view)
    assert view['module']['state'] == 'passed'
    assert len(e.store.records('question_answers', PLAN)) == 3


def test_223_answered_cloze_grade_kept_when_resuming_original_pending(env):
    e = env
    position(e, 'm0')
    view = answer(e, act(e, 'start_test', 'module_test'), False)
    pending = copy.deepcopy(e.store.classroom_position(PLAN)['state']['pending'])
    retire_as_cloze(e, 'q0-0', 'q0-4')
    before = immutable(e)
    view = reopen(e)
    state = e.store.classroom_position(PLAN)['state']
    assert view['question']['id'] == pending['question']['id']
    assert state['pending']['answer_id'] == pending['answer_id']
    assert state['test']['question_ids'] == ['q0-0', 'q0-1', 'q0-2', 'q0-3']
    assert immutable(e) == before
    while view['question']:
        view = answer(e, view)
    assert view['module']['state'] == 'passed'
    assert e.store.load_mastery(PLAN).stats()['modules']['m0']['first_test']['correct_questions'] == 3
    assert e.store.load_mastery(PLAN).stats()['modules']['m0']['first_test']['total_questions'] == 4


def test_223_all_cloze_unanswered_direct_test_returns_to_learning(env):
    e = env
    position(e, 'm0', 'teach')
    act(e, 'start_test', 'skip_test')
    retire_as_cloze(e, *(f'q0-{i}' for i in range(5)))
    before = immutable(e)
    view = reopen(e)
    assert view['question'] is None
    assert view['step'] == 'teach'
    assert view['module']['state'] != 'passed'
    assert immutable(e) == before


def test_223_final_original_pending_is_unchanged_and_cloze_not_reoffered(env):
    e = env
    for mid in ('m0', 'm2'):
        position(e, mid)
        view = act(e, 'start_test', 'module_test')
        while view['question']:
            view = answer(e, view)
        if mid == 'm0':
            position(e, 'm1', 'closing')
            act(e, 'next')
    act(e, 'next_module')
    view = act(e, 'start_check')
    keep = view['question']['id']
    pending = copy.deepcopy(e.store.classroom_position(PLAN)['state']['pending'])
    retire_as_cloze(e, *(q['id'] for q in e.store.records('question_bank', PLAN) if q['id'] != keep))
    before = immutable(e)
    view = reopen(e)
    assert view['step'] == 'checking' and view['question']['id'] == keep
    assert e.store.classroom_position(PLAN)['state']['pending'] == pending
    assert e.store.get_plan(PLAN)['state'] != 'completed'
    assert immutable(e) == before


def test_223_final_pending_cloze_continues_with_original_question(env):
    e = env
    for mid in ('m0', 'm2'):
        position(e, mid)
        view = act(e, 'start_test', 'module_test')
        while view['question']:
            view = answer(e, view)
        if mid == 'm0':
            position(e, 'm1', 'closing')
            act(e, 'next')
    act(e, 'next_module')
    view = act(e, 'start_check')
    cloze = view['question']['id']
    retire_as_cloze(e, cloze)
    before = immutable(e)
    view = reopen(e)
    assert view['question'] and view['question']['id'] != cloze
    assert view['step'] == 'checking'
    assert immutable(e) == before


def test_223_practice_cloze_does_not_reset_its_saved_original_test(env):
    e = env
    position(e, 'm0', 'closing')
    act(e, 'next')
    position(e, 'm0')
    original = act(e, 'start_test', 'module_test')['question']['id']
    view = act(e, 'start_practice', 'm0', None, 2)
    # Pick a practice-only cloze, keeping the suspended module-test card original.
    if view['question']['id'] == original:
        view = answer(e, view)
    cloze = view['question']['id']
    assert cloze != original
    retire_as_cloze(e, cloze)
    before = immutable(e)
    view = reopen(e)
    assert view['question']['id'] != cloze and view['step'] == 'practicing'
    state = e.store.classroom_position(PLAN)['state']
    assert state['practice']['resume']['pending']['question']['id'] == original
    assert immutable(e) == before
