"""Refresh deterministic layout after a rule fix, reusing validated excerpts.

Only newly ambiguous purpose classifications and new groups' literal notes
may call the model. No semantic duplicate judgments are made.
"""
import argparse
from decimal import Decimal
import json
from refactor.learning.course import planner_agents as a
from tests.refactor.points112.run112 import dump,select_scope
from tests.refactor.points115.run115 import ROOT,ACCOUNT,BoundedCalls,artifacts


def reflow(result,old,chunks,questions,complete):
    paragraphs=result['paragraphs'];_,required=a.concept_materials(old,chunks)
    sections=a.directory_purposes(paragraphs,complete,result['sections'])
    result['sections']=sections;result['navigation']=[]
    for section in sections:
        for kind in ('knowledge','navigation'):
            for ref in section[kind]:paragraphs[ref]['purpose']=kind
        if section['navigation']:
            result['navigation'].append(dict(id=f'N{len(result["navigation"])+1}',title=section['title'],refs=section['navigation'],
                concepts=[],reason=section['reason'],minutes=0,assessed=False))
    result['section_counts']={kind:sum(bool(s['knowledge']) and bool(s['navigation']) if kind=='mixed' else
        bool(s[kind]) and not s['navigation' if kind=='knowledge' else 'knowledge'] for s in sections)
        for kind in ('knowledge','navigation','mixed')}
    points=a.directory_structure(paragraphs,old,required,questions)
    def signature(ps):return [(p['title'],p['minutes'],[(s['title'],s['refs'],s['minutes']) for s in p['subpoints']]) for p in ps]
    if signature(points)==signature(result['points']):return result
    old_subs=[s for p in result['points'] for s in p['subpoints']]
    result['reflow']=dict(reason='同名原文标题按连续位置保留；复合技术文档不因标题含路线整篇导航；纯通关计划整篇导航',
        old_points=len(result['points']),old_minutes=sum(p['minutes'] for p in result['points']),reused_subpoints=0,new_note_groups=[])
    failures=[]
    for point in points:
        for sub in point['subpoints']:
            candidates=[b for s in old_subs for b in s.get('bullets',[]) if b['ref'] in sub['refs']]
            candidates.sort(key=lambda b:sub['refs'].index(b['ref']))
            kept=[]
            for b in candidates:
                plain=''.join(b['text'].split())
                if not any(b['ref']==k['ref'] and (plain in ''.join(k['text'].split()) or ''.join(k['text'].split()) in plain) for k in kept):
                    kept.append(b)
            if len(kept)>=2:
                sub['bullets']=kept[:6];result['reflow']['reused_subpoints']+=1;continue
            if any(set(s['refs'])==set(sub['refs']) and not s.get('bullets') for s in old_subs):
                failures.append(sub['id']);continue  # Already exhausted original retries.
            result['reflow']['new_note_groups'].append(sub['id'])
            packet=[dict(id=point['id'],subpoints=[sub])]
            system,messages=a.concept_notes_request(packet,paragraphs)
            system+='\n允许逐字短代码/SQL（15行、300字），长示例摘连续关键行。'
            for attempt in range(2):
                try:
                    reply=complete('notes',system+('' if not attempt else '\n上次未通过校验，请重新核对逐字摘句和出处。'),messages)
                    parsed=a.parse_classified_notes(reply,packet,paragraphs,allow_short_examples=True)
                    sub['bullets']=parsed[0]['subpoints'][0]['bullets'];break
                except a.AgentFormatError:
                    if attempt:failures.append(sub['id'])
    result['points']=points
    result.update(a.concept_coverage(old,required,result))
    for pid,item in result['old_to_new'].items():
        item['sections']=[dict(title=s['title'],reason=s['reason'],kind='mixed' if s['knowledge'] and s['navigation'] else
            'knowledge' if s['knowledge'] else 'navigation') for s in sections if set(s['refs']).intersection(required[pid])]
    result.update(notes_failures=failures,new_minutes=sum(p['minutes'] for p in points),
                  hard_rules=a.validate_directory_structure(result,paragraphs),
                  minor_issues=[dict(id=p['id'],minutes=p['minutes'],subpoints=len(p['subpoints'])) for p in points
                                if not 20<=p['minutes']<=40 or not 3<=len(p['subpoints'])<=8])
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true')
    parser.add_argument('--account-id',default=ACCOUNT);parser.add_argument('--preset-root',default='D:/MentorFlowData/account-shell')
    args=parser.parse_args(argv)
    if not args.live:print('SKIP: --live required even for possible notes fallback');return 0
    data=json.loads((ROOT/'input112.json').read_text(encoding='utf-8'));scope=select_scope(data)
    caller=BoundedCalls(ROOT,args.account_id,args.preset_root,Decimal('10'))
    try:
        for m,old in scope:
            path=ROOT/f'module-{m["position"]+1}.json';result=json.loads(path.read_text(encoding='utf-8'))
            result=reflow(result,old,data['chunks'],data.get('questions',[]),caller);dump(path,result)
            print('REFLOW',m['position']+1,len(result['points']),result['new_minutes'],result.get('reflow'),flush=True)
        artifacts(ROOT,data,scope)
    finally:caller.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
