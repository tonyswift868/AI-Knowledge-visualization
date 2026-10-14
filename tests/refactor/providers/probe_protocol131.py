"""Opt-in bounded wire probe; no paid requests during pytest collection.

Only metadata/lengths are saved. Credentials use the existing gateway exclusively.
"""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch

from refactor.learning.connections import providers
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.providers._http import HTTP
from tests.refactor.simulation.run import live_factory


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--live', action='store_true')
    p.add_argument('--account-id', required=True)
    p.add_argument('--preset-root', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--requests', type=int, default=20)
    args = p.parse_args()
    if not args.live:
        p.error('--live required')
    root = args.root.resolve()
    if not root.is_relative_to(Path('D:/MentorFlow/.local/test-tmp/proto131')):
        p.error('isolated proto131 directory required')
    root.mkdir(parents=True, exist_ok=True)
    real = live_factory(args.preset_root, args.account_id)(SimpleNamespace(data_root=root/'data'))
    original_events, original_complete = HTTP.events, providers.complete
    rows, sequence = [], []
    spent = Decimal(0)
    config = real.get_configuration()
    assert config['provider_id'] == 'deepseek'
    messages = [dict(role='user', content='学生：TCP 和 TCP/IP 是一回事吗？请解释当前要点，并给我两个可点的后续问题。')]
    system = ('你是中文课堂老师，只讲当前知识点。资料：TCP 是传输层协议，提供可靠、有序的字节流；'
              'IP 是网络层协议，负责寻址和路由。TCP/IP 常指互联网协议族，不是单独的 TCP。'
              '用自然中文解释，不扩大到没有资料的细节。正文后输出 <options>两个后续问题</options>。')

    async def events(http, *a, **kw):
        async for name, data in original_events(http, *a, **kw):
            if isinstance(data, dict):
                item = dict(event=data.get('type', name))
                if 'index' in data:
                    item['index'] = data['index']
                block = data.get('content_block')
                if isinstance(block, dict):
                    item['block_type'] = block.get('type')
                delta = data.get('delta')
                if isinstance(delta, dict):
                    item['delta_type'] = delta.get('type')
                    if 'stop_reason' in delta:
                        item['stop_reason'] = delta['stop_reason']
                    item['lengths'] = {k: len(v) for k, v in delta.items()
                                       if k in {'text', 'thinking', 'signature', 'partial_json'} and isinstance(v, str)}
                sequence.append(item)
            yield name, data

    def complete(*a, **kw):
        # Keep the administrator preset, request shape and real accounting path;
        # bounded output is a probe-only budget limit, not a product change.
        kw['max_tokens'] = 2048
        return original_complete(*a, **kw)

    try:
        with patch.object(HTTP, 'events', events), patch.object(providers, 'complete', complete):
            for i in range(args.requests):
                reserve = real.price_catalog.estimate_cost(config['provider_id'], config['model'],
                    input_tokens=(4096, 4096), output_tokens=(2048, 2048), cache_hit_rate=(0, 0),
                    context=dict(real.estimate_context(), price_period='peak', cache_mode='implicit'))
                if reserve is None or spent + reserve.high > Decimal('3'):
                    break
                sequence.clear()
                start = time.monotonic()
                received = []
                result = None
                error = None
                try:
                    result = real.complete('teacher', messages, system=system, reasoning_effort='high',
                                           on_text=lambda t: received.append(len(t)))
                except ModelError as exc:
                    error = exc
                usage = result.usage if result else getattr(error, 'usage', None)
                billable = real._billable_usage(usage, config, 'high') if usage else None
                cost = real.price_catalog.cost(config['provider_id'], config['model'], billable,
                    context=dict(real.estimate_context(), price_period='peak', cache_mode='implicit')) if billable else None
                spent += cost.amount if cost else reserve.high
                row = dict(number=i+1, seconds=round(time.monotonic()-start, 3),
                    code=error.code if error else 'OK', diagnostics=getattr(error, 'diagnostics', None),
                    block_types=[v['block_type'] for v in sequence if 'block_type' in v],
                    stop_reasons=[v['stop_reason'] for v in sequence if 'stop_reason' in v],
                    visible_characters=sum(received), peak_cost=str(cost.amount) if cost else None,
                    reserved_cost=str(reserve.high), events=list(sequence))
                rows.append(row)
                (root/'results.json').write_text(json.dumps(dict(requests=rows, peak_cost=str(spent)),
                                                          ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({k:v for k,v in row.items() if k not in {'events', 'diagnostics'}}, ensure_ascii=False), flush=True)
                if error or cost is None:
                    break  # First failure/unknown bill: stop first stage immediately.
    finally:
        real.close()
    print('TOTAL_PEAK_COST', spent, flush=True)


if __name__ == '__main__':
    main()
