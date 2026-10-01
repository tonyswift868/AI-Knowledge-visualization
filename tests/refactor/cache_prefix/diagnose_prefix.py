"""Opt-in offline diagnostic; no credentials, sockets or model calls.

Run with the isolated prefixcash environment, from the repository root:
python -m tests.refactor.cache_prefix.diagnose_prefix --calls PATH --out PATH
Without both arguments it skips. prefixcash is NOT a product dependency.
Its whitespace words are positions, not DeepSeek tokens. The separate character
LCP/token estimate is also not a measured cache hit or a checkpoint guarantee.
"""
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
import argparse
import html
import json
from pathlib import Path

from refactor.learning.connections.pricing import estimate_tokens
from refactor.learning.course.request_layout import AppendOnlyPage
PURPOSES = {'teacher', 'judge', 'questions', 'profile', 'lesson_prep', 'lesson_review', 'check_profile'}


def canonical(row):
    # Same public framing as batch25, without importing the Qt scenario runner.
    text = '<system>\n' + (row.get('system') or '') + '\n</system>'
    for m in row['messages']:
        text += '\n<' + m['role'] + '>\n' + m['content'] + '\n</' + m['role'] + '>'
    return text


def lcp(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def normalized(row):
    """Program Usage counts all input, unlike Anthropic raw input_tokens.

    prefixcash.to_metrics('deepseek', Anthropic raw) falls back to OpenAI and
    loses counts. Feed its documented normalized format; never sum twice.
    Unknown fields remain unknown in our result (not measured zero).
    """
    usage = row.get('usage') or {}
    absent = usage.get('raw', {}).get('_mentorflow', {}).get('unreported_fields', [])
    known = bool(usage.get('input_tokens')) and not {'input_tokens', 'cached_input_tokens'} & set(absent)
    return dict(provider='deepseek', model=row.get('model', 'deepseek-v4-flash'),
                session_id=row['purpose'], agent=row['purpose'], ts=row.get('at'),
                prompt=canonical(row), usage=dict(input_tokens=usage.get('input_tokens', 0),
                cache_read_tokens=usage.get('cached_input_tokens', 0),
                output_tokens=usage.get('output_tokens', 0))), known


def transcript(row):
    return [json.loads(m['content'].partition('\n')[2]) for m in row['messages']
            if m['content'].startswith('【对话原文（数据）】\n')]


def rearrange(rows):
    """Replay the assembly on saved output text, not a future model prediction."""
    pages = defaultdict(AppendOnlyPage)
    result = []
    for original in rows:
        row = deepcopy(original)
        if row['purpose'] in ('teacher', 'profile'):
            dialogue = transcript(original)
            page = pages[row['purpose']]
            row['messages'] = page.assemble(row['system'], row['messages'], dialogue)
            if row.get('status') == 'received':
                page.accept(row['system'], row['messages'], dialogue, row.get('text', ''))
        result.append(row)
    return result


def positions(rows):
    previous = {}
    result = []
    for row in rows:
        purpose = row['purpose']
        text = canonical(row)
        prev = previous.get(purpose)
        n = lcp(text, canonical(prev)) if prev else 0
        output_n = n
        if prev and prev.get('status') == 'received' and prev.get('text'):
            # V4 persists public output boundaries as well as complete inputs.
            # This is still a public-framing estimate, not hidden chat tokens.
            completed = dict(prev, messages=[*prev['messages'],
                             dict(role='assistant',content=prev['text'])])
            output_n = max(n, lcp(text, canonical(completed)))
        estimated = estimate_tokens(text)
        fraction = min(1, estimate_tokens(text[:n]) / estimated) if estimated and prev else 0
        _, known = normalized(row)
        usage = row.get('usage') or {}
        result.append(dict(id=row.get('id', row.get('index')), purpose=purpose, first=prev is None,
            chars=len(text), break_char=n, last_label=text[:n].rpartition('【')[2].partition('】')[0],
            break_context=text[max(0, n-40):n+100], estimated_input=estimated,
            lcp_estimated_rate=fraction, known_usage=known,
            output_boundary_char=output_n,
            output_boundary_estimated_rate=min(1,estimate_tokens(text[:output_n])/estimated) if estimated and prev else 0,
            input_tokens=usage.get('input_tokens') if known else None,
            cached_tokens=usage.get('cached_input_tokens') if known else None))
        previous[purpose] = row
    return result


def summary(items, *, measured=True):
    groups = defaultdict(list)
    for item in items:
        groups[item['purpose']].append(item)
    result = {}
    for purpose, group in groups.items():
        warm = [i for i in group if not i['first']]
        known = [i for i in warm if i['known_usage']]
        inp = sum(i['input_tokens'] for i in known)
        est = sum(i['estimated_input'] for i in warm)
        result[purpose] = dict(calls=len(group), warm=len(warm), known_warm=len(known),
            measured_warm_rate=sum(i['cached_tokens'] for i in known)/inp if inp and measured else None,
            measured_input=inp if measured else None, estimated_input=est,
            lcp_estimated_warm_rate=sum(i['lcp_estimated_rate']*i['estimated_input'] for i in warm)/est if est else None,
            output_boundary_estimated_warm_rate=sum(i['output_boundary_estimated_rate']*i['estimated_input'] for i in warm)/est if est else None)
    return result


def run(calls_dir, out):
    # Imported ONLY when explicitly requested in the isolated environment.
    from prefixcash import iter_calls, diagnose_log, build_heatmap
    rows = [json.loads(p.read_text(encoding='utf-8')) for p in Path(calls_dir).glob('*.json')]
    rows = [r for r in rows if r.get('purpose') in PURPOSES and r.get('messages')]
    rows.sort(key=lambda r: (r.get('at', ''), r.get('id', '')))
    out.mkdir(parents=True, exist_ok=True)
    converted = [normalized(row) for row in rows]
    path = out/'normalized.jsonl'
    path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r, known in converted if known), encoding='utf-8')
    calls = list(iter_calls(path))
    assert sum(c.metrics.input_tokens for c in calls) == sum(r['usage']['input_tokens'] for r, known in converted if known)
    assert sum(c.metrics.cache_read_tokens for c in calls) == sum(r['usage']['cache_read_tokens'] for r, known in converted if known)
    findings = {key: [asdict(f) for f in value] for key, value in diagnose_log(str(path)).items()}
    grouped = defaultdict(list)
    for call in calls:
        grouped[call.metrics.session_id].append(call)
    heatmaps = {key: asdict(build_heatmap(key, value)) for key, value in grouped.items()}
    before, after = positions(rows), positions(rearrange(rows))
    for item in after:
        item.update(usage_origin='not remeasured', known_usage=False,
                    input_tokens=None, cached_tokens=None)
    result = dict(requests=len(rows), known_requests=len(calls), unknown_requests=len(rows)-len(calls),
        measured_input=sum(c.metrics.input_tokens for c in calls),
        measured_cached=sum(c.metrics.cache_read_tokens for c in calls),
        before=before, after=after, before_summary=summary(before), after_summary=summary(after, measured=False),
        findings=findings, heatmaps=heatmaps,
        limitations=['word positions are not BPE tokens', 'after usage is NOT remeasured; only LCP estimate',
                    'unknown usage excluded from prefixcash usage diagnostics',
                    'saved received text may not have passed host parsing; offline replay is assembly only'])
    (out/'diagnosis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    # Compact inspectable figure with labels, no additional graphics dependency.
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1050" height="'+str(70+len(grouped)*60)+'">',
         '<rect width="100%" height="100%" fill="#fff"/>',
         '<text x="20" y="25" font-family="sans-serif" fill="#111">prefixcash whitespace-word boundaries (not measured cache tokens)</text>']
    for i,(purpose,data) in enumerate(heatmaps.items()):
        cells=data['cells']; boundary=min(data['breaks'], default=len(cells))
        width=680*boundary/max(1,len(cells)); y=60+i*60
        svg += [f'<text x="20" y="{y+18}" fill="#111">{html.escape(purpose)}</text>',
                f'<rect x="150" y="{y}" width="680" height="22" fill="#e6a65a"/>',
                f'<rect x="150" y="{y}" width="{width:.2f}" height="22" fill="#26855d"/>',
                f'<line x1="{150+width:.2f}" x2="{150+width:.2f}" y1="{y-4}" y2="{y+26}" stroke="#c32026" stroke-width="3"/>',
                f'<text x="850" y="{y+18}" fill="#111">word {boundary}/{len(cells)}</text>']
    (out/'prefix-breaks.svg').write_text(''.join(svg)+'</svg>', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calls', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.calls is None or args.out is None:
        print('SKIPPED: opt in with --calls and --out; offline only')
        return
    result = run(args.calls, args.out)
    print(json.dumps({k:result[k] for k in ('requests','known_requests','unknown_requests','measured_input','measured_cached','before_summary','after_summary')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
