"""Opt-in public website read + existing real outline gateway; never pytest-live."""
import argparse,hashlib,json,sys,threading,time
from pathlib import Path
from datetime import date,timedelta
from types import SimpleNamespace
from unittest.mock import patch

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.app.bridge import CallContext
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.store import CourseStore
from refactor.learning.course.source_filter import load_lists
from refactor.learning.course.site_book import Reader
from tests.refactor.simulation.run import live_factory
from tests.refactor.simulation.engine import Evidence,RecordedGateway


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--live',action='store_true')
    p.add_argument('--account-id',required=True);p.add_argument('--preset-root',type=Path,required=True)
    p.add_argument('--site',choices=['network','python'],required=True);p.add_argument('--resume',action='store_true');args=p.parse_args()
    if not args.live:print('SKIP: --live required');return
    root=Path('D:/MentorFlow/.local/test-tmp/site125');directory=root/args.site;directory.mkdir(parents=True,exist_ok=True)
    expected=('预计：网站自己的目录发现章节（不调用模型）；模拟用户确认默认全选，再按robots串行读。'
        '小林网络与Python教程各合成一份资料、保持网站层级，单页失败列清，不补其他网址；'
        '只在读完后调用现有outline进入资料结构，合计预算¥0.50。为聚焦读取，需求表单固定写入隔离草稿，不另花钱测需求核对。')
    with (root/'report.md').open('a',encoding='utf-8') as file:file.write('\n\n真实核对前预期：'+args.site+'：'+expected+'\n')
    evidence=Evidence(root/'audit');real=live_factory(args.preset_root,args.account_id)(SimpleNamespace(account_id=args.account_id,data_root=root/'credentials-test'))
    class Budget(RecordedGateway):
        def complete(self,purpose,messages,**kw):
            with self.lock:
                config=real.get_configuration();upper=len((kw.get('system','')+json.dumps(messages,ensure_ascii=False)).encode('utf-8'))+4096
                estimate=real.price_catalog.estimate_cost(config['provider_id'],config['model'],input_tokens=(upper,upper),output_tokens=(32768,32768),cache_hit_rate=(0,0),context=dict(real.estimate_context(),price_period='peak',cache_mode='implicit'))
                if estimate is None or self.spent+estimate.high>self.limit:self.reject('BUDGET','最高费用预留超¥0.50，未发送',purpose)
                return super().complete(purpose,messages,**kw)
    gateway=Budget(real,evidence,budget='.50',allowed={'outline'})
    store=CourseStore(args.account_id,directory/'data');services=SimpleNamespace(store=store,gateway=gateway,
        account_id=args.account_id,data_root=directory/'data',catalog=SimpleNamespace(load_lists=load_lists))
    started=time.monotonic();summary=dict(expected=expected,site=args.site);capture=directory/'pages';capture.mkdir(exist_ok=True)
    original=Reader.raw
    def raw(reader,url,**kw):
        try:value=original(reader,url,**kw)
        except Exception as exc:
            print('HTTP ERROR',url,type(exc).__name__,str(exc),flush=True)
            raise
        name=hashlib.sha256(url.encode()).hexdigest()+'.html'
        (capture/name).write_bytes(value.body)
        (capture/(name+'.json')).write_text(json.dumps(dict(url=url,final_url=value.final_url,content_type=value.content_type),ensure_ascii=False),encoding='utf-8')
        return value
    def push(name,data):
        if name=='plan_draft':
            for card in data.get('sources',[]):
                if card.get('read_total'):print('progress',card.get('read_done',0),card['read_total'],flush=True)
    flow=PlanFlow(services,push,date.today);ctx=CallContext('live125',threading.Event(),lambda _:None)
    url='https://xiaolincoding.com/network/' if args.site=='network' else 'https://docs.python.org/zh-cn/3/tutorial/'
    goal='图解网络：TCP/IP、HTTP与可靠传输' if args.site=='network' else 'Python入门：语法、控制流、数据结构与模块'
    try:
        previous=json.loads((directory/'verification.json').read_text(encoding='utf-8')) if args.resume else None
        if previous:
            pid=previous['plan_id'];d=flow.load(pid)
        else:
            d=flow.current();pid=d['plan_id'];day=date.today();form=dict(goal=goal,final_goal='理解所选网站专题的基础知识',level='beginner',level_note='',notes='按所选网站目录学习，先只整理资料结构。',start=day.isoformat(),end=(day+timedelta(days=30)).isoformat(),daily_cap=90,weekday_weights=[1]*7,special_days=[],only_mine=True)
            store.create_plan(pid,goal=goal,final_goal=form['final_goal'],level='beginner',window=PlanWindow(day,day+timedelta(days=30),90))
            d.update(form=form,goal=goal,phase='sources');flow.save(d)
        summary['plan_id']=pid
        with patch.object(Reader,'raw',raw):
            began=time.monotonic()
            if not previous:flow.run(pid,lambda d,c:flow._import_links(d,c,[url]),ctx)
            cards=flow.load(pid)['sources'];card=next(v for v in cards if v.get('site_book'))
            summary.update(directory_seconds=previous['directory_seconds'] if previous else time.monotonic()-began,directory=card['site_book'])
            assert not card.get('source_id')
            began=time.monotonic();flow.run(pid,lambda d,c:flow.decide(d,card['card_id'],True,ctx=c,selected_pages=[v['id'] for v in card['site_book']['entries']]),ctx)
            card=next(v for v in flow.load(pid)['sources'] if v['card_id']==card['card_id'])
            summary.update(read_seconds=time.monotonic()-began,card=card)
            assert card['status']=='adopted',card.get('read_error','')
        summary['sources']=store.records('sources',pid)
        before=gateway.spent;began=time.monotonic()
        flow.run(pid,lambda d,c:flow.material_structure(d,c),ctx)
        draft=flow.load(pid);summary.update(phase=draft['phase'],structure=draft.get('material_structure'),structure_seconds=time.monotonic()-began,structure_peak_cost=str(gateway.spent-before))
        assert draft['phase']=='material_preview'
    except Exception as exc:
        summary['error']=dict(type=type(exc).__name__,message=str(exc));raise
    finally:
        summary.update(seconds=time.monotonic()-started,peak_cost=str(gateway.spent),calls=evidence.calls)
        (directory/'verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        services._closed=True;store.close();real.close()
        print(json.dumps({k:v for k,v in summary.items() if k not in ('sources','structure','directory','calls','card')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
