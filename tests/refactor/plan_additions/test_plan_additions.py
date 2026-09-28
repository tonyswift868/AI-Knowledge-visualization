from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading

import pytest

from refactor.learning.course import plan_additions as additions
from refactor.learning.course import planner_agents as agents
from refactor.learning.course.ledger import PlanWindow, Ledger, estimate_check_minutes
from refactor.learning.course.mastery import Judgement, AttemptClosed
from refactor.learning.course.store import CourseStore, StoreError


START = date(2026, 9, 28)
TODAY = START + timedelta(days=2)
ROOT = Path(r'D:\MentorFlow\.local\test-tmp\plan-additions\offline')


@pytest.fixture
def store():
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT) as folder:
        with CourseStore('test-additions', folder) as s:
            window = PlanWindow(START, START + timedelta(days=4), 30, date_weights={TODAY+timedelta(days=1): .5})
            s.create_plan('p', goal='Python 基础', final_goal='能解释和应用', level='beginner', window=window)
            s.add_sources('p', [dict(id='old', url='https://example.invalid/old', title='旧资料', adopted=True, chunks=[dict(id='oc', text='旧资料全文')])])
            s.save_outline('p', [dict(id='s', title='Python')],
                           [dict(id=f'm{i}', title=f'旧模块{i}', summary=f'旧模块摘要{i}', stage_id='s',
                                 prerequisites=[f'm{i-1}'] if i else []) for i in range(3)])
            for i in range(3):
                s.save_generated_module('p', f'm{i}', [dict(id=f'p{i}', title=f'旧点{i}', summary='旧摘要',
                                        chunk_ids=['oc'], minutes=20)], check_minutes=5, today=START)
            s.start_plan('p', today=START)
            s.append_judgement('p', Judgement('j0', 1, datetime(2026, 9, 28, tzinfo=timezone.utc),
                                'module_test', 'm0', 'test0', (('p0', True),)))
            s.append_attempt_closed('p', AttemptClosed('a0', 2, 'm0', 'test0', 'module_test'))
            s.add_credit('p', 'learn0', 'p0', 20, START, 'confirmed')
            s.add_sources('p', [dict(id='new', url='https://example.invalid/new', title='新增教程', adopted=True,
                          chunks=[dict(id='nc', text='新主题和已有知识点的补充解释。', section=['新章节'])])])
            yield s


def proposal(store, *, modules=True, supplements=False):
    state = additions.snapshot(store, 'p', ['new'])
    data = dict(supplements=[dict(point_id='p0', chunk_ids=['nc'], reason='新的例子')] if supplements else [],
                new_modules=[dict(key='n1', title='新模块', summary='能使用新方法', stage_id='s', after_module_id='m1',
                                  prerequisites=['m1'], required_by=['m2'], minutes=[20, 30], chunk_ids=['nc'], reason='尚未覆盖的新方法')] if modules else [],
                overlaps=[], ignored=[])
    if not modules and not supplements: data['overlaps'] = [dict(point_ids=['p0'], chunk_ids=['nc'], reason='已经学习')]
    return state, data


def parse(state, data):
    return additions.parse_analysis(json.dumps(data, ensure_ascii=False), state)


def generated(keys=('n1',)):
    return {key: [dict(title=f'{key}概念', summary='能解释含义', minutes=10, chunk_ids=['nc']),
                  dict(title=f'{key}应用', summary='能独立完成应用', minutes=12, chunk_ids=['nc'])] for key in keys}


def history(s):
    return {e: s.records(e, 'p') for e in ('judgements', 'attempt_closed', 'credits', 'messages')}


def sql(s):
    return '\n'.join(s._db.iterdump())


def apply(s, state, parsed, points, selected=None):
    view = additions.preview(state, parsed, today=TODAY, selected=selected, generated=points)
    return additions.apply_additions(s, state, parsed, points, view, confirmed=True, today=TODAY)


def test_supplement_only_unions_citations_without_replanning_or_rewriting(store):
    state, data = proposal(store, modules=False, supplements=True)
    old_history, ledger = history(store), store.load_ledger('p').to_dict()
    old_modules, old_plan = store.records('modules', 'p'), store.get_plan('p')
    old_points = store.records('points', 'p')
    parsed = parse(state, data)
    assert additions.generation_jobs(state, parsed) == []
    view = additions.preview(state, parsed, today=TODAY)
    assert view['added_minutes'] == [0, 0] and view['timing_high']['extra_daily_minutes'] == 0
    result = apply(store, state, parsed, {})
    assert result['module_ids'] == {} and result['supplement_count'] == 1
    assert store.get_plan('p') == old_plan
    assert store.records('modules', 'p') == old_modules
    assert history(store) == old_history and store.load_ledger('p').to_dict() == ledger
    for p in store.records('points', 'p'):
        expected = next(v for v in old_points if v['id'] == p['id'])
        if p['id'] == 'p0': expected = dict(expected, chunk_ids=['oc', 'nc'])
        assert p == expected


def test_new_module_in_middle_and_remaining_time_preserve_events_and_debt(store):
    state, data = proposal(store)
    parsed = parse(state, data)
    preview = additions.preview(state, parsed, today=TODAY)
    assert preview['order'] == ['m0', 'm1', 'n1', 'm2']
    before = history(store)
    learnt = store.records('modules', 'p')[0]
    expected = store.load_ledger('p')
    points = generated()
    add = 22 + estimate_check_minutes(2, minutes_per_question=1.5)
    expected.replan(TODAY, expected.window, expected.total_minutes + add)
    result = apply(store, state, parsed, points)
    mid = result['module_ids']['n1']
    modules = store.records('modules', 'p')
    assert [m['id'] for m in modules] == ['m0', 'm1', mid, 'm2']
    assert modules[0] == learnt
    assert modules[-1]['prerequisites'] == ['m1', mid]
    assert history(store) == before
    assert store.load_ledger('p').to_dict() == expected.to_dict()
    assert expected.status(TODAY).history_debt_minutes > 0
    assert store.get_plan('p')['state'] == 'active'
    assert [p for p in store.records('points', 'p') if p['module_id'] in ('m0', 'm1', 'm2')] == state['points']
    assert max(store.records('ledger_events', 'p'), key=lambda e: e['operation_seq'])['cause'] == 'material_added'


def test_prerequisite_of_studied_module_becomes_first_supplementary_module(store):
    state, data = proposal(store)
    m = data['new_modules'][0]
    m.update(required_by=['m0'], prerequisites=[], after_module_id=None)
    parsed = parse(state, data)
    view = additions.preview(state, parsed, today=TODAY)
    assert view['order'] == ['m0', 'n1', 'm1', 'm2']
    assert view['modules'][0]['kind'] == 'supplementary'
    assert view['modules'][0]['supplement_for'] == ['m0']
    old = store.records('modules', 'p')[0]
    result = apply(store, state, parsed, generated())
    assert store.records('modules', 'p')[0] == old
    assert store.records('modules', 'p')[1]['id'] == result['module_ids']['n1']


def test_deselection_generates_and_applies_only_selected_modules(store):
    state, data = proposal(store)
    data['new_modules'].append(dict(data['new_modules'][0], key='n2', title='不需要的模块'))
    parsed = parse(state, data)
    jobs = additions.generation_jobs(state, parsed, selected=['n2'])
    assert len(jobs) == 1 and jobs[0]['key'] == 'n2'
    result = apply(store, state, parsed, generated(['n2']), selected=['n2'])
    assert set(result['module_ids']) == {'n2'}
    assert len(store.records('modules', 'p')) == 4


def test_deselected_dependency_is_reported_not_silently_reincluded(store):
    state, data = proposal(store)
    data['new_modules'].append(dict(data['new_modules'][0], key='n2', prerequisites=['n1']))
    parsed = parse(state, data)
    with pytest.raises(additions.AdditionError, match='依赖已勾掉'):
        additions.preview(state, parsed, today=TODAY, selected=['n2'])


def test_over_cap_suggests_end_using_ledger_and_can_confirm_extension(store):
    state, data = proposal(store)
    data['new_modules'][0]['minutes'] = [100, 120]
    parsed = parse(state, data)
    points = {'n1': [dict(title='新概念', summary='掌握新概念', minutes=55, chunk_ids=['nc']),
                     dict(title='新应用', summary='完成练习', minutes=55, chunk_ids=['nc'])]}
    view = additions.preview(state, parsed, today=TODAY, generated=points)
    assert view['timing_high']['exceeds_cap']
    end = date.fromisoformat(view['timing_high']['suggested_end'])
    assert end > date.fromisoformat(state['plan']['window']['end'])
    updated = additions.preview(state, parsed, today=TODAY, generated=points, new_end=end)
    assert not updated['timing_high']['exceeds_cap']
    additions.apply_additions(store, state, parsed, points, updated, confirmed=True, today=TODAY)
    assert store.get_plan('p')['window']['end'] == end.isoformat()


def test_requests_use_structured_studied_state_and_only_new_material(store):
    state, data = proposal(store)
    system, messages = additions.analysis_request(state)
    body = json.loads(messages[0]['content'])
    assert body['route'][0]['modules'][0]['points'][0]['studied'] is True
    assert body['route'][0]['modules'][1]['points'][0]['studied'] is False
    assert body['material'][0]['id'] == 'nc' and '资料' in system
    jobs = additions.generation_jobs(state, parse(state, data))
    assert set(jobs[0]['aliases'].values()) == {'nc'}
    assert '旧资料全文' not in jobs[0]['messages'][0]['content']


def test_batches_cover_full_new_text_and_finish_cites_original_ids(store):
    store.add_sources('p', [dict(id='new', url='https://example.invalid/new', adopted=True, chunks=[dict(id='nc', text='新增材料。' * 6000)])])
    state, data = proposal(store)
    data['new_modules'][0]['minutes'] = [60, 100]
    parsed = parse(state, data)
    jobs = additions.generation_jobs(state, parsed)
    assert len(jobs) == 3
    responses = {}
    for i, j in enumerate(jobs):
        responses[j['id']] = json.dumps({'points': [dict(title=f'独立主题{i}', summary=f'解释第{i}个原理',
                                                        minutes=j['minutes'][0], chunks=['c1'])]})
    points = additions.finish_generation(state, parsed, responses)
    assert len(points['n1']) == 3
    assert all(p['chunk_ids'] == ['nc'] for p in points['n1'])
    with pytest.raises(additions.AdditionError): additions.finish_generation(state, parsed, {})


@pytest.mark.parametrize('fault', ['unknown', 'duplicate_json', 'missing_chunk', 'old_chunk', 'cycle', 'overlap_unlearnt', 'extra', 'bool_minutes'])
def test_strict_analysis_rejects_invalid_model_results(store, fault):
    state, data = proposal(store)
    if fault == 'unknown': data['new_modules'][0]['required_by'] = ['invented']
    if fault == 'missing_chunk': data['new_modules'] = []
    if fault == 'old_chunk': data['new_modules'][0]['chunk_ids'] = ['oc']
    if fault == 'cycle': data['new_modules'][0]['required_by'] = ['m1']
    if fault == 'overlap_unlearnt': data['overlaps'] = [dict(point_ids=['p1'], chunk_ids=['nc'], reason='无依据地说学过')]
    if fault == 'extra': data['new_modules'][0]['rewrite'] = True
    if fault == 'bool_minutes': data['new_modules'][0]['minutes'] = [True, 30]
    text = json.dumps(data)
    if fault == 'duplicate_json': text = text.replace('"supplements": []', '"supplements": [], "supplements": []')
    with pytest.raises(additions.AdditionError): additions.parse_analysis(text, state)


@pytest.mark.parametrize('change', ['credit', 'source', 'window', 'message'])
def test_late_model_response_rejected_after_progress_or_sources_change(store, change):
    state, data = proposal(store)
    parsed = parse(state, data); points = generated()
    view = additions.preview(state, parsed, today=TODAY, generated=points)
    if change == 'credit': store.add_credit('p', 'late', 'p1', 5, TODAY)
    if change == 'source': store.add_sources('p', [dict(id='new', url='https://example.invalid/new', adopted=True, chunks=[dict(id='nc', text='变化了')])])
    if change == 'window': store.save_intake('p', window=PlanWindow(START, TODAY+timedelta(days=10), 20), today=TODAY)
    if change == 'message': store.append_message('p', 'm1', 'assistant', '已开始讲解', at=datetime.now(timezone.utc))
    before = sql(store)
    with pytest.raises(additions.AdditionError, match='已变化'):
        additions.apply_additions(store, state, parsed, points, view, confirmed=True, today=TODAY)
    assert sql(store) == before


def test_confirmation_binds_generated_text_selection_and_day(store):
    state, data = proposal(store)
    parsed = parse(state, data); points = generated()
    view = additions.preview(state, parsed, today=TODAY, generated=points)
    before = sql(store)
    with pytest.raises(additions.AdditionError): additions.apply_additions(store, state, parsed, points, view, confirmed=False, today=TODAY)
    changed = deepcopy(points); changed['n1'][0]['summary'] = '更换了生成内容'
    with pytest.raises(additions.AdditionError): additions.apply_additions(store, state, parsed, changed, view, confirmed=True, today=TODAY)
    with pytest.raises(additions.AdditionError): additions.apply_additions(store, state, parsed, points, view, confirmed=True, today=TODAY+timedelta(days=1))
    assert sql(store) == before


def test_cancellation_and_atomic_rollback_include_outbox(store, monkeypatch):
    state, data = proposal(store, supplements=True)
    parsed = parse(state, data); points = generated()
    view = additions.preview(state, parsed, today=TODAY, generated=points)
    before = sql(store)
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(additions.AdditionError): additions.apply_additions(store, state, parsed, points, view, confirmed=True, today=TODAY, cancel=cancelled)
    assert sql(store) == before
    original = store._put
    def fail(entity, plan, payload):
        result = original(entity, plan, payload)
        if entity == 'ledger_events': raise OSError('simulated disk failure')
        return result
    monkeypatch.setattr(store, '_put', fail)
    with pytest.raises(OSError): apply(store, state, parsed, points)
    assert sql(store) == before


def test_store_new_api_cannot_rewrite_points_reorder_old_or_change_supplement_time(store):
    before = sql(store)
    with pytest.raises(StoreError): store.append_material_additions('p', [], [], ['m1', 'm0', 'm2'], today=TODAY)
    with pytest.raises(StoreError): store.append_material_additions('p', [], [], ['m0', 'm1', 'm2'], today=TODAY, new_window=PlanWindow(START, TODAY+timedelta(days=10), 30))
    with pytest.raises(StoreError): store.save_generated_module('p', 'm0', [dict(id='p0', summary='改写', minutes=2, chunk_ids=['oc'])], check_minutes=1, today=TODAY)
    assert sql(store) == before


def test_sync_roundtrip_preserves_new_citations_order_and_history(store):
    state, data = proposal(store, supplements=True)
    apply(store, state, parse(state, data), generated())
    with tempfile.TemporaryDirectory(dir=ROOT) as folder:
        with CourseStore('test-additions', folder) as target:
            target.apply_remote([(r, 1) for r in store.pending_records(10000)])
            assert target.records('modules', 'p') == store.records('modules', 'p')
            assert target.records('points', 'p') == store.records('points', 'p')
            assert target.load_ledger('p').to_dict() == store.load_ledger('p').to_dict()
            assert history(target) == history(store)


def test_other_account_cannot_apply_snapshot_even_with_identical_plan_ids(store):
    state, data = proposal(store); parsed = parse(state, data)
    points = generated(); view = additions.preview(state, parsed, today=TODAY, generated=points)
    with tempfile.TemporaryDirectory(dir=ROOT) as folder:
        with CourseStore('other-account', folder) as other:
            # Same projected payloads, different authenticated account identity.
            other.apply_remote([(r, 1) for r in store.pending_records(10000)])
            before = sql(other)
            with pytest.raises(additions.AdditionError): additions.apply_additions(other, state, parsed, points, view, confirmed=True, today=TODAY)
            assert sql(other) == before


def test_overlaps_only_and_all_deselected_are_noop(store):
    state, data = proposal(store, modules=False)
    before = sql(store)
    assert apply(store, state, parse(state, data), {})['added_minutes'] == 0
    assert sql(store) == before
    state, data = proposal(store)
    assert apply(store, state, parse(state, data), {}, selected=[])['module_ids'] == {}
    assert sql(store) == before


def test_cancellation_after_writes_rolls_back_transaction(store, monkeypatch):
    state, data = proposal(store)
    parsed = parse(state, data); points = generated()
    view = additions.preview(state, parsed, today=TODAY, generated=points)
    before = sql(store)
    cancelled = threading.Event()
    original = store.append_material_additions
    def cancel_after(*args, **kwargs):
        result = original(*args, **kwargs)
        cancelled.set()
        return result
    monkeypatch.setattr(store, 'append_material_additions', cancel_after)
    with pytest.raises(additions.AdditionError, match='已取消'):
        additions.apply_additions(store, state, parsed, points, view, confirmed=True, today=TODAY, cancel=cancelled)
    assert sql(store) == before


def test_failure_after_merge_cannot_save_shortened_duplicate_minutes(store):
    store.add_sources('p', [dict(id='new', url='https://example.invalid/new', adopted=True,
                               chunks=[dict(id='nc', text='不同批次的相同描述。' * 1800)])])
    state, data = proposal(store)
    data['new_modules'][0]['minutes'] = [40, 50]
    parsed = parse(state, data)
    jobs = additions.generation_jobs(state, parsed)
    assert len(jobs) == 2
    responses = {j['id']: json.dumps({'points':[dict(title='重复主题', summary='完全相同的掌握目标',
                  minutes=j['minutes'][0], chunks=['c1'])]}) for j in jobs}
    before = sql(store)
    with pytest.raises(agents.AgentFormatError): additions.finish_generation(state, parsed, responses)
    assert sql(store) == before


def test_sparse_positions_and_supplement_only_preserve_all_old_modules(store):
    # Existing synchronized data can have non-contiguous integer positions.
    with store.transaction():
        for m in store.records('modules', 'p'):
            store._put('modules', 'p', dict(m, position=m['position'] * 5))
    before = store.records('modules', 'p')
    state, data = proposal(store, modules=False, supplements=True)
    apply(store, state, parse(state, data), {})
    assert store.records('modules', 'p') == before


def test_analysis_too_large_explicitly_rejected_and_chunk_omission_not_silent(store):
    state, data = proposal(store)
    with pytest.raises(additions.AdditionError, match='超出单次分析上限'):
        additions.analysis_request(state, max_chars=1)
    store.add_sources('p', [dict(id='new', url='https://example.invalid/new', adopted=True,
                  chunks=[dict(id='nc2', text='另一部分新增资料')])])
    state = additions.snapshot(store, 'p', ['new'])
    with pytest.raises(additions.AdditionError, match='未分类'): parse(state, data)


def test_existing_source_cannot_be_readded_and_missing_stage_rejected(store):
    with pytest.raises(additions.AdditionError, match='已经关联'):
        additions.snapshot(store, 'p', ['old'])
    state, data = proposal(store)
    data['new_modules'][0]['stage_id'] = 'invented-stage'
    with pytest.raises(additions.AdditionError): parse(state, data)


def test_addition_does_not_recompute_existing_negotiated_total(store):
    store.replan_plan('p', TODAY, new_total_minutes=90, reason='已有时长调整')
    state, data = proposal(store)
    parsed = parse(state, data)
    result = apply(store, state, parsed, generated())
    assert result['total_minutes'] == 90 + result['added_minutes']


def test_messages_only_protect_placement_not_claim_each_point_was_learnt(store):
    store.append_message('p', 'm1', 'assistant', '开始讲课', at=datetime.now(timezone.utc))
    state, data = proposal(store)
    assert 'm1' in state['studied']['module_ids']
    assert 'p1' not in state['studied']['point_ids']
    data['new_modules'][0].update(after_module_id=None, prerequisites=[], required_by=['m1'])
    parsed = parse(state, data)
    view = additions.preview(state, parsed, today=TODAY)
    assert view['order'] == ['m0', 'm1', 'n1', 'm2']
    assert view['modules'][0]['kind'] == 'supplementary'


def test_related_supplementary_modules_follow_dependencies_at_front(store):
    state, data = proposal(store)
    first = data['new_modules'][0]
    first.update(after_module_id=None, prerequisites=[], required_by=['m0'])
    second = dict(first, key='n2', prerequisites=['n1'], title='补充模块二')
    data['new_modules'] = [second, first]  # input order need not be topological
    parsed = parse(state, data)
    view = additions.preview(state, parsed, today=TODAY)
    assert view['order'] == ['m0', 'n1', 'n2', 'm1', 'm2']
    result = apply(store, state, parsed, generated(['n1', 'n2']))
    assert [m['id'] for m in store.records('modules', 'p')][:3] == ['m0', result['module_ids']['n1'], result['module_ids']['n2']]
