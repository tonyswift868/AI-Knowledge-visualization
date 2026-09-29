"""Deterministic report comments assembled solely from stored judgements."""
from __future__ import annotations


def evidence_for_report(store, plan_id, report):
    """Keep judgement order stable, including imported events received out of order."""
    rows = sorted(store.records('judgements', plan_id),
                  key=lambda row: (row['event']['seq'], row['event']['event_id']))
    evidence = []
    for point in report['points']:
        pid = point['point_id']
        mistakes = []
        for row in rows:
            for key, correct in row['event']['results']:
                if key == pid and not correct:
                    mistakes.append((row['event']['context'], (row.get('details') or {}).get('reason', '')))
        evidence.append(dict(point_id=pid, summary=point.get('summary') or '',
                             mistakes=mistakes, passed=point['level'] == 3))
    return evidence


def generate_comments(gateway, store, plan_id, report, *, cancel=None):
    """The gateway argument is retained for the existing report call site; never used."""
    comments = {}
    names = {'casual': '随口问', 'quick_confirm': '快速确认', 'module_test': '模块检测',
             'skip_test': '直接考', 'retest': '重测', 'check': '最终检验'}
    for row in evidence_for_report(store, plan_id, report):
        mistakes = row['mistakes']
        counts = {kind: sum(1 for context, _ in mistakes if context == kind) for kind in names}
        stages = [f'{name}错 {counts[kind]} 次' for kind, name in names.items() if counts[kind]]
        reasons = list(dict.fromkeys(reason.strip()[:120] for _, reason in mistakes if reason.strip()))[:2]
        status = '现已通过。' if row['passed'] else '目前尚未通过。'
        quiet = ('“' + row['summary'].strip()[:40] + '”无错答记录；') if row['summary'].strip() else '记录中没有答错；'
        comments[row['point_id']] = ('、'.join(stages) + '；' if stages else quiet) + (
            '当时的判定原因：' + '；'.join(reasons) + '；' if reasons else '') + status
    return comments
