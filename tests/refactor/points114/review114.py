"""Offline viewer and apply-ready export. No model call or database writes."""
import argparse
import json
from pathlib import Path
from tests.refactor.points113.review113 import build as previous_build,page as previous_page
from tests.refactor.points112.run112 import dump,select_scope
from .export114 import apply_payload


def failed_candidate(root,source,module,old):
    """Display a validated merge draft without pretending the stage passed."""
    from refactor.learning.course import planner_agents as a
    work=root/'work';prefix=f'module-{module["position"]+1}'
    if not (work/(prefix+'-concepts.json')).exists():return None
    forecasts=[json.loads(line) for line in (root/'audit/events.jsonl').read_text(encoding='utf-8').splitlines()
               if json.loads(line).get('kind')=='forecast']
    records=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((root/'audit/model').glob('call-*.json'))]
    merges=[r for r,e in zip(records,forecasts) if e['stage']=='merge' and r.get('text')
            and json.loads(r['messages'][0]['content']).get('module')==module['title']]
    if not merges:return None
    concepts=json.loads((work/(prefix+'-concepts.json')).read_text(encoding='utf-8'))
    structure=a.parse_concept_merge(merges[-1]['text'],concepts,content_minutes=True)
    paragraphs,required=a.concept_materials(old,source['chunks'])
    sections=json.loads((work/(prefix+'-sections.json')).read_text(encoding='utf-8'))
    for s in sections:
        for kind in ('knowledge','navigation'):
            for ref in s[kind]:paragraphs[ref].update(purpose=kind,section_id=s['id'])
    a.concept_content_minutes(structure,paragraphs,old,required,source['questions'])
    structure['navigation']=[dict(id=f'N{i}',title=s['title'],refs=s['navigation'],concepts=[],reason=s['reason'],minutes=0,assessed=False)
        for i,s in enumerate((s for s in sections if s['navigation']),1)]
    for p in structure['points']:p['notes_status']='not_generated_after_stop'
    mapping=a.concept_coverage(old,required,structure)
    for p in old:
        mapping['old_to_new'][p['id']]['sections']=[dict(id=s['id'],title=s['title'],kind=s['kind'],reason=s['reason'])
            for s in sections if set(required[p['id']]).intersection(s['refs'])]
    for p in paragraphs.values():p.pop('block_fragments',None)
    return dict(module_id=module['id'],module_position=module['position'],title=module['title'],sections=sections,
        section_counts={k:sum(s['kind']==k for s in sections) for k in ('navigation','knowledge','mixed')},
        paragraphs=paragraphs,concepts=concepts,old_points=old,old_minutes=sum(p['minutes'] for p in old),
        new_minutes=sum(p['minutes'] for p in structure['points']),generation_status='failed_semantic_review',
        semantic_reviews=json.loads((work/(prefix+'-semantic.json')).read_text(encoding='utf-8')),
        **structure,**mapping)


def page(payload):
    # Same approved layout. Only additional evidence/status wording changes.
    return previous_page(payload).replace("el('li','',b.text)","el('li','',b.text+(b.excerpted?'（原文代码节选，完整示例见出处）':''))")


def build(root):
    root=Path(root)
    payload=previous_build(root)
    source=json.loads((root/'input112.json').read_text(encoding='utf-8'));scope=select_scope(source)
    reviews=json.loads((root/'module-reviews.json').read_text(encoding='utf-8')) if (root/'module-reviews.json').exists() else {}
    first,old=scope[0]
    present={m['module_id'] for m in payload['modules']}
    errors=json.loads((root/'generation-errors.json').read_text(encoding='utf-8')) if (root/'generation-errors.json').exists() else []
    for module,points in scope:
        if module['id'] not in present and any(e['module_id']==module['id'] for e in errors):
            candidate=failed_candidate(root,source,module,points)
            if candidate:payload['modules'].append(candidate);present.add(module['id'])
    payload['not_run']=[m for m in payload['not_run'] if m['id'] not in present]
    payload.update(schema='mentorflow.concept-proposal.v3',
        scope_text=f'第{first["position"]+1}模块第{old[0]["position"]+1}旧点“{old[0]["title"]}”起；此前已学/跳过保留，不改用户数据。',
        module_reviews=reviews)
    payload['complete']=len(payload['modules'])==len(scope) and all(reviews.get(m['id'],{}).get('passed') is True for m,_ in scope)
    payload['status_text']='全部范围已生成并通过本轮人工核对，等待用户审阅与授权换入' if payload['complete'] else (
        payload['quality_review'].get('reason') or '第一段/其余模块尚待完成与人工核对，不能应用')
    raw_calls=root/'audit/calls.json'
    forecasts=[]
    if (root/'audit/events.jsonl').exists():
        forecasts=[e for line in (root/'audit/events.jsonl').read_text(encoding='utf-8').splitlines()
                   if (e:=json.loads(line)).get('kind')=='forecast']
    payload['calls']=[]
    if raw_calls.exists():
        for i,c in enumerate(json.loads(raw_calls.read_text(encoding='utf-8'))):
            raw=json.loads((root/'audit'/c['evidence']).read_text(encoding='utf-8'))
            payload['calls'].append({k:raw[k] for k in ('id','seconds','usage','peak_cost_cny','time_cost_cny','charged_bound','error','wall_utc') if k in raw})
            payload['calls'][-1]['stage']=forecasts[i]['stage']
    dump(root/'new-structure.json',payload)
    (root/'comparison.html').write_text(page(payload),encoding='utf-8')
    if payload['complete']:
        ready=apply_payload(payload,[c['id'] for c in source['chunks']])
    else:
        ready=dict(schema='mentorflow.concept-apply.v1',plan_id=payload['plan_id'],complete=False,
                   modules=[],reason=payload['status_text'])
    dump(root/'apply-ready.json',ready)
    return payload


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('D:/MentorFlow/.local/test-tmp/points114'))
    print('complete',build(parser.parse_args().root)['complete'])
