from copy import deepcopy
import json

from refactor.learning.course.request_layout import AppendOnlyPage
from refactor.learning.course import teacher_agent, profile_agent
from tests.refactor.teacher_agent.test_class_teacher_agent import context
from tests.refactor.profile_agent.test_class_profile_agent import context as profile_context, response
from .diagnose_prefix import normalized
from tests.refactor.app.test_app_classroom import room  # isolated synthetic course fixture
from refactor.learning.app.classroom import _Course
from unittest.mock import patch


def test_teacher_reuses_exact_request_and_public_output_before_mutable_tail():
    ctx = context(request_page=AppendOnlyPage())
    system, first = teacher_agent.teacher_request(ctx)
    raw = '  传感器接收环境信息 [c1]。\n'
    ctx['request_page'].accept(system, first, teacher_agent._dialogue(ctx['dialogue']), raw)
    ctx['dialogue'].append(dict(role='assistant', text='传感器接收环境信息 [〔1〕](mf-source:chunk-a)。'))
    ctx.update(director='讲解 p2。', user='接下来呢', excerpts={'chunk-z':'执行器改变环境。'}, profile='新档案')
    _, after = teacher_agent.teacher_request(ctx)
    assert after[:len(first)] == first
    assert after[len(first)] == dict(role='assistant', content=raw)
    assert 'chunk-z' in after[-2]['content'] and 'chunk-a' not in after[-2]['content']
    assert json.loads(after[-1]['content'].partition('\n')[2])['profile'] == '新档案'
    # All current information still occurs in order; historical task/source are
    # additional original request bytes, not edited/merged current snapshots.
    legacy = teacher_agent.teacher_request({k:v for k,v in ctx.items() if k != 'request_page'})[1]
    assert after[0] == legacy[0]
    assert after[-3:] == [legacy[-2], legacy[1], legacy[-1]]


def test_profile_uses_original_json_not_derived_display_text_and_latest_snapshot():
    ctx = profile_context(request_page=AppendOnlyPage())
    system, first = profile_agent.profile_request(ctx)
    raw = response(ctx)
    profile_agent.parse_profile(raw, ctx)
    ctx['request_page'].accept(system, first, teacher_agent._dialogue(ctx['dialogue']), raw)
    ctx.update(progress='新的宿主进度', weak_point_ids=[], profile='新档案')
    _, after = profile_agent.profile_request(ctx)
    assert after[:len(first)] == first
    assert after[len(first)]['content'] == raw
    assert json.loads(after[-1]['content'].partition('\n')[2])['progress'] == '新的宿主进度'
    assert profile_agent.parse_profile(response(ctx), ctx).progress == '新的宿主进度'


def test_identical_latest_sources_reused_but_return_to_old_alias_map_is_appended():
    ctx = context(request_page=AppendOnlyPage())
    system, first = teacher_agent.teacher_request(ctx)
    ctx['request_page'].accept(system, first, teacher_agent._dialogue(ctx['dialogue']), '原回复')
    _, same = teacher_agent.teacher_request(ctx)
    assert sum(m['content'].startswith('【当前模块原文（数据）】') for m in same) == 1
    ctx['excerpts']={'chunk-z':'执行器改变环境。'}
    _, other = teacher_agent.teacher_request(ctx)
    ctx['request_page'].accept(system, other, teacher_agent._dialogue(ctx['dialogue']), '第二回复')
    ctx['excerpts']=context()['excerpts']
    _, restored = teacher_agent.teacher_request(ctx)
    assert restored[-2] == first[1]


def test_page_resets_on_module_system_transcript_edit_and_overflow():
    for change in ('module', 'system', 'dialogue', 'cap'):
        page = AppendOnlyPage(max_messages=8)
        ctx = context(request_page=page)
        system, first = teacher_agent.teacher_request(ctx)
        page.accept(system, first, teacher_agent._dialogue(ctx['dialogue']), '原回复')
        if change == 'module': ctx['module'] = {**ctx['module'], 'id':'module-b'}
        elif change == 'system': ctx['prefs']['teacher_style']='strict'
        elif change == 'dialogue': ctx['dialogue'] = ctx['dialogue'][1:]
        else: ctx['dialogue'] += [dict(role='user', text='追加')]*4
        expected = teacher_agent.teacher_request({k:v for k,v in ctx.items() if k != 'request_page'})
        assert teacher_agent.teacher_request(ctx) == expected


def test_snapshot_is_immutable_and_fallback_not_cached():
    page = AppendOnlyPage()
    ctx = context(request_page=page)
    system, first = teacher_agent.teacher_request(ctx)
    expected = deepcopy(first)
    page.accept(system, first, teacher_agent._dialogue(ctx['dialogue']), '原回复')
    first[1]['content']='调用方后来修改'
    page.accept(system, first, [], '')
    assert teacher_agent.teacher_request(ctx)[1][:len(expected)] == expected


def test_pending_answer_stays_host_only_and_guard_is_unchanged():
    page = AppendOnlyPage()
    ctx = context(request_page=page, pending_question=dict(stem='机器人用什么感知？', options=[]))
    system, messages = teacher_agent.teacher_request(ctx)
    assert 'current_points' in messages[-1]['content']
    assert 'reference' not in messages[-1]['content']
    assert '判定' in system
    # No accept on a rejected output: it cannot become the next assistant turn.
    assert teacher_agent.teacher_request(ctx) == (system, messages)


def test_prefixcash_translation_preserves_all_input_and_unknown_is_not_zero():
    row = dict(purpose='teacher', system='固定', messages=[], usage=dict(input_tokens=120,
               cached_input_tokens=100, output_tokens=5, raw=dict(input_tokens=20,cache_read_input_tokens=100)))
    translated, known = normalized(row)
    assert known and translated['usage'] == dict(input_tokens=120,cache_read_tokens=100,output_tokens=5)
    assert not normalized({**row,'usage':None})[1]


def test_real_host_accepts_only_parsed_output_and_reuses_it_next_turn(room):
    classroom, agents, store = room
    samples=[]
    complete=agents.complete
    def capture(purpose,messages,**kwargs):
        result=complete(purpose,messages,**kwargs)
        samples.append((deepcopy(messages),result.text))
        return result
    agents.complete=capture
    agents.teacher_format_errors=1
    plan_id = 'a'*32
    course = _Course(store,store.get_plan(plan_id))
    state=dict(module_id='m0',index=0,pending=None,lesson=None)
    classroom._teacher(course,state,None,'简短讲解 p0，不提问。',['p0'])
    first=samples[-1]
    classroom._teacher(course,state,None,'简短讲解 p1，不提问。',['p1'])
    second=samples[-1]
    assert second[0][:len(first[0])] == first[0]
    assert second[0][len(first[0])] == dict(role='assistant',content=first[1])
    assert not any(m['content']==samples[0][1] for m in second[0])
    assert not store.records('judgements',plan_id)
