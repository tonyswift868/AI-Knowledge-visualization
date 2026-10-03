"""One opt-in tiny real search/map check; readonly administrator config, RMB 1 cap.

Not a student simulation or a pytest live test. Run explicitly with --live.
All data/account usage is isolated under fix37-plan; no application/user DB writes.
"""
from contextlib import ExitStack
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO), str(REPO / 'tests/refactor')]
from plan_dialogue import plan_dialogue_live as helpers
from refactor.learning.app.bridge import CallContext
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.connections.providers import _http
from refactor.learning.course.source_filter import load_lists
from refactor.learning.course.store import CourseStore

ROOT = Path(r'D:\MentorFlow\.local\test-tmp\fix37-plan')


class Budget37(helpers.Budget):
    reservation = Decimal('0.65')
    def reserve(self):
        if (self.state['blocked'] or Decimal(self.state['held'])
                or Decimal(self.state['peak_amount']) + self.reservation > Decimal('1')):
            raise ModelError('EVAL_BUDGET_STOP', '达到本次一元预算，不发送后续请求。')
        self.state['held'] = str(self.reservation)
        helpers.dump(self.path, self.state)


def run(continue_map=False, finish_map=False):
    ROOT.mkdir(parents=True, exist_ok=True)
    live_root = ROOT / 'live'
    live_root.mkdir(exist_ok=True)
    if (live_root / 'result.json').exists() and not continue_map:
        raise RuntimeError('One round only; an existing live result must not be overwritten')
    helpers.OUTPUT_ROOT = ROOT
    budget = Budget37(live_root / 'budget.json', PriceCatalog(REPO / 'refactor/learning/course/data/model-prices.json'))
    result = {'baseline': 'eecb4a6', 'goal': '初二物理：压强与浮力', 'status': 'started', 'points': [], 'limit_cny': '1'}
    store = CourseStore('mf05_plan37_live_isolated', live_root / 'data')
    try:
        with ExitStack() as stack:
            gateway = helpers.live_gateway(stack, live_root, budget)
            original = _http.HTTP.json
            async def bounded_json(http, url, body, headers=None):
                # Tiny-topic search: bound search uses and output before sending;
                # price reservation includes up to 256k tool-context tokens.
                body['max_tokens'] = min(body.get('max_tokens',8192),32768)
                for tool in body.get('tools',[]): tool['max_uses'] = min(tool.get('max_uses',2),2)
                searching = bool(body.get('tools'))
                input_upper = max(256000 if searching else 0, len(json.dumps(body,ensure_ascii=False))*2)
                quote = budget.catalog.estimate_cost('deepseek', gateway.get_configuration()['model'],
                    input_tokens=(0,input_upper), output_tokens=(0,body['max_tokens']), search_requests=(0,2 if searching else 0),
                    context={'region':'global','service_tier':'standard','price_period':'peak'})
                if quote is None or quote.currency != 'CNY':
                    raise ModelError('EVAL_BUDGET_STOP','无法计价，不发送请求。')
                budget.reservation = quote.high
                return await original(http,url,body,headers)
            stack.enter_context(patch.object(_http.HTTP, 'json', bounded_json))
            services = SimpleNamespace(account_id='mf05_plan37_live_isolated', data_root=live_root / 'data',
                store=store, gateway=gateway, catalog=SimpleNamespace(load_lists=load_lists))
            events = []
            flow = PlanFlow(services, lambda kind,data: events.append((kind,data)), date.today)
            if finish_map:
                ask = flow._ask
                def resume_output_budget(purpose, request, parse, ctx, **kwargs):
                    if purpose == 'outline': kwargs['max_tokens'] = 16384
                    return ask(purpose, request, parse, ctx, **kwargs)
                flow._ask = resume_output_budget
            ctx = CallContext('plan37-real',threading.Event(),lambda _:None)
            draft = flow.load(flow.drafts()[0]['plan_id']) if continue_map else flow.current()
            today = date.today()
            form = dict(goal=result['goal'],final_goal='理解压强、增大减小压强与浮力的基本概念，能解释生活现象',
                level='beginner',level_note='学过初一基础知识',notes='接受中文与英文正规教材；可找 OpenStax College Physics 的 Pressure 和 Buoyancy 章节。只需要这两个主题，不学液体压强公式 p=ρgh。',
                start=today.isoformat(),end=(today+timedelta(days=13)).isoformat(),daily_cap=30,
                weekday_weights=[1]*7,special_days=[],only_mine=False)
            if continue_map:
                previous = json.loads((live_root/'result.json').read_text(encoding='utf-8'))
                result['initial_validation'] = previous.get('initial_validation') or {k:previous.get(k) for k in ('status','coverage','cost_cny_peak')}
                if finish_map:
                    result['harness_correction'] = 'Previous test wrapper capped the automatic 16384-token retry at 8192; reuse obtained originals and allow the requested retry budget, still under RMB 1.'
                # Continue the same tiny check using already fetched/adopted
                # originals; no second search or student simulation. Choose
                # the supported definitions instead of the uncovered examples.
                response = flow.run(draft['plan_id'],lambda d,c: flow._update_form(d,c,{
                    'goal':result['goal'],'final_goal':'理解压强与浮力的基本定义，不要求计算和生活现象分析'}),ctx)
            else:
                response = flow.run(draft['plan_id'],lambda d,c: flow.submit_form(d,c,form),ctx)
            draft = flow.load(draft['plan_id'])
            result['form_busy'] = response['draft']['busy']
            if draft['phase'] == 'questions':
                result['status'] = 'needs_requirements_clarification'
            elif not draft['sources']:
                result['status'] = 'no_sources_passed_current_baseline'
            else:
                # Test learner explicitly approves at most three actually read
                # sources returned by automatic research; no invented URLs.
                if not continue_map:
                    for card in draft['sources'][:3]:
                        flow.run(draft['plan_id'],lambda d,c,card=card: flow.decide(d,card['card_id'],True),ctx)
                flow.run(draft['plan_id'],lambda d,c: flow.make_outline(d,c),ctx)
                draft = flow.load(draft['plan_id'])
                result['status'] = 'map_ready' if draft.get('outline') else 'coverage_incomplete'
                chunks = {c['id']:c for c in store.records('source_chunks',draft['plan_id'])}
                for stage in (draft.get('outline') or {}).get('stages',[]):
                    for module in stage['modules']:
                        for point in module['points']:
                            citations = []
                            for citation in point['citations']:
                                chunk = chunks[citation['chunk_id']]
                                citations.append(dict(citation,verified=citation['quote'] in chunk['text'],
                                    original_sha256=hashlib.sha256(chunk['text'].encode()).hexdigest()))
                            result['points'].append({'module':module['title'],'title':point['title'],'citations':citations})
                helpers.dump(live_root/'adopted-original-chunks.json',list(chunks.values()))
            result['coverage'] = draft.get('coverage')
            result['dropped'] = (draft.get('outline') or {}).get('dropped',[])
            result['messages'] = [{'kind':m['kind'],'text':m['text']} for m in draft['messages']]
            result['usage'] = gateway.summary()
    except Exception as exc:
        result.update(status='blocked',error_code=getattr(exc,'code',type(exc).__name__))
    finally:
        result['cost_cny_peak'] = budget.state['peak_amount']
        result['unsettled_reservation'] = budget.state['held']
        helpers.dump(live_root/'result.json',result)
        store.close()
    print(json.dumps({k:v for k,v in result.items() if k in ('status','error_code','cost_cny_peak','unsettled_reservation')},ensure_ascii=False))


if __name__ == '__main__':
    if '--live' not in sys.argv: raise SystemExit('Explicit --live required; this costs at most RMB 1')
    run('--continue-map' in sys.argv or '--finish-map' in sys.argv, '--finish-map' in sys.argv)
