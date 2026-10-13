"""Opt-in directory proposal; writes only task evidence, never a user's store."""
import argparse
from decimal import Decimal
import hashlib
import json
import threading
from pathlib import Path

from refactor.learning.course import planner_agents as a
from tests.refactor.points112.run112 import PaidCalls, PLAN, dump, select_scope, snapshot
from tests.refactor.points113.review113 import page

ROOT=Path('D:/MentorFlow/.local/test-tmp/points115')
ACCOUNT='mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932'


class BoundedCalls(PaidCalls):
    """Reserve full worst-case costs of every in-flight notes request."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.bound_lock=threading.Lock();self.inflight=Decimal(0)

    def __call__(self,stage,system,messages):
        if stage not in ('classify','notes'):raise ValueError('Only section classification / notes allowed')
        real=self.gateway.real;config=real.get_configuration()
        upper=len((system+json.dumps(messages,ensure_ascii=False)).encode('utf-8'))+4096
        cost=real.price_catalog.estimate_cost(config['provider_id'],config['model'],input_tokens=(upper,upper),
            output_tokens=(self.cap,self.cap),cache_hit_rate=(0,0),
            context=dict(real.estimate_context(),price_period='peak',cache_mode='implicit'))
        if cost is None or cost.currency!='CNY':raise RuntimeError('Unpriced call')
        with self.bound_lock:
            if self.gateway.spent+self.inflight+cost.high>self.gateway.limit:raise RuntimeError('BUDGET: in-flight worst bounds exceed limit')
            self.inflight+=cost.high
        try:return super().__call__(stage,system,messages)
        finally:
            with self.bound_lock:self.inflight-=cost.high


def export_module(module, known):
    a.validate_directory_structure(module,module['paragraphs'])
    def cid(ref):
        value=module['paragraphs'][ref]['parent_chunk_id']
        if value not in known:raise ValueError('Unknown source_chunks id')
        return value
    def refs(values):return list(dict.fromkeys(cid(r) for r in values))
    def ident(kind,value):return module['module_id']+'-'+kind+'-'+hashlib.sha256(value.encode()).hexdigest()[:16]
    points=[];start=min(p['position'] for p in module['old_points'])
    for i,p in enumerate(module['points']):
        pid=ident('directory',p['id']+'|'+p['title']+'|'+','.join(r for s in p['subpoints'] for r in s['refs']))
        subs=[dict(id=pid+f'-s{j}',title=s['title'],minutes=s['minutes'],chunk_ids=refs(s['refs']),
                   notes=[dict(text=b['text'],chunk_id=cid(b['ref'])) for b in s.get('bullets',[])])
              for j,s in enumerate(p['subpoints'],1)]
        points.append(dict(id=pid,module_id=module['module_id'],position=start+i,title=p['title'],summary=p['goal'],
                           goal=p['goal'],structure='concept_v1',minutes=sum(s['minutes'] for s in subs),
                           chunk_ids=list(dict.fromkeys(c for s in subs for c in s['chunk_ids'])),subpoints=subs))
    nav=[dict(id=ident('nav',n['title']+'|'+','.join(n['refs'])),title=n['title'],chunk_ids=refs(n['refs']),reason=n['reason'])
         for n in module['navigation']]
    return dict(module_id=module['module_id'],replace_point_ids=[p['id'] for p in module['old_points']],points=points,navigation=nav)


def export_payload(modules, known, *, complete):
    converted=[export_module(m,known) for m in modules]
    owners={}
    for module in converted:
        for point in module['points']:
            for sub in point['subpoints']:
                for cid in sub['chunk_ids']:
                    if cid in owners:raise ValueError('A chunk belongs to two subpoints: '+cid)
                    owners[cid]=sub['id']
    return dict(plan_id=PLAN,complete=complete,modules=converted)


def directory_page(payload):
    html=page(payload).replace('从当前位置开始，按概念重新组织','从当前位置开始，按原文目录重新组织')
    html=html.replace('nav.open=true;','nav.open=m.navigation.length<=5;')
    html=html.replace("body.append(el('p','goal','掌握目标：'+p.goal));", "body.append(el('p','goal','掌握目标：'+p.goal));body.append(el('p','muted','原文标题：'+(p.original_heading||[]).join('；')));")
    html=html.replace("sub.append(el('h3','',s.title+' · '+s.minutes+' 分钟'));", "sub.append(el('h3','',s.title+' · '+s.minutes+' 分钟'));sub.append(el('p','muted','原文标题：'+(s.original_heading||'')));")
    html=html.replace("for(const b of s.bullets||[])","if(!(s.bullets||[]).length)sub.append(el('p','muted','讲义摘录未通过，本小要点照原文讲；点出处查看。'));for(const b of s.bullets||[])")
    return html


def artifacts(root,data,scope):
    modules=[json.loads((root/f'module-{m["position"]+1}.json').read_text(encoding='utf-8'))
             for m,_ in scope if (root/f'module-{m["position"]+1}.json').exists()]
    for module in modules:
        for nav in module['navigation']:nav.setdefault('concepts',[])
        for paragraph in module['paragraphs'].values():paragraph.pop('block_fragments',None)
        for point in module['points']:
            for sub in point['subpoints']:
                sub['original_heading']='；'.join(dict.fromkeys(' / '.join(module['paragraphs'][r].get('section') or ['正文'])
                                                               for r in sub['refs']))
    complete=len(modules)==len(scope)
    payload=dict(plan_id=PLAN,source_manifest=data['manifest'],complete=complete,
        scope_text='从第5模块第8点起；已学与已跳过的旧点保留（以副本位置为准）',
        status_text='按原文目录与程序规则切分；讲义缺失时使用原文。'+('第5～8模块全部生成。' if complete else '第一段结果，其他模块尚未生成。'),
        modules=modules,not_run=[dict(position=m['position'],title=m['title']) for m,_ in scope
                              if not (root/f'module-{m["position"]+1}.json').exists()],quality_review=dict(passed=complete))
    dump(root/'new-structure.json',payload)
    output=export_payload(modules,{c['id'] for c in data['chunks']},complete=complete)
    dump(root/'apply-ready.json',output)
    html=directory_page(payload)
    (root/'comparison.html').write_text(html,encoding='utf-8',newline='\n')
    return payload


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT);parser.add_argument('--snapshot',action='store_true')
    parser.add_argument('--live',action='store_true');parser.add_argument('--rest',action='store_true')
    parser.add_argument('--account-id',default=ACCOUNT)
    parser.add_argument('--preset-root',type=Path,default=Path('D:/MentorFlowData/account-shell'))
    parser.add_argument('--budget',default='10');args=parser.parse_args(argv);root=args.root.resolve()
    if not root.is_relative_to(ROOT.resolve()):parser.error('Only points115 task directory permitted')
    if not 0<Decimal(args.budget)<=10:parser.error('Budget must be <=10')
    if args.snapshot:snapshot(root,args.account_id)
    if not (root/'input112.json').exists():
        if args.live:parser.error('A read-only snapshot is required before paid calls')
        print('SKIP: no snapshot, no model call');return 0
    data=json.loads((root/'input112.json').read_text(encoding='utf-8'));scope=select_scope(data)
    if not args.live:artifacts(root,data,scope);print('SKIP live: --live required');return 0
    if args.rest:
        first=json.loads((root/f'module-{scope[0][0]["position"]+1}.json').read_text(encoding='utf-8'))
        if not first['hard_rules']['passed']:parser.error('Stage 1 hard rules failed')
        export_module(first,{c['id'] for c in data['chunks']})
    caller=BoundedCalls(root,args.account_id,args.preset_root,Decimal(args.budget))
    try:
        for m,ps in scope[1:] if args.rest else scope[:1]:
            target=root/f'module-{m["position"]+1}.json'
            if target.exists():continue
            def checkpoint(stage,value):dump(root/'work'/f'module-{m["position"]+1}-{stage}.json',value)
            result=a.rebuild_directory_module(m,ps,data['chunks'],data.get('questions',[]),caller,checkpoint=checkpoint,note_workers=3)
            result.update(old_points=ps,old_minutes=sum(p['minutes'] for p in ps),module_position=m['position'],
                          new_minutes=sum(p['minutes'] for p in result['points']))
            dump(target,result);artifacts(root,data,scope)
            print('COMPLETE',m['position']+1,len(ps),'->',len(result['points']),result['new_minutes'],result['hard_rules'],flush=True)
    finally:caller.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
