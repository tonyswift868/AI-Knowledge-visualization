"""Opt-in real new-plan and first-module verification; never runs in pytest.

python -m tests.refactor.wire121.run_live121 --live --account-id ... --preset-root ... --material ...
"""
import argparse
import json
import threading
import time
from datetime import date,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.app.bridge import CallContext
from refactor.learning.course.store import CourseStore
from refactor.learning.course.source_filter import load_lists
from tests.refactor.simulation.run import live_factory
from tests.refactor.simulation.engine import Evidence,RecordedGateway


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live',action='store_true');p.add_argument('--account-id',required=True)
    p.add_argument('--preset-root',type=Path,required=True);p.add_argument('--material',type=Path,required=True)
    p.add_argument('--root',type=Path,default=Path('D:/MentorFlow/.local/test-tmp/wire121/live'))
    args=p.parse_args()
    if not args.live:print('SKIP: --live required');return
    root=args.root.resolve()
    if not root.is_relative_to(Path('D:/MentorFlow/.local/test-tmp/wire121')):p.error('isolated wire121 root required')
    evidence=Evidence(root/'audit')
    real=live_factory(args.preset_root,args.account_id)(SimpleNamespace(account_id=args.account_id,data_root=root/'data'))
    from refactor.learning.connections.gateway import MODEL_MAX_OUTPUT,PURPOSE_MIN_OUTPUT
    class Budget(RecordedGateway):
        def complete(self,purpose,messages,**kwargs):
            # Serial paid calls with full worst-case reservations, including
            # background links, provider minimums and unknown-usage failures.
            with self.lock:
                config=real.get_configuration()
                upper=len((kwargs.get('system','')+json.dumps(messages,ensure_ascii=False)).encode('utf-8'))+4096
                tokens=max(kwargs.get('max_tokens',4096),PURPOSE_MIN_OUTPUT.get(purpose,0),32768)
                cost=real.price_catalog.estimate_cost(config['provider_id'],config['model'],input_tokens=(upper,upper),
                    output_tokens=(tokens,tokens),cache_hit_rate=(0,0),context=dict(real.estimate_context(),price_period='peak',cache_mode='implicit'))
                if cost is None or self.spent+cost.high>self.limit:
                    self.reject('BUDGET','下一次最高预留超过¥4，未发送',purpose)
                return super().complete(purpose,messages,**kwargs)
    gateway=Budget(real,evidence,budget='4',allowed={'sufficiency','research','outline','generate','point_materials',
        'point_links','point_difficulty','distractor_check','plan_intent','source_identity'})
    store=CourseStore(args.account_id,root/'data')
    services=SimpleNamespace(store=store,gateway=gateway,account_id=args.account_id,data_root=root/'data',
        catalog=SimpleNamespace(load_lists=load_lists))
    events=[];day=date.today();ctx=CallContext('live121',threading.Event(),lambda _:None)
    flow=PlanFlow(services,lambda name,data:events.append(dict(name=name,data=data)),lambda:day)
    summary={};start=time.monotonic()
    try:
        with patch.dict(MODEL_MAX_OUTPUT,{'deepseek-v4-flash':32768,'deepseek-flash':32768}):
            pid=flow.current()['plan_id'];summary['plan_id']=pid
            def action(name,fn):
                print(name,flush=True)
                value=flow.run(pid,fn,ctx)
                evidence.save('draft-'+name+'.json',flow.load(pid))
                return value
            action('form',lambda d,c:flow.submit_form(d,c,dict(goal='计算机入门：计算机是什么、硬件和软件、常见类型',
                final_goal='能用自己的话说明这份GCFGlobal短教材的主要内容',level='beginner',level_note='',
                notes='只学上传的小教材，不扩展；模块按原文目录。',start=day.isoformat(),end=(day+timedelta(days=5)).isoformat(),
                daily_cap=60,weekday_weights=[1]*7,special_days=[],only_mine=True)))
            if flow.load(pid)['phase']=='questions':
                action('clarify',lambda d,c:flow.chat(d,c,'只需要理解定义、硬件软件区别和教材列的类型，零基础，每天60分钟。'))
            action('confirm',lambda d,c:flow._confirm_preview(d,c))
            flow.add_file(pid,args.material)
            action('structure',lambda d,c:flow.material_structure(d,c))
            action('points',lambda d,c:flow.resolve_missing(d,c,'continue'))
            outline=flow.load(pid)['outline']
            assert all(s['notes']==[] for stage in outline['stages'] for m in stage['modules'] for p in m['points'] for s in p['subpoints'])
            action('estimate',lambda d,c:flow.estimate(d))
            summary.update(creation_seconds=time.monotonic()-start,creation_calls=len(evidence.calls),creation_peak_cost=str(gateway.spent))
            before=gateway.spent;began=time.monotonic()
            action('prepare',lambda d,c:flow.generate(d,c))
            # Current module can be used immediately. Let only our already
            # scheduled workers finish before closing the isolated services.
            for shared in (flow._point_link_jobs,flow._point_minute_jobs):
                for job in list(shared['jobs'].values()):job['thread'].join(180)
            summary.update(preparation_seconds=time.monotonic()-began,preparation_peak_cost=str(gateway.spent-before),
                peak_cost=str(gateway.spent),calls=evidence.calls,phase=flow.load(pid)['phase'])
            summary['modules']=store.records('modules',pid);summary['points']=store.records('points',pid)
            summary['chunks']=store.records('source_chunks',pid)
            assert flow.load(pid)['phase']=='ready'
            flow.start_request(pid)
            summary['started']=store.get_plan(pid)['state']=='active'
            summary['total_minutes']=store.get_plan(pid)['total_minutes']
    finally:
        services._closed=True
        for shared in (flow._point_link_jobs,flow._point_minute_jobs):
            for job in list(shared['jobs'].values()):job['cancel'].set();job['thread'].join(10)
        evidence.save('verification.json',summary)
        store.close();real.close()
    print(json.dumps({k:v for k,v in summary.items() if k not in ('points','modules','chunks','calls')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
