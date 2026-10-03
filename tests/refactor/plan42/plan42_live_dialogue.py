"""Opt-in real intake conversation; no search, generation, account or data writes.

Run --live once, then --live --reply with a human-written reply until preview.
Uses existing read-only administrator preset; all usage and drafts are isolated.
"""
import argparse
from contextlib import ExitStack
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))
from tests.refactor.plan_dialogue import plan_dialogue_live as helpers
from refactor.learning.app.bridge import CallContext
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.providers import _http
from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.course.store import CourseStore

ROOT=Path(r'D:\MentorFlow\.local\test-tmp\fix42-plan')
GOAL='我不太懂计算机，想把各种各样的术语概念都搞明白，不需要上手操作，要老师用生活化例子讲清楚，以后交流时能说得头头是道'


class Budget42(helpers.Budget):
    reservation=Decimal('0.10')
    def reserve(self):
        if self.state['blocked'] or Decimal(self.state['held']) or Decimal(self.state['peak_amount'])+self.reservation>Decimal('1'):
            raise ModelError('EVAL_BUDGET_STOP','本次验证达到一元预算，停止请求。')
        self.state['held']=str(self.reservation)
        helpers.dump(self.path,self.state)


def run(reply=None):
    ROOT.mkdir(parents=True,exist_ok=True)
    live=ROOT/'live';live.mkdir(exist_ok=True)
    previous=json.loads((live/'result.json').read_text(encoding='utf-8')) if (live/'result.json').exists() else None
    if previous and previous.get('phase')=='preview':raise RuntimeError('Preview complete; no further paid testing allowed')
    if previous and not reply:raise RuntimeError('Reply required to continue existing dialogue')
    helpers.OUTPUT_ROOT=ROOT
    budget=Budget42(live/'budget.json',PriceCatalog(REPO/'refactor/learning/course/data/model-prices.json'))
    fingerprint=helpers._admin_fingerprints()
    store=CourseStore('mf01_plan42_isolated',live/'data')
    try:
        with ExitStack() as stack:
            gateway=helpers.live_gateway(stack,live,budget)
            guarded=_http.HTTP.json
            async def bounded(http,url,body,headers=None):
                if body.get('tools'):raise ModelError('EVAL_BUDGET_STOP','本轮不搜索资料。')
                quote=budget.catalog.estimate_cost('deepseek',gateway.get_configuration()['model'],
                    input_tokens=(0,len(json.dumps(body,ensure_ascii=False))*2),output_tokens=(0,body.get('max_tokens',4096)),
                    context={'region':'global','service_tier':'standard','price_period':'peak'})
                if not quote or quote.currency!='CNY':raise ModelError('EVAL_BUDGET_STOP','无法计价，停止请求。')
                budget.reservation=quote.high
                return await guarded(http,url,body,headers)
            stack.enter_context(patch.object(_http.HTTP,'json',bounded))
            flow=PlanFlow(SimpleNamespace(account_id='mf01_plan42_isolated',data_root=live/'data',store=store,gateway=gateway),lambda *_:None,date.today)
            flow._research=lambda *_:(_ for _ in ()).throw(AssertionError('Stop at preview, never search'))
            ctx=CallContext('plan42-real',threading.Event(),lambda _:None)
            if previous:
                pid=previous['plan_id']
                flow.run(pid,lambda d,c:flow.chat(d,c,reply),ctx)
            else:
                pid=flow.current()['plan_id'];today=date.today()
                form=dict(goal=GOAL,final_goal='日常交流能解释常见计算机术语，听懂别人讨论',level='zero',level_note='不了解计算机',
                    notes='不要上手操作，用生活化例子；先讨论要学的范围。',start=today.isoformat(),end=(today+timedelta(days=6)).isoformat(),
                    daily_cap=45,weekday_weights=[1]*7,special_days=[],only_mine=False)
                flow.run(pid,lambda d,c:flow.submit_form(d,c,form),ctx)
            draft=flow.load(pid)
            result=dict(plan_id=pid,phase=draft['phase'],preview=draft.get('preview'),
                        cost_cny_peak=budget.state['peak_amount'],calls=len(budget.state['responses']))
            helpers.dump(live/'result.json',result)
            lines=['# 第四十二批真实需求对话（无资料、没有搜索）','']
            for m in draft['messages']:
                lines.extend([f"**{'用户' if m['role']=='user' else 'AI'}**：",m['text'],''])
            (ROOT/'dialogue.md').write_text('\n'.join(lines),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False))
            print(draft['messages'][-1]['text'])
    finally:
        store.close()
        assert helpers._admin_fingerprints()==fingerprint,'Administrator files changed'


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--reply')
    args=parser.parse_args()
    if not args.live:parser.error('Use --live only for the explicitly authorized small dialogue')
    run(args.reply)
