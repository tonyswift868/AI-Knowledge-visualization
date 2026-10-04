"""Position-only distractors and literal complete parallel answer sets."""
import pytest
from refactor.learning.course import point_materials as pm
from refactor.learning.course.question_bank import parallel_answer_parts,validate_question
from refactor.learning.course.answer_compare import grade

def data47():
    p=dict(id='pt',module_id='m',chunk_ids=['c'],importance='normal')
    own=dict(id='c',source_id='s',text='三角帆增强操纵、逆风航行和任意方向航行。',section='帆',
        source=dict(title='教材',url='https://school.edu/book',locator='第1章'),authority={'level':1,'type':'school'})
    other=dict(own,id='d',text='其他设备用于定位星位、记录距离和发出信号。',section='其他设备')
    i=dict(target='三角帆有什么作用？',chunk_id='c',sentence=own['text'],word='增强操纵、逆风航行和任意方向航行',
        answer_parts=['增强操纵','逆风航行','任意方向航行'],importance='normal',keywords=['定位星位','记录距离','发出信号'])
    return p,[own,other],i

@pytest.mark.parametrize('sentence,word,parts,expected',[
 ('包括CPU、RAM和SSD。','CPU、RAM和SSD',['CPU','RAM','SSD'],('CPU','RAM','SSD')),
 ('包括CPU以及RAM。','CPU以及RAM',['RAM','CPU'],('CPU','RAM')),
 ('Includes sailors, explorers, and shipbuilders.','sailors, explorers, and shipbuilders',
  ['sailors','explorers','shipbuilders'],('sailors','explorers','shipbuilders')),
 ('Includes CPU and RAM.','CPU and RAM',['CPU','RAM'],('CPU','RAM')),
 ('A、B和C。','B和C',['B','C'],()),
 ('A、B和C。','A、B和C',['A','C'],()),
 ('A提高速度。B改善方向。','A提高速度。B改善方向',['A提高速度','B改善方向'],()),
 ('CPU和网络协议。','CPU和网络协议',['CPU','协议'],()),
 ('CPU、CPU和RAM。','CPU、CPU和RAM',['CPU','RAM'],()),
 ('CPU或RAM。','CPU或RAM',['CPU','RAM'],()),
])
def test_cloze47_only_complete_literal_parallel_lists(sentence,word,parts,expected):
    assert parallel_answer_parts(sentence,word,parts)==expected

def test_cloze47_multi_set_is_complete_and_program_graded():
    p,passages,i=data47();q=pm._cloze(i,p,{'c':passages[0]},passages)
    assert q['kind']=='multi'
    assert {q['options'][n] for n in q['answer']}==set(i['answer_parts'])
    validate_question(q)
    assert grade(q,list(reversed(q['answer'])))[0]
    assert not grade(q,q['answer'][:-1])[0]
    assert not grade(q,[*q['answer'],next(n for n in range(len(q['options'])) if n not in q['answer'])])[0]
    with pytest.raises(ValueError):validate_question(dict(q,answer=q['answer'][:1]))

def test_cloze47_quick_keeps_one_gap_and_two_foreign_distractors():
    p,passages,i=data47();q=pm._quick(i,p,{'c':passages[0]},passages)
    assert q['kind']=='choice' and q['role']=='quick' and len(q['answer'])==1
    assert q['options'][q['answer'][0]]=='增强操纵'
    assert '逆风航行和任意方向航行' in q['stem']
    assert len(q['options'])>=3
    validate_question(q)

def test_cloze47_blocks_true_fragments_in_any_linked_chunk_and_answer_sentence():
    p,passages,i=data47()
    i=dict(i,answer_parts=[],word='增强操纵',keywords=['逆风航行','任意方向航行','定位星位'])
    rejected=[]
    assert pm._cloze(i,p,{'c':passages[0]},passages,rejected=rejected) is None
    assert '不足' in rejected[-1]['reason']
    linked=dict(passages[1],id='linked',section='第三章');p['chunk_ids'].append('linked')
    passages.append(linked)
    i['keywords']=['定位星位','记录距离','发出信号']
    assert pm._quick(i,p,{'c':passages[0]},passages) is None

def test_cloze47_same_module_other_point_is_an_allowed_source():
    p,passages,i=data47();passages[1].update(source_id='other',section='帆',module_ids=['m'],point_ids=['otherpoint'])
    assert pm._cloze(i,p,{'c':passages[0]},passages)['kind']=='multi'
    passages[1]['module_ids']=['othermodule']
    assert pm._cloze(i,p,{'c':passages[0]},passages) is None

def test_cloze47_invalid_list_falls_back_to_one_full_span_answer():
    p,passages,i=data47();i['answer_parts']=['增强操纵','任意方向航行']
    q=pm._cloze(i,p,{'c':passages[0]},passages)
    assert q['kind']=='choice' and q['options'][q['answer'][0]]==i['word']

def test_cloze47_single_has_exactly_one_answer_and_does_not_relax_two_distractors():
    p,passages,i=data47();i=dict(i,word='增强操纵',answer_parts=[],keywords=['定位星位','记录距离'])
    q=pm._cloze(i,p,{'c':passages[0]},passages)
    assert q['kind']=='choice' and len(q['answer'])==1
    i['keywords']=['定位星位'];assert pm._quick(i,p,{'c':passages[0]},passages) is None

def test_cloze47_distractor_context_does_not_expand_point_provenance():
    p,passages,i=data47()
    ctx=dict(finder=lambda *a,**kw:dict(passages=['c','d'],found=[],cloze=[i]),_cloze_calls=[])
    output=pm._locate_tier(p,passages,ctx,'adopted')
    assert output['passages']==['c']
    assert ctx['_cloze_calls'][0]['request']['distractor_passages']==['d']
    output=pm._locate_tier(p,passages,dict(ctx,cloze_rejections=[{'reason':'换句'}]),'cloze_retry')
    assert output['passages']==['c'] and ctx['_cloze_calls'][-1]['request']['distractor_passages']==['d']


def test_cloze47_external_keyword_anchors_resolve_in_their_original_chapter():
    p,passages,i=data47()
    i=dict(i,keywords=[{'quote':'定位星位'},{'quote':'记录距离'}])
    rejected=[]
    output=pm._expand_output(dict(passages=['c'],found=[],cloze=[i]),passages,rejected)
    assert not rejected
    assert output['cloze'][0]['keywords']==['定位星位','记录距离']
    assert pm._cloze(output['cloze'][0],p,{'c':passages[0]},passages)['kind']=='multi'


def test_cloze47_bad_keyword_anchor_does_not_erase_the_valid_goal_pointer():
    p,passages,i=data47()
    i=dict(i,keywords=[{'quote':'并不存在'},{'quote':'定位星位'},{'quote':'记录距离'}])
    rejected=[]
    output=pm._expand_output(dict(passages=['c'],found=[],cloze=[i]),passages,rejected)
    assert len(output['cloze'])==1 and len(rejected)==1
    assert '不存在' in rejected[0]['reason']
    assert pm._cloze(output['cloze'][0],p,{'c':passages[0]},passages)['kind']=='multi'
    with pytest.raises(ValueError):pm._keyword_quote({'quote':'其他','end_quote':'信号'},[
        {'text':'其他设备用于信号'},{'text':'其他工具用于信号'}])
