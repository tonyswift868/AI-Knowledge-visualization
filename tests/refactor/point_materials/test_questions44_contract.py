from types import SimpleNamespace
import threading
import pytest
from refactor.learning.course import point_materials as pm
from refactor.learning.course import question_bank as qb
from refactor.learning.course.source_quality import question_priority


def passage(cid='c',level=1,qa=False):
    text='1. 压强单位是？\nA. 帕\nB. 牛顿\nC. 焦耳\n答案：A\n压强是压力与受力面积的比值。'
    return dict(id=cid,source_id=cid,text=text,section='',authority=dict(level=level,type='general_site'),
        source=dict(url='https://example.test/'+('interview-questions' if qa else cid),title='问答' if qa else '教材练习',locator='1'))


def found(cid='c',kind='choice'):
    return dict(kind=kind,stem='压强单位是？',answer=[0],options=['帕','牛顿','焦耳'],answer_quote='A',
        explanation='',pairing='same_block',question_chunk=cid,answer_chunk=cid)


POINT=dict(id='p',module_id='m',importance='normal')


def test_questions44_high_bands_stop_before_qa_without_cloze():
    passages=[passage('high',1),passage('second',2),passage('normal',3),passage('qa',3,qa=True)]
    calls=[]
    def finder(point,ps,**kw):
        calls.append(ps[0]['id'])
        # Independent standard-answer source questions.
        for p in ps:p['text']='\n'.join(f'{i}. 题{i}？\nA. 帕\nB. 牛顿\nC. 焦耳\n答案：A' for i in range(3))
        return dict(passages=[ps[0]['id']],found=[dict(found(ps[0]['id']),stem=f'题{i}？') for i in range(3)],cloze=[])
    # The stored originals already contain these three questions.
    passages[0]['text']='\n'.join(f'{i}. 题{i}？\nA. 帕\nB. 牛顿\nC. 焦耳\n答案：A' for i in range(3))
    result=pm._locate(POINT,passages,dict(finder=finder),'adopted')
    assert calls==['high'] and len(result['found'])==3


def test_questions44_third_qa_runs_after_normal_even_if_more_relevant():
    calls=[]
    def finder(point,ps,**kw):calls.append(ps[0]['id']);return dict(passages=[],found=[],cloze=[])
    pm._locate(POINT,[passage('qa',3,qa=True),passage('c',3)],dict(finder=finder),'adopted')
    assert calls==['c']  # one bounded batch, no publisher bands
    assert question_priority('https://zhidao.baidu.com/q',authority=dict(level=3))==(3,True)


@pytest.mark.parametrize('kind',['short','essay'])
def test_questions44_subjective_locator_cannot_enter_bank(kind):
    assert pm._found(found(kind=kind),POINT,{'c':passage()}) is None


@pytest.mark.parametrize('value,valid',[('Pa',True),('帕',True),('0.5',True),('数据库',True),('central processing unit',True),
    ('Because the pressure is high.',False),('因为压力变大，所以压强变大',False),('Explain all four forms',False),('a'*33,False)])
def test_questions44_fixed_fill_terms(value,valid):assert qb.fixed_answer(value)==valid


@pytest.mark.parametrize('answer,valid',[('12 Pa',True),('1.5±0.01 N',True),('3/2 kg',True),('2e3',True),
    ('x=12 because F/A=12',False),('NaN',False),('1/0',False),('2 ± -0.1',False)])
def test_questions44_calc_requires_literal_standard_number(answer,valid):
    parsed=qb.numeric_reference(answer)
    assert bool(parsed)==valid
    if parsed:assert parsed['tolerance']>=0


def test_questions44_calc_metadata_and_original_tolerance():
    p=passage();p['text']='1. 计算压力？\n答案：12±0.1 N'
    item=dict(found(kind='calc'),stem='计算压力？',answer='12±0.1 N',answer_quote='12±0.1 N')
    row=pm._found(item,POINT,{'c':p})
    assert row['numeric_answer']=='12' and row['answer_unit']=='N' and row['tolerance']==.1
    qb.validate_question(row)
    with pytest.raises(ValueError):qb.validate_question(dict(row,tolerance=100))
    assert not qb.objective_question(dict(row,answer='原文未给出数值'))


@pytest.mark.parametrize('a,b',[('协议','网络协议'),('private','local'),('Pa','帕'),('CPU','central processing unit'),('kernel','kernel space')])
def test_questions44_synonyms_and_containment_block(a,b):assert qb.overlapping_options(a,b)


def test_questions44_ambiguity_group_is_not_a_fill_spelling():
    assert 'local' not in qb.answer_spellings('private')
    assert '网络协议' not in qb.answer_spellings('协议')
    assert 'processor' not in qb.answer_spellings('CPU')
    assert 'central processing unit' in qb.answer_spellings('CPU')


@pytest.mark.parametrize('sentence,word,valid',[
    ('CPU is the central processing unit that executes instructions.','CPU',False),
    ('CPU executes instructions; cpu is another capitalization.','CPU',False),
    ('压强越大，压强也越容易比较。','压强',False),
    ('压强是压力与受力面积的比值。','压强',True)])
def test_questions44_remaining_sentence_cannot_reveal_alias(sentence,word,valid):assert qb.safe_blank(sentence,word)==valid


def test_questions44_quick_filters_overlapping_distractors_not_whole_row():
    p=passage();p['text']='The private address is used within a network.'
    other=dict(p,id='other44',section='Other chapter',text='local public global public address')
    other['text']='Loopback is local. Routed is public. Broadcast is public address. Universal is global.'
    item=dict(target='本点原文定义是什么？',subject='address',chunk_id='c',sentence='The private address is used within a network.',word='private',contrasts=[
        dict(chunk_id='other44',contrast_quote=quote,contrast_subject=subj,contrast_value=value) for quote,subj,value in
        [('Loopback is local.','Loopback','local'),('Routed is public.','Routed','public'),('Broadcast is public address.','Broadcast','public address'),('Universal is global.','Universal','global')]])
    q=pm._quick(item,POINT,{'c':p},[p,other])
    assert q['options']==['global','private','public'] and q['role']=='quick'
    assert pm._quick(dict(item,contrasts=item['contrasts'][:1]),POINT,{'c':p},[p,other]) is None


def test_questions44_long_blank_uses_choice_not_fill():
    p=passage();word='a uniquely written but very long technical phrase'
    sentence='The term '+word+' identifies this abstraction.'
    p['text']=sentence
    other=dict(p,id='other44',section='Other chapter',text='Object B is beta. Object C is gamma.')
    item=dict(target='本点原文定义是什么？',subject='abstraction',chunk_id='c',sentence=sentence,word=word,importance='key',contrasts=[
        dict(chunk_id='other44',contrast_quote='Object B is beta.',contrast_subject='Object B',contrast_value='beta'),
        dict(chunk_id='other44',contrast_quote='Object C is gamma.',contrast_subject='Object C',contrast_value='gamma')])
    q=pm._cloze(item,dict(POINT,importance='key'),{'c':p},[p,other])
    assert q['kind']=='choice'


@pytest.mark.parametrize('enabled,state',[(False,'原文明确矛盾'),(True,'原文支持'),(True,'原文没提到')])
def test_questions44_selected_third_band_questions_never_get_model_correctness_review(enabled,state):
    # User 2026-10-05: selected material is trusted, including its own answers.
    high=passage('high',1);p=passage(level=3)
    calls=[]
    def finder(point,ps,**kw):
        return dict(passages=[p['id'] for p in ps],found=[found()],cloze=[])
    def comparator(packet,**kw):calls.append(packet);return state
    result=pm._locate(dict(POINT,chunk_ids=['high']),[high,p],dict(finder=finder,compare_questions=enabled,comparator=comparator),'adopted')
    assert not calls
    assert len(pm._collect(result['found'],pm._found,POINT,[high,p]))==1


def test_questions44_no_authority_never_calls_model_or_uses_third_as_evidence():
    comparator=lambda *a,**k:pytest.fail('third-band cannot be evidence')
    assert pm.compare_question(found(),[passage(level=3)],comparator=comparator)=='原文没提到'
    cancel=threading.Event();cancel.set()
    with pytest.raises(pm.ResearchCancelled):pm.compare_question(found(),[],cancel=cancel)


def test_questions44_selection_skips_subjective_history_prioritizes_band():
    rows=[]
    for cid,level,qa in [('hi',1,False),('mid',2,False),('lo',3,False),('qa',3,True)]:
        p=passage(cid,level,qa)
        rows.append(pm._found(found(cid),POINT,{cid:p}))
    assert all(row is not None for row in rows)
    rows=rows[:2]
    rows=pm.check_distractors(rows,[],[passage('hi',1),passage('mid',2)],dict(distractor_checker=lambda packet,**kw:
        dict(questions=[dict(id=q['id'],checks=[dict(index=i,verdict='not_supported',quote='') for i in range(len(q['options'])) if i not in q['answer']]) for q in packet['questions']])))
    subjective=dict(rows[0],id='old',kind='short',answer='旧主观题');subjective.pop('options')
    chosen=qb.select_question([subjective,*reversed(rows)],'p','module')
    assert chosen['id']==min(rows,key=lambda q:(q['created_at'],q['id']))['id']
    assert qb.select_question([subjective],'p','module') is None


def test_questions44_classroom_history_cannot_bypass_objective_gate():
    from refactor.learning.app.classroom import Classroom
    q=dict(pm._found(found(),POINT,{'c':passage()}),id='historical',kind='short',answer='历史参考答案')
    q.pop('options')
    def records(entity,plan):
        return [q] if entity=='question_bank' else [dict(question_id=q['id'],point_ids=['p'],context='module_test')]
    store=SimpleNamespace(records=records,require_material_plan=lambda _:None)
    course=SimpleNamespace(store=store,plan_id='fixture',point=lambda _:POINT,points_of=lambda _: [POINT])
    room=object.__new__(Classroom)
    room._materials=lambda:pm
    prepared=[]
    room._prepare_one=lambda *args:prepared.append(True) or ''
    assert room._pick(course,'p','retest') is None and prepared==[True]
    assert q['kind']=='short'  # no rewrite or deletion of history


@pytest.mark.parametrize('answer,valid', [('3.2 km', True), ('1.5 ± 0.01 N', True), ('5 个', False), ('9.8 m/s^2', False)])
def test_questions44_calc_bank_admits_only_units_the_grader_checks(answer, valid):
    # Integration of 44-MF02 (admission) with 44-MF03 (program grading): a calc
    # row the classroom cannot grade would only be skipped there.
    row = dict(kind='calc', answer=answer, **(qb.numeric_reference(answer) or {}))
    assert qb.objective_question(row) is valid
