"""Explicit contrast pointers, delete-only review and durable selection gate."""
from datetime import date,timedelta
from threading import Event
from types import SimpleNamespace
import json
import pytest
from refactor.learning.course import point_materials as pm, question_bank as qb
from refactor.learning.course.store import CourseStore,StoreError
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.connections.gateway import PURPOSE_EFFORT

TEXT48='队列按先进先出处理。队列中先加入的元素先取出。栈按后进先出处理。集合按任意顺序处理。栈中后加入的元素先取出。集合中随机取出元素。'

def data48():
    source=dict(title='算法教材',url='https://school.edu/text',locator='第1段')
    chunk=dict(id='c',source_id='s',text=TEXT48,section='数据结构',source=source,authority={'level':1})
    point=dict(id='pt',module_id='m',chunk_ids=['c'],importance='key',summary='掌握队列先进先出的处理顺序')
    contrasts=[dict(chunk_id='c',contrast_quote='栈按后进先出处理。',contrast_subject='栈',contrast_value='后进先出'),
        dict(chunk_id='c',contrast_quote='集合按任意顺序处理。',contrast_subject='集合',contrast_value='任意顺序')]
    item=dict(target='队列按什么顺序处理？',subject='队列',chunk_id='c',sentence='队列按先进先出处理。',word='先进先出',importance='key',contrasts=contrasts)
    second=dict(item,target='队列先取出哪一类元素？',sentence='队列中先加入的元素先取出。',word='先加入的元素先取出',contrasts=[
        dict(chunk_id='c',contrast_quote='栈中后加入的元素先取出。',contrast_subject='栈',contrast_value='后加入的元素先取出'),
        dict(chunk_id='c',contrast_quote='集合中随机取出元素。',contrast_subject='集合',contrast_value='随机取出元素')])
    return point,chunk,item,second

def approved48(packet,**kw):
    return dict(questions=[dict(id=q['id'],checks=[dict(index=i,verdict='not_supported',quote='')
        for i in range(len(q['options'])) if i not in q['answer']]) for q in packet['questions']])

def test_contrast48_same_paragraph_is_valid_when_it_explicitly_names_other_objects():
    p,c,i,_=data48();q=pm._cloze(i,p,{'c':c},[c])
    assert q['kind']=='choice' and len(q['options'])==3
    assert q['options'][q['answer'][0]]=='先进先出'
    assert q['contrast_evidence']==i['contrasts'] and q['cloze_subject']=='队列'
    assert not qb.checked_question(q)
    assert '只找两道' in pm._SYSTEM and 'contrast_subject' in pm._SYSTEM
    assert PURPOSE_EFFORT['distractor_check']=='high'

@pytest.mark.parametrize('changes,reason',[
    ({'contrast_subject':'队列'},'对象'),
    ({'contrast_subject':'特殊队列'},'原文'),
    ({'contrast_value':'先进先出'},'原文'),
    ({'contrast_quote':'栈不按先进先出处理。'},'原文'),
    ({'contrast_subject':'不存在'},'原文'),
])
def test_contrast48_bad_contrast_never_counts_as_a_distractor(changes,reason):
    p,c,i,_=data48();i['contrasts'][0].update(changes);rejected=[]
    assert pm._cloze(i,p,{'c':c},[c],rejected=rejected) is None
    assert reason in rejected[0]['reason']

def test_contrast48_subject_containment_answer_synonyms_and_foreign_documents_rejected():
    p,c,i,_=data48()
    c['text']+='特殊队列按后进先出处理。栈按FIFO处理。'
    i['contrasts'][0].update(contrast_quote='特殊队列按后进先出处理。',contrast_subject='特殊队列')
    assert pm._cloze(i,p,{'c':c},[c]) is None
    i['contrasts'][0].update(contrast_quote='栈按FIFO处理。',contrast_subject='栈',contrast_value='FIFO')
    assert pm._cloze(i,p,{'c':c},[c]) is None
    _,_,i,_=data48();other=dict(c,id='other',source_id='another')
    i['contrasts'][0]['chunk_id']='other'
    assert pm._cloze(i,p,{'c':c},[c,other]) is None

def test_contrast48_pointer_expansion_preserves_evidence_and_parallel_multi():
    p,c,i,_=data48();c['text']+='队列支持入队、出队和查看队首。栈支持压栈。集合支持求交集。'
    i.update(sentence={'quote':'队列支持入队、出队和查看队首。'},word={'quote':'入队、出队和查看队首'},subject={'quote':'队列'},
        answer_parts=[{'quote':'入队'},{'quote':'出队'},{'quote':'查看队首'}],contrasts=[
        dict(chunk_id='c',contrast_quote={'quote':'栈支持压栈。'},contrast_subject={'quote':'栈'},contrast_value={'quote':'压栈'}),
        dict(chunk_id='c',contrast_quote={'quote':'集合支持求交集。'},contrast_subject={'quote':'集合'},contrast_value={'quote':'求交集'})])
    output=pm._expand_output(dict(found=[],passages=['c'],cloze=[i]),[c])
    q=pm._cloze(output['cloze'][0],p,{'c':c},[c])
    assert q['kind']=='multi' and len(q['answer'])==3
    quick=pm._quick(output['cloze'][0],p,{'c':c},[c]);assert quick['kind']=='choice'

def review_question48():
    p,c,_,_=data48();c['text']+='甲支持A，也支持B。'
    q=pm._base(p,'choice','甲支持什么？',[1],'found',c,c,options=['B','A','C','D'])
    return p,c,q

def test_contrast48_review_only_deletes_and_remaps_indices_and_is_not_repeated():
    p,c,q=review_question48();calls=[]
    def check(packet,**kw):
        calls.append(packet)
        return dict(questions=[dict(id=q['id'],checks=[dict(index=0,verdict='also_supported',quote='甲支持A，也支持B。'),
            dict(index=2,verdict='not_supported',quote=''),dict(index=3,verdict='not_supported',quote='')])])
    result=pm.check_distractors([q],[p],[c],dict(distractor_checker=check))[0]
    assert result['options']==['A','C','D'] and result['answer']==[0] and result['stem']==q['stem']
    assert qb.checked_question(result) and result['distractor_check']['checks'][0]['option']=='B'
    assert pm.check_distractors([result],[p],[c],dict(distractor_checker=lambda *a:pytest.fail('rechecked')))==[result]
    assert qb.select_question([q],'pt','module')['id']==q['id']  # Original answered exercises need no cloze review.
    assert qb.select_question([result],'pt','module')['id']==q['id'] and len(calls)==1

@pytest.mark.parametrize('mode',['missing','duplicate','bad_quote','missing_option','correct_index','modified_options','too_few','malformed'])
def test_contrast48_uncertainty_discards_whole_question(mode):
    p,c,q=review_question48()
    def check(packet,**kw):
        result=approved48(packet);row=result['questions'][0]
        if mode=='missing':result['questions']=[]
        if mode=='duplicate':result['questions'].append(dict(row))
        if mode=='bad_quote':row['checks'][0].update(verdict='also_supported',quote='伪造原文')
        if mode=='missing_option':row['checks'].pop()
        if mode=='correct_index':row['checks'][0]['index']=1
        if mode=='modified_options':row['options']=['作弊新选项']
        if mode=='too_few':
            for check in row['checks'][:2]:check.update(verdict='also_supported',quote='甲支持A，也支持B。')
        if mode=='malformed':return 'not json'
        return result
    row=pm.check_distractors([q],[p],[c],dict(distractor_checker=check))[0]
    assert row['distractor_check']['state']=='rejected'
    assert qb.select_question([row],'pt','quick') is None and qb.select_question([row],'pt','module') is None

def test_contrast48_one_bad_answer_does_not_reject_an_independent_valid_question():
    p,c,q=review_question48();second=dict(q,id='second')
    def check(packet,**kw):return approved48(dict(questions=packet['questions'][1:]))
    rows=pm.check_distractors([q,second],[p],[c],dict(distractor_checker=check))
    assert [r['distractor_check']['state'] for r in rows]==['rejected','passed']

def test_contrast48_store_prepares_two_bank_and_one_quick_and_keeps_the_check(tmp_path):
    p,c,i,second=data48();seen=[]
    with CourseStore('check48',tmp_path) as store:
        day=date(2026,10,4)
        store.create_plan('p',goal='队列',window=PlanWindow(day,day+timedelta(days=2),100))
        store.add_sources('p',[dict(id='s',title=c['source']['title'],url=c['source']['url'],adopted=True,chunks=[dict(chunk_id='c',text=c['text'])])])
        store.save_outline('p',[dict(id='st',title='算法')],[dict(id='m',stage_id='st',title='队列')])
        store.save_generated_module('p','m',[dict(p,title='队列',minutes=15)],check_minutes=5,today=day)
        def check(packet,**kw):seen.append(packet);return approved48(packet)
        ctx=dict(allow_bank_cloze=True,finder=lambda *a,**kw:dict(passages=['c'],found=[],cloze=[i,second]),search=lambda *a,**kw:[],
            fetcher=lambda *a,**kw:(_ for _ in ()).throw(OSError('offline')),distractor_checker=check)
        point=store.records('points','p')[0]
        result=pm.prepare_point(store,'p',point,ctx)
        assert result['questions']==2 and len(seen)==1 and len(seen[0]['questions'])==3
        assert pm.prepare_point(store,'p',point,ctx)==result and len(seen)==1
        assert pm.pick_question(store,'p','pt','quick')['distractor_check']['state']=='passed'
        assert pm.pick_question(store,'p','pt','module')['distractor_check']['state']=='passed'
        assert all(qb.checked_question(q) for q in store.records('question_bank','p'))
    with CourseStore('check48',tmp_path) as store:
        assert pm.pick_question(store,'p','pt','module')['distractor_check']['state']=='passed'
        assert pm.prepare_point(store,'p',store.records('points','p')[0],ctx)==result and len(seen)==1


def create48(store, *, points=1, extended=False):
    p,c,i,second=data48();day=date(2026,10,4)
    if extended:c['text']+='队列的元素按进入次序移出。栈的元素按储存次序移出。集合的元素按随机次序移出。'
    store.create_plan('p',goal='队列',window=PlanWindow(day,day+timedelta(days=2),100))
    store.add_sources('p',[dict(id='s',title=c['source']['title'],url=c['source']['url'],adopted=True,chunks=[dict(chunk_id='c',text=c['text'])])])
    store.save_outline('p',[dict(id='st',title='算法')],[dict(id='m',stage_id='st',title='队列')])
    store.save_generated_module('p','m',[dict(p,id='pt'+str(n),title='队列',minutes=15) for n in range(points)],check_minutes=5,today=day)
    return i,second


def test_contrast48_module_checks_all_points_and_quick_in_one_batch(tmp_path):
    with CourseStore('batch48',tmp_path) as store:
        i,second=create48(store,points=2);seen=[]
        def checker(packet,**kw):seen.append(packet);return approved48(packet)
        # Adjacent points must test distinct facts, while still sharing review.
        stack=dict(i,target='栈按什么顺序处理？',subject='栈',sentence='栈按后进先出处理。',word='后进先出',
            contrasts=[dict(chunk_id='c',contrast_quote='队列按先进先出处理。',contrast_subject='队列',contrast_value='先进先出'),
                dict(chunk_id='c',contrast_quote='集合按任意顺序处理。',contrast_subject='集合',contrast_value='任意顺序')])
        stack_second=dict(second,target='栈先取出哪一类元素？',subject='栈',sentence='栈中后加入的元素先取出。',word='后加入的元素先取出',
            contrasts=[dict(chunk_id='c',contrast_quote='队列中先加入的元素先取出。',contrast_subject='队列',contrast_value='先加入的元素先取出'),
                dict(chunk_id='c',contrast_quote='集合中随机取出元素。',contrast_subject='集合',contrast_value='随机取出元素')])
        ctx=dict(allow_bank_cloze=True,finder=lambda p,*a,**kw:dict(passages=['c'],found=[],cloze=[i,second] if p['id']=='pt0' else [stack,stack_second]),search=lambda *a,**kw:[],
            distractor_checker=checker,fetcher=lambda *a,**kw:(_ for _ in ()).throw(OSError('offline')))
        result=pm.prepare_module(store,'p','m',ctx,lambda *a:None)
        assert result==dict(points=2,cloze_points=2,cloze_heavy=True)
        assert len(seen)==1 and len(seen[0]['questions'])==6
        assert all(qb.checked_question(q) for q in store.records('question_bank','p'))


def test_contrast48_review_rejection_regaps_with_reasons_and_checks_only_new_cards(tmp_path):
    with CourseStore('retry48',tmp_path) as store:
        i,second=create48(store,extended=True);seen=[];retry=[]
        third=dict(i,sentence='队列的元素按进入次序移出。',word='进入次序',contrasts=[
            dict(chunk_id='c',contrast_quote='栈的元素按储存次序移出。',contrast_subject='栈',contrast_value='储存次序'),
            dict(chunk_id='c',contrast_quote='集合的元素按随机次序移出。',contrast_subject='集合',contrast_value='随机次序')])
        def finder(point,passages,**kw):
            if kw['mode']=='cloze_retry':retry.append(point['cloze_rejections']);return dict(passages=['c'],found=[],cloze=[third])
            return dict(passages=['c'],found=[],cloze=[i,second])
        def checker(packet,**kw):
            seen.append(packet);response=approved48(packet)
            if len(seen)==1:
                for question,reply in zip(packet['questions'],response['questions']):
                    if '先进先出' in question['options']:reply['checks'][0].update(verdict='also_supported',quote='伪造引文')
            return response
        ctx=dict(allow_bank_cloze=True,finder=finder,search=lambda *a,**kw:[],distractor_checker=checker,
            fetcher=lambda *a,**kw:(_ for _ in ()).throw(OSError('offline')))
        result=pm.prepare_point(store,'p',store.records('points','p')[0],ctx)
        assert result['questions']==2 and len(retry)==1 and retry[0][0]['reason']=='也成立的quote对不上逐字原文'
        assert len(seen)==2 and len(seen[1]['questions'])==2
        records=store.records('question_bank','p');assert sum(qb.checked_question(q) for q in records)==3
        assert sum(q['distractor_check']['state']=='rejected' for q in records)==2
        assert not set(q['id'] for q in seen[0]['questions']) & set(q['id'] for q in seen[1]['questions'])


def test_contrast48_cancel_during_review_never_persists_a_passed_check(tmp_path):
    with CourseStore('cancel48',tmp_path) as store:
        i,second=create48(store);cancel=Event()
        def checker(packet,**kw):cancel.set();return approved48(packet)
        ctx=dict(allow_bank_cloze=True,finder=lambda *a,**kw:dict(passages=['c'],found=[],cloze=[i,second]),search=lambda *a,**kw:[],
            distractor_checker=checker,cancel=cancel,fetcher=lambda *a,**kw:(_ for _ in ()).throw(OSError('offline')))
        with pytest.raises(pm.ResearchCancelled):pm.prepare_point(store,'p',store.records('points','p')[0],ctx)
        assert all('distractor_check' not in q for q in store.records('question_bank','p'))
        assert pm.pick_question(store,'p','pt0','module') is None


def test_contrast48_shared_batch_quote_cannot_be_borrowed_from_another_material():
    # Same-material distractor origins are now intentionally visible (batch 58).
    # A different material in the shared batch must still stay out of this scope.
    p,c,q=review_question48();other=dict(c,id='other',source_id='other-material',text='另一对象支持C。',source=dict(c['source'],locator='other'))
    p['chunk_ids']=['c'];second=pm._base(dict(p,id='other-point'),'choice','另一对象支持什么？',[0],'found',other,other,options=['C','E','F'])
    def checker(packet,**kw):
        response=approved48(packet);response['questions'][0]['checks'][0].update(verdict='also_supported',quote='另一对象支持C。')
        assert all(set(v)=={'id','text'} for v in packet['passages'])
        return response
    rows=pm.check_distractors([q,second],[p,dict(p,id='other-point',chunk_ids=['other'])],[c,other],dict(distractor_checker=checker))
    assert [q['distractor_check']['state'] for q in rows]==['rejected','passed']


def test_contrast48_third_candidate_does_not_fill_the_bank_with_a_detail(tmp_path):
    with CourseStore('cap48',tmp_path) as store:
        i,second=create48(store,extended=True)
        detail=dict(i,target='课外附带细节',sentence='栈的元素按储存次序移出。',subject='栈',word='储存次序',contrasts=[])
        ctx=dict(allow_bank_cloze=True,finder=lambda *a,**kw:dict(passages=['c'],found=[],cloze=[i,second,detail]),search=lambda *a,**kw:[],
            fetcher=lambda *a,**kw:(_ for _ in ()).throw(OSError('offline')),distractor_checker=approved48)
        assert pm.prepare_point(store,'p',store.records('points','p')[0],ctx)['questions']==2
        assert len(store.records('question_bank','p'))==3


def test_contrast48_historical_subjective_rows_never_satisfy_ready_even_with_a_check():
    p,c,q=review_question48();q.update(kind='short',answer='A');q.pop('options')
    q['distractor_check']=dict(state='passed',checks=[],reason='',at=q['created_at'])
    qb.validate_question(q)
    assert not qb.checked_question(q)
    assert pm._counts([q],['c'])['questions']==0
    assert pm._counts([q],['c'],provisional=True)['questions']==0
