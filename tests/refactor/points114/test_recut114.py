import copy
import json
import pytest
from refactor.learning.course import planner_agents as a
from .export114 import apply_module,apply_payload


def fixture():
    texts=['数据库是数据集合。DBMS管理数据库。','键值数据有键和值。Redis用于缓存。','图数据是节点和边。Neo4j存储图。']*2
    chunks=[dict(id=f'chunk{i}',source_id='s',section=['知识正文'],kind='paragraph',text=t) for i,t in enumerate(texts)]
    old=[dict(id=f'old{i}',module_id='m',position=i+7,title='旧点',difficulty='中',minutes=8,
        chunk_ids=[c['id']],citations=[dict(chunk_id=c['id'],quote=c['text'])]) for i,c in enumerate(chunks)]
    return old,chunks


def model_factory(always_duplicate=False):
    counts={'merge':0,'dedup':0};seen=[]
    def model(stage,system,messages):
        seen.append(stage);packet=json.loads(messages[0]['content'])
        if stage=='classify':
            s=packet['sections'][0]
            return json.dumps([dict(id=s['id'],knowledge=[p['id'] for p in s['paragraphs']],navigation=[],reason='直接解释概念和类型')])
        if stage=='extract':
            return json.dumps([dict(title=['概述','键值类型','图类型'][i%3],kind='knowledge',refs=[p['id']]) for i,p in enumerate(packet['paragraphs'])])
        if stage=='merge':
            counts[stage]+=1
            subs=[dict(title=t,concepts=[f'E{i+1}']) for i,t in enumerate(['概述','键值','图','总览','键值重复','图重复'])]
            points=[dict(title='概述与子类',goal='理解概述与两个子类',subpoints=subs[:3]),dict(title='另一个分类',goal='理解同样分类',subpoints=subs[3:])]
            if counts[stage]>1:
                points=[dict(title='概述与子类',goal='理解概述与两个子类',subpoints=[dict(title=t,concepts=[f'E{i+1}',f'E{i+4}']) for i,t in enumerate(['总览定位','键值类型','图类型'])])]
            return json.dumps(dict(points=points,navigation=[]))
        if stage=='dedup':
            counts[stage]+=1
            return json.dumps(dict(duplicates=[dict(subpoints=['K1-S1','K1-S2'],reason='故意报警')] if always_duplicate else
                [dict(subpoints=['K1-S2','K2-S2'],reason='同一键值类型重复讲')] if counts[stage]==1 else [],
                hierarchy=[dict(points=['K1','K2'],reason='概述和两类被分成两点')] if counts[stage]==1 else []))
        points=packet['points']
        return json.dumps(dict(points=[dict(id=p['id'],subpoints=[dict(id=s['id'],bullets=[dict(ref=s['refs'][0],text=t+'。')
            for t in packet['paragraphs'][s['refs'][0]].split('。') if t]) for s in p['subpoints']]) for p in points]))
    return model,seen


def result():
    old,chunks=fixture();model,seen=model_factory()
    row=a.rebuild_classified_concept_module(dict(id='m',title='数据库'),old,chunks,[],model,semantic_check=True,allow_short_examples=True)
    row.update(old_points=old,module_position=4)
    return row,chunks,seen


def test_114_overview_children_one_family_and_semantic_correction():
    row,_,seen=result()
    assert len(row['points'])==1 and len(row['points'][0]['subpoints'])==3
    assert row['points'][0]['subpoints'][0]['title']=='总览定位'
    assert row['points'][0]['subpoints'][1]['concepts']==['E2','E5']
    assert row['semantic_reviews'][0]['duplicates'] and row['semantic_reviews'][1]==dict(duplicates=[],hierarchy=[])
    assert seen.count('merge')==2 and seen.count('dedup')==2 and not row['uncovered']
    assert '总览做第一个小要点' in a.CLASSIFIED_MERGE_SYSTEM


def test_114_semantic_duplicate_not_resolved_is_an_explicit_alarm():
    old,chunks=fixture();model,seen=model_factory(always_duplicate=True);saved={}
    with pytest.raises(a.AgentFormatError,match='语义去重仍有重复'):
        a.rebuild_classified_concept_module(dict(id='m',title='数据库'),old,chunks,[],model,
            semantic_check=True,checkpoint=lambda key,value:saved.update({key:copy.deepcopy(value)}))
    assert len(saved['semantic'])==2 and 'notes' not in seen


def test_114_semantic_review_cannot_invent_subpoint_ids():
    structure=dict(points=[dict(id='K1',subpoints=[dict(id='K1-S1'),dict(id='K1-S2')])])
    with pytest.raises(a.AgentFormatError,match='编号'):
        a.parse_semantic_concept_review(json.dumps(dict(duplicates=[dict(subpoints=['K1-S1','X'],reason='重复')],hierarchy=[])),structure)


def test_114_live_hierarchy_field_with_real_subpoint_ids_keeps_duplicate_alarm():
    structure=dict(points=[dict(id='K1',subpoints=[dict(id='K1-S1'),dict(id='K1-S2')])])
    review=a.parse_semantic_concept_review(json.dumps(dict(duplicates=[],hierarchy=[
        dict(points=['K1-S1','K1-S2'],reason='总览展开了子类又重复讲')])) ,structure)
    assert review['duplicates'][0]['subpoints']==['K1-S1','K1-S2']
    assert review['hierarchy']==[] and review['normalized_hierarchy_subpoints']


def note_case(code):
    paragraphs={'c1':dict(text=code+'\n索引加速查询。',kind='code',purpose='knowledge')}
    points=[dict(id='K1',subpoints=[dict(id='K1-S1',refs=['c1'])])]
    reply=json.dumps(dict(points=[dict(id='K1',subpoints=[dict(id='K1-S1',bullets=[dict(ref='c1',text=code),dict(ref='c1',text='索引加速查询。')])])]))
    return reply,points,paragraphs


def test_114_original_six_line_sql_example_is_allowed_verbatim():
    code='CREATE TABLE t1\n(\n    col1 VARCHAR(5)\n      CHARACTER SET latin1\n      COLLATE latin1_german1_ci\n);'
    args=note_case(code)
    with pytest.raises(a.AgentFormatError):a.parse_classified_notes(*args)
    row=a.parse_classified_notes(*args,allow_short_examples=True)
    assert row[0]['subpoints'][0]['bullets'][0]==dict(text=code,ref='c1')


@pytest.mark.parametrize('code',['\n'.join(f'SELECT {i};' for i in range(30)),"SELECT '"+'x'*400+"';"])
def test_114_overlong_example_extracts_bounded_literal_key_lines(code):
    row=a.parse_classified_notes(*note_case(code),allow_short_examples=True)
    bullet=row[0]['subpoints'][0]['bullets'][0]
    assert bullet['excerpted'] and len(bullet['text'])<=300 and len(bullet['text'].splitlines())<=15
    assert bullet['text'] in code


def test_114_fifteen_line_example_kept_and_unknown_source_still_rejected():
    code='\n'.join(f'SELECT {i};' for i in range(15))
    reply,points,paragraphs=note_case(code)
    row=a.parse_classified_notes(reply,points,paragraphs,allow_short_examples=True)
    assert row[0]['subpoints'][0]['bullets'][0]['text']==code
    with pytest.raises(a.AgentFormatError):a.parse_classified_notes(reply.replace('"ref": "c1"','"ref": "c99"'),points,paragraphs,allow_short_examples=True)


def test_114_apply_ready_matches_exact_shared_fields_and_real_chunk_ids():
    module,chunks,_=result();converted=apply_module(module,[c['id'] for c in chunks])
    point=converted['points'][0]
    assert set(point)=={'id','module_id','position','title','summary','minutes','chunk_ids','structure','goal','subpoints'}
    assert point['position']==7 and point['structure']=='concept_v1' and point['goal']==point['summary']
    assert point['minutes']==sum(s['minutes'] for s in point['subpoints'])
    assert point['chunk_ids']==list(dict.fromkeys(c for s in point['subpoints'] for c in s['chunk_ids']))
    for i,s in enumerate(point['subpoints'],1):
        assert s['id']==point['id']+f'-s{i}'
        assert set(s)=={'id','title','minutes','chunk_ids','notes'}
        assert all(set(n)=={'text','chunk_id'} and n['chunk_id'] in s['chunk_ids'] for n in s['notes'])
        assert all('@' not in cid and not cid.startswith('c1') for cid in s['chunk_ids'])
    assert converted['replace_point_ids']==[p['id'] for p in module['old_points']]
    assert apply_module(module,[c['id'] for c in chunks])==converted
    module['navigation']=[dict(id='N1',title='延伸资料',refs=['c1'],reason='只供查看')]
    ready=apply_payload(dict(complete=True,plan_id='plan',source_manifest=[],modules=[module]),[c['id'] for c in chunks])
    nav=ready['modules'][0]['navigation'][0]
    assert set(nav)=={'id','title','chunk_ids','reason'} and nav['chunk_ids']==['chunk0']
    assert ready['complete'] and ready['modules'][0]['points'][0]['structure']=='concept_v1'


@pytest.mark.parametrize('mutation',['missing_notes','minutes','unknown_chunk','outside_note'])
def test_114_apply_ready_rejects_incomplete_or_inconsistent_payload(mutation):
    module,chunks,_=result();point=module['points'][0];sub=point['subpoints'][0]
    if mutation=='missing_notes':sub.pop('bullets')
    if mutation=='minutes':point['minutes']+=1
    if mutation=='unknown_chunk':module['paragraphs'][sub['refs'][0]]['parent_chunk_id']='missing'
    if mutation=='outside_note':sub['bullets'][0]['ref']='c99'
    with pytest.raises(ValueError):apply_module(module,[c['id'] for c in chunks])


def test_114_incomplete_gate_cannot_be_exported_for_application():
    with pytest.raises(ValueError,match='incomplete'):apply_payload(dict(complete=False),[])


def test_114_failed_candidate_is_visible_but_apply_ready_stays_empty(tmp_path):
    from tests.refactor.points112.run112 import dump
    from .review114 import build
    module,chunks,_=result();old=module['old_points']
    kept=[dict(old[0],id=f'kept{i}',position=i) for i in range(7)]
    source=dict(plan={'id':'plan'},modules=[dict(id='m',title='数据库',position=4)],points=kept+old,chunks=chunks,
        credits=[],questions=[],module_states={'m':'untested'},position={'state':dict(module_id='m',index=7)},manifest=[])
    dump(tmp_path/'input112.json',source)
    dump(tmp_path/'stage1-review.json',dict(passed=False,reason='语义重复报警'))
    dump(tmp_path/'generation-errors.json',[dict(module_id='m',code='AgentFormatError')])
    dump(tmp_path/'work/module-5-concepts.json',module['concepts'])
    dump(tmp_path/'work/module-5-sections.json',module['sections'])
    dump(tmp_path/'work/module-5-semantic.json',module['semantic_reviews'])
    dump(tmp_path/'audit/model/call-00001.json',dict(messages=[dict(content=json.dumps({'module':'数据库'}))],
        text=json.dumps(dict(navigation=[],points=module['points']))))
    (tmp_path/'audit/events.jsonl').write_text(json.dumps(dict(kind='forecast',stage='merge'))+'\n',encoding='utf-8')
    payload=build(tmp_path)
    assert not payload['complete'] and len(payload['modules'])==1
    assert payload['modules'][0]['generation_status']=='failed_semantic_review'
    assert all(p['notes_status']=='not_generated_after_stop' for p in payload['modules'][0]['points'])
    ready=json.loads((tmp_path/'apply-ready.json').read_text(encoding='utf-8'))
    assert ready['complete'] is False and ready['modules']==[]
    assert '语义重复报警' in (tmp_path/'comparison.html').read_text(encoding='utf-8')
