"""Offline tests for regroup mode in the plan replacement tool."""
from copy import deepcopy
from datetime import timedelta

import pytest

from refactor.learning.course.restructure import apply_structure
from refactor.learning.course.store import CourseStore
from tests.refactor.points158.proposed_contract158 import (
    allow_question_subpoint_ids, allow_stable_subpoint_ids,
)
from refactor.learning.course import question_bank
from tests.refactor.restructure114.test_restructure114_contract import (
    DAY, dump, seed,
)


def _as_concept_points(store):
    with store.transaction():
        for point in store.records('points', 'p'):
            sid = point['id'] + '-s1'
            point.update(structure='concept_v1', goal='掌握 ' + point['title'],
                         summary='掌握 ' + point['title'],
                         subpoints=[dict(id=sid, title='小要点 ' + point['id'], minutes=20,
                             chunk_ids=['c'], notes=[dict(text='原讲义 ' + point['id'], chunk_id='c')])])
            store._put('points', 'p', point)
        for question in store.records('question_bank', 'p'):
            question['subpoint_id'] = question['point_ids'][0] + '-s1'
            store._put('question_bank', 'p', question)


def _proposal(store):
    old = sorted(store.records('points', 'p'), key=lambda p: (p['position'], p['id']))
    # First group contains only protected children and must disappear. The
    # second groups the remaining small points without altering their content.
    first = [p['subpoints'][0] for p in old[:3]]
    second = [p['subpoints'][0] for p in old[3:]]
    second.reverse()  # Reversed proposal order must not reorder source teaching order.
    def big(pid, title, subs):
        return dict(id=pid, title=title, summary='重新分组目标', goal='重新分组目标',
                    structure='concept_v1', minutes=sum(s['minutes'] for s in subs),
                    chunk_ids=['c'], subpoints=[dict(s, title='改过的小要点标题', minutes=1,
                        chunk_ids=['invented-chunk'], notes=[dict(text='提案改写讲义', chunk_id='invented-chunk')])
                        for s in subs])
    return dict(plan_id='p', today=DAY.isoformat(), regroup_subpoints=True,
                modules=[dict(module_id='m', replace_point_ids=[p['id'] for p in old],
                    points=[big('new-empty', '只含保留点的组', first),
                            big('new-group', '迁移后的主题', second)], navigation=[])])


@pytest.fixture
def store(tmp_path):
    with CourseStore('account', tmp_path) as instance:
        seed(instance)
        _as_concept_points(instance)
        yield instance


def test_regroup158_proposed_contract_preserves_progress_current_scene_and_skips_empty_group(store):
    # Explicitly labelled proposed-contract test: all actual Store validation
    # remains active except the obsolete parent-prefix constraint.
    before_scene = deepcopy(store.classroom_position('p'))
    before_module = next(row for row in store.records('modules', 'p') if row['id'] == 'm')
    before_progress = {name: store.records(name, 'p') for name in
                       ('credits', 'judgements', 'question_answers', 'practice_results', 'ledger_events')}
    # A prior answer in the current point is immutable and stays byte-for-byte
    # unchanged while questions on untouched points move by subpoint id.
    answer = dict(id='answer-moved-question', module_id='m', at='2026-10-14T01:00:00+00:00',
                  context='module_test', correct=True, point_ids=['old2'], wrong_points=[])
    with store.transaction():
        store._put('question_answers', 'p', answer)
        retired = store._get('question_bank', 'p', 'old3')
        retired.update(role='retired', retirement=dict(version=8, reason='existing-history'))
        store._put('question_bank', 'p', retired)
    before_progress['question_answers'] = store.records('question_answers', 'p')
    before_questions = {q['id']: q for q in store.records('question_bank', 'p')}
    before_subpoints = {s['id']: deepcopy(s) for p in store.records('points', 'p') for s in p['subpoints']}

    proposal = _proposal(store)
    with allow_stable_subpoint_ids(store):
        preview = apply_structure(store, 'p', proposal, dry_run=True)
        assert preview['can_apply'], preview['issues']
        assert preview['regroup_subpoints'] is True
        assert preview['protected_subpoints'] == 3  # old0, old1 and the live cursor old2
        assert preview['empty_regroup_groups'] == ['new-empty']
        assert preview['regrouped_questions'] == 3
        apply_structure(store, 'p', proposal, dry_run=False)

    points = {p['id']: p for p in store.records('points', 'p')}
    assert set(points) == {'old0', 'old1', 'old2', 'new-group'}
    assert store.classroom_position('p') == before_scene
    after_progress = {name: store.records(name, 'p') for name in before_progress}
    for name in before_progress.keys() - {'ledger_events'}:
        assert after_progress[name] == before_progress[name]
    # The tool appends its expected schedule replan, but never edits historical
    # ledger events (the store returns newest-first).
    after_ledger_by_id = {row['id']: row for row in after_progress['ledger_events']}
    for event in before_progress['ledger_events']:
        assert after_ledger_by_id[event['id']] == event
    assert points['old0']['subpoints'][0] == before_subpoints['old0-s1']
    assert points['old1']['subpoints'][0] == before_subpoints['old1-s1']
    assert points['old2']['subpoints'][0] == before_subpoints['old2-s1']
    moved_subpoints = {s['id']: s for s in points['new-group']['subpoints']}
    assert set(moved_subpoints) == {'old3-s1', 'old4-s1', 'old5-s1'}
    assert [s['id'] for s in points['new-group']['subpoints']] == ['old3-s1', 'old4-s1', 'old5-s1']
    for sid, sub in moved_subpoints.items():
        assert {**sub, 'title': before_subpoints[sid]['title']} == before_subpoints[sid]
        assert sub['title'] == '改过的小要点标题'
    assert points['new-group']['minutes'] == sum(s['minutes'] for s in moved_subpoints.values())
    assert points['new-group']['chunk_ids'] == ['c']

    after_questions = {q['id']: q for q in store.records('question_bank', 'p')}
    for qid, original in before_questions.items():
        current = after_questions[qid]
        assert current['subpoint_id'] == original['subpoint_id']
        assert {k: v for k, v in current.items() if k != 'point_ids'} == {
            k: v for k, v in original.items() if k != 'point_ids'}
        expected = ['new-group'] if qid in {'old3', 'old4', 'old5'} else original['point_ids']
        assert current['point_ids'] == expected
    after_module = next(row for row in store.records('modules', 'p') if row['id'] == 'm')
    assert after_module.get('material_preparation') == before_module.get('material_preparation')
    assert after_module.get('question_extraction') == before_module.get('question_extraction')


def test_regroup158_real_store_rejects_parent_prefixed_smallpoint_ids(store):
    preview = apply_structure(store, 'p', _proposal(store), dry_run=True)
    assert not preview['can_apply']
    assert any('新点校验失败' in issue for issue in preview['issues'])
    assert preview['regrouped_questions'] == 3, preview


def test_regroup158_proposed_question_contract_retains_stable_sid(store):
    question = next(q for q in store.records('question_bank', 'p')
                    if q.get('subpoint_id') == 'old3-s1' and q.get('role') != 'retired')
    migrated = dict(question, point_ids=['new-group'])
    with pytest.raises(ValueError, match='小要点编号'):
        question_bank.validate_question(migrated)
    with allow_question_subpoint_ids():
        assert question_bank.validate_question(migrated) == migrated


def test_regroup158_proposed_contract_progress_added_after_proposal_is_protected(store):
    proposal = _proposal(store)
    # Simulate the user learning another point after generation of apply-ready.
    store.add_credit('p', 'learn-tomorrow-old4', 'old4', 10, DAY + timedelta(days=1), 'confirmed')
    with allow_stable_subpoint_ids(store):
        preview = apply_structure(store, 'p', proposal, dry_run=True)
    assert preview['can_apply'], preview['issues']
    assert 'old4' in preview['protected']
    assert 'old4-s1' in preview['regroup_details'][0]['preserved_subpoint_ids']
    assert preview['regrouped_questions'] == 2


def test_regroup158_future_progress_stays_before_appended_topics(store):
    proposal = _proposal(store)
    store.add_credit('p', 'future-credit-old4', 'old4', 10, DAY + timedelta(days=1), 'confirmed')
    with allow_stable_subpoint_ids(store):
        preview = apply_structure(store, 'p', proposal, dry_run=True)
        assert preview['can_apply'], preview['issues']
        apply_structure(store, 'p', proposal, dry_run=False)
    ordered = store.records('points', 'p')
    ids = [point['id'] for point in ordered]
    assert ids == ['old0', 'old1', 'old2', 'old4', 'new-group']
    assert ids.index('old4') < ids.index('new-group')


def test_regroup158_multipoint_question_preserves_unreplaced_owner(store):
    with store.transaction():
        extra = dict(id='old6', module_id='m', position=6, title='仍保留的旧点',
                     summary='不在本次取代范围', minutes=20, chunk_ids=['c'])
        store._put('points', 'p', extra)
        module = store.records('modules', 'p')[0]
        module.update(point_count=7, learning_minutes=140)
        store._put('modules', 'p', module)
    _as_concept_points(store)
    with store.transaction():
        base = next(q for q in store.records('question_bank', 'p') if q['id'] == 'old3')
        multi = dict(base, id='question-multi-owner', point_ids=['old3', 'old6'])
        store._put('question_bank', 'p', multi)
    proposal = _proposal(store)
    # old6 exists in this module but is deliberately outside this operation's
    # replacement scope. Its association must survive migration of old3-s1.
    proposal['modules'][0]['replace_point_ids'].remove('old6')
    proposal['modules'][0]['points'][1]['subpoints'] = [
        sub for sub in proposal['modules'][0]['points'][1]['subpoints']
        if not sub['id'].startswith('old6-')]
    proposal['modules'][0]['points'][1]['minutes'] = sum(
        sub['minutes'] for sub in proposal['modules'][0]['points'][1]['subpoints'])
    with allow_stable_subpoint_ids(store):
        preview = apply_structure(store, 'p', proposal, dry_run=True)
        assert preview['can_apply'], preview['issues']
        apply_structure(store, 'p', proposal, dry_run=False)
    migrated = store._get('question_bank', 'p', 'question-multi-owner')
    assert migrated['point_ids'] == ['new-group', 'old6']


def test_regroup158_default_mode_keeps_old_refusal_behavior(store):
    proposal = _proposal(store)
    proposal.pop('regroup_subpoints')
    preview = apply_structure(store, 'p', proposal, dry_run=True)
    assert not preview['can_apply']
    assert any('已有进度' in issue for issue in preview['issues'])


def test_regroup158_questions_without_subpoint_id_keep_their_original_point(store):
    with store.transaction():
        question = next(q for q in store.records('question_bank', 'p') if q['id'] == 'old3')
        question.pop('subpoint_id', None)
        store._put('question_bank', 'p', question)
    with allow_stable_subpoint_ids(store):
        preview = apply_structure(store, 'p', _proposal(store), dry_run=True)
    assert preview['can_apply'], preview['issues']
    assert question['point_ids'][0] in preview['protected']
