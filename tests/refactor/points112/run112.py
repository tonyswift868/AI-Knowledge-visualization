"""Opt-in proposal generator. No AppServices, login, sync or course writes.

Uses simulation.run.live_factory + simulation.engine.RecordedGateway, with the
same --live/--account-id/--preset-root flags. Default operation is read-only.
"""
from __future__ import annotations
import argparse
from decimal import Decimal
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from refactor.learning.course import planner_agents as a

PLAN='029b076315034c00bbd6842a5c9c31d9'
DEFAULT_ROOT=Path('D:/MentorFlow/.local/test-tmp/points112')


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)


def snapshot(root,account):
    """Consistent ro backup; authoritative synced classroom position is in course."""
    from refactor.learning.course.store import CourseStore
    source=Path('D:/MentorFlowApp/MentorFlow/Data/learning/accounts')/account
    target=root/'snapshot/learning/accounts'/account
    target.mkdir(parents=True,exist_ok=True)
    manifest=[]
    for name in ('course.sqlite3','classroom.sqlite3'):
        src,dst=source/name,target/name
        if not src.exists():continue
        if dst.exists():raise ValueError('副本已存在，不覆盖；复跑用已保存输入或新的任务子目录')
        with sqlite3.connect(src.as_uri()+'?mode=ro',uri=True) as a_db,sqlite3.connect(dst) as b_db:a_db.backup(b_db)
        manifest.append(dict(source=str(src),copy=str(dst),bytes=dst.stat().st_size,
                             sha256=hashlib.sha256(dst.read_bytes()).hexdigest()))
    with CourseStore(account,root/'snapshot') as store:
        modules=store.records('modules',PLAN);book=store.load_mastery(PLAN)
        position=store.classroom_position(PLAN)
        if not position:raise ValueError('没有可确认的课堂位置，不猜测重切起点')
        dump(root/'input112.json',dict(manifest=manifest,plan=store.get_plan(PLAN),modules=modules,
            points=store.records('points',PLAN),chunks=store.records('source_chunks',PLAN),position=position,
            credits=store.records('credits',PLAN),module_states={m['id']:book.module(m['id']).state for m in modules}))
    dump(root/'snapshot-manifest.json',manifest)


def select_scope(data):
    modules=sorted(data['modules'],key=lambda m:(m['position'],m['id']))
    state=data['position']['state']
    active=next(m for m in modules if m['id']==state['module_id'])
    taught={r['credit']['unit_id'] for r in data['credits'] if r['id'].startswith(('learn:','relearn:'))}
    result=[]
    for module in modules:
        if module['position']<active['position'] or data['module_states'][module['id']] in ('passed','skipped'):continue
        points=sorted([p for p in data['points'] if p['module_id']==module['id']],key=lambda p:(p['position'],p['id']))
        points=[p for i,p in enumerate(points) if p['id'] not in taught
                and (module['id']!=active['id'] or i>=state['index'])]
        if points:result.append((module,points))
    return result


class PaidCalls:
    def __init__(self,root,account,preset,budget):
        from tests.refactor.simulation.run import live_factory
        from tests.refactor.simulation.engine import Evidence,RecordedGateway
        self.root=root; self.cap=32768
        self.evidence=Evidence(root/'audit')
        real=live_factory(preset,account)(SimpleNamespace(account_id=account,data_root=root/'model-data'))
        self.gateway=RecordedGateway(real,self.evidence,budget=str(budget),offline=False,allowed={'generate'})
        # The experiment has a strict monetary ceiling. Only this process uses
        # a smaller output cap; no source, stored preset or shared config changes.
        from refactor.learning.connections.gateway import MODEL_MAX_OUTPUT
        self.cap_patch=patch.dict(MODEL_MAX_OUTPUT,{'deepseek-v4-flash':self.cap,'deepseek-flash':self.cap})
        self.cap_patch.start()
        self.calls={}
        for path in (root/'call-cache').glob('*.json'):
            row=json.loads(path.read_text(encoding='utf-8')); self.calls[row['key']]=row['text']

    def __call__(self,stage,system,messages):
        key=hashlib.sha256(json.dumps([stage,system,messages],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        if key in self.calls:return self.calls[key]
        real=self.gateway.real; config=real.get_configuration()
        upper=len((system+json.dumps(messages,ensure_ascii=False)).encode('utf-8'))+4096
        cost=real.price_catalog.estimate_cost(config['provider_id'],config['model'],input_tokens=(upper,upper),
            output_tokens=(self.cap,self.cap),cache_hit_rate=(0,0),context=dict(real.estimate_context(),price_period='peak',cache_mode='implicit'))
        if cost is None or cost.currency!='CNY' or self.gateway.spent+cost.high>self.gateway.limit:
            raise RuntimeError('BUDGET:下一次完整输出上限预留会超过总预算，未调用')
        self.evidence.event('forecast',stage=stage,expected={'classify':'先按整节用途分类；混合节逐段归属并说明理由',
                            'extract':'每段有概念/导航归属','merge':'全模块同概念合并，3～8小要点，分钟由宿主规则决定','notes':'2～6条逐字原文摘句'}[stage],
                            worst_peak_cny=str(cost.high),spent_peak_cny=str(self.gateway.spent))
        print('calling',stage,'spent peak',self.gateway.spent,'reserve',cost.high,flush=True)
        result=self.gateway.complete('generate',messages,system=system,max_tokens=self.cap,plan_id=PLAN,
                                     reasoning_effort='high' if stage=='merge' else 'low')
        self.calls[key]=result.text
        dump(self.root/'call-cache'/(key+'.json'),dict(key=key,stage=stage,text=result.text))
        print('completed',stage,'spent peak',self.gateway.spent,flush=True)
        return result.text

    def close(self):
        self.gateway.close();self.cap_patch.stop()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--account-id',default='mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932')
    parser.add_argument('--preset-root',type=Path,default=Path('D:/MentorFlowData/account-shell'))
    parser.add_argument('--budget',default='5')
    parser.add_argument('--rest',action='store_true',help='人工阶段一通过后才处理其他模块')
    parser.add_argument('--snapshot',action='store_true',help='只读备份指定用户库到本任务目录')
    args=parser.parse_args(argv)
    root=args.root.resolve()
    if not root.is_relative_to(DEFAULT_ROOT.parent):parser.error('只允许D盘test-tmp独立目录')
    if args.snapshot:snapshot(root,args.account_id)
    if not (root/'input112.json').exists():
        print('SKIP:先用--snapshot准备新的只读副本，默认不访问模型');return 0
    data=json.loads((root/'input112.json').read_text(encoding='utf-8'))
    scope=select_scope(data)
    inventory=[]
    for m,ps in scope:
        catalogue,required=a.concept_materials(ps,data['chunks'])
        inventory.append(dict(module=m['title'],position=m['position'],old_count=len(ps),
            old_minutes=sum(p['minutes'] for p in ps),paragraphs=len(catalogue),characters=sum(len(p['text']) for p in catalogue.values())))
    dump(root/'scope.json',inventory)
    print(json.dumps(inventory,ensure_ascii=False,indent=2),flush=True)
    if not args.live:print('SKIP real model: add --live only within authorized budget');return 0
    if args.rest:
        gate=json.loads((root/'stage1-review.json').read_text(encoding='utf-8'))
        if gate.get('passed') is not True:parser.error('第一段尚未人工通过')
    caller=PaidCalls(root,args.account_id,args.preset_root,Decimal(args.budget))
    try:
        for m,ps in (scope[1:] if args.rest else scope[:1]):
            output=root/f'module-{m["position"]+1}.json'
            if output.exists():continue
            def checkpoint(stage,value):dump(root/'work'/f'module-{m["position"]+1}-{stage}.json',value)
            result=a.rebuild_concept_module(m,ps,data['chunks'],caller,checkpoint=checkpoint)
            result.update(old_points=ps,old_minutes=sum(p['minutes'] for p in ps),
                          new_minutes=sum(p['minutes'] for p in result['points']),module_position=m['position'])
            dump(output,result)
            print('MODULE COMPLETE',m['title'],len(ps),'->',len(result['points']),'navigation',len(result['navigation']),
                  'uncovered',len(result['uncovered']),flush=True)
    finally:caller.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
