"""Machine reasons survive privacy formatting; ready speculation stays final."""
import copy,json,logging,threading
from types import SimpleNamespace
import pytest
from refactor.runtime_logging import DiagnosticFormatter
from refactor.learning.app.classroom import Classroom,RESET,TODAY_EVENT
from refactor.learning.course import teacher_agent as agent
from tests.refactor.teacher_agent.test_class_teacher_agent import context
from tests.refactor.concept114.test_concept114_classroom import concept_environment
from tests.refactor.conversation37.test_grounded_conversation37 import act
from tests.refactor.speed116.test_classroom_speed116 import prepare,wait_jobs

GOOD='==单位面积上的压力叫压强。==你会怎样比较面积？'

def case(reason):
    ctx=context()
    ctx['concept']=dict(notes=[dict(text='单位面积上的压力叫压强。',chunk_id='chunk-a')],
        first_turn=False,recent_styles=[])
    bad={
        'STATE_CLAIM':'我已经修改计划。',
        'CHINESE_REQUIRED':'A clear explanation.',
        'PARSE_FAILED':'',
        'HIGHLIGHT_COUNT':'中文说明没有荧光。',
        'HIGHLIGHT_QUESTION':'==你怎么看？==',
        'TOO_LONG':'==单位面积上的压力叫压强。=='+('对照压力与面积理解这个前提。'*35),
        'WRAPUP_TOO_LONG':'==单位面积上的压力叫压强。=='+('对照压力与面积理解这个前提，'*8)+'结论。',
        'WRAPUP_SENTENCE_COUNT':'==第一条结论。==第二条结论。第三条结论。',
        'WRAPUP_QUESTION':'==先认识压强。==你怎么看？',
        'REPEATED_ENDING':'==先认识压强。==这一层先把定义记清。',
        'REPEATED_OPENING':'这块可以这样理解。==单位面积上的压力叫压强。==',
    }[reason]
    if reason.startswith('WRAPUP'):ctx['concept']['wrapping']=True
    if reason=='REPEATED_ENDING':ctx['concept'].update(first_turn=True,previous_intro_ending='summary')
    if reason=='REPEATED_OPENING':ctx['concept']['recent_styles']=[dict(lead='这块可以这样理解。')]
    return ctx,bad

REASONS=['STATE_CLAIM','CHINESE_REQUIRED','PARSE_FAILED','TOO_LONG']

@pytest.mark.parametrize('reason',REASONS)
@pytest.mark.parametrize('prefetched',[False,True])
def test_teacher138_each_rule_logs_machine_reason_without_reply(reason,prefetched,caplog):
    ctx,bad=case(reason)
    good='==单位面积上的压力叫压强。==' if reason.startswith('WRAPUP') else GOOD
    replies=iter([bad,good]);calls=[]
    def complete(*args,**kw):
        calls.append(args);text=next(replies);kw['on_text'](text);return SimpleNamespace(text=text)
    room=Classroom(SimpleNamespace(gateway=SimpleNamespace(complete=complete)),lambda:None)
    emitted=[]
    with caplog.at_level(logging.INFO,logger='refactor.runtime'):
        result=room._generate_teacher('plan',ctx,agent.teacher_request(ctx),None,emitted.append,prefetched=prefetched)
    records=[r for r in caplog.records if getattr(r,'mf_event',None)=='classroom']
    payloads=[json.loads(DiagnosticFormatter().format(r)) for r in records]
    assert len(payloads)==1
    payload=payloads[0]
    assert (payload['state'],payload['reason'],payload['count'])==('TEACHER_REJECTED',reason,1)
    assert payload['code']==('PREFETCH' if prefetched else 'FOREGROUND')
    assert payload['purpose']=='teacher'
    assert not any(key in payload for key in ('message','text','speech','content','exception_type'))
    assert not bad or bad not in json.dumps(payload,ensure_ascii=False)
    assert result[0].speech==good and len(calls)==2 and RESET in emitted

def test_teacher138_second_rejection_is_logged_and_kept_without_truncation(caplog):
    ctx,bad=case('TOO_LONG')
    gateway=SimpleNamespace(complete=lambda *a,**k:SimpleNamespace(text=bad))
    room=Classroom(SimpleNamespace(gateway=gateway),lambda:None)
    with caplog.at_level(logging.INFO,logger='refactor.runtime'):
        result=room._generate_teacher('plan',ctx,agent.teacher_request(ctx),None,lambda _:None)
    fields=[r.mf_fields for r in caplog.records if getattr(r,'mf_event',None)=='classroom']
    assert [r['count'] for r in fields]==[1,2]
    assert all(r['reason']=='TOO_LONG' for r in fields)
    assert result[0].speech==bad

def test_teacher138_prompts_match_existing_limits_and_explain_existing_endings():
    ctx,_=case('REPEATED_ENDING');system,messages=agent.teacher_request(ctx)
    assert '350–550' not in system and '700–1000' not in system
    assert '全部可见正文' in system and '不超过350字' in system
    task=json.loads(messages[-1]['content'].split('\n',1)[1])
    assert task['concept']['allowed_intro_endings']==['question','task','transition']
    expanded=context(user='请详细展开讲讲')
    agent.validate_presentation('==这是结论。=='+'必要解释。'*100,expanded)

@pytest.mark.parametrize('text',['原文那条排序只有一行。','讲义里只有两句。','正文只有 3 段。',
    '这一段其实只有两个问题：谁大概更快，为什么。','本轮只有三个要点。'])
def test_teacher138_absolute_wording_is_accepted(text):
    # User 2026-10-15: no absolute-word rewrite at all.
    ctx,_=case('TOO_LONG')
    assert agent.parse_teaching(text,ctx).speech==text
    strong=text+'系统只能做这一件事。'
    assert agent.parse_teaching(strong,ctx).speech==strong

def test_teacher138_mixed_language_counts_terms_not_letters_and_keeps_length_guard():
    text='==原文介绍三个删除命令。=='+'\n'.join(['比较 `TRUNCATE` 和 `DELETE`。']*20)
    metrics=agent.reply_metrics(text)
    assert metrics['characters']>350 and metrics['word_count']<350
    agent.validate_presentation(text,context())
    with pytest.raises(agent.TeachingFormatError) as error:
        agent.validate_presentation(text+'这里补充必要解释。'*40,context())
    assert error.value.reason=='TOO_LONG'

def test_teacher138_ready_prefetch_has_rewritten_before_click_no_reset_or_extra_call(tmp_path,monkeypatch,caplog):
    env=concept_environment(tmp_path);monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:env.materials))
    calls=[];lock=threading.Lock()
    try:
        act(env,'next')
        before=copy.deepcopy(env.store.pending_records(10000))
        def complete(*args,**kw):
            repair=any('上一条回复格式不合格' in m['content'] for m in args[1])
            with lock:calls.append(repair)
            text=GOOD if repair else '==单位面积上的压力叫压强。=='+('对照压力与面积理解这个前提。'*35)+'你会怎样比较面积？'
            kw['on_text'](text);return SimpleNamespace(text=text)
        monkeypatch.setattr(env.gateway,'complete',complete)
        with caplog.at_level(logging.INFO):
            assert prepare(env)['total']==2
            group=wait_jobs(env)
            assert all(j['done'] and not j['error'] for j in group['jobs'].values())
            assert calls.count(False)==calls.count(True)==2
            assert env.store.pending_records(10000)==before
            count=len(calls);env.stream.clear()
            result=act(env,'next')
        assert not result['failure'] and len(calls)==count
        progress=[json.loads(item[len(TODAY_EVENT):]) for item in env.stream if item.startswith(TODAY_EVENT)]
        assert len(progress)==1 and progress[0]['today_tentative']==5
        assert [item for item in env.stream if not item.startswith(TODAY_EVENT)]==[GOOD] and RESET not in env.stream
        reasons=[r.mf_fields for r in caplog.records
                 if getattr(r,'mf_event',None)=='classroom'
                 and r.mf_fields.get('code')=='PREFETCH']
        assert len(reasons)==2 and all(r['code']=='PREFETCH' and r['reason']=='TOO_LONG' for r in reasons)
        assert any(getattr(r,'mf_event',None)=='classroom'
                   and r.mf_fields.get('state')=='PREFETCH_HIT'
                   for r in caplog.records)
    finally:env.room.cancel_materials();env.store.close()
