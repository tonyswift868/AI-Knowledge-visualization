"""Offline prefix audit; byte equality is exact, token rates are NOT measured.

Current official docs (checked 2026-09-29):
https://api-docs.deepseek.com/zh-cn/guides/kv_cache/
No numeric minimum/block size is published there. --block is an explicit
sensitivity assumption, not a provider guarantee. Default 1 is the most
optimistic unrounded prefix estimate. No warm-up calls/padding are inserted.
The legacy 2024 announcement states a 64-token unit/minimum:
https://api-docs.deepseek.com/news/news0802/
It does not establish that V4's current checkpoint rules use that same interval.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import tempfile

from refactor.learning.connections.pricing import estimate_tokens
from .scenario import run_scenario


def canonical(request):
    # Delimiters stand in for hidden vendor chat tokens; do not count JSON keys
    # such as max_tokens/plan_id as though those were part of a model prompt.
    text = '<system>\n' + (request.get('system') or '') + '\n</system>'
    for m in request['messages']:
        text += '\n<' + m['role'] + '>\n' + m['content'] + '\n</' + m['role'] + '>'
    return text


def lcp(a, b):
    end = min(len(a), len(b))
    for i in range(end):
        if a[i] != b[i]:
            return i
    return end


def compatible(a, b):
    return (a.get('model'), bool(a.get('options', {}).get('enable_search')),
            a.get('options', {}).get('reasoning_effort')) == (
        b.get('model'), bool(b.get('options', {}).get('enable_search')),
        b.get('options', {}).get('reasoning_effort'))


def analyze(capture, block=1):
    if not isinstance(block, int) or block < 1:
        raise ValueError('block must be a positive sensitivity assumption')
    history, rows = [], []
    for request in capture['requests']:
        text = canonical(request)
        comparisons = [(lcp(text, prior_text), prior) for prior, prior_text in history if compatible(request, prior)]
        length, prior = max(comparisons, key=lambda v: v[0], default=(0, None))
        same = max((n for n, p in comparisons if p['purpose'] == request['purpose']), default=0)
        # Input ends/public-prefix detection, excluding undocumented periodic
        # checkpoints. This is an explanatory simulation, not a lower guarantee:
        # actual persistence and TTL are best effort and timing-dependent.
        endpoints = [len(t) for p, t in history if compatible(request, p) and text.startswith(t)]
        for i, (a, ta) in enumerate(history):
            if not compatible(request, a):
                continue
            for b, tb in history[:i]:
                if compatible(a, b):
                    n = lcp(ta, tb)
                    if text.startswith(ta[:n]):
                        endpoints.append(n)
        checkpoint = max(endpoints, default=0)
        tokens = estimate_tokens(text)
        reusable = min(tokens, estimate_tokens(text[:length]) // block * block)
        same_tokens = min(tokens, estimate_tokens(text[:same]) // block * block)
        model_tokens = min(tokens, estimate_tokens(text[:checkpoint]) // block * block)
        first_system = not any(compatible(request, p) and p.get('system') == request.get('system') for p, _ in history)
        label = text[:length].rpartition('【')[2].partition('】')[0]
        if prior is None or prior['purpose'] != request['purpose']:
            cause = '首次用途/系统提示不同，不能复用其他 agent 的大段内容'
        elif length == len(text):
            cause = '完全相同；实际仍取决于落盘与保留'
        elif '对话' in label:
            cause = '对话新增或窗口起点变化；详见逐请求差异'
        elif any(s in label for s in ('原文', '资料', '知识点', '模块')):
            cause = '知识点/资料子集或其排列变化'
        else:
            cause = '当前任务、档案、表单或序列化字段在此变化'
        rows.append(dict(index=request['index'], purpose=request['purpose'], input_est=tokens,
                         prefix_est=reusable, same_purpose_est=same_tokens, endpoint_model_est=model_tokens,
                         prefix_chars=length, input_chars=len(text), previous=prior['index'] if prior else None,
                         first_system=first_system, unavoidable_cold_est=tokens-reusable if first_system else 0,
                         cause=cause, boundary_label=label,
                         mismatch_before=canonical(prior)[length:length+120] if prior else '', mismatch_after=text[length:length+120]))
        history.append((request, text))
    def group(items):
        totals = {k: sum(r[k] for r in items) for k in ('input_est', 'prefix_est', 'same_purpose_est', 'endpoint_model_est', 'input_chars', 'prefix_chars', 'unavoidable_cold_est')}
        return dict(calls=len(items), **totals, prefix_rate=totals['prefix_est']/totals['input_est'] if totals['input_est'] else 0,
                    cold_ceiling_est=1-totals['unavoidable_cold_est']/totals['input_est'] if totals['input_est'] else 0,
                    same_purpose_rate=totals['same_purpose_est']/totals['input_est'] if totals['input_est'] else 0,
                    endpoint_model_rate=totals['endpoint_model_est']/totals['input_est'] if totals['input_est'] else 0)
    purposes = defaultdict(list)
    for row in rows:
        purposes[row['purpose']].append(row)
    return dict(token_basis='estimate_tokens; NOT DeepSeek tokenizer or measured cache usage',
                block_assumption=block, official_block_tokens=None, official_minimum_tokens=None,
                legacy_published_block_tokens=64,
                cold_start_included=True, tool_internal_rounds_included=False,
                limitations=['Hidden role/tool tokens and server search turns are unavailable offline.',
                             'Endpoint simulation includes input ends, not cached generated-output continuations.',
                             'Shared-prefix model assumes completed requests and immediate persistence, not guaranteed.',
                             'Different system prompts prevent cross-agent reuse; first requests count in denominator.'],
                total=group(rows), purposes={p: group(v) for p,v in purposes.items()}, requests=rows,
                paid_gate=False, paid_gate_reason='No verified tokenizer/alignment; proxy rates cannot authorize paid acceptance.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--block', type=int, default=1)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    # TemporaryDirectory removes only the fresh isolated DB created here.
    with tempfile.TemporaryDirectory(prefix='scenario-', dir=args.out) as root:
        capture = run_scenario(Path(root))
    for name, value in [('requests.json', capture), ('analysis.json', analyze(capture, args.block))]:
        (args.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(analyze(capture, args.block)['total'], ensure_ascii=False))


if __name__ == '__main__':
    main()
