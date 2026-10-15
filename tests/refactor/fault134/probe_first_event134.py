"""Opt-in ten tiny latency probes; normal pytest never invokes live models."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
import time

from refactor.learning.connections.errors import ModelError
from tests.refactor.simulation.run import live_factory


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--account-id',required=True)
    parser.add_argument('--preset-root',type=Path,required=True)
    args=parser.parse_args()
    root=args.root.resolve()
    assert root.is_relative_to(Path('D:/MentorFlow/.local/test-tmp/fault134'))
    gateway=live_factory(args.preset_root,args.account_id)(SimpleNamespace(data_root=root/'data'))
    rows=[];spent=Decimal(0)
    try:
        config=gateway.get_configuration()
        for thinking in ['high']*5+['none']*5:
            reserve=gateway.price_catalog.estimate_cost('deepseek',config['model'],input_tokens=(256,256),output_tokens=(512,512),
                cache_hit_rate=(0,0),context=dict(gateway.estimate_context(),price_period='peak',thinking_mode='thinking' if thinking=='high' else 'non_thinking'))
            if reserve is None or spent+reserve.high>Decimal('2'):raise RuntimeError('budget unavailable/exhausted; stopped')
            result=None;error=None;start=time.monotonic()
            try:
                result=gateway.complete('connection_test',[dict(role='user',content='Reply only OK.')],
                                        on_text=lambda _:None,reasoning_effort=thinking,max_tokens=512)
            except ModelError as exc:error=exc
            usage=result.usage if result else getattr(error,'usage',None)
            billable=gateway._billable_usage(usage,config,thinking) if usage else None
            cost=gateway.price_catalog.cost('deepseek',config['model'],billable,
                context=dict(gateway.estimate_context(),price_period='peak',thinking_mode='thinking' if thinking=='high' else 'non_thinking')) if billable else None
            spent+=cost.amount if cost else reserve.high
            meta=usage.raw.get('_mentorflow',{}) if usage else {}
            row=dict(number=len(rows)+1,thinking=thinking,first_event_ms=meta.get('first_event_ms'),
                seconds=round(time.monotonic()-start,3),code=error.code if error else 'OK',
                peak_cost=str(cost.amount) if cost else None,reserved=str(reserve.high),
                input_tokens=usage.input_tokens if usage else None,output_tokens=usage.output_tokens if usage else None,
                sent_reasoning=meta.get('sent_reasoning_parameters'),thinking_blocks=meta.get('thinking_blocks'))
            rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
            (root/'latency134.json').write_text(json.dumps(dict(rows=rows,peak_spent_or_reserved=str(spent)),ensure_ascii=False,indent=2),encoding='utf-8')
            if cost is None and error and error.code!='MODEL_FIRST_EVENT_TIMEOUT':break
    finally:gateway.close()


if __name__=='__main__':main()
