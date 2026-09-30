"""Replay saved real prompts; prefix equality is exact, token positions estimated.

No model calls, credentials or user-data writes. Aggregate measured counters
separately from per-request token-scaled prefix estimates. Not a server cache
simulator: undocumented checkpoint sizes/persistence remain unresolved.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

from refactor.learning.connections.pricing import estimate_tokens
from .audit import canonical, lcp

PURPOSES = {'teacher', 'judge', 'questions', 'profile', 'lesson_prep', 'lesson_review', 'check_profile'}


def load_calls(path):
    rows = [json.loads(p.read_text(encoding='utf-8')) for p in Path(path).glob('*.json')]
    return sorted((r for r in rows if r['purpose'] in PURPOSES), key=lambda r: (r['at'], r['id']))


def reorder(row):
    """Same field values/labels/roles; mirror the product assembly change."""
    from refactor.learning.course.request_layout import ordered_payload
    row = deepcopy(row)
    if row['purpose'] == 'teacher':
        # Point-scoped sources vary inside a module. They cannot be a stable
        # module prefix; keep the growing transcript ahead of that subset.
        skeleton, source, *rest = row['messages']
        skeleton['content'] = ordered_payload(skeleton['content'], ('plan', 'module'))
        row['messages'] = [skeleton, *rest[:-1], source, rest[-1]]
    elif row['purpose'] == 'profile':
        row['messages'][0]['content'] = ordered_payload(row['messages'][0]['content'], ('plan', 'module'))
    elif row['purpose'] == 'lesson_review':
        message = row['messages'][1]
        message['content'] = ordered_payload(message['content'], ('point', 'draft'))
    return row


def effort(row):
    context = (row.get('usage') or {}).get('raw', {}).get('_mentorflow', {}).get('billing_context', {})
    return context.get('requested_reasoning_effort', context.get('reasoning_effort'))


def analyze(rows):
    history = defaultdict(list)
    output = []
    for row in rows:
        text = canonical(row)
        # Also keep missing-usage requests: they were really submitted and may
        # have populated the cache. Missing counters never become zero input.
        candidates = [(lcp(text, t), p) for p, t in history[row['purpose']] if effort(p) == effort(row)]
        n, prior = max(candidates, key=lambda x: x[0], default=(0, None))
        units = estimate_tokens(text)
        fraction = min(1, estimate_tokens(text[:n]) / units) if units else 0
        u = row.get('usage')
        measured = bool(u and u.get('input_tokens'))
        total = u['input_tokens'] if measured else None
        hit = u['cached_input_tokens'] if measured else None
        theory = total * fraction if measured else None
        change = text[n:n+160]
        different = next((i for i, m in enumerate(row['messages'])
                          if prior is None or i >= len(prior['messages']) or m != prior['messages'][i]), len(row['messages'])-1)
        label = row['messages'][different]['content'].partition('\n')[0].strip('【】')
        if prior is None:
            cause = 'first_purpose'
        elif n == len(text):
            cause = 'identical_prompt'
        elif n < len('<system>\n' + row['system'] + '\n</system>'):
            cause = 'system_variant'
        elif label == '当前模块原文（数据）':
            cause = 'point_source_subset'
        elif label in ('当前模块原文', '资料原文（数据）'):
            cause = 'module_source_change'
        elif label == '计划与当前模块':
            cause = 'module_change'
        elif label == '对话原文（数据）':
            # A block tail can restart from a different original turn.
            start = next((m['content'] for m in row['messages'] if m['content'].startswith('【对话原文')), None)
            old_start = next((m['content'] for m in prior['messages'] if m['content'].startswith('【对话原文')), None)
            cause = 'dialogue_window_reset' if old_start is not None and start != old_start else 'dialogue_append'
        else:
            cause = {'lesson_review':'new_draft', 'judge':'new_question_answer',
                     'teacher':'new_teacher_task', 'lesson_prep':'new_point_task',
                     'profile':'new_profile_state', 'questions':'new_question_requirement'}.get(row['purpose'], 'new_task')
        gap = (datetime.fromisoformat(row['at']) - datetime.fromisoformat(prior['at'])).total_seconds() if prior else None
        output.append(dict(id=row['id'], purpose=row['purpose'], at=row['at'], status=row['status'],
            measured=measured, input_tokens=total, cached_tokens=hit, prefix_tokens_scaled=theory,
            prefix_chars=n, prompt_chars=len(text), previous=prior['id'] if prior else None,
            gap_seconds=gap, cause=cause, boundary=label, mismatch=change,
            structural_miss_scaled=total-theory if measured else None,
            server_gap_scaled=theory-hit if measured else None,
            actual_miss=total-hit if measured else None,
            attributed_structure_est=min(total-hit, total-theory) if measured else None,
            unattributed_est=max(0, theory-hit) if measured else None))
        history[row['purpose']].append((row, text))
    def group(items):
        known = [r for r in items if r['measured']]
        total = sum(r['input_tokens'] for r in known)
        hit = sum(r['cached_tokens'] for r in known)
        theoretical = sum(r['prefix_tokens_scaled'] for r in known)
        return dict(calls=len(items), measured_calls=len(known), missing_calls=len(items)-len(known),
            input_tokens=total, cached_tokens=hit, prefix_tokens_scaled=theoretical,
            actual_rate=hit/total if total else None, theoretical_rate=theoretical/total if total else None,
            structural_miss_scaled=total-theoretical, server_gap_scaled=theoretical-hit,
            actual_miss=sum(r['actual_miss'] for r in known),
            attributed_structure_est=sum(r['attributed_structure_est'] for r in known),
            unattributed_est=sum(r['unattributed_est'] for r in known))
    by_purpose = defaultdict(list)
    by_cause = defaultdict(list)
    for r in output:
        by_purpose[r['purpose']].append(r)
        by_cause[r['cause']].append(r)
    warm = [r for r in output if r['previous'] is not None]
    return dict(token_basis='Exact character LCP; estimate_tokens prefix fraction scaled by each measured input. NOT exact DeepSeek token positions.',
        total=group(output), warm=group(warm), purposes={k: group(v) for k,v in by_purpose.items()},
        causes={k: group(v) for k,v in by_cause.items()}, requests=output)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--calls', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args=parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows=load_calls(args.calls)
    for name, value in [('before.json',analyze(rows)), ('after.json',analyze([reorder(r) for r in rows]))]:
        value['counter_provenance'] = 'Historical baseline counters, NOT post-change measurements.' if name == 'after.json' else 'Saved server counters from original requests.'
        (args.out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        print(name, json.dumps(value['total']))


if __name__ == '__main__':
    main()
