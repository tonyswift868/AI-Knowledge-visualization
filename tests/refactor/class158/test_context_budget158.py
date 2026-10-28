"""Bound concept closing context to taught notes, with source IDs retained."""
import copy
import json

from refactor.learning.app.classroom import Classroom, CONCEPT_WRAP_CONTEXT_CHARS
from refactor.learning.course import teacher_agent
from tests.refactor.concept114.test_concept114_classroom import concept_environment
from tests.refactor.conversation37.test_grounded_conversation37 import PLAN


def _request_chars(ctx):
    system, messages = teacher_agent.teacher_request(ctx)
    return len(system) + sum(len(row['content']) for row in messages)


def _eighty_point_scene(root):
    env = concept_environment(root)
    chunk_ids = [f'budget158-{i:03d}' for i in range(80)]
    env.store.add_sources(PLAN, [dict(
        id='budget158-source', kind='url', url='https://example.invalid/budget158',
        title='固定80段测试来源', adopted=True,
        chunks=[dict(chunk_id=cid, text=f'第{i + 1}段原文完整说明。' + ('原文内容。' * 180), section=[f'第{i + 1}段'])
                for i, cid in enumerate(chunk_ids)])])
    with env.store.transaction():
        point = next(p for p in env.store.records('points', PLAN) if p['id'] == 'p0')
        point.update(summary='解释压力、面积和压强之间的关系。',
            goal='解释压力、面积和压强之间的关系。', minutes=160, chunk_ids=chunk_ids,
            subpoints=[dict(id=f'p0-s{i + 1}', title=f'小要点 {i + 1}', minutes=2,
                chunk_ids=[cid], notes=[dict(text='结论甲完整说明。结论乙补充条件。' * 4, chunk_id=cid)])
                for i, cid in enumerate(chunk_ids)])
        env.store._put('points', PLAN, point)
    course = env.room._course(PLAN)
    point = course.point('p0')
    state = env.room._load(course)
    state.update(step='closing', phase='learning', module_id=point['module_id'], index=0,
                 pending=None, direct_review=None,
                 concept=dict(point_id='p0', subpoint_id='p0-s80', wrapping=True, rounds=0))
    current = env.room._teacher_context(course, state, '收束。', ['p0'], allow_plan=True)
    before = copy.deepcopy(current)
    before['request_page'] = None
    before['excerpts'] = course.excerpts(['p0'])
    before['plan_passages'] = before['excerpts']
    before['concept']['notes'] = [dict(subpoint_title=sub['title'], **note)
        for sub in point['subpoints'] for note in sub['notes']]
    # Match the pre-budget request shape: no truncation counters or synthesized
    # subpoint-title list existed, while the same 80 adopted chunks and notes
    # were sent as full excerpts and concept notes.
    before['concept'].pop('subpoint_titles', None)
    before['concept'].pop('omitted_subpoint_titles', None)
    before['concept'].pop('omitted_note_sentences', None)
    return env, course, point, state, current, before


def test_context158_eighty_subpoints_wrap_request_is_bounded_and_smaller(tmp_path, monkeypatch):
    env, course, point, state, current, before = _eighty_point_scene(tmp_path)
    monkeypatch.setattr(Classroom, '_materials', staticmethod(lambda: env.materials))
    try:
        assert current['request_page'] is None
        assert current['plan_passages'] == current['excerpts']
        assert set(current['excerpts'])
        assert set(current['excerpts']).issubset(set(point['chunk_ids']))
        assert all(value == '讲义出处' for value in current['excerpts'].values())
        assert len(current['concept']['subpoint_titles']) == 80
        assert all('原文内容' not in value for value in current['excerpts'].values())
        assert all(row['subpoint_title'] and row['text'].endswith(('。', '！', '？', '.', '!', '?'))
                   for row in current['concept']['notes'])

        after_chars = _request_chars(current)
        before_chars = _request_chars(before)

        # The cap applies to the serialized titles, goal, note excerpts, and
        # provenance map; the full request also includes stable prompt/dialogue.
        bounded_bundle = dict(
            title=current['concept']['title'], goal=current['concept']['goal'],
            completed_titles=current['concept']['completed_titles'],
            title_current=current['concept']['title_current'],
            subpoint_titles=current['concept']['subpoint_titles'],
            omitted_subpoint_titles=current['concept']['omitted_subpoint_titles'],
            omitted_note_sentences=current['concept']['omitted_note_sentences'],
            subpoint_id=current['concept']['subpoint_id'], number=current['concept']['number'],
            total=current['concept']['total'], wrapping=current['concept']['wrapping'],
            first_turn=current['concept']['first_turn'], recent_styles=current['concept']['recent_styles'],
            previous_intro_ending=current['concept']['previous_intro_ending'],
            source_premises=[],
            notes=[dict(row, alias=next((f'c{i}' for i, cid in enumerate(sorted(current['excerpts']), 1)
                                        if cid == row['chunk_id']), None))
                   for row in current['concept']['notes']],
            excerpts=current['excerpts'])
        assert len(json.dumps(bounded_bundle, ensure_ascii=False, separators=(',', ':'))) <= CONCEPT_WRAP_CONTEXT_CHARS
        assert before_chars > after_chars * 4
    finally:
        env.room.cancel_materials()
        env.store.close()


def test_context158_concept_subpoint_does_not_expand_to_plan_passages(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(Classroom, '_materials', staticmethod(lambda: env.materials))
    try:
        course = env.room._course(PLAN)
        state = env.room._load(course)
        state.update(step='teaching', phase='learning', module_id=course.points[0]['module_id'], index=0,
                     pending=None, direct_review=None,
                     concept=dict(point_id='p0', subpoint_id='p0-s1', wrapping=False, rounds=1))
        current = env.room._teacher_context(course, state, '回答插话。', ['p0'], user='请解释', allow_plan=True)
        assert current['request_page'] is None
        assert set(current['excerpts']) == {'c0'}
        assert current['plan_passages'] == current['excerpts']
    finally:
        env.room.cancel_materials()
        env.store.close()


def test_context158_oversized_titles_and_notes_cannot_bypass_wrap_budget(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(Classroom, '_materials', staticmethod(lambda: env.materials))
    try:
        course = env.room._course(PLAN)
        point = course.point('p0')
        point['subpoints'] = [dict(id=f'p0-s{i+1}', title='很长的小要点标题'*300,
            minutes=2, chunk_ids=['c0'], notes=[dict(text='完整讲义句子。'*1000, chunk_id='c0')])
            for i in range(80)]
        state = env.room._load(course)
        state.update(step='closing', concept=dict(point_id='p0',subpoint_id='p0-s80',wrapping=True,rounds=1))
        current = env.room._teacher_context(course,state,'收束。',['p0'])
        material = {k:v for k,v in current['concept'].items()}
        material['excerpts'] = current['excerpts']
        assert len(json.dumps(material,ensure_ascii=False,separators=(',',':'))) <= CONCEPT_WRAP_CONTEXT_CHARS
        assert current['concept']['omitted_subpoint_titles'] == 80
        assert not current['concept']['notes']
    finally:
        env.room.cancel_materials()
        env.store.close()


def test_context158_pending_question_uses_its_subpoint_in_actual_teacher_request(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(Classroom, '_materials', staticmethod(lambda: env.materials))
    try:
        course = env.room._course(PLAN)
        state = env.room._load(course)
        state.update(step='testing', phase='learning', module_id=course.points[0]['module_id'], index=0,
                     pending=dict(question=dict(subpoint_id='p0-s2', chunk_ids=['c0'])),
                     direct_review=None, concept=dict(point_id='p0', subpoint_id='p0-s1', rounds=2))
        # Explicit question subpoint wins even when its provenance overlaps
        # another small point's chunk.
        current = env.room._teacher_context(course, state, '解释当前题目以外的概念。', ['p0'], user='为什么？', allow_plan=True)
        assert set(current['excerpts']) == {'c1'}
        assert current['request_page'] is None
        request = teacher_agent.teacher_request(current)
        # The QA request should carry the selected subpoint's source text.
        # The title is not itself source content and need not be sent verbatim.
        assert '单位面积上的压力叫压强。' in '\n'.join(row['content'] for row in request[1])
    finally:
        env.room.cancel_materials()
        env.store.close()


def test_context158_direct_review_restores_saved_subpoint_scope_in_request(tmp_path, monkeypatch):
    env = concept_environment(tmp_path)
    monkeypatch.setattr(Classroom, '_materials', staticmethod(lambda: env.materials))
    try:
        course = env.room._course(PLAN)
        state = env.room._load(course)
        state.update(step='direct_review', phase='learning', module_id=course.points[0]['module_id'], index=0,
                     pending=None, concept=None,
                     direct_review=dict(resume=dict(concept=dict(point_id='p0', subpoint_id='p0-s2'))))
        current = env.room._teacher_context(course, state, '根据本点原文随口提问。', ['p0'])
        assert set(current['excerpts']) == {'c1'}
        assert current['request_page'] is None
        request = teacher_agent.teacher_request(current)
        assert '定义压强' in '\n'.join(row['content'] for row in request[1])
    finally:
        env.room.cancel_materials()
        env.store.close()
