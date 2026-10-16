"""159-MF06: preparation markers survive concept-point regrouping."""
from datetime import date, timedelta

from refactor.learning.course import point_materials as pm
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore


def _store(tmp_path, *, point_id='old', subpoints=None, marker=None):
    store = CourseStore('prep159', tmp_path)
    today = date(2026, 10, 17)
    store.create_plan('p', goal='测试', window=PlanWindow(today, today + timedelta(days=2), 100))
    store.add_sources('p', [dict(id='src', title='教材', adopted=True, url='https://example.test', chunks=[
        dict(chunk_id='c1', text='甲概念的原文。', section=['甲']),
        dict(chunk_id='c2', text='乙概念的原文。', section=['乙']),
    ])])
    store.save_outline('p', [dict(id='stage', title='阶段')],
                       [dict(id='m', stage_id='stage', title='模块', prerequisites=[])])
    subs = subpoints or [
        dict(id=f'{point_id}-s1', title='甲', minutes=5, chunk_ids=['c1'], notes=[]),
        dict(id=f'{point_id}-s2', title='乙', minutes=5, chunk_ids=['c2'], notes=[]),
    ]
    union = []
    for sub in subs:
        for cid in sub['chunk_ids']:
            if cid not in union: union.append(cid)
    point = dict(id=point_id, title='点', summary='目标', minutes=sum(s['minutes'] for s in subs),
                 chunk_ids=union, structure='concept_v1', goal='目标', subpoints=subs)
    if marker:
        point['subpoint_preparation'] = marker
    store.save_generated_module('p', 'm', [point], check_minutes=5, today=today)
    return store


def _retired_row():
    return dict(id='retired159', module_id='m', point_ids=['old'], subpoint_id='old-s1',
                kind='objective', stem='旧题', answer='甲', options=[], origin='cloze',
                source={'url': 'https://example.test', 'locator': '[c1]'},
                answer_source={'url': 'https://example.test', 'locator': '[c1]'},
                role='retired', uses=[], retirement={'version': 8, 'preparation_reset': False,
                                                    'previous_role': 'bank', 'reason': '测试'})


def test_prep159_retired_and_explicit_rejection_are_terminal(tmp_path):
    marker = dict(state='done', fingerprint='old-fingerprint', result={},
                  rejections=[dict(subpoint_id='old-s2', reason='没有合格的原文挖空候选')])
    store = _store(tmp_path, marker=marker)
    with store.transaction():
        store._put('question_bank', 'p', _retired_row())
    calls = []
    result = pm.prepare_point(store, 'p', store.records('points', 'p')[0],
                              {'finder': lambda *a, **k: calls.append(1)})
    assert result['questions'] == 0 and result['skipped'] == 2
    assert calls == []
    saved = store.records('points', 'p')[0]['subpoint_preparation']
    assert {v['status'] for v in saved['processed_subpoints'].values()} == {'retired', 'rejected'}
    store.close()


def test_prep159_moved_subpoint_uses_legacy_marker_without_model(tmp_path):
    marker = dict(state='done', fingerprint='old-fingerprint', result={}, rejections=[],
                  processed_subpoints={'old-s1': {'subpoint_id': 'old-s1', 'status': 'rejected'}})
    subs = [dict(id='new-s1', legacy_subpoint_id='old-s1', title='甲', minutes=5,
                 chunk_ids=['c1'], notes=[])]
    store = _store(tmp_path, point_id='new', subpoints=subs, marker=marker)
    calls = []
    result = pm.prepare_point(store, 'p', store.records('points', 'p')[0],
                              {'finder': lambda *a, **k: calls.append(1)})
    assert result['questions'] == 0 and result['skipped'] == 1
    assert calls == []
    saved = store.records('points', 'p')[0]['subpoint_preparation']
    assert saved['processed_subpoints']['new-s1']['status'] == 'rejected'
    store.close()


def test_prep159_unprocessed_subpoint_still_reaches_finder(tmp_path):
    store = _store(tmp_path)
    calls = []
    def finder(packet, *args, **kwargs):
        calls.append([s['id'] for s in packet['subpoints']])
        return {'subpoints': []}
    pm.prepare_point(store, 'p', store.records('points', 'p')[0], {'finder': finder})
    assert calls and set(calls[0]) == {'old-s1', 'old-s2'}
    store.close()
