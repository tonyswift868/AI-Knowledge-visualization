"""Explicit --live only: recommend, select webpage/repository, material structure.

No real calls during pytest; administrator credentials use existing live_factory.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import threading
import time
from datetime import date,timedelta
from types import SimpleNamespace
from unittest.mock import patch

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.app.bridge import CallContext
from refactor.learning.course.source_filter import load_lists
from refactor.learning.course.store import CourseStore
from refactor.learning.course import github_repo
from refactor.learning.connections.gateway import MODEL_MAX_OUTPUT
from tests.refactor.simulation.run import live_factory
from tests.refactor.simulation.engine import Evidence,RecordedGateway


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true');parser.add_argument('--account-id',required=True)
    parser.add_argument('--preset-root',type=Path,required=True)
    args=parser.parse_args()
    if not args.live:print('SKIP: --live required');return
    root=Path('D:/MentorFlow/.local/test-tmp/adopt124/live');root.mkdir(parents=True,exist_ok=True)
    expected=('预计：管理员DeepSeek推荐Python虚拟环境/安装包资料，选择原推荐网页后原卡片获得source_id及字数；'
        '选仓库走现有GitHub读取。缺一种类型则按任务授权主动贴同类链接，不暗换失败卡片。最后只到资料结构，不切点、不备题。'
        '正文读取不用模型，预算共¥0.30；模型最大输出仅测试进程限8192，不改管理员配置。')
    report=root.parent/'report.md'
    report.write_text('# 124-MF06 执行记录\n\n基线 b8459056。\n\n真实调用前预期：'+expected+'\n',encoding='utf-8')
    evidence=Evidence(root/'audit')
    real=live_factory(args.preset_root,args.account_id)(SimpleNamespace(account_id=args.account_id,data_root=root/'data'))
    class Budget(RecordedGateway):
        def complete(self,purpose,messages,**kwargs):
            with self.lock:
                config=real.get_configuration()
                upper=len((kwargs.get('system','')+json.dumps(messages,ensure_ascii=False)).encode('utf-8'))+4096
                cost=real.price_catalog.estimate_cost(config['provider_id'],config['model'],input_tokens=(upper,upper),
                    output_tokens=(8192,8192),cache_hit_rate=(0,0),context=dict(real.estimate_context(),price_period='peak',cache_mode='implicit'))
                if cost is None or self.spent+cost.high>self.limit:self.reject('BUDGET','本次最高预留会超过¥0.30，未发送',purpose)
                return super().complete(purpose,messages,**kwargs)
    gateway=Budget(real,evidence,budget='.30',allowed={'sufficiency','research','outline','plan_intent'})
    store=CourseStore(args.account_id,root/'data')
    services=SimpleNamespace(store=store,gateway=gateway,account_id=args.account_id,data_root=root/'data',catalog=SimpleNamespace(load_lists=load_lists))
    flow=PlanFlow(services,lambda *a:None,date.today);ctx=CallContext('live124',threading.Event(),lambda _:None)
    summary=dict(expected=expected,imports=[]);started=time.monotonic()
    try:
        with patch.dict(MODEL_MAX_OUTPUT,{'deepseek-v4-flash':8192,'deepseek-flash':8192}):
            pid=flow.current()['plan_id'];summary['plan_id']=pid;day=date.today()
            def action(name,fn):
                print(name,flush=True);value=flow.run(pid,fn,ctx)
                evidence.save('draft-'+name+'.json',flow.load(pid));return value
            action('form',lambda d,c:flow.submit_form(d,c,dict(goal='Python入门：创建虚拟环境，用pip安装包，阅读最小Python包项目',
                final_goal='理解venv与pip的基本用法及一个最小包项目的目录',level='beginner',level_note='',notes='只学基础，推荐文字网页与小型GitHub示例仓库，不要视频。',
                start=day.isoformat(),end=(day+timedelta(days=6)).isoformat(),daily_cap=60,weekday_weights=[1]*7,special_days=[],only_mine=False)))
            if flow.load(pid)['phase']=='questions':
                action('answer',lambda d,c:flow.chat(d,c,'在Windows用Python，只理解venv、pip和最小包目录，零基础，不发布软件。'))
            action('recommend',lambda d,c:flow._confirm_preview(d,c))
            cards=flow.load(pid).get('recommendations',[])
            summary.update(recommendations=cards,recommend_peak_cost=str(gateway.spent))
            for kind,fallback in [('web','https://docs.python.org/3/tutorial/venv.html'),('repo','https://github.com/pypa/sampleproject')]:
                card=next((v for v in cards if bool(github_repo.parse(v['url']))==(kind=='repo') and not v.get('is_video')),None)
                began=time.monotonic();cost=gateway.spent
                if card:
                    action('adopt-'+kind,lambda d,c:flow.decide(d,card['card_id'],True,ctx=c))
                    selected=next(v for v in flow.load(pid)['recommendations'] if v['card_id']==card['card_id'])
                    if selected.get('status')!='adopted':raise RuntimeError('Selected original card failed: '+selected.get('read_error',''))
                    summary['imports'].append(dict(kind=kind,url=card['url'],mode='checkbox',chars=selected['chars'],
                        seconds=time.monotonic()-began,peak_cost=str(gateway.spent-cost),source_id=selected['source_id']))
                else:
                    action('paste-'+kind,lambda d,c:flow._import_links(d,c,[fallback]))
                    source=next((s for s in store.records('sources',pid) if s['url']==fallback),None)
                    if source is None:raise RuntimeError('Explicit fallback import failed: '+fallback)
                    summary['imports'].append(dict(kind=kind,url=fallback,mode='explicit paste (type missing from recommendations)',
                        chars=source['char_count'],seconds=time.monotonic()-began,peak_cost=str(gateway.spent-cost),source_id=source['id']))
            before=gateway.spent;began=time.monotonic()
            action('structure',lambda d,c:flow.material_structure(d,c))
            summary.update(structure=flow.load(pid).get('material_structure'),structure_seconds=time.monotonic()-began,
                structure_peak_cost=str(gateway.spent-before),phase=flow.load(pid)['phase'])
            assert summary['phase']=='material_preview'
    except Exception as exc:
        summary['error']=dict(type=type(exc).__name__,message=str(exc));raise
    finally:
        summary.update(peak_cost=str(gateway.spent),seconds=time.monotonic()-started,calls=evidence.calls)
        evidence.save('verification.json',summary);services._closed=True;store.close();real.close()
        print(json.dumps({k:v for k,v in summary.items() if k not in ('calls','structure','recommendations')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
