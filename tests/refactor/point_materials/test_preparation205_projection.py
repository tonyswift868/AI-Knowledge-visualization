"""205: compact module prep metadata and derive late questions read-only."""
import copy
from datetime import datetime, timedelta, timezone

from refactor.learning.course import point_materials as pm, question_bank as qb
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.restructure import apply_structure
from tests.refactor.point_materials.test_subpoint114_questions import (
    ctx, reviewer, store as seeded_store,
)


def _put(store, entity, row):
    with store.transaction():
        store._put(entity, 'p', row)


def test_205_module_marker_keeps_summary_not_unbounded_rejection_rows(seeded_store):
    point = seeded_store.records('points', 'p')[0]
    pm.prepare_point(seeded_store, 'p', point, ctx())
    module = seeded_store._get('modules', 'p', 'm')
    module['question_extraction'] = dict(state='partial', source_fingerprint='stale',
        rejections=[dict(subpoint_id='old-s1', reason='legacy row', raw='x' * 300)
                    for _ in range(1200)])
    _put(seeded_store, 'modules', module)

    result = pm.prepare_module(seeded_store, 'p', 'm',
        dict(finder=lambda *args, **kwargs: {'subpoints': []}, distractor_checker=reviewer),
        lambda *args: None)

    saved = seeded_store._get('modules', 'p', 'm')['question_extraction']
    assert 'rejections' not in saved
    assert saved['rejection_count'] == result['rejected']
    assert len(str(saved).encode('utf-8')) < 4096
    assert seeded_store._get('points', 'p', 'point')['subpoint_preparation']['rejections']


def test_205_supplemental_ids_are_new_valid_questions_not_original_test_answers(seeded_store):
    point = seeded_store.records('points', 'p')[0]
    pm.prepare_point(seeded_store, 'p', point, ctx())
    original = qb.assessment_questions(seeded_store.records('question_bank', 'p'), 'm')[0]
    answered_at = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    seeded_store.append_judgement('p', Judgement('answer-original', 1, answered_at,
        'module_test', 'm', 'attempt-1', (('point', True),), question_id=original['id']))
    seeded_store.append_attempt_closed('p', AttemptClosed('closed-original', 2, 'm',
        'attempt-1', 'module_test'))

    boundary = qb.module_supplemental_cutoff(seeded_store, 'p', 'm')
    assert boundary == dict(at=answered_at.isoformat(), basis='last_answer')
    module = seeded_store._get('modules', 'p', 'm')
    module['question_extraction'] = dict(state='done', supplemental_after=boundary['at'],
        supplemental_after_basis=boundary['basis'])
    _put(seeded_store, 'modules', module)

    supplemental = dict(original, id='new-after-close', stem='新增资料里的另一道检查题？',
        created_at=(answered_at + timedelta(seconds=1)).isoformat())
    assert seeded_store.add_bank_questions('p', 'm', [supplemental]) == ['new-after-close']
    assert qb.supplemental_question_ids(seeded_store, 'p', 'm') == {'new-after-close'}
    assert original['id'] not in qb.supplemental_question_ids(seeded_store, 'p', 'm')

    # Old module summaries have no persisted cutoff. A recorded answer gives
    # enough evidence to project later questions without writing metadata.
    module = seeded_store._get('modules', 'p', 'm')
    module['question_extraction'].pop('supplemental_after')
    module['question_extraction'].pop('supplemental_after_basis')
    _put(seeded_store, 'modules', module)
    assert qb.supplemental_question_ids(seeded_store, 'p', 'm') == {'new-after-close'}


def test_205_skip_close_without_answers_is_explicitly_preparation_start(seeded_store):
    seeded_store.append_attempt_closed('p', AttemptClosed('closed-skip', 1, 'm',
        'attempt-skip', 'skip_test'))
    boundary = qb.module_supplemental_cutoff(seeded_store, 'p', 'm')
    assert boundary['basis'] == 'preparation_start'
    assert datetime.fromisoformat(boundary['at']).tzinfo is not None
    assert qb.supplemental_question_ids(seeded_store, 'p', 'm') == set()


def test_205_summary_projects_current_valid_question_pool(seeded_store):
    point = seeded_store.records('points', 'p')[0]
    pm.prepare_point(seeded_store, 'p', point, ctx())
    base = seeded_store._get('points', 'p', 'point')
    for index in range(45):
        current = copy.deepcopy(base)
        point_id = 'point' if index == 0 else f'{index:032x}'
        count = 4 if index < 22 else 3  # 22*4 + 23*3 = the 157 current rejections.
        if index:
            current.update(id=point_id, position=index, title=f'当前结构点 {index}')
            for sub_index, sub in enumerate(current['subpoints'], 1):
                sub['id'] = f'{point_id}-s{sub_index}'
        current['subpoint_preparation'] = dict(state='done', fingerprint=f'current-{index}',
            rejections=[dict(subpoint_id=point_id + '-s1', reason=f'current rejection {i}') for i in range(count)])
        _put(seeded_store, 'points', current)
    module = seeded_store._get('modules', 'p', 'm')
    module.update(point_count=45, question_count=999, learning_minutes=450)
    module['question_extraction'] = dict(state='done', source_fingerprint='old-80-point-fingerprint',
        rejection_count=137, result=dict(points=80, questions=48, rejected=137))
    _put(seeded_store, 'modules', module)

    summary = pm.preparation_summary(seeded_store, 'p', 'm')
    valid = qb.assessment_questions(seeded_store.records('question_bank', 'p'), 'm')
    assert summary['questions'] == len(valid)
    assert summary['points'] == 45
    assert summary['rejected'] == 157
    assert summary['question_extraction_done'] is False

    module = seeded_store._get('modules', 'p', 'm')
    module['question_extraction']['source_fingerprint'] = pm._fingerprint(
        seeded_store.records('points', 'p'), pm._material_chunks(seeded_store, 'p')[1])
    _put(seeded_store, 'modules', module)
    assert pm.preparation_summary(seeded_store, 'p', 'm')['question_extraction_done'] is True


def test_205_reprepare_after_material_addition_keeps_answered_original_question_identity(seeded_store):
    no_cloze = lambda packet, *args, **kwargs: dict(subpoints=[
        dict(id=sub['id'], cloze=[]) for sub in packet['subpoints']])
    chunk = seeded_store._get('source_chunks', 'p', 'c1')
    chunk['text'] = '1. 计算 1+1=____。\n答案：2'
    _put(seeded_store, 'source_chunks', chunk)
    point = seeded_store.records('points', 'p')[0]
    pm.prepare_module(seeded_store, 'p', 'm', dict(finder=no_cloze), lambda *args: None)
    original = qb.assessment_questions(seeded_store.records('question_bank', 'p'), 'm')[0]
    answered_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    seeded_store.append_judgement('p', Judgement('answer-original', 1, answered_at,
        'module_test', 'm', 'attempt-1', (('point', True),), question_id=original['id']))
    seeded_store.append_attempt_closed('p', AttemptClosed('closed-original', 2, 'm',
        'attempt-1', 'module_test'))

    chunk = seeded_store._get('source_chunks', 'p', 'c2')
    chunk['text'] = '2. 计算 2+2=____。\n答案：4'
    _put(seeded_store, 'source_chunks', chunk)
    result = pm.prepare_module(seeded_store, 'p', 'm', dict(finder=no_cloze), lambda *args: None)

    bank = seeded_store.records('question_bank', 'p')
    new_ids = {q['id'] for q in bank} - {original['id']}
    answer = next(row for row in seeded_store.records('judgements', 'p')
                  if row['id'] == 'answer-original')
    assert answer['event']['question_id'] == original['id']
    assert len(new_ids) == 1
    assert result['supplemental_questions'] == 1
    assert qb.supplemental_question_ids(seeded_store, 'p', 'm') == new_ids
    assert len(qb.assessment_questions(bank, 'm')) == 2


def test_205_regroup_then_reprepare_keeps_question_and_answers_only_new_question_as_supplement(seeded_store):
    day = datetime(2026, 10, 18).date()
    seeded_store.start_plan('p', today=day)
    first_chunk = seeded_store._get('source_chunks', 'p', 'c1')
    first_chunk['text'] = '1. 计算 1+1=____。\n答案：2'
    _put(seeded_store, 'source_chunks', first_chunk)
    old_point = seeded_store._get('points', 'p', 'point')
    for index, sub in enumerate(old_point['subpoints'], 1):
        sub['notes'] = [dict(text=f'原文要点 {index}', chunk_id=sub['chunk_ids'][0])]
    _put(seeded_store, 'points', old_point)
    no_cloze = lambda packet, *args, **kwargs: dict(subpoints=[
        dict(id=sub['id'], cloze=[]) for sub in packet['subpoints']])
    pm.prepare_module(seeded_store, 'p', 'm', dict(finder=no_cloze), lambda *args: None)
    original = qb.assessment_questions(seeded_store.records('question_bank', 'p'), 'm')[0]

    incoming = copy.deepcopy(old_point)
    incoming.update(id='regrouped', title='按原文结构重组', summary='掌握同一批原文',
        goal='掌握同一批原文', minutes=sum(s['minutes'] for s in incoming['subpoints']),
        chunk_ids=list(dict.fromkeys(cid for s in incoming['subpoints'] for cid in s['chunk_ids'])))
    proposal = dict(plan_id='p', today='2026-10-19', regroup_subpoints=True,
        modules=[dict(module_id='m', replace_point_ids=['point'], points=[incoming], navigation=[])])
    preview = apply_structure(seeded_store, 'p', proposal, dry_run=True)
    assert preview['can_apply'], preview['issues']
    apply_structure(seeded_store, 'p', proposal, dry_run=False)

    migrated = next(q for q in seeded_store.records('question_bank', 'p') if q['id'] == original['id'])
    assert migrated['point_ids'] == ['regrouped']
    assert migrated['subpoint_id'] == 'regrouped-s1'
    at = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    seeded_store.append_judgement('p', Judgement('regroup-answer', 1, at, 'module_test',
        'm', 'regroup-attempt', (('regrouped', True),), question_id=original['id']))
    seeded_store.append_attempt_closed('p', AttemptClosed('regroup-close', 2, 'm',
        'regroup-attempt', 'module_test'))

    second_chunk = seeded_store._get('source_chunks', 'p', 'c2')
    second_chunk['text'] = '2. 计算 2+2=____。\n答案：4'
    _put(seeded_store, 'source_chunks', second_chunk)
    result = pm.prepare_module(seeded_store, 'p', 'm', dict(finder=no_cloze), lambda *args: None)

    bank = seeded_store.records('question_bank', 'p')
    assert sum(q['id'] == original['id'] for q in bank) == 1
    assert next(q for q in bank if q['id'] == original['id'])['subpoint_id'] == 'regrouped-s1'
    answer = next(row for row in seeded_store.records('judgements', 'p') if row['id'] == 'regroup-answer')
    assert answer['event']['question_id'] == original['id']
    supplemental = qb.supplemental_question_ids(seeded_store, 'p', 'm')
    assert len(supplemental) == 1 and original['id'] not in supplemental
    assert result['supplemental_questions'] == 1
