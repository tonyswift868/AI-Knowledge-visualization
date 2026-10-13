import copy
import json
import pytest
from refactor.learning.course import planner_agents as a


def material():
    text='数据库管理系统管理数据。事务保证一致性。索引用于加速查询。推荐先阅读数据库教材。'
    chunks=[dict(id='chunk',text=text,section=['数据库'],kind='paragraph')]
    points=[dict(id=k,title=k,minutes=8,chunk_ids=['chunk'],citations=[dict(chunk_id='chunk',quote=text)])
            for k in ('old1','old2')]
    return points,chunks


def extracts():
    return [dict(title=t,kind='knowledge',refs=['c1']) for t in ('DBMS','事务','索引','DBMS重复')] + [
        dict(title='数据库书单',kind='navigation',refs=['c1'])]


def merged():
    return dict(navigation=[dict(title='数据库教材',concepts=['E5'])],points=[dict(
        title='数据库基础',goal='理解管理、事务和索引',minutes=30,
        subpoints=[dict(title=t,concepts=ids) for t,ids in (
            ('管理',['E1','E4']),('事务',['E2']),('索引',['E3']))])])


def test_112_fake_chain_navigation_merge_notes_and_complete_coverage():
    old,chunks=material(); calls=[]
    def model(stage,system,messages):
        calls.append(stage)
        if stage=='extract':return json.dumps(extracts())
        if stage=='merge':return json.dumps(merged())
        p=json.loads(messages[0]['content'])['points'][0]
        return json.dumps(dict(points=[dict(id=p['id'],subpoints=[dict(id=s['id'],bullets=[
            dict(text='数据库管理系统管理数据。',ref='c1'),dict(text='事务保证一致性。',ref='c1')]) for s in p['subpoints']])]))
    before=copy.deepcopy((old,chunks))
    result=a.rebuild_concept_module(dict(id='m',title='数据库'),old,chunks,model)
    assert calls==['extract','merge','notes']
    assert (old,chunks)==before
    assert result['uncovered']==[] and result['merged_count']==1
    assert result['navigation'][0]['minutes']==0 and not result['navigation'][0]['assessed']
    assert result['points'][0]['subpoints'][0]['concepts']==['E1','E4']
    assert len(result['points'][0]['subpoints'][0]['bullets'])==2


def test_112_uncovered_old_paragraph_is_explicit_alarm():
    old,chunks=material()
    paragraphs,required=a.concept_materials(old,chunks)
    result=a.concept_coverage(old,required,dict(points=[],navigation=[]))
    assert result['uncovered']==[dict(old_id=p['id'],refs=['c1']) for p in old]
    assert result['old_to_new']['old1']['disposition']=='uncovered'


@pytest.mark.parametrize('mutation',['unknown','omitted','wrong_kind','too_small','too_many','duplicate'])
def test_112_merge_contract_and_classification(mutation):
    ps={'c1':dict(text='原文')}; concepts=a.parse_concept_extract(json.dumps(extracts()),ps)
    row=merged()
    if mutation=='unknown':row['points'][0]['subpoints'][0]['concepts']=['E999']
    if mutation=='omitted':row['navigation']=[]
    if mutation=='wrong_kind':row['navigation'][0]['concepts']=['E1']
    if mutation=='too_small':row['points'][0]['minutes']=15
    if mutation=='too_many':row['points'][0]['subpoints']*=3
    if mutation=='duplicate':row['points'][0]['subpoints'][1]['concepts'].append('E1')
    with pytest.raises(a.AgentFormatError):a.parse_concept_merge(json.dumps(row),concepts)


def test_112_extract_unknown_and_omitted_source_number():
    with pytest.raises(a.AgentFormatError):a.parse_concept_extract(json.dumps(extracts()),{'c2':dict(text='原文')})
    with pytest.raises(a.AgentFormatError):a.parse_concept_extract(json.dumps(extracts()),{'c1':{},'c2':{}})


@pytest.mark.parametrize('text,ref',[('原文没有的结论','c1'),('事务保证一致性。','c9'),('x'*201,'c1')])
def test_112_notes_only_literal_given_source(text,ref):
    old,chunks=material(); ps,_=a.concept_materials(old,chunks)
    cs=a.parse_concept_extract(json.dumps(extracts()),ps)
    points=a.parse_concept_merge(json.dumps(merged()),cs)['points']
    notes=dict(points=[dict(id='K1',subpoints=[dict(id=s['id'],bullets=[
        dict(text=text,ref=ref),dict(text='数据库管理系统管理数据。',ref='c1')]) for s in points[0]['subpoints']])])
    with pytest.raises(a.AgentFormatError):a.parse_concept_notes(json.dumps(notes),points,ps)


def test_112_navigation_only_module_needs_no_notes_or_fake_knowledge():
    old,chunks=material()
    def model(stage,*args):
        if stage=='extract':return json.dumps([dict(title='书单',kind='navigation',refs=['c1'])])
        if stage=='merge':return json.dumps(dict(navigation=[dict(title='书单',concepts=['E1'])],points=[]))
        raise AssertionError('导航不能编讲义或计时')
    result=a.rebuild_concept_module(dict(id='m',title='导航'),old,chunks,model)
    assert result['points']==[] and result['uncovered']==[]


def test_112_missing_old_provenance_rejected_not_silently_dropped():
    old,chunks=material();old[0]['citations'][0]['quote']='不存在'
    with pytest.raises(a.AgentFormatError):a.concept_materials(old,chunks)


def test_112_map_batches_share_one_global_merge_and_stable_source_numbers():
    text='事务保证一致性。索引用于加速查询。'
    chunks=[dict(id=f'c{i:03}',text=text,section=[],kind='paragraph') for i in range(100)]
    old=[dict(id=f'o{i}',title='旧点',chunk_ids=[c['id']],citations=[dict(chunk_id=c['id'],quote=text)]) for i,c in enumerate(chunks)]
    extracts_seen=[]
    def model(stage,system,messages):
        payload=json.loads(messages[0]['content'])
        if stage=='extract':
            refs=[p['id'] for p in payload['paragraphs']];extracts_seen.extend(refs)
            return json.dumps([dict(title=t,kind='knowledge',refs=refs) for t in ('管理','事务','索引')])
        if stage=='merge':
            assert len(payload['concepts'])==6
            return json.dumps(dict(navigation=[],points=[dict(title='数据库',goal='理解数据库',minutes=30,
                subpoints=[dict(title=t,concepts=ids) for t,ids in (
                    ('管理',['E1','E4']),('事务',['E2','E5']),('索引',['E3','E6']))])]))
        return json.dumps(dict(points=[dict(id='K1',subpoints=[dict(id=s['id'],bullets=[
            dict(text='事务保证一致性。',ref=s['refs'][0]),dict(text='索引用于加速查询。',ref=s['refs'][0])])
            for s in payload['points'][0]['subpoints']])]))
    result=a.rebuild_concept_module(dict(id='m',title='数据库'),old,chunks,model)
    assert extracts_seen==[f'c{i}' for i in range(1,101)]
    assert len(result['points'])==1 and not result['uncovered']
    for prompt in (a.CONCEPT_EXTRACT_SYSTEM,a.CONCEPT_MERGE_SYSTEM,a.CONCEPT_NOTES_SYSTEM):
        assert '15分钟' not in prompt and 'minutes固定15' not in prompt


def test_112_notes_cannot_pad_count_with_substrings_of_same_sentence():
    old,chunks=material();ps,_=a.concept_materials(old,chunks)
    concepts=a.parse_concept_extract(json.dumps(extracts()),ps)
    points=a.parse_concept_merge(json.dumps(merged()),concepts)['points']
    notes=dict(points=[dict(id='K1',subpoints=[dict(id=s['id'],bullets=[
        dict(text='事务保证一致性。',ref='c1'),dict(text='一致性。',ref='c1')]) for s in points[0]['subpoints']])])
    with pytest.raises(a.AgentFormatError,match='包含'):a.parse_concept_notes(json.dumps(notes),points,ps)


def test_112_default_runner_missing_input_never_constructs_live_gateway(tmp_path,monkeypatch):
    from . import run112
    monkeypatch.setattr(run112,'DEFAULT_ROOT',tmp_path/'run')
    monkeypatch.setattr(run112,'PaidCalls',lambda *a:pytest.fail('unexpected live gateway'))
    assert run112.main(['--root',str(tmp_path/'run')])==0


def test_112_failed_stage_gate_blocks_remaining_paid_work_and_page_is_honest(tmp_path,monkeypatch):
    from . import run112,review112
    old,chunks=material()
    for i,p in enumerate(old):p.update(position=i,module_id='m')
    module=dict(id='m',position=4,title='数据库')
    source=dict(plan=dict(id='plan'),modules=[module],points=old,chunks=chunks,credits=[],
                module_states={'m':'untested'},position={'state':dict(module_id='m',index=0)},manifest=[])
    run112.dump(tmp_path/'input112.json',source)
    run112.dump(tmp_path/'stage1-review.json',dict(passed=False,reason='质量不合格，已停止'))
    monkeypatch.setattr(run112,'DEFAULT_ROOT',tmp_path/'sub')
    monkeypatch.setattr(run112,'PaidCalls',lambda *a:pytest.fail('failed gate must not spend'))
    with pytest.raises(SystemExit) as error:run112.main(['--root',str(tmp_path),'--live','--rest'])
    assert error.value.code==2
    ps,required=a.concept_materials(old,chunks)
    cs=a.parse_concept_extract(json.dumps(extracts()),ps)
    structure=a.parse_concept_merge(json.dumps(merged()),cs)
    old[0]['title']='旧点</script><b>'
    run112.dump(tmp_path/'module-5.json',dict(module_id='m',title='数据库',module_position=4,
        old_points=old,old_minutes=16,new_minutes=30,paragraphs=ps,concepts=cs,**structure,
        **a.concept_coverage(old,required,structure)))
    payload=review112.build(tmp_path)
    assert payload['complete'] is False and payload['quality_review']['passed'] is False
    html=(tmp_path/'comparison.html').read_text(encoding='utf-8')
    assert html.count('</script>')==2 and '质量不合格' in html
