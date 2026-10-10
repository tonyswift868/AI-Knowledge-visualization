from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from refactor.learning.course import teacher_agent
from refactor.learning.course.request_layout import ordered_payload
from tests.refactor.teacher_agent.test_class_teacher_agent import context
from .acceptance_audit import analyze, reorder, load_calls
from .audit import canonical, lcp


def call(identifier, messages, purpose='teacher', usage=None, system='fixed'):
    return dict(id=identifier, at='2026-09-30T01:00:00+00:00', status='received',
                purpose=purpose, system=system, messages=messages, usage=usage)


def test_new_page_source_change_preserves_identical_transcript_content():
    ctx = context(dialogue=[dict(role='user',text='同模块课堂原文'*100)])
    frozen = deepcopy(ctx)
    system, before = teacher_agent.teacher_request(ctx)
    ctx['excerpts'] = {'chunk-z':'执行器改变环境。'}
    _, after = teacher_agent.teacher_request(ctx)
    assert before[0] == after[0] and before[2:] == after[2:]
    assert '【当前模块原文' in before[1]['content']
    assert before[-1] == after[-1]
    assert json.loads(before[0]['content'].split('\n',1)[1]) == teacher_agent._skeleton(frozen)
    assert list(json.loads(before[0]['content'].split('\n',1)[1])) == ['plan','module']
    assert [json.loads(m['content'].split('\n',1)[1]) for m in before[2:-1]] == teacher_agent._dialogue(frozen['dialogue'])
    assert json.loads(before[1]['content'].split('\n',1)[1]) == [
        dict(alias=alias,chunk_id=identifier,text=frozen['excerpts'][identifier])
        for alias,identifier in teacher_agent.citation_map(frozen).items()]
    assert system == teacher_agent.teacher_request(frozen)[0]


@pytest.mark.parametrize('data', [{'draft':{'text':'新稿'},'point':{'id':'p'},'extra':3},
                                 [{'draft':'甲','point':'p1'},{'draft':'乙','point':'p2'}]])
def test_ordered_fields_preserve_every_value_and_batch_order(data):
    text='【原标签】\n'+json.dumps(data,ensure_ascii=False)
    out=ordered_payload(text, ('point','draft'))
    assert out.startswith('【原标签】\n')
    assert json.loads(out.split('\n',1)[1]) == data
    records=json.loads(out.split('\n',1)[1])
    for record in records if isinstance(records,list) else [records]:
        assert list(record)[:2] == ['point','draft']


def test_current_teacher_has_no_draft_or_review_input():
    system,messages=teacher_agent.teacher_request(context())
    assert 'prepared_lesson' not in str(messages) and '备课稿' not in system


def test_reordering_saved_record_does_not_mutate_or_lose_messages():
    messages=[dict(role='user',content='【计划与当前模块】\n{"module":{},"plan":{}}'),
              dict(role='user',content='【本点原文（数据）】\n[]'),
              dict(role='user',content='【对话原文（数据）】\n{"role":"user","text":"原文"}'),
              dict(role='user',content='【本步任务】\n{"user":"本次"}')]
    old=call('1',messages)
    frozen=deepcopy(old)
    new=reorder(old)
    assert old == frozen and new['system'] == old['system']
    assert new['messages'][1] == old['messages'][2]
    assert new['messages'][2:] == [old['messages'][1],old['messages'][3]]
    assert json.loads(new['messages'][0]['content'].split('\n',1)[1]) == json.loads(old['messages'][0]['content'].split('\n',1)[1])


def test_missing_usage_not_counted_as_zero_but_request_still_forms_history():
    msg=[dict(role='user',content='完全同一份请求')]
    rows=[call('1',msg),call('2',msg,usage=dict(input_tokens=100,cached_input_tokens=64))]
    result=analyze(rows)
    assert result['total']['missing_calls'] == 1
    assert result['total']['input_tokens'] == 100
    assert result['total']['actual_rate'] == .64
    assert result['requests'][1]['prefix_tokens_scaled'] == 100
    assert result['requests'][1]['cause'] == 'identical_prompt'
    assert result['total']['attributed_structure_est'] + result['total']['unattributed_est'] == 36


def test_system_variant_and_dialogue_window_are_distinct_causes():
    source=dict(role='user',content='【本点原文】\n{"c":"资料"}')
    r=analyze([call('1',[source],system='固定'),call('2',[source],system='固定 双点')])
    assert r['requests'][1]['cause'] == 'system_variant'
    rows=[call(str(i),[dict(role='user',content='【对话原文（数据）】\n'+json.dumps(dict(role='user',text=text)))])
          for i,text in enumerate(['旧起点','新起点'])]
    assert analyze(rows)['requests'][1]['cause'] == 'dialogue_window_reset'


def test_call_loader_uses_timestamp_and_excludes_planning(tmp_path):
    for r in [call('2',[],purpose='generate'),call('3',[]),call('1',[])]:
        (tmp_path/(r['id']+'.json')).write_text(json.dumps(r),encoding='utf-8')
    assert [r['id'] for r in load_calls(tmp_path)] == ['1','3']
