"""206: concept-point material additions resume preparation and sync intact."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest

from refactor.learning.course import point_materials as pm, question_bank as qb
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.store import CourseStore
from tests.refactor.point_materials.test_subpoint114_questions import (
    ctx, store as seeded_store,
)


def _put(store, entity, row):
    with store.transaction():
        store._put(entity, 'p', row)


@pytest.mark.parametrize(('section', 'expected_subpoint'), [
    (['密度'], 'point-s2'),       # unique exact match to a child title
    (['没有对应章节'], 'point-s1'),  # stable point-level fallback
])
def test_concept_addition_resumes_only_changed_child_and_syncs(seeded_store, tmp_path,
                                                                 section, expected_subpoint):
    store = seeded_store
    store.start_plan('p', today=date(2026, 10, 14))
    point = store._get('points', 'p', 'point')

    # Give each child an existing, valid note before preparation. The old
    # exercise belongs to s3 so both exact-match and fallback targets are free
    # to receive a newly extracted exercise.
    for sub in point['subpoints']:
        old_chunk = sub['chunk_ids'][0]
        sub['notes'] = [dict(text=f"既有说明：{sub['title']}", chunk_id=old_chunk)]
    _put(store, 'points', point)
    old_question_text = '1. 计算 1+1=____。\n答案：2'
    old_chunk = store._get('source_chunks', 'p', 'empty')
    old_chunk.update(text=old_question_text, section=['旧练习'])
    _put(store, 'source_chunks', old_chunk)

    finder_calls = []

    def no_cloze(packet, passages, **_kwargs):
        finder_calls.append(tuple(sub['id'] for sub in packet['subpoints']))
        return dict(subpoints=[dict(id=sub['id'], cloze=[]) for sub in packet['subpoints']])

    preparation = dict(ctx(), finder=no_cloze)
    first = pm.prepare_module(store, 'p', 'm', preparation, lambda *_args: None)
    assert first['questions'] == 1
    original = qb.assessment_questions(store.records('question_bank', 'p'), 'm')
    assert len(original) == 1 and original[0]['subpoint_id'] == 'point-s3'
    original_id = original[0]['id']
    assert finder_calls == [('point-s1', 'point-s2')]

    # Record a completed detection after the first preparation. Make its
    # chronology deterministic relative to the new question created below.
    now = datetime.now(timezone.utc)
    old = dict(original[0], created_at=(now - timedelta(days=2)).isoformat())
    _put(store, 'question_bank', old)
    answered_at = now - timedelta(days=1)
    store.append_judgement('p', Judgement('answer-before-addition', 1, answered_at,
        'module_test', 'm', 'attempt-before-addition', (('point', True),),
        question_id=original_id))
    store.append_attempt_closed('p', AttemptClosed('close-before-addition', 2, 'm',
        'attempt-before-addition', 'module_test'))

    before_plan = store.get_plan('p')
    before_ledger = store.load_ledger('p').to_dict()
    before_events = store.records('ledger_events', 'p')
    before_answers = store.records('judgements', 'p')
    before_closes = store.records('attempt_closed', 'p')
    before_point = store._get('points', 'p', 'point')
    before_markers = deepcopy(before_point['subpoint_preparation']['processed_subpoints'])
    before_minutes = tuple(sub['minutes'] for sub in before_point['subpoints'])
    module_before = store._get('modules', 'p', 'm')
    old_module_fingerprint = module_before['question_extraction']['source_fingerprint']

    added_text = '补充说明：这段话为目标小要点增加一个可核对的例子。\n\n1. 计算 3+3=____。\n答案：6'
    store.add_sources('p', [dict(id='added-source', title='追加练习', adopted=True,
        url='https://example.invalid/addition', chunks=[dict(chunk_id='added-chunk',
        text=added_text, section=section)])])
    store.append_material_additions('p', [], [dict(point_id='point',
        chunk_ids=['added-chunk'])], ['m'], today=date(2026, 10, 14))

    after_add = store._get('points', 'p', 'point')
    target = next(sub for sub in after_add['subpoints'] if sub['id'] == expected_subpoint)
    assert target['chunk_ids'][-1] == 'added-chunk'
    assert target['notes'][:-1] == next(sub for sub in before_point['subpoints']
        if sub['id'] == expected_subpoint)['notes']
    assert target['notes'][-1] == dict(
        text='补充说明：这段话为目标小要点增加一个可核对的例子。',
        chunk_id='added-chunk')
    assert all('added-chunk' not in sub['chunk_ids'] for sub in after_add['subpoints']
               if sub['id'] != expected_subpoint)
    expected_union = list(dict.fromkeys(cid for sub in after_add['subpoints']
                                        for cid in sub['chunk_ids']))
    assert after_add['chunk_ids'] == expected_union
    assert after_add['question_preparation'] == 'pending'
    assert tuple(sub['minutes'] for sub in after_add['subpoints']) == before_minutes
    assert after_add['minutes'] == before_point['minutes']

    # New-source fingerprints must make the module and selected child stale,
    # while the untouched children's checkpoints remain reusable.
    result = pm.prepare_module(store, 'p', 'm', preparation, lambda *_args: None)
    assert finder_calls == [('point-s1', 'point-s2')]
    prepared = store._get('points', 'p', 'point')
    after_markers = prepared['subpoint_preparation']['processed_subpoints']
    assert after_markers[expected_subpoint]['input_fingerprint'] != before_markers[
        expected_subpoint]['input_fingerprint']
    for sid in {'point-s1', 'point-s2', 'point-s3'} - {expected_subpoint}:
        assert after_markers[sid] == before_markers[sid]

    bank = qb.assessment_questions(store.records('question_bank', 'p'), 'm')
    assert sum(q['id'] == original_id for q in bank) == 1
    added = [q for q in bank if q['id'] != original_id]
    assert len(added) == 1
    assert added[0]['subpoint_id'] == expected_subpoint
    assert '3+3' in added[0]['stem']
    module = store._get('modules', 'p', 'm')
    marker = module['question_extraction']
    assert marker['state'] == 'done'
    assert marker['source_fingerprint'] != old_module_fingerprint
    assert marker['supplemental_after'] == answered_at.isoformat()
    assert result['supplemental_questions'] == 1
    assert qb.supplemental_question_ids(store, 'p', 'm') == {added[0]['id']}

    # Adding and preparing material does not rewrite the completed answer,
    # detection history, negotiated schedule, or learning ledger.
    assert store.records('judgements', 'p') == before_answers
    assert store.records('attempt_closed', 'p') == before_closes
    assert store.get_plan('p')['total_minutes'] == before_plan['total_minutes']
    assert store.load_ledger('p').to_dict() == before_ledger
    assert store.records('ledger_events', 'p') == before_events

    # Exercise the existing RecordStore sync contract with a second real store.
    with CourseStore(store.account_id, tmp_path / 'second-device') as replica:
        records = store.pending_records(10000)
        replica.apply_remote([(record, index + 1) for index, record in enumerate(records)])
        for entity in ('modules', 'points', 'sources', 'source_chunks', 'question_bank',
                       'judgements', 'attempt_closed', 'ledger_events'):
            assert replica.records(entity, 'p') == store.records(entity, 'p')
        assert replica.get_plan('p') == store.get_plan('p')
        assert replica.load_ledger('p').to_dict() == store.load_ledger('p').to_dict()
        assert replica.pending_records(10000) == []
