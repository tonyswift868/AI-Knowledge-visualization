"""Selection, not remote discovery, is the question preparation boundary."""
import copy
import pytest
from refactor.learning.course import point_materials as pm
from test_point_materials37_contract import store, ctx, pointer, TEXT


def forbid50(*args,**kwargs):
    raise AssertionError('Question preparation must not search or fetch')


def test_selected50_deleted_discovery_and_full_cloze_module_never_searches(store):
    assert all(hasattr(pm,name) for name in ('_discover_questions','question_queries','_excerpt_candidates','_save_excerpts'))
    context=dict(ctx(),search=forbid50,fetcher=forbid50,extra_search=True)
    class NoResearch:
        def complete(self,purpose,*args,**kwargs):
            assert purpose not in ('research','source_scoring')
            raise AssertionError('Explicit finder/checker should handle this call')
    context['gateway']=NoResearch()
    result=pm.prepare_module(store,'p','m',context,lambda *args:None)
    assert result==dict(points=1,cloze_points=1,cloze_heavy=True)
    assert pm.prepare_point(store,'p',store.records('points','p')[0],context)['questions']==2


def test_selected50_original_answered_questions_win_over_cloze(store):
    extra='\n2. 压强的单位符号是____？\n答案：Pa。'
    with store.transaction():
        # Use the public source replacement to retain real storage validation.
        store.add_sources('p',[dict(id='s',title='物理教材',url='https://www.pep.com.cn/physics/pressure',adopted=True,
            chunks=[dict(chunk_id='c',text=TEXT+extra,section=['第九章 压强']),
                    dict(chunk_id='other37',text=__import__('test_point_materials37_contract').CONTRAST37,section=['运动学'])])])
    output=pointer()
    output['found']=[dict(kind='fill',stem=stem,answer=[[answer]],answer_quote=answer+'。',explanation='',
        question_chunk='c',answer_chunk='c',pairing='same_block')
        for stem,answer in [('压强单位是____？','帕'),('压强的单位符号是____？','Pa')]]
    result=pm.prepare_point(store,'p',store.records('points','p')[0],dict(ctx(output),search=forbid50,fetcher=forbid50))
    assert result['questions']==2 and result['cloze']==0
    rows=store.records('question_bank','p')
    assert [q['origin'] for q in rows if q['role']=='bank']==['found','found']
    assert sum(q['role']=='quick' for q in rows)==1


@pytest.mark.parametrize('adopted,excerpt',[(False,False),(True,True)])
def test_selected50_old_external_questions_not_selected_or_deleted(store,adopted,excerpt):
    context=dict(ctx(),search=forbid50,fetcher=forbid50)
    point=store.records('points','p')[0]
    pm.prepare_point(store,'p',point,context)
    old=copy.deepcopy(store.records('question_bank','p')[0])
    old.update(id='historic-external',source=dict(title='旧自动找到的题',url='https://www.pep.com.cn/external',locator='外部题'),
               answer_source=dict(title='旧自动找到的题',url='https://www.pep.com.cn/external',locator='外部题'))
    store.add_sources('p',[dict(id='external',title='旧自动资料',url=old['source']['url'],adopted=adopted,excerpt=excerpt,
        chunks=[dict(chunk_id='external-c',text='外部题：'+TEXT,original_locator='外部题')])])
    store.add_bank_questions('p','m',[old])
    eligible=[q['id'] for q in store.records('question_bank','p') if q['id']!='historic-external']
    assert pm.pick_question(store,'p',point['id'],'module',eligible) is None
    assert any(q['id']=='historic-external' for q in store.records('question_bank','p'))


def test_selected50_exercise_elsewhere_in_selected_book_can_pair(store):
    passages=pm._passages(store,'p',dict(store=store,plan_id='p',lists=pm.load_lists()))
    other=next(p for p in passages if p['id']=='other37')
    text='1. 速度是____与时间的比值？\n答案：位移。'
    other=dict(other,text=text)
    item=dict(kind='fill',question_chunk='other37',answer_chunk='other37',pairing='same_block',
        stem='速度是____与时间的比值？',answer=[['位移']],answer_quote='位移。',explanation='')
    assert pm._found(item,store.records('points','p')[0],{'other37':other})['origin']=='found'
