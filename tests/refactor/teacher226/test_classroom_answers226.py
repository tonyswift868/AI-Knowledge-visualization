"""BUG-183: real classroom/store, synthetic de-identified questions, no IO."""
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from tests.refactor.conversation37.test_grounded_conversation37 import make_environment, PLAN
from refactor.learning.app.classroom import RESET
from refactor.learning.course import teacher_agent


@pytest.fixture
def lesson(tmp_path):
    e = make_environment(tmp_path)
    e.services.rag = SimpleNamespace(search_plan=lambda *a, **k: {'status': 'not_ready', 'passages': []})
    e.store.add_sources(PLAN, [dict(id='elsewhere', kind='url', title='一致性说明', adopted=True,
        url='https://example.invalid/consistency', chunks=[dict(chunk_id='outside-point',
        text='强一致性是写入后读取最新值；弱一致性不保证立即读到新值；最终一致性保证一定时间后达到一致。',
        section=['一致性的三个级别'])])])
    e.store.add_sources(PLAN, [dict(id='rejected', kind='url', title='未采用', adopted=False,
        url='https://example.invalid/no', chunks=[dict(chunk_id='rejected-chunk', text='一致性秘密无关结论。')])])
    e.state = dict(step='opening', phase='learning', module_id='m0', index=0,
                   conversation=dict(point_id='p0', options=[], interrupted=False))
    e.ctx = SimpleNamespace(cancel=threading.Event(), stream=e.stream.append)
    e.requests = []
    e.response = '【依据】supplement【/依据】讲义里没有，下面是补充：没有获奖也可以如实介绍项目经历。'
    def complete(purpose, messages, **kw):
        e.requests.append((purpose, messages, kw['system']))
        task = json.loads(next(m['content'].split('\n', 1)[1] for m in reversed(messages)
                              if m['content'].startswith('【本步任务】')))
        text = e.response(task) if callable(e.response) else e.response
        for char in text:
            kw['on_text'](char)
        return SimpleNamespace(text=text)
    e.gateway.complete = complete
    yield e
    e.services._closed = True
    e.store.close()


def test_226_whole_plan_orphan_and_tail_window(lesson):
    e = lesson
    e.store.add_sources(PLAN, [dict(id='long', kind='url', title='长资料', adopted=True,
        url='https://example.invalid/long', chunks=[dict(chunk_id='long-tail', text='前言。'*2000+'尾部独有标记解释业务校验。')])])
    co = e.room._course(PLAN)
    found = e.room._related_passages(co, {}, '三个一致性和尾部独有标记是什么？')
    assert 'outside-point' in found
    assert '尾部独有标记' in found['long-tail']
    assert 'rejected-chunk' not in found


def test_226_other_material_has_real_source_and_does_not_mark_point_taught(lesson):
    e = lesson
    def answer(task):
        assert task['qa'] is True
        return '【依据】material:c2【/依据】强一致性立即读到新值，最终一致性在一定时间后达到一致。'
    e.response = answer
    e.room.ask(e.room._course(PLAN), e.state, e.ctx, '强弱最终三个一致性有什么区别？')
    row = e.store.messages(PLAN, 'm0')[-1]
    assert 'mf-source:outside-point' in row['content']
    assert 'mf-source:c0' not in row['content']
    assert not e.store.records('teacher_profile', PLAN)


def test_226_supplement_prefix_streaming_and_no_assessment_side_effects(lesson):
    e = lesson
    before = {k: copy.deepcopy(e.store.records(k, PLAN)) for k in
              ('credits', 'judgements', 'question_answers', 'question_bank', 'teacher_profile')}
    e.room.ask(e.room._course(PLAN), e.state, e.ctx, '没有获奖该怎样回答？')
    row = e.store.messages(PLAN, 'm0')[-1]
    assert row['kind'] == 'teach_supplement'
    assert row['content'].startswith('讲义里没有，下面是补充')
    assert row['content'].count('讲义里没有，下面是补充') == 1
    assert '看原文' not in row['content'] and 'mf-source:' not in row['content']
    assert '【依据】' not in ''.join(e.stream)
    assert {k: e.store.records(k, PLAN) for k in before} == before
    history = e.room._dialogue(e.room._course(PLAN), e.state)
    assert '讲义里没有，下面是补充' in history[-1]['text']


@pytest.mark.parametrize('status', ['not_ready', 'unavailable', 'partial'])
def test_226_rag_failure_is_local_search_not_missing_evidence(lesson, status):
    e = lesson
    e.services.rag.search_plan = lambda *a, **k: dict(status=status, passages=[])
    e.response = '【依据】material:c2【/依据】最终一致性保证在一定时间后达到一致。'
    e.room.ask(e.room._course(PLAN), e.state, e.ctx, '最终一致性是什么？')
    assert 'mf-source:outside-point' in e.store.messages(PLAN, 'm0')[-1]['content']
    assert len(e.requests) == 1


def test_226_active_teaching_and_pending_grading_contract_stay_separate(lesson):
    e = lesson
    co = e.room._course(PLAN)
    plain = e.room._teacher_context(co, e.state, '讲本点', ['p0'])
    assert not plain.get('qa')
    assert teacher_agent.parse_teaching('压力相同时面积越小压强越大。[c1]', plain).answer_basis == 'lesson'
    e.state['step'] = 'testing'
    e.state['pending'] = dict(question=dict(id='q', point_ids=['p0']), point_ids=['p0'], answer_id='test-answer')
    old = copy.deepcopy(e.state['pending'])
    e.response = '【依据】supplement【/依据】这是课外的背景说明，回到原题自行作答。'
    e.room.ask(co, e.state, e.ctx, '这道题的背景是什么意思？')
    assert e.state['pending'] == old
    assert not e.store.records('judgements', PLAN)
    assert not e.store.records('credits', PLAN)


def test_226_ready_remote_chunk_is_attached_with_its_local_citation(lesson):
    e = lesson
    e.services.rag.search_plan = lambda *a, **k: dict(status='ready', passages=[
        dict(chunk_id='outside-point', text='最终一致性保证一定时间后达到一致。')])
    e.response = '【依据】material:c2【/依据】最终一致性保证一定时间后达到一致。'
    e.room.ask(e.room._course(PLAN), e.state, e.ctx, '这个词是什么意思？')
    assert 'mf-source:outside-point' in e.store.messages(PLAN, 'm0')[-1]['content']


def test_226_remote_full_chunk_does_not_replace_local_tail_evidence(lesson):
    e = lesson
    text = '前言。' * 2000 + '尾部独有标记解释业务校验。'
    e.store.add_sources(PLAN, [dict(id='long', kind='url', title='长资料', adopted=True,
        url='https://example.invalid/long', chunks=[dict(chunk_id='long-tail', text=text)])])
    e.services.rag.search_plan = lambda *a, **k: dict(status='ready', passages=[
        dict(chunk_id='long-tail', text=text)])
    course = e.room._course(PLAN)
    local = e.room._related_passages(course, {}, '尾部独有标记是什么？')
    merged = e.room._qa_passages(course, '尾部独有标记是什么？', local, e.ctx.cancel)
    assert '尾部独有标记' in merged['long-tail']


def test_226_explicit_question_rejects_lesson_and_rewrites_without_taught_profile(lesson):
    e = lesson
    answers = iter(['【依据】lesson【/依据】这是讲义以外的补充。',
                    '【依据】supplement【/依据】没有获奖也可以如实写项目经历。'])
    def answer(task):
        assert task.get('question_only') is True
        return next(answers)
    e.response = answer
    e.room.ask(e.room._course(PLAN), e.state, e.ctx, '没有获奖怎么写简历？')
    assert len(e.requests) == 2
    assert e.store.messages(PLAN, 'm0')[-1]['kind'] == 'teach_supplement'
    assert not e.store.records('teacher_profile', PLAN)
