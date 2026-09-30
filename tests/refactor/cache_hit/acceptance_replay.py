"""Opt-in, capped saved-prompt replay. Dry-run never reads credentials.

No classroom decisions or user data writes; responses do not feed the host.
The short teacher segment isolates the source-subset placement change.
"""
from contextlib import ExitStack
from dataclasses import asdict
from decimal import Decimal
import argparse
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

from refactor.learning.connections import gateway as gateway_module
from refactor.learning.connections.credentials import AccountCredentials
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.pricing import PriceCatalog
from refactor.learning.connections.usage import UsageLog
from refactor.learning.preferences.storage import AccountJSON
from .acceptance_audit import load_calls, reorder, analyze

IDS = {'call-0097','call-0100','call-0103','call-0106','call-0109'}
ROOT = Path(r'D:\MentorFlow\.local\test-tmp\cache-hit3')
ADMIN_ROOT = Path(r'D:\MentorFlowData\account-shell')
ADMIN = 'mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932'
REPO = Path(__file__).resolve().parents[3]
CAP = Decimal('0.5')


def save(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


def run(live=False):
    ROOT.mkdir(parents=True,exist_ok=True)
    originals=[r for r in load_calls(Path(r'D:\MentorFlow\.local\test-tmp\acceptance\calls')) if r['id'] in IDS]
    rows=[reorder(r) for r in originals]
    if len(rows)!=len(IDS):
        raise RuntimeError('Fixed sample incomplete')
    catalogue=PriceCatalog(REPO/'refactor/learning/course/data/model-prices.json')
    def forbidden(*args,**kwargs):
        raise AssertionError('Admin writes forbidden')
    with ExitStack() as stack:
        stack.enter_context(patch.object(AccountJSON,'write',forbidden))
        for name in ('put','delete','clear'):
            stack.enter_context(patch.object(AccountCredentials,name,forbidden))
        stack.enter_context(patch.object(gateway_module,'UsageLog',lambda _p,**kw:UsageLog(ROOT/'live-usage.sqlite3',**kw)))
        gateway=gateway_module.ModelGateway(ADMIN,ADMIN_ROOT,price_catalog=catalogue)
        stack.callback(gateway.close)
        config=gateway.get_configuration()
        if not config or config['provider_id']!='deepseek' or config['model'] not in ('deepseek-flash','deepseek-v4-flash'):
            raise RuntimeError('Existing admin DeepSeek Flash preset required')
        context=dict(gateway.estimate_context(),cache_mode='implicit',price_period='peak')
        tickets=[]
        for row in rows:
            bound=len(json.dumps(dict(system=row['system'],messages=row['messages']),ensure_ascii=False).encode('utf-8'))+2048
            estimate=catalogue.estimate_cost(config['provider_id'],config['model'],input_tokens=(bound,bound),
                output_tokens=(row['max_tokens'],row['max_tokens']),cache_hit_rate=(0,0),context=context)
            if estimate is None or estimate.currency!='CNY':
                raise RuntimeError('No verified spending bound')
            tickets.append(dict(id=row['id'],reserved=str(estimate.high),status='pending'))
        total=sum(Decimal(t['reserved']) for t in tickets)
        save(ROOT/'replay-prediction.json',dict(before=analyze(originals),after=analyze(rows),reserve=str(total),tickets=tickets))
        print('calls',len(rows),'peak_no_cache_reserve',total,flush=True)
        if not live:
            return
        if os.environ.get('MF_CACHE_HIT3_LIVE')!='1' or total>CAP:
            raise RuntimeError('Live switch missing or first-stage reserve exceeds cap; no requests sent')
        if (ROOT/'live-budget.json').exists():
            raise RuntimeError('Replay already journalled; do not spend twice')
        results=[]
        save(ROOT/'live-budget.json',tickets)
        for row,ticket in zip(rows,tickets):
            observed=dict(id=row['id'],at=None,purpose=row['purpose'],system=row['system'],messages=row['messages'])
            from datetime import datetime,timezone
            observed['at']=datetime.now(timezone.utc).isoformat()
            start=time.monotonic()
            try:
                result=gateway.complete(row['purpose'],row['messages'],system=row['system'],max_tokens=row['max_tokens'])
                usage=result.usage
                observed.update(status='received',text=result.text,usage=asdict(usage))
            except ModelError as exc:
                observed.update(status='failed',error=exc.code)
                usage=exc.usage
                if usage is not None:
                    observed['usage']=asdict(usage)
            if usage is not None:
                quote=catalogue.cost(config['provider_id'],config['model'],gateway._billable_usage(usage,config,'low'),context=context)
                ticket['peak_cost']=str(quote.amount) if quote else None
            ticket['status']=observed['status']
            observed['seconds']=round(time.monotonic()-start,3)
            results.append(observed)
            save(ROOT/'live-results.json',results)
            save(ROOT/'live-budget.json',tickets)
            print(row['id'],observed['status'],(observed.get('usage') or {}).get('input_tokens'),
                (observed.get('usage') or {}).get('cached_input_tokens'),observed['seconds'],flush=True)
            # Allow documented best-effort prefix writes without extra requests.
            time.sleep(3)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    run(parser.parse_args().live)
