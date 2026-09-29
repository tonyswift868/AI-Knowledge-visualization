"""One evidence-bound teacher comment per point; never changes mastery or progress.

The model selects a short teaching action. The historical claim is assembled from
stored judgements, so a free-form answer cannot invent a past mistake.
"""
from __future__ import annotations

import json


CHOICES = {
    'review': '接下来可再复述一遍原文要点。',
    'apply': '接下来可换一道应用题巩固。',
    'compare': '接下来可与相近概念对照练习。',
}


class CommentFormatError(ValueError):
    pass


def evidence_for_report(store, plan_id, report):
    points = {p['id']: p for p in store.records('points', plan_id)}
    chunks = {c['id']: c['text'] for c in store.records('source_chunks', plan_id)}
    judgements = store.records('judgements', plan_id)
    evidence = []
    for point in report['points']:
        pid = point['point_id']
        source = points[pid]
        events = [dict(id=j['id'], context=j['event']['context'], correct=correct)
                  for j in judgements for key, correct in j['event']['results'] if key == pid]
        excerpts = [chunks[cid][:500] for cid in source.get('chunk_ids', []) if cid in chunks][:2]
        evidence.append(dict(point_id=pid, summary=point['summary'], source_excerpts=excerpts,
                             judgements=events, errors=dict(casual=point['casual_errors'],
                                 module=point['module_errors'], check=point['check_errors_before_pass']),
                             now_passed=point['level'] == 3))
    return evidence


def build_request(evidence):
    system = ('你是学习报告的老师。只依据给出的原文要点与逐点判定记录选择下一步练习方式。'
              '只返回 JSON：{"comments":[{"point_id":"...","action":"review|apply|compare"}]}。'
              '每个点恰好一个；不要写任何历史事实或解释。')
    return system, [{'role': 'user', 'content': json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))}]


def parse_comments(raw, evidence):
    try:
        value = json.loads(raw)
        rows = value['comments']
        if set(value) != {'comments'} or not isinstance(rows, list) or len(rows) != len(evidence):
            raise ValueError()
        expected = {p['point_id']: p for p in evidence}
        if len(expected) != len(evidence):
            raise ValueError()
        choices = {}
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {'point_id', 'action'}
                    or row['point_id'] not in expected or row['point_id'] in choices
                    or row['action'] not in CHOICES):
                raise ValueError()
            choices[row['point_id']] = row['action']
        if set(choices) != set(expected):
            raise ValueError()
    except (TypeError, ValueError, KeyError):
        raise CommentFormatError('评语格式无效。') from None
    comments = {}
    for point in evidence:
        pid = point['point_id']
        errors = point['errors']
        parts = []
        if errors['casual']:
            parts.append(f"随口问曾错 {errors['casual']} 次")
        if errors['module']:
            parts.append(f"模块检测曾错 {errors['module']} 次")
        if errors['check']:
            parts.append(f"最终检验通过前曾错 {errors['check']} 次")
        history = '、'.join(parts) + '；' if parts else '记录中没有答错；'
        now = '现已通过，' if point['now_passed'] else '目前尚未通过，'
        comments[pid] = history + now + CHOICES[choices[pid]]
    return comments


def generate_comments(gateway, store, plan_id, report, *, cancel=None):
    evidence = evidence_for_report(store, plan_id, report)
    output = {}
    for start in range(0, len(evidence), 20):
        batch = evidence[start:start + 20]
        system, messages = build_request(batch)
        try:
            result = gateway.complete('report_comment', messages, system=system,
                                      plan_id=plan_id, max_tokens=4096, cancel=cancel)
            output.update(parse_comments(result.text, batch))
        except Exception:
            # Each batch is optional; the saved event-based report remains readable.
            output.update({item['point_id']: None for item in batch})
    return output
