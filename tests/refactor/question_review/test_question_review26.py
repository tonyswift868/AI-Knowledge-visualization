"""Batch 26 contract candidates; no real provider or public web calls."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import random
import threading
from types import SimpleNamespace

import pytest
from tests.refactor.question_solver.fakes import response as solver_response, grade as solver_grade

from refactor.learning.app.classroom import Classroom
from refactor.learning.connections.errors import ModelCancelled, ModelError
from refactor.learning.connections.gateway import PURPOSE_EFFORT, PURPOSE_MIN_OUTPUT, BACKGROUND_PURPOSES
from refactor.learning.course import question_review as qr
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.planner_agents import AgentFormatError
from refactor.learning.course.question_agent import Question
from refactor.learning.course.store import CourseStore

POINTS = [dict(id='p0', title='列表追加', summary='追加的行为', chunk_ids=['c'])]
EXCERPTS = {'c': 'append(x) appends x to the end of the original list. It returns None.'}


def choice(**updates):
    result = dict(id='q1', type='choice', stem='对列表执行 append(x)，会怎样添加 x？',
        options=['追加到列表末尾', '替换第一个元素', '删除最后一个元素', '创建新列表并保留原列表'],
        answer='追加到列表末尾', reference='原文说明 append 追加元素。', point_ids=['p0'], chunk_ids=['c'], angle='recall')
    result.update(updates)
    return result


def short(**updates):
    q = choice(type='short', stem='请说明 append 对原列表的修改方式。', options=[], answer='修改原列表',
        required_points=[dict(point='append 修改原列表', accept=['在原列表上追加'], reject=['返回一个新列表'])])
    q.update(updates)
    return q


def check(q, **kwargs):
    return qr.check_question(q, points=POINTS, excerpts=EXCERPTS, **kwargs)


@pytest.mark.parametrize('connector', ['和', '以及', '并且', '、', '；', '并返回', '并沿用', '并作为'])
def test_connector_is_review_hint_not_semantic_proof(connector):
    q = short()
    q['required_points'][0]['point'] = 'append 原地修改列表' + connector + '返回 None'
    assert 'bundled_required_point' not in check(q)
    assert 'possible_bundled_required_point' in qr.semantic_hints(q)


def test_contract_fields_requirements_and_question_coverage():
    assert not check(choice()) and not check(short())
    q = short(); q['required_points'].append(dict(point='解释海上丝绸之路的贸易角色', accept=['贸易纽带'], reject=['没有贸易']))
    assert 'possible_requirement_not_asked' in qr.semantic_hints(q)
    q = short(required_points=['append 修改原列表'])
    assert not check(q)
    q = choice(); del q['reference']
    assert check(q) == ('missing_fields',)
    assert 'invalid_type' in check(choice(type=[]))
    assert 'invalid_provenance' in check(choice(chunk_ids=['not-provided']))
    assert 'answer_in_stem' in check(choice(stem='append 会追加到列表末尾，请选它如何追加。'))
    assert 'choice_not_unique' in check(choice(options=['a'] * 4, answer='a'))
    assert 'required_points_count' in check(short(required_points=[]))
    q = short(stem='请说明求和函数的作用。', required_points=[dict(point='求和函数返回数值总和',
              accept=['返回各值相加的结果'], reject=['返回一个列表'])])
    assert 'bundled_required_point' not in check(q)


def test_exact_answer_and_tolerance_contract():
    q = choice(type='exact', stem='a = [8, 9, 10] 的长度是多少？', options=[], answer='3',
               answer_kind='number', accepted=['3', '3.0'], tolerance=0)
    assert not check(q)
    assert 'accepted_missing_answer' in check(dict(q, accepted=['three']))
    assert 'invalid_tolerance' in check(dict(q, tolerance=-1))
    assert 'unexpected_tolerance' in check(dict(q, answer_kind='expression'))


def test_essay_rubric_and_per_module_limit():
    rubric = [dict(criterion=c, met='结论有资料依据', not_met='未达到该项', required=required)
              for c, required in [('核心观点', True), ('没有事实错误', True), ('资料论据', False)]]
    q = choice(type='essay', stem='请分析列表追加的设计，结合原文说明观点和依据。', options=[], answer='把观点与原文结合', rubric=rubric)
    assert not check(q)
    assert 'essay_module_limit' in check(q, essay_count=1)
    assert 'rubric_count' in check(dict(q, rubric=rubric[:2]))
    changed = deepcopy(q); changed['rubric'][1]['required'] = False
    assert 'rubric_missing_core' in check(changed)


def test_independent_review_contract_and_revision_bounds():
    q = short()
    system, messages = qr.review_request(q, points=POINTS, excerpts=EXCERPTS, structure_issues=['requirement_not_asked'])
    assert EXCERPTS['c'] in messages[0]['content'] and 'requirement_not_asked' in messages[1]['content']
    assert '独立题目核对员' in system and len(messages) == 2
    assert qr.parse_review(json.dumps(dict(verdict='approve', issues=[], revised=None)), q).verdict == 'approve'
    revised = dict(q, stem='请解释 append 怎样修改原列表。')
    assert qr.parse_review(json.dumps(dict(verdict='revise', issues=['题面补明确'], revised=revised)), q).revised == revised
    for bad in [dict(verdict='approve', issues=['存在问题'], revised=None),
                dict(verdict='reject', issues=[], revised=None),
                dict(verdict='revise', issues=['改题'], revised=dict(revised, point_ids=['p1']))]:
        with pytest.raises(AgentFormatError): qr.parse_review(json.dumps(bad), q)


def test_gateway_review_policy_matches_lesson_review():
    assert PURPOSE_EFFORT['question_review'] == PURPOSE_EFFORT['lesson_review'] == 'high'
    assert PURPOSE_MIN_OUTPUT['question_review'] == PURPOSE_MIN_OUTPUT['lesson_review']
    assert 'question_review' in BACKGROUND_PURPOSES


def test_legacy_requirements_use_same_rules_and_revision_retains_format():
    q = short(required_points=['append 修改原列表'])
    assert not check(q)
    assert 'possible_bundled_required_point' in qr.semantic_hints(short(required_points=['append 修改原列表并返回 None']))
    assert 'possible_requirement_not_asked' in qr.semantic_hints(short(required_points=['丝路形成时间']))
    assert 'required_points_count' in check(short(required_points=['a', 'b', 'c']))
    mixed = short(required_points=['append 修改原列表', short()['required_points'][0]])
    assert 'mixed_requirement_formats' in check(mixed)
    system, _ = qr.review_request(q, points=POINTS, excerpts=EXCERPTS)
    assert '修订时保持字符串数组格式' in system
    revised = dict(q, stem='append 如何修改原列表？')
    assert qr.parse_review(json.dumps(dict(verdict='revise', issues=['范围明确'], revised=revised)), q).revised == revised
    with pytest.raises(AgentFormatError, match='原有要点格式'):
        qr.parse_review(json.dumps(dict(verdict='revise', issues=['迁移'], revised=short())), q)


def test_incomplete_revision_is_format_failure_not_a_crash():
    revised = choice(); del revised['chunk_ids']
    with pytest.raises(AgentFormatError):
        qr.parse_review(json.dumps(dict(verdict='revise', issues=['修订'], revised=revised)), choice())


PLAN, DAY = 'e' * 32, date(2026, 10, 1)


class Reviewer:
    def __init__(self):
        self.calls, self.actions = [], []
        self.forced_calls, self.forced_result = [], None

    def complete(self, purpose, messages, **kwargs):
        if purpose == 'questions':
            points = json.JSONDecoder().raw_decode(messages[-1]['content'].split('【知识点清单】\n')[1])[0]
            self.forced_calls.append([p['id'] for p in points])
            if self.forced_result:
                return SimpleNamespace(text=json.dumps(dict(questions=self.forced_result), ensure_ascii=False))
        extra = solver_response(purpose, messages)
        if extra is not None:
            return extra
        if purpose == 'judge':
            return solver_grade(messages)
        assert purpose == 'question_review'
        assert kwargs['max_tokens'] == 24576
        payload = json.loads(messages[-1]['content'].split('\n', 1)[1])
        self.calls.append(payload)
        action = self.actions.pop(0) if self.actions else dict(verdict='approve', issues=[], revised=None)
        if isinstance(action, Exception): raise action
        if callable(action): action = action(payload)
        return SimpleNamespace(text=json.dumps(action, ensure_ascii=False))


@pytest.fixture
def lab(tmp_path):
    with CourseStore('review26', tmp_path) as store:
        store.create_plan(PLAN, goal='列表追加', window=PlanWindow(DAY, DAY + timedelta(days=3), 100))
        store.add_sources(PLAN, [dict(id='s', url='https://docs.python.org/3/tutorial/datastructures.html',
                                    chunks=[dict(chunk_id='c', text=EXCERPTS['c'])])])
        store.save_outline(PLAN, [dict(id='st', title='基础')], [dict(id='m', stage_id='st', title='列表')])
        store.save_generated_module(PLAN, 'm', [dict(id=p, title='列表追加' + p, summary='追加到末尾', minutes=10,
            chunk_ids=['c']) for p in ('p0', 'p1')], check_minutes=8, final_check_minutes=6,
            check_question_minutes=2, today=DAY)
        store.start_plan(PLAN, today=DAY)
        reviewer = Reviewer()
        services = SimpleNamespace(store=store, account_id='review26', data_root=tmp_path, gateway=reviewer,
            settings=SimpleNamespace(load=lambda: dict(refresh_time='04:00')))
        room = Classroom(services, lambda: DAY, now=lambda: datetime(2026, 10, 1, 10, tzinfo=timezone.utc), rng=random.Random(26))
        calls = []
        def questions(co, ctx, purpose, ids, **kwargs):
            calls.append(list(ids))
            return [Question(f'{p}-{len(calls)}-{i}', 'short' if i == 1 else 'choice', f'第{len(calls)}批 {p} 题{i}：追加的行为是什么？',
                () if i == 1 else ('追加到列表末尾', '替换首元素', '删除末元素', '创建新列表'), '追加到列表末尾', '原文支持追加。',
                (p,), ('c',), angle, ('追加到列表末尾',) if i == 1 else None)
                for p in ids for i, angle in enumerate([a for a in ('recall', 'apply', 'contrast', 'fix_error', 'transfer')
                    if a not in kwargs.get('used_angles', {}).get(p, ())][:kwargs['counts'][p]])]
        room._questions = questions
        yield room, store, calls, reviewer, services


def fill(room, **kwargs):
    return room._bank_fill(room._course(PLAN), None, 'm', **kwargs)


def test_revised_question_must_be_reviewed_again_before_store(lab):
    room, store, calls, reviewer, _ = lab
    def revise(payload):
        revised = dict(payload['question'], stem='修改后：列表 append 把元素加到哪个位置？')
        return dict(verdict='revise', issues=['补充问题范围'], revised=revised)
    reviewer.actions = [revise]
    fill(room)
    rows = store.records('question_bank', PLAN)
    assert len(rows) == 4 and len(calls) == 1 and len(reviewer.calls) == 5
    revised = next(q for q in rows if q['stem'].startswith('修改后'))
    assert reviewer.calls[1]['question']['stem'] == revised['stem']


def test_rejection_only_reissues_missing_point_once_and_is_durable(lab):
    room, store, calls, reviewer, _ = lab
    reviewer.actions = [dict(verdict='reject', issues=['资料未支持该说法'], revised=None)] * 2
    fill(room)
    assert calls == [['p0', 'p1']] and reviewer.forced_calls == [['p0']]
    assert len(store.records('question_bank', PLAN)) == 4
    fill(room)
    assert len(calls) + len(reviewer.forced_calls) == 2 and len(store.records('question_bank', PLAN)) == 4


def test_two_unapproved_revisions_are_dropped_no_infinite_generation_on_reopen(lab):
    room, store, calls, reviewer, services = lab
    def revise(payload):
        return dict(verdict='revise', issues=['还不明确'], revised=dict(payload['question'], stem=payload['question']['stem'] + '？'))
    reviewer.actions = [revise] * 20
    with pytest.raises(ModelError, match='初次出题与一次重出'): fill(room, point_ids=['p0'])
    assert calls == [['p0']] and reviewer.forced_calls == [['p0']] and len(reviewer.calls) == 8
    assert not store.records('question_bank', PLAN)
    reopened = Classroom(services, lambda: DAY)
    reopened._questions = lambda *a, **k: pytest.fail('must not generate again after reopening')
    with pytest.raises(ModelError, match='初次出题与一次重出'): fill(reopened, point_ids=['p0'])
    assert len(reviewer.calls) == 8


def test_timeout_preserves_all_drafts_and_reopens_only_review(lab):
    room, store, calls, reviewer, services = lab
    reviewer.actions = [ModelError('MODEL_TIMEOUT', '核对超时')]
    with pytest.raises(ModelError, match='题目已保留'): fill(room)
    assert calls == [['p0', 'p1']] and len(store.records('question_bank', PLAN)) == 3
    reopened = Classroom(services, lambda: DAY)
    reopened._questions = lambda *a, **k: pytest.fail('review timeout must not regenerate')
    fill(reopened)
    assert len(store.records('question_bank', PLAN)) == 4 and len(reviewer.calls) == 5
    assert reviewer.calls[-1]['question'] == reviewer.calls[0]['question']


def test_cancelled_review_and_late_material_result_do_not_enter_bank(lab):
    room, store, calls, reviewer, _ = lab
    event = threading.Event()
    def cancelled(payload):
        event.set()
        return dict(verdict='approve', issues=[], revised=None)
    reviewer.actions = [cancelled]
    with pytest.raises(ModelCancelled):
        room._bank_fill(room._course(PLAN), SimpleNamespace(cancel=event), 'm', point_ids=['p0'])
    assert not store.records('question_bank', PLAN) and len(calls) == 1
    event.clear(); fill(room, point_ids=['p0'])
    assert len(calls) == 1 and len(store.records('question_bank', PLAN)) == 2


def test_material_changed_during_review_discards_late_approval(lab):
    room, store, _, reviewer, _ = lab
    def change_material(payload):
        room._test_fingerprint = lambda *_: 'changed-material'
        return dict(verdict='approve', issues=[], revised=None)
    reviewer.actions = [change_material]
    with pytest.raises(ModelError) as exc:
        fill(room, point_ids=['p0'])
    assert exc.value.code == 'QUESTION_MATERIAL_CHANGED'
    assert not store.records('question_bank', PLAN)


def test_foreground_background_recount_under_same_module_lock(lab):
    room, store, calls, _, _ = lab
    original = room._questions
    entered, release = threading.Event(), threading.Event()
    errors = []
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)
    room._questions = slow
    def work():
        try: fill(room)
        except Exception as exc: errors.append(exc)
    first, second = threading.Thread(target=work), threading.Thread(target=work)
    first.start(); assert entered.wait(1)
    second.start(); release.set()
    first.join(3); second.join(3)
    assert not first.is_alive() and not second.is_alive() and not errors
    assert len(calls) == 1 and len(store.records('question_bank', PLAN)) == 4


def test_structural_problem_cannot_be_approved_by_model(lab):
    room, store, _, reviewer, _ = lab
    def invalid(*args, **kwargs):
        return [choice(id='bad', point_ids=['p0'], stem='已知会追加到列表末尾，append 会如何操作？')]
    room._questions = invalid
    reviewer.forced_result = invalid()
    with pytest.raises(ModelError): fill(room, point_ids=['p0'])
    assert not store.records('question_bank', PLAN)
    assert len(reviewer.calls) == 1  # repair parser drops the same leaked question before review
    assert all('answer_in_stem' in c['structure_issues'] for c in reviewer.calls)


def test_durable_scopes_isolate_accounts_and_targeted_reentry(lab):
    room, _, calls, reviewer, services = lab
    reviewer.actions = [dict(verdict='reject', issues=['不合格'], revised=None)] * 4
    with pytest.raises(ModelError): fill(room, point_ids=['p0'])
    assert calls == [['p0']] and reviewer.forced_calls == [['p0']]
    fill(room, point_ids=['p1'])
    assert calls[-1] == ['p1']
    other = qr.ReviewJournal('other-account', services.data_root, PLAN, 'm', {'p0': {'scope': 'same'}})
    assert other.generations('p0') == 0


def test_legacy_short_survives_review_and_stores_original_format(lab):
    room, store, _, reviewer, _ = lab
    room._questions = lambda *a, **kw: [short(id='old', required_points=['append 修改原列表'])]
    fill(room, point_ids=['p0'], needs={'p0': 1})
    row = store.records('question_bank', PLAN)[0]
    assert row['required_points'] == ['append 修改原列表'] and row['type'] == 'short'
    assert len(reviewer.calls) == 1 and not reviewer.calls[0]['structure_issues']


def test_network_disconnect_and_invalid_response_retain_same_candidate(lab):
    room, store, calls, reviewer, _ = lab
    reviewer.actions = [ModelError('MODEL_NETWORK', '断线')]
    with pytest.raises(ModelError, match='题目已保留'): fill(room, point_ids=['p0'])
    reviewer.actions = [{'verdict': 'not-a-verdict', 'issues': [], 'revised': None}]
    with pytest.raises(ModelError, match='题目已保留'): fill(room, point_ids=['p0'])
    fill(room, point_ids=['p0'])
    assert len(calls) == 1 and len(store.records('question_bank', PLAN)) == 2
    assert reviewer.calls[0]['question'] == reviewer.calls[2]['question'] == reviewer.calls[3]['question']


def test_bank_insert_crash_before_journal_commit_does_not_double_count(lab, monkeypatch):
    room, store, calls, _, _ = lab
    original = qr.ReviewJournal.save
    failed = False
    def crash(self, token, row):
        nonlocal failed
        if row['status'] == 'stored' and not failed:
            failed = True
            raise OSError('simulated journal write failure after bank insert')
        original(self, token, row)
    monkeypatch.setattr(qr.ReviewJournal, 'save', crash)
    with pytest.raises(OSError): fill(room, point_ids=['p0'])
    assert len(store.records('question_bank', PLAN)) == 1
    fill(room, point_ids=['p0'])
    assert len(store.records('question_bank', PLAN)) == 2 and len(calls) == 1


def test_long_windows_account_and_module_paths_can_persist(tmp_path):
    journal = qr.ReviewJournal('mfa1_' + 'c' * 64, tmp_path, 'a' * 32, 'b' * 32, {'p0': {'v': 1}})
    journal.start_generation(['p0'])
    token, _ = journal.add(choice())
    assert journal.generations('p0') == 1 and journal.candidates(['p0'])[0][0] == token


@pytest.mark.parametrize('peak, expected', [(True, ['m0']), (False, ['m0', 'm1'])])
def test_reviewed_bank_uses_existing_peak_current_module_only_schedule(lab, peak, expected):
    room, _, _, _, _ = lab
    course = room._course(PLAN)
    course.modules = [dict(id='m0'), dict(id='m1')]
    room._test_fingerprint = lambda *a: 'fixed'
    room._peak_prep = lambda: peak
    requested = []
    room._prepare_bank_later = lambda co, mid: requested.append(mid)
    room._prefetch_banks(course, dict(module_id='m0'), testing=True)
    assert requested == expected


@pytest.mark.parametrize('kind', ['short', 'fix_error', 'exact', 'essay'])
def test_new_contract_fields_pass_bank_wiring_without_legacy_parser_changes(lab, monkeypatch, kind):
    """Contract capture sink only: MF03's new store/parser are not merged yet."""
    room, store, _, reviewer, _ = lab
    # Exact/essay must not occupy the module's only reserved written slot.
    if kind in ('exact', 'essay'):
        store.add_bank_questions(PLAN, 'm', [short(id='existing', angle='apply')],
                                 fingerprint=room._test_fingerprint(room._course(PLAN), 'm'))
    if kind in ('short', 'fix_error'):
        q = short(type=kind)
    elif kind == 'exact':
        q = choice(type='exact', stem='a=[8,9,10] 的长度是多少？', options=[], answer='3',
                   answer_kind='number', accepted=['3', '3.0'], tolerance=0)
    else:
        q = choice(type='essay', stem='请分析列表追加的设计，结合原文说明观点与依据。', options=[],
                   answer='把观点与原文结合', rubric=[dict(criterion=c, met='有原文支持', not_met='缺少支持', required=r)
                   for c,r in [('核心观点',True),('没有事实错误',True),('原文依据',False)]])
    room._questions = lambda *a, **kw: [q]
    captured = []
    monkeypatch.setattr(store, 'add_bank_questions', lambda plan, mid, rows, **kw: captured.extend(deepcopy(rows)))
    fill(room, point_ids=['p0'], needs={'p0': 1})
    assert captured == [q] and len(reviewer.calls) == 1


def test_optional_composite_review_timeout_does_not_block_covered_points(lab):
    room, store, calls, reviewer, _ = lab
    original = room._questions
    def with_composite(*args, **kwargs):
        return original(*args, **kwargs) + [choice(id='composite', point_ids=['p0', 'p1'])]
    room._questions = with_composite
    reviewer.actions = [dict(verdict='approve', issues=[], revised=None)] * 4 + [ModelError('MODEL_TIMEOUT','综合题核对超时')]
    fill(room)
    assert len(store.records('question_bank', PLAN)) == 4 and len(calls) == 1
    fill(room)
    assert len(store.records('question_bank', PLAN)) == 5 and len(calls) == 1
    assert reviewer.calls[-1]['question']['id'] == 'composite'


def test_blind_disagreement_cannot_be_overruled_by_approval28(lab):
    room, store, _, reviewer, _ = lab
    original = reviewer.complete
    def complete(purpose, messages, **kw):
        if purpose == 'question_solve':
            return SimpleNamespace(text=json.dumps(dict(answerable=False, answer='', viable_options=[],
                                                        reason='原文不能支持此题。')))
        return original(purpose, messages, **kw)
    reviewer.complete = complete
    with pytest.raises(ModelError): fill(room, point_ids=['p0'])
    assert not store.records('question_bank', PLAN)
    assert reviewer.calls and all(c['verification']['matched'] is False for c in reviewer.calls)
    assert all(not c['structure_issues'] for c in reviewer.calls)


def test_revision_runs_new_blind_solution28(lab):
    room, store, _, reviewer, _ = lab
    original = reviewer.complete
    solved = []
    def complete(purpose, messages, **kw):
        if purpose == 'question_solve':
            solved.append(json.loads(messages[-1]['content'].split('\n', 1)[1])['stem'])
        return original(purpose, messages, **kw)
    reviewer.complete = complete
    reviewer.actions = [lambda payload: dict(verdict='revise', issues=['明确题面'],
        revised=dict(payload['question'], stem='修订后：append 的添加位置是什么？'))]
    fill(room, point_ids=['p0'])
    assert len(solved) == 3 and solved[1].startswith('修订后')
    assert len(store.records('question_bank', PLAN)) == 2


def test_blind_timeout_resumes_without_regenerating28(lab):
    room, store, calls, reviewer, services = lab
    original = reviewer.complete
    failed = False
    def complete(purpose, messages, **kw):
        nonlocal failed
        if purpose == 'question_solve' and not failed:
            failed = True
            raise ModelError('MODEL_TIMEOUT', '盲解超时')
        return original(purpose, messages, **kw)
    reviewer.complete = complete
    with pytest.raises(ModelError, match='题目已保留'): fill(room, point_ids=['p0'])
    assert len(store.records('question_bank', PLAN)) == 1
    reopened = Classroom(services, lambda: DAY)
    reopened._questions = lambda *a, **k: pytest.fail('blind timeout must not regenerate')
    fill(reopened, point_ids=['p0'])
    assert calls == [['p0']] and len(store.records('question_bank', PLAN)) == 2


def test_wrong_type_repair_cannot_fill_reserved_written_slot28(lab):
    room, store, _, reviewer, _ = lab
    room._questions = lambda *a, **kw: [choice(id='choice1'), choice(id='choice2', angle='apply')]
    reviewer.forced_result = [choice(id='choice3', angle='contrast')]
    with pytest.raises(ModelError): fill(room, point_ids=['p0'])
    assert len(store.records('question_bank', PLAN)) == 1
    assert reviewer.forced_calls == [['p0']]
    fill_again = lambda: fill(room, point_ids=['p0'])
    with pytest.raises(ModelError): fill_again()
    assert reviewer.forced_calls == [['p0']]  # durable once-only repair
